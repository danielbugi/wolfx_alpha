"""The asymmetric breakout study (spec.py). PURE: numpy + stdlib; no database, clock, network or environment.

Reused, not re-implemented: Wilder ATR and the month-block bootstrap from `research.strategy_lab.engine`, the price-bar container from `research.strategy_lab.data`, and the
fill conventions of `research.exit_policies` (stop at its level or at the open when the bar gaps through it, the entry bar's range counts, levels from earlier bars only,
time exit at the cap bar's close). The chandelier exit is tested to be identical to the registered `C_atr_trail_3x` policy when started from the same stop.
What is new: structural stops (the actual risk at entry is 1R), long holds (250 bars), weekly/trend exits, tail statistics, a capacity-limited portfolio and the decision rule.

Point in time: a signal uses bars <= s only (a test rewrites every later bar and requires identical signals); the cross-sectional inputs (RS rank, regime) use the same date's
closes of the other names. Every price is vendor back-adjusted (revised), which the report states.
"""
from __future__ import annotations

import csv
import gzip
import heapq
import json
import math
import os
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from research.strategy_lab.data import Bars, DataError, sha256_file
from research.strategy_lab.engine import atr14, block_ci, block_diff_ci, without_top

EXITS = ("X1_stop_only", "X2_atr_trail", "X3_weekly_trail", "X4_trend_sma50", "X5_time_60")
CAP = 250
TAIL_LEVELS = (3.0, 5.0, 10.0, 20.0)
LOOKBACK_GUARD = 260


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ data
@dataclass
class Universe:
    bars: Dict[str, Bars]
    disc: Dict[str, List[date]]
    data_hash: str
    meta: Mapping[str, Any] = field(default_factory=dict)


def load_extract(directory: str) -> Universe:
    """prices.csv.gz + discontinuities.csv.gz, refused when either differs from the sha256 recorded in extract_meta.json."""
    meta = json.load(open(os.path.join(directory, "extract_meta.json"), encoding="utf-8"))
    paths = {k: os.path.join(directory, f"{k}.csv.gz") for k in ("prices", "discontinuities")}
    shas = {k: sha256_file(p) for k, p in paths.items()}
    for k in paths:
        if shas[k] != meta.get(f"{k}_sha256"):
            raise DataError(f"{k}.csv.gz does not match extract_meta.json; refused")
    raw: Dict[str, List[Tuple]] = defaultdict(list)
    with gzip.open(paths["prices"], "rt", newline="") as fh:
        rd = csv.reader(fh)
        if next(rd) != ["symbol", "date", "open", "high", "low", "close", "volume"]:
            raise DataError("unexpected prices header")
        for sym, d, o, h, l, c, v in rd:
            if "" in (o, h, l, c):
                continue
            raw[sym].append((d, float(o), float(h), float(l), float(c), float(v) if v else 0.0))
    bars: Dict[str, Bars] = {}
    for sym, rows in raw.items():
        ds = [date.fromisoformat(r[0]) for r in rows]
        if any(ds[i] >= ds[i + 1] for i in range(len(ds) - 1)):
            continue                                              # unordered or duplicated dates: unusable, never repaired
        a = np.array([r[1:] for r in rows], dtype=float)
        bars[sym] = Bars(ds, a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4])
    disc: Dict[str, List[date]] = defaultdict(list)
    with gzip.open(paths["discontinuities"], "rt", newline="") as fh:
        rd = csv.reader(fh)
        next(rd)
        for sym, d, _kind, _ratio in rd:
            disc[sym].append(date.fromisoformat(d))
    data_hash = __import__("hashlib").sha256(f"prices:{shas['prices']}|disc:{shas['discontinuities']}".encode()).hexdigest()
    return Universe(bars, {k: sorted(set(v)) for k, v in disc.items()}, data_hash, meta)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ indicators (bars <= i only)
def _roll(x: np.ndarray, n: int, fn) -> np.ndarray:
    """fn over x[i-n+1..i]; NaN before n values exist."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = fn(np.lib.stride_tricks.sliding_window_view(x, n), axis=1)
    return out


def _prev(x: np.ndarray) -> np.ndarray:
    """x shifted one bar later: value at i is x[i-1] (the window that ENDS before bar i)."""
    return np.r_[np.nan, x[:-1]]


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return _roll(x, n, np.mean)


def weekly_trail_level(dates: Sequence[date], low: np.ndarray, weeks: int = 3) -> np.ndarray:
    """At bar j: the lowest low of the `weeks` calendar weeks COMPLETED before the week of bar j (NaN until that many weeks exist)."""
    keys = [d.isocalendar()[:2] for d in dates]
    order, wlow, wid = {}, [], np.empty(len(dates), dtype=int)
    for j, k in enumerate(keys):
        if k not in order:
            order[k] = len(wlow)
            wlow.append(low[j])
        else:
            wlow[order[k]] = min(wlow[order[k]], low[j])
        wid[j] = order[k]
    W = np.array(wlow, dtype=float)
    m = np.full(len(W), np.nan)
    for k in range(weeks, len(W)):
        m[k] = W[k - weeks:k].min()
    return m[wid]


@dataclass
class SymbolData:
    sym: str
    bars: Bars
    atr: np.ndarray
    sma50: np.ndarray
    wk3: np.ndarray
    liquid: np.ndarray
    ret126: np.ndarray
    d55: np.ndarray            # bool: fresh 55-bar breakout at s
    base: np.ndarray           # bool: fresh base breakout at s
    stop_d55: np.ndarray
    stop_base: np.ndarray
    vol_ok: np.ndarray
    gidx: np.ndarray           # index of each bar in the global calendar
    disc_idx: np.ndarray       # bar indexes of flagged discontinuities (sorted)
    feats: Dict[str, np.ndarray] = field(default_factory=dict)


def prepare_symbol(sym: str, b: Bars, disc_dates: Sequence[date], spec: Mapping[str, Any]) -> Optional[SymbolData]:
    n = len(b.c)
    if n < spec["universe"]["min_history_bars"] + 2:
        return None
    o, h, l, c, v = b.o, b.h, b.l, b.c, b.v
    liq = spec["universe"]["liquidity_at_signal"]
    atr = atr14(h, l, c)
    hh55p, hh40p, hh252p = _prev(_roll(h, 55, np.max)), _prev(_roll(h, 40, np.max)), _prev(_roll(h, 252, np.max))
    ll40p, ll20 = _prev(_roll(l, 40, np.min)), _roll(l, 20, np.min)
    with np.errstate(invalid="ignore", divide="ignore"):
        d55 = (c > hh55p) & (_prev(c) <= _prev(hh55p))
        depth = (hh40p - ll40p) / hh40p
        base = (c > hh40p) & (_prev(c) <= _prev(hh40p)) & (depth <= 0.25) & (hh40p >= 0.85 * hh252p)
        adv20 = _roll(c * v, 20, np.mean)
        liquid = (c >= liq["min_close"]) & (adv20 >= liq["min_avg_dollar_volume_20"])
        vmean50p = _prev(_roll(v, 50, np.mean))
        vol_ok = v >= 1.5 * vmean50p
        ret126 = np.full(n, np.nan)
        ret126[126:] = c[126:] / c[:-126] - 1.0
        sma200 = sma(c, 200)
        feats = {"atr_pct": atr / c * 100.0, "vol_ratio": v / vmean50p, "depth40": depth, "dist_52w_high": c / hh252p - 1.0,
                 "ret20": np.r_[np.full(20, np.nan), c[20:] / c[:-20] - 1.0], "above_sma200": (c > sma200).astype(float), "adv20_musd": adv20 / 1e6}
    feats["above_sma200"][~np.isfinite(sma200)] = np.nan
    di = np.searchsorted(np.array(b.dates, dtype="datetime64[D]"), np.array(list(disc_dates), dtype="datetime64[D]")) if disc_dates else np.array([], dtype=int)
    bad = np.flatnonzero((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0) | (h < l))
    di = np.unique(np.r_[di, bad]).astype(int)
    return SymbolData(sym, b, atr, sma(c, 50), weekly_trail_level(b.dates, l), np.nan_to_num(liquid, nan=0).astype(bool), ret126,
                      np.nan_to_num(d55, nan=0).astype(bool), np.nan_to_num(base, nan=0).astype(bool), ll20, ll40p,
                      np.nan_to_num(vol_ok, nan=0).astype(bool), np.empty(0, dtype=int), di, feats)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ cross-section
@dataclass
class Market:
    dates: List[date]
    rs_rank: Dict[str, np.ndarray]          # per symbol, aligned with its own bars
    regime_on: np.ndarray                   # per global date
    breadth: np.ndarray                     # per global date: share of liquid names above their 200-session mean
    ew_index: np.ndarray


def build_market(sd: Mapping[str, SymbolData]) -> Market:
    """RS rank (cross-sectional percentile of the 126-session return among liquid names on each date), the equal-weight median-return index and its 200-session regime, breadth."""
    all_dates = sorted({d for s in sd.values() for d in s.bars.dates})
    pos = {d: i for i, d in enumerate(all_dates)}
    T, syms = len(all_dates), sorted(sd)
    R = np.full((T, len(syms)), np.nan, dtype=np.float32)
    D = np.full((T, len(syms)), np.nan, dtype=np.float32)
    A = np.full((T, len(syms)), np.nan, dtype=np.float32)
    for k, s in enumerate(syms):
        x = sd[s]
        x.gidx = np.array([pos[d] for d in x.bars.dates], dtype=int)
        r = np.where(x.liquid, x.ret126, np.nan)
        R[x.gidx, k] = r
        dr = np.full(len(x.bars.c), np.nan)
        dr[1:] = x.bars.c[1:] / x.bars.c[:-1] - 1.0
        dr[1:][~x.liquid[:-1]] = np.nan                           # a name counts in day t's median only when it was liquid at t-1
        D[x.gidx, k] = dr
        A[x.gidx, k] = np.where(x.liquid, x.feats["above_sma200"], np.nan)
    rank = {}
    P = np.full_like(R, np.nan)
    for t in range(T):
        row = R[t]
        ok = np.isfinite(row)
        m = int(ok.sum())
        if m >= 50:
            o = np.argsort(row[ok], kind="stable")
            rk = np.empty(m)
            rk[o] = (np.arange(m) + 1) / m
            P[t, np.flatnonzero(ok)] = rk
    for k, s in enumerate(syms):
        rank[s] = P[sd[s].gidx, k].astype(float)
    with np.errstate(all="ignore"):
        med = np.nanmedian(D, axis=1)
        breadth = np.nanmean(A, axis=1)
    idx = np.cumprod(1.0 + np.nan_to_num(med, nan=0.0))
    s200 = sma(idx, 200)
    regime = np.where(np.isfinite(s200), idx > s200, False)
    return Market(all_dates, rank, regime, breadth, idx)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ exits
@dataclass(frozen=True)
class Outcome:
    exit_bar: int
    exit_price: float
    reason: str               # stop | trail | trend | time
    censored: bool
    slippage_r: float         # stop exits only: (level - fill) / risk when the bar gapped through the level, else 0


def _first_hit(o: np.ndarray, l: np.ndarray, level: np.ndarray) -> int:
    hit = np.flatnonzero((o <= level) | (l <= level))
    return int(hit[0]) if len(hit) else -1


def simulate_exit(kind: str, x: SymbolData, e: int, stop0: float, atr_s: float, cap: int = CAP) -> Outcome:
    """One long position entered at the open of bar e with initial stop `stop0`. Levels in force on bar j use bars < j only; the stop is checked before anything else on a bar."""
    b = x.bars
    n = len(b.c)
    last = e + (60 if kind == "X5_time_60" else cap) - 1
    end = min(last, n - 1)
    o, h, l, c = b.o[e:end + 1], b.h[e:end + 1], b.l[e:end + 1], b.c[e:end + 1]
    entry = float(b.o[e])
    risk = entry - stop0
    if kind in ("X1_stop_only", "X5_time_60", "X4_trend_sma50"):
        level = np.full(len(o), stop0)
    elif kind == "X2_atr_trail":
        best = np.maximum.accumulate(np.r_[entry, h[:-1]])
        level = np.maximum(stop0, best - 3.0 * atr_s)
    elif kind == "X3_weekly_trail":
        w = np.nan_to_num(x.wk3[e:end + 1], nan=-np.inf)
        level = np.maximum(stop0, np.maximum.accumulate(w))
    else:
        raise ValueError(kind)
    js = _first_hit(o, l, level)
    jt = -1
    if kind == "X4_trend_sma50":
        t = np.flatnonzero(c < x.sma50[e:end + 1])
        jt = int(t[0]) if len(t) else -1
    if js >= 0 and (jt < 0 or js <= jt):
        lv = float(level[js])
        fill = float(o[js]) if o[js] <= lv else lv
        reason = "stop" if lv <= stop0 + 1e-12 else "trail"
        return Outcome(e + js, fill, reason, False, (lv - fill) / risk)
    if jt >= 0:
        if e + jt == last:                                        # the close below the mean falls on the cap bar: the time exit at that close comes first
            return Outcome(last, float(c[jt]), "time", False, 0.0)
        if e + jt + 1 > n - 1:
            return Outcome(n - 1, float(b.c[n - 1]), "open", True, 0.0)
        return Outcome(e + jt + 1, float(b.o[e + jt + 1]), "trend", False, 0.0)
    if last <= n - 1:
        return Outcome(last, float(b.c[last]), "time", False, 0.0)
    return Outcome(n - 1, float(b.c[n - 1]), "open", True, 0.0)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ positions
@dataclass
class Pos:
    set_name: str
    exit_kind: str
    sym: str
    s: int
    signal_date: date
    entry_bar: int
    entry: float
    stop0: float
    risk: float
    out: Outcome
    exit_date: date
    segment: str
    path_disc: bool
    rs: float
    regime_on: bool
    breadth: float
    feats: Dict[str, float]
    mfe_r: float
    mae_r: float
    mfe250_r: float               # the theoretical maximum within 250 bars of entry, whatever the exit did (NaN when immature)
    entry_gap_r: float            # (entry open - signal close) / risk

    def r_gross(self) -> float:
        return (self.out.exit_price - self.entry) / self.risk

    def r_net(self, bps: float) -> float:
        return self.r_gross() - (bps / 1e4) * self.entry / self.risk

    @property
    def duration(self) -> int:
        return self.out.exit_bar - self.entry_bar + 1


def segment_of(spec: Mapping[str, Any], signal_date: date, exit_date: date) -> str:
    sp = spec["split"]
    d0, d1 = date.fromisoformat(sp["discovery"][0]), date.fromisoformat(sp["discovery"][1])
    v0 = date.fromisoformat(sp["validation_start"])
    if signal_date < d0:
        return "warmup"
    if signal_date >= v0:
        return "validation"
    return "purged" if exit_date >= d1 else "discovery"


def set_mask(x: SymbolData, rs: np.ndarray, regime_at: np.ndarray, set_name: str) -> np.ndarray:
    parts = set_name.split("+")
    m = (x.d55 if parts[0] == "D55" else x.base).copy()
    m &= x.liquid
    if "RS" in parts:
        m &= np.nan_to_num(rs, nan=0.0) >= 0.80
    if "VOL" in parts:
        m &= x.vol_ok
    if "REGIME" in parts:
        m &= regime_at
    m[:LOOKBACK_GUARD] = False
    m[-1] = False                                                 # needs an entry bar
    return m


class Simulator:
    """Memoised standalone trades: (family, symbol, s, exit) -> Pos (or a skip reason). Entry sets share the trades of their family, and the one-open-position rule is applied
    per (set, exit, symbol) on top of them."""

    def __init__(self, spec: Mapping[str, Any], sd: Mapping[str, SymbolData], mk: Market):
        self.spec, self.sd, self.mk = spec, sd, mk
        self.cache: Dict[Tuple[str, str, int, str], Any] = {}
        self.max_risk = 0.30

    def trade(self, family: str, sym: str, s: int, kind: str, set_name: str = "") -> Any:
        key = (family, sym, s, kind)
        if key in self.cache:
            return self.cache[key]
        x = self.sd[sym]
        b = x.bars
        e = s + 1
        entry = float(b.o[e])
        atr_s = float(x.atr[s])
        struct = float(x.stop_d55[s] if family in ("D55", "RANDOM") else x.stop_base[s])
        res: Any
        if not (math.isfinite(atr_s) and atr_s > 0 and math.isfinite(struct)):
            res = "skip_no_atr_or_structure"
        else:
            stop0 = min(struct, entry - atr_s)
            risk = entry - stop0
            if entry <= stop0:
                res = "skip_entry_at_or_below_stop"
            elif risk > self.max_risk * entry:
                res = "skip_risk_above_30pct"
            elif len(x.disc_idx) and np.any((x.disc_idx >= s - LOOKBACK_GUARD) & (x.disc_idx <= s)):
                res = "skip_discontinuity_in_lookback"
            else:
                out = simulate_exit(kind, x, e, stop0, atr_s)
                path_disc = bool(len(x.disc_idx) and np.any((x.disc_idx > s) & (x.disc_idx <= out.exit_bar)))
                hi = b.h[e:out.exit_bar + 1] if out.reason not in ("trend",) else np.r_[b.h[e:out.exit_bar], b.o[out.exit_bar]]
                lo = b.l[e:out.exit_bar + 1] if out.reason not in ("trend",) else np.r_[b.l[e:out.exit_bar], b.o[out.exit_bar]]
                mfe = (float(np.max(hi)) - entry) / risk
                mae = (entry - float(np.min(lo))) / risk
                mfe250 = (float(np.max(b.h[e:e + CAP])) - entry) / risk if e + CAP - 1 <= len(b.c) - 1 else float("nan")
                gd = int(x.gidx[s])
                res = Pos(set_name, kind, sym, s, b.dates[s], e, entry, stop0, risk, out, b.dates[out.exit_bar],
                          segment_of(self.spec, b.dates[s], b.dates[out.exit_bar]), path_disc, float(self.mk.rs_rank[sym][s]),
                          bool(self.mk.regime_on[gd]), float(self.mk.breadth[gd]), {k: float(v[s]) for k, v in x.feats.items()},
                          mfe, mae, mfe250, (entry - float(b.c[s])) / risk)
        self.cache[key] = res
        return res


def account(sim: Simulator, set_name: str, kind: str, counts: Counter) -> List[Pos]:
    """The one-open-position rule per symbol over the set's signals, in time order. A skipped signal (no trade) does not occupy the slot; a position with a path discontinuity does."""
    family = set_name.split("+")[0]
    out: List[Pos] = []
    for sym in sorted(sim.sd):
        x = sim.sd[sym]
        m = set_mask(x, sim.mk.rs_rank[sym], sim.mk.regime_on[x.gidx], set_name)
        last_exit = -1
        for s in np.flatnonzero(m):
            s = int(s)
            if s <= last_exit:
                counts["signal_skipped_position_open"] += 1
                continue
            t = sim.trade(family, sym, s, kind)
            if isinstance(t, str):
                counts[t] += 1
                continue
            last_exit = t.out.exit_bar
            if t.segment == "warmup":
                continue
            p = Pos(**{**t.__dict__, "set_name": set_name})
            out.append(p)
    return out


def random_controls(sim: Simulator, primary: Sequence[Pos], kind: str, seed: int) -> List[Pos]:
    """One random session per primary position: same symbol, same segment, liquid, >= 10 sessions from any primary signal of that symbol, D55's stop rule, the same exit."""
    by_sym: Dict[str, List[Pos]] = defaultdict(list)
    for p in primary:
        by_sym[p.sym].append(p)
    out: List[Pos] = []
    for sym in sorted(by_sym):
        x = sim.sd[sym]
        n = len(x.bars.c)
        blocked = np.zeros(n, dtype=bool)
        for p in by_sym[sym]:
            blocked[max(0, p.s - 10):min(n, p.s + 11)] = True
        rng = np.random.default_rng([seed, zlib.crc32(sym.encode())])
        for p in sorted(by_sym[sym], key=lambda q: q.s):
            for _ in range(40):
                j = int(rng.integers(LOOKBACK_GUARD, n - 1))
                if blocked[j] or not x.liquid[j]:
                    continue
                t = sim.trade("RANDOM", sym, j, kind)
                if isinstance(t, str) or t.segment != p.segment:
                    continue
                out.append(Pos(**{**t.__dict__, "set_name": "RANDOM"}))
                break
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ statistics
def bonferroni_ci(values: np.ndarray, months: np.ndarray, m: int, n_boot: int, seed: int) -> List[float]:
    keys = sorted(set(months.tolist()))
    blocks = [values[months == k] for k in keys]
    rng = np.random.default_rng(seed)
    means = np.array([np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))]).mean() for _ in range(n_boot)])
    a = 5.0 / m
    lo, hi = np.percentile(means, [a / 2, 100 - a / 2])
    return [round(float(lo), 4), round(float(hi), 4)]


def usable(ps: Sequence[Pos], include_path_disc: bool = False) -> List[Pos]:
    return [p for p in ps if not p.out.censored and (include_path_disc or not p.path_disc)]


def series(ps: Sequence[Pos], bps: float) -> Tuple[np.ndarray, np.ndarray]:
    return np.array([p.r_net(bps) for p in ps], dtype=float), np.array([p.exit_date.strftime("%Y-%m") for p in ps])


def cell_stats(ps_all: Sequence[Pos], bps: float, n_boot: int, seed: int, m_cells: int) -> Dict[str, Any]:
    ps = usable(ps_all)
    out: Dict[str, Any] = {"n": len(ps), "n_censored": sum(1 for p in ps_all if p.out.censored), "n_path_discontinuity_excluded": sum(1 for p in ps_all if p.path_disc and not p.out.censored)}
    if not ps:
        return out
    r, months = series(ps, bps)
    win, loss = r[r > 0], r[r <= 0]
    pos_sorted = np.sort(win)[::-1]
    k1 = max(1, int(len(r) * 0.01))
    stop_exits = [p for p in ps if p.out.reason in ("stop", "trail")]
    gapped = [p for p in stop_exits if p.out.slippage_r > 1e-9]
    mature = [p for p in ps if math.isfinite(p.mfe250_r)]
    years = sorted({p.signal_date.year for p in ps})
    ci = block_ci(r, months, n_boot, seed)
    out.update({
        "expectancy_r": round(float(r.mean()), 4), "median_r": round(float(np.median(r)), 4), "ci95": ci.get("ci95"),
        "ci_bonferroni": bonferroni_ci(r, months, m_cells, n_boot, seed + 7) if len(r) >= 20 else None,
        "win_rate": round(float((r > 0).mean()), 4), "avg_winner_r": round(float(win.mean()), 3) if len(win) else None, "avg_loser_r": round(float(loss.mean()), 3) if len(loss) else None,
        "profit_factor": round(float(win.sum() / -loss.sum()), 3) if loss.sum() < 0 else None,
        "share_ge": {f"{int(k)}R": round(float((r >= k).mean()), 4) for k in TAIL_LEVELS},
        "count_ge": {f"{int(k)}R": int((r >= k).sum()) for k in TAIL_LEVELS},
        "max_r": round(float(r.max()), 2), "min_r": round(float(r.min()), 2),
        "pctl": {q: round(float(np.percentile(r, int(q[1:]))), 3) for q in ("p10", "p25", "p75", "p90", "p99")},
        "expectancy_without_top_1pct": without_top(r), "top1pct_share_of_gross_profit": round(float(pos_sorted[:k1].sum() / win.sum()), 3) if win.sum() > 0 else None,
        "mfe_r_mean_in_trade": round(float(np.mean([p.mfe_r for p in ps])), 3), "mae_r_mean": round(float(np.mean([p.mae_r for p in ps])), 3),
        "mae_r_p90": round(float(np.percentile([p.mae_r for p in ps], 90)), 3),
        "theoretical_mfe250": {"n_mature": len(mature), "mean_r": round(float(np.mean([p.mfe250_r for p in mature])), 3) if mature else None,
                               "share_ge_5R": round(float(np.mean([p.mfe250_r >= 5 for p in mature])), 4) if mature else None,
                               "share_ge_10R": round(float(np.mean([p.mfe250_r >= 10 for p in mature])), 4) if mature else None,
                               "realised_gross_mean_same_positions": round(float(np.mean([p.r_gross() for p in mature])), 3) if mature else None},
        "duration_bars": {"mean": round(float(np.mean([p.duration for p in ps])), 1), "median": float(np.median([p.duration for p in ps]))},
        "exit_reasons": dict(sorted(Counter(p.out.reason for p in ps).items())),
        "initial_stop_out_share": round(float(np.mean([p.out.reason == "stop" for p in ps])), 4),
        "gap_through_stop": {"share_of_stop_exits": round(len(gapped) / len(stop_exits), 4) if stop_exits else None,
                             "mean_extra_loss_r_when_gapped": round(float(np.mean([p.out.slippage_r for p in gapped])), 3) if gapped else None,
                             "mean_over_all_positions_r": round(float(np.sum([p.out.slippage_r for p in gapped]) / len(ps)), 4)},
        "entry_gap_r_mean": round(float(np.mean([p.entry_gap_r for p in ps])), 3),
        "initial_risk_pct_median": round(float(np.median([p.risk / p.entry * 100 for p in ps])), 2),
        "positions_per_year": {str(y): sum(1 for p in ps if p.signal_date.year == y) for y in years},
    })
    return out


def censored_marked(ps_all: Sequence[Pos], bps: float) -> Dict[str, Any]:
    """Sensitivity: censored positions valued at the last close (an open winner is not a missing loser)."""
    r = np.array([p.r_net(bps) for p in ps_all if not p.path_disc], dtype=float)
    if not len(r):
        return {"n": 0}
    return {"n": int(len(r)), "expectancy_r": round(float(r.mean()), 4), "share_ge_5R": round(float((r >= 5).mean()), 4)}


def by_bucket(ps_all: Sequence[Pos], bps: float, key) -> Dict[str, Any]:
    g: Dict[str, List[float]] = defaultdict(list)
    for p in usable(ps_all):
        g[key(p)].append(p.r_net(bps))
    return {k: {"n": len(v), "expectancy_r": round(float(np.mean(v)), 4), "share_ge_5R": round(float(np.mean(np.array(v) >= 5)), 4)} for k, v in sorted(g.items())}


def breadth_bucket(p: Pos) -> str:
    b = p.breadth
    return "unknown" if not math.isfinite(b) else ("breadth<40%" if b < 0.4 else "breadth40-60%" if b <= 0.6 else "breadth>60%")


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ portfolio
def portfolio(ps: Sequence[Pos], sd: Mapping[str, SymbolData], mk: Market, bps: float, max_conc: int, risk_pct: float) -> Dict[str, Any]:
    """Capacity-limited book: positions taken in signal order (then higher RS, then symbol) while fewer than `max_conc` are open; each risks 1R = `risk_pct` % of INITIAL equity
    (no compounding). Marked daily at the close; censored positions are marked to the last close. Path-discontinuity positions are left out (they cannot be valued)."""
    T = len(mk.dates)
    unreal = np.zeros(T)
    real = np.zeros(T)
    order = sorted((p for p in ps if not p.path_disc), key=lambda p: (p.signal_date, -p.rs if math.isfinite(p.rs) else 0.0, p.sym))
    heap: List[int] = []
    taken, rejected, conc = 0, 0, []
    for p in order:
        x = sd[p.sym]
        g_entry = int(x.gidx[p.entry_bar])
        while heap and heap[0] < g_entry:
            heapq.heappop(heap)
        if len(heap) >= max_conc:
            rejected += 1
            continue
        g_exit = int(x.gidx[p.out.exit_bar])
        heapq.heappush(heap, g_exit)
        conc.append(len(heap))
        taken += 1
        cost = (bps / 1e4) * p.entry / p.risk
        bars = np.arange(p.entry_bar, p.out.exit_bar)
        if len(bars):
            unreal[x.gidx[bars]] += (x.bars.c[bars] - p.entry) / p.risk - cost
        real[g_exit:] += p.r_net(bps)
    eq = unreal + real
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    dd = float((peak - eq).max())
    span_years = max(1e-9, (mk.dates[-1] - mk.dates[0]).days / 365.25)
    return {"positions_taken": taken, "signals_rejected_capacity": rejected, "mean_concurrent_at_entry": round(float(np.mean(conc)), 2) if conc else 0,
            "total_r": round(float(eq[-1]), 1), "max_drawdown_r": round(dd, 1), "max_drawdown_pct_at_risk": round(dd * risk_pct, 1),
            "return_pct_per_year_at_risk": round(float(eq[-1]) * risk_pct / span_years, 2), "risk_pct_per_position": risk_pct}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ decision
def decide(prim: Mapping[str, Any], ctrl_diff: Mapping[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    v, d = prim["validation"], prim["discovery"]
    rv, rd = prim["random_validation"], prim["random_discovery"]
    nv = v.get("n", 0)
    if nv < 150:
        return "INSUFFICIENT_DATA", [{"check": "n_validation >= 150", "value": nv, "ok": False}]
    s5 = lambda c: (c.get("share_ge") or {}).get("5R")
    checks = [
        {"check": "n_validation >= 150", "value": nv, "ok": True},
        {"check": "validation expectancy 95% interval entirely above 0", "value": v.get("ci95"), "ok": bool(v.get("ci95") and v["ci95"][0] > 0)},
        {"check": "validation minus random-entry control, 95% interval entirely above 0", "value": ctrl_diff.get("ci95"), "ok": bool(ctrl_diff.get("ci95") and ctrl_diff["ci95"][0] > 0)},
        {"check": "discovery expectancy > 0", "value": d.get("expectancy_r"), "ok": bool(d.get("expectancy_r") is not None and d["expectancy_r"] > 0)},
        {"check": ">=5R share above the random control in both segments", "value": [s5(d), s5(rd), s5(v), s5(rv)],
         "ok": bool(None not in (s5(d), s5(rd), s5(v), s5(rv)) and s5(d) > s5(rd) and s5(v) > s5(rv))},
    ]
    if all(c["ok"] for c in checks):
        return "CONTINUE", checks
    if (v.get("expectancy_r") or 0) <= 0 or (d.get("expectancy_r") or 0) <= 0:
        return "REJECT", checks
    return "REVISE", checks
