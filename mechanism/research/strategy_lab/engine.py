"""Backtest engine for the strategy lab. PURE: numpy + stdlib; no database, clock, network or environment.

It reuses, and does not re-implement, the platform's research engine: `research.exit_policies` (the exit simulation, shared with the forward paper tracker) and
`research.experiment_runner` (position accounting with the one-open-position rule, censoring, the purge-aware chronological split, net-R costs). What it adds is the
ENTRY side: turning an earnings report into a signal, the two controls, the bootstrap intervals and the pre-declared decision rule.

Point in time: every signal uses only bars up to and including its own signal bar (`detect` is tested by mutating the future); the position enters at the next open.
"""
from __future__ import annotations

import math
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from research import exit_policies as X
from research import experiment_runner as ER
from research.lab.manifest import canonical_hash
from research.strategy_lab.data import Bars, Panel, Report
from research.strategy_lab.specs import spec_hash

ATR_N = 14
FULL_WINDOW = 60                  # sessions after the signal a random control needs, so that the same controls serve every hold cap
SIGNAL, ALL_REPORTS, RANDOM, PARENT = "signal", "all_reports", "random_date", "parent_signal"
FEATURES = ("direction", "surprise_abs", "reaction_dir", "relvol_event", "range_expansion", "gap_dir", "pre_trend_20_dir")


def atr14(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Wilder ATR(14); NaN until 14 true ranges exist. Uses bars <= i only."""
    n = len(c)
    out = np.full(n, np.nan)
    if n <= ATR_N:
        return out
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    out[ATR_N] = tr[1:ATR_N + 1].mean()
    for i in range(ATR_N + 1, n):
        out[i] = (out[i - 1] * (ATR_N - 1) + tr[i]) / ATR_N
    return out


@dataclass(frozen=True)
class Signal:
    symbol: str
    kind: str                     # signal | all_reports | random_date
    signal_idx: int
    direction: int
    report_date: Optional[date]
    surprise_pct: Optional[float]
    reaction_pct: Optional[float]
    features: Mapping[str, float] = field(default_factory=dict)      # signal-time features (bars up to the signal bar only); see event_features


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    tr = np.empty(len(c))
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    return tr


def event_features(bars: Bars, atr: np.ndarray, D: int, direction: int, surprise_pct: float, reaction_pct: float) -> Dict[str, float]:
    """Features of one report event, from bars <= D+1 (the signal bar) only. All are stated in the trade's direction where a sign matters.
      direction          +1 long (a beat), -1 short (a miss)
      surprise_abs       |reported surprise| in percent
      reaction_dir       the two-session reaction in percent, signed so that positive = in the trade's direction
      relvol_event       mean volume of the two reaction sessions / mean volume of the 20 sessions before the report
      range_expansion    mean true range of the two reaction sessions / ATR14 known before the report (D-1)
      gap_dir            the report session's open versus the previous close, in percent, in the trade's direction
      pre_trend_20_dir   the 20 sessions before the report, in percent, in the trade's direction
    NaN when the history or a denominator does not allow it (never filled)."""
    c, o, v = bars.c, bars.o, bars.v
    nan = float("nan")
    base_v = float(np.mean(v[D - 20:D]))
    tr = true_range(bars.h, bars.l, bars.c)
    a_prev = float(atr[D - 1]) if D - 1 >= 0 else nan
    return {
        "direction": float(direction),
        "surprise_abs": abs(float(surprise_pct)),
        "reaction_dir": float(reaction_pct) * direction,
        "relvol_event": float(np.mean(v[D:D + 2]) / base_v) if base_v > 0 else nan,
        "range_expansion": float(np.mean(tr[D:D + 2]) / a_prev) if math.isfinite(a_prev) and a_prev > 0 else nan,
        "gap_dir": float((o[D] / c[D - 1] - 1.0) * 100.0 * direction) if c[D - 1] > 0 else nan,
        "pre_trend_20_dir": float((c[D - 1] / c[D - 21] - 1.0) * 100.0 * direction) if D >= 21 and c[D - 21] > 0 else nan,
    }


def _extra_ok(extra: Sequence[Mapping[str, Any]], feats: Mapping[str, float]) -> bool:
    """Every extra condition holds (a NaN feature never satisfies one: missing data is not a pass)."""
    for cnd in extra:
        x = feats.get(cnd["feature"], float("nan"))
        if not math.isfinite(x):
            return False
        if cnd["op"] == ">=" and not x >= cnd["value"]:
            return False
        if cnd["op"] == "<=" and not x <= cnd["value"]:
            return False
    return True


def _report_session(dates: Sequence[date], d: date, max_gap: int) -> Optional[int]:
    lo, hi = 0, len(dates)
    while lo < hi:                                           # first session on or after the report date
        mid = (lo + hi) // 2
        if dates[mid] < d:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(dates) or (dates[lo] - d).days > max_gap:
        return None
    return lo


def _guard_ok(c: np.ndarray, d_idx: int, limit_pct: float) -> bool:
    """No daily close change beyond the limit in the 25 sessions BEFORE the report session (a split or a bad print in the history the signal rests on).
    Only information known at the signal is used: nothing after it may decide whether a trade is kept (that would be look-ahead)."""
    lo, hi = max(1, d_idx - 24), d_idx - 1
    if hi < lo:
        return True
    j = np.arange(lo, hi + 1)
    return bool(np.all(np.abs(c[j] / c[j - 1] - 1.0) * 100.0 <= limit_pct))


def detect(spec: Mapping[str, Any], symbol: str, bars: Bars, reports: Sequence[Report], counts: Counter, atr: Optional[np.ndarray] = None) -> List[Signal]:
    """Signals and the all-reports control for one symbol. `counts` records why events were dropped (nothing is dropped silently).
    When the spec carries `event.extra` (extra conditions on signal-time features), the events that meet the base conditions are also emitted as the PARENT control
    and only those that also meet every extra condition are signals."""
    ev, liq, g = spec["event"], spec["liquidity_at_signal"], spec["data_guard"]
    rx = ev["reaction"]
    extra = ev.get("extra") or []
    if atr is None:
        atr = atr14(bars.h, bars.l, bars.c)
    dates, c, v = bars.dates, bars.c, bars.v
    n = len(c)
    out: List[Signal] = []
    for r in reports:
        counts["reports"] += 1
        if r.eps_actual is None or r.eps_estimate is None or r.surprise_pct is None:
            counts["dropped_no_eps_or_surprise"] += 1
            continue
        if r.surprise_pct == 0:
            counts["dropped_zero_surprise"] += 1
            continue
        D = _report_session(dates, r.report_date, ev["report_session_max_gap_days"])
        if D is None:
            counts["dropped_no_trading_session_near_report"] += 1
            continue
        if D < 21 or D + 2 >= n:
            counts["dropped_history_too_short_or_no_entry_bar"] += 1
            continue
        s = D + 1
        if c[s] < liq["min_price"] or float(np.mean(c[D - 20:D] * v[D - 20:D])) < liq["min_avg_dollar_volume_20_sessions_before_report"]:
            counts["dropped_liquidity"] += 1
            continue
        if not _guard_ok(c, D, g["skip_if_any_daily_close_change_exceeds_pct"]):
            counts["dropped_data_guard"] += 1
            continue
        direction = 1 if r.surprise_pct > 0 else -1
        reaction = (c[D + 1] / c[D - 1] - 1.0) * 100.0
        feats = event_features(bars, atr, D, direction, r.surprise_pct, float(reaction))
        out.append(Signal(symbol, ALL_REPORTS, s, direction, r.report_date, r.surprise_pct, float(reaction), feats))
        if abs(r.surprise_pct) < ev["min_abs_surprise_pct"]:
            counts["not_signal_surprise_below_threshold"] += 1
            continue
        if abs(reaction) > rx["max_abs_return_pct"]:
            counts["not_signal_reaction_above_max_split_or_deal"] += 1
            continue
        if abs(reaction) < rx["min_abs_return_pct"]:
            counts["not_signal_reaction_below_threshold"] += 1
            continue
        if rx["must_agree_with_surprise_sign"] and (reaction > 0) != (direction > 0):
            counts["not_signal_reaction_disagrees_with_surprise"] += 1
            continue
        if extra:
            out.append(Signal(symbol, PARENT, s, direction, r.report_date, r.surprise_pct, float(reaction), feats))
            counts["parent_signals"] += 1
            if not _extra_ok(extra, feats):
                counts["not_signal_extra_condition_failed"] += 1
                continue
        out.append(Signal(symbol, SIGNAL, s, direction, r.report_date, r.surprise_pct, float(reaction), feats))
        counts["signals"] += 1
    return out


def random_controls(spec: Mapping[str, Any], symbol: str, bars: Bars, report_idx: Sequence[int], signals: Sequence[Signal], seed: int) -> List[Signal]:
    """One random-date control per signal: a session of the same symbol at least 15 sessions from every report session, same direction, same liquidity rule."""
    liq = spec["liquidity_at_signal"]
    c, v, n = bars.c, bars.v, len(bars.c)
    rng = np.random.default_rng([seed, zlib.crc32(symbol.encode())])
    blocked = np.zeros(n, dtype=bool)
    for i in report_idx:
        blocked[max(0, i - 15): min(n, i + 16)] = True
    lo, hi = 25, n - 3 - FULL_WINDOW
    if hi <= lo:
        return []
    out = []
    for sg in sorted((x for x in signals if x.kind == SIGNAL), key=lambda x: x.signal_idx):
        for _ in range(20):
            j = int(rng.integers(lo, hi))
            if blocked[j]:
                continue
            if c[j] < liq["min_price"] or float(np.mean(c[j - 21:j - 1] * v[j - 21:j - 1])) < liq["min_avg_dollar_volume_20_sessions_before_report"]:
                continue
            out.append(Signal(symbol, RANDOM, j, sg.direction, None, None, None))
            break
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ statistics
def block_ci(values: Sequence[float], months: Sequence[str], n_boot: int, seed: int) -> Dict[str, Any]:
    """Mean and month-block bootstrap 95% interval (observations of one month are not independent)."""
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return {"n": 0}
    m = np.asarray(months)
    keys = sorted(set(m.tolist()))
    blocks = [v[m == k] for k in keys]
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.integers(0, len(blocks), len(blocks))
        means[b] = np.concatenate([blocks[i] for i in pick]).mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"n": int(len(v)), "mean": round(float(v.mean()), 5), "ci95": [round(float(lo), 5), round(float(hi), 5)]}


def block_diff_ci(a: Sequence[float], am: Sequence[str], b: Sequence[float], bm: Sequence[str], n_boot: int, seed: int) -> Dict[str, Any]:
    """mean(a) - mean(b) with a month-block bootstrap (the same drawn months are used for both groups)."""
    a, b, am, bm = np.asarray(a, float), np.asarray(b, float), np.asarray(am), np.asarray(bm)
    if len(a) == 0 or len(b) == 0:
        return {"n_a": int(len(a)), "n_b": int(len(b))}
    keys = sorted(set(am.tolist()) | set(bm.tolist()))
    ab = [a[am == k] for k in keys]
    bb = [b[bm == k] for k in keys]
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        ca = np.concatenate([ab[i] for i in pick])
        cb = np.concatenate([bb[i] for i in pick])
        if len(ca) and len(cb):
            diffs.append(ca.mean() - cb.mean())
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"n_a": int(len(a)), "n_b": int(len(b)), "diff": round(float(a.mean() - b.mean()), 5), "ci95": [round(float(lo), 5), round(float(hi), 5)]}


def without_top(values: Sequence[float], share: float = 0.01) -> Optional[float]:
    v = np.sort(np.asarray(values, dtype=float))
    if len(v) < 20:
        return None
    k = max(1, int(len(v) * share))
    return round(float(v[:-k].mean()), 5)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the run
def _variant(spec: Mapping[str, Any]) -> ER.Variant:
    e = spec["exit"]
    return ER.Variant(spec["key"], spec["version"], spec_hash(spec), e["policy"], int(e["primary_max_hold_bars"]))


def run_backtest(spec: Mapping[str, Any], panel: Panel, *, seed: int = 20261031, n_boot: int = 1000, symbols: Optional[Sequence[str]] = None,
                 rows_out: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Run the spec over the panel. Returns a JSON-able result (no clock, no randomness beyond `seed`).
    `rows_out`, when given, receives one plain dict per accounted position (kind, split, direction, features, net R at the primary cost, ...) for the analysis step."""
    sp = spec["split"]
    train_start = date.fromisoformat(sp["train_start"])
    train_end, test_start = date.fromisoformat(sp["train_end_exclusive"]), date.fromisoformat(sp["test_start_inclusive"])
    caps = [int(spec["exit"]["primary_max_hold_bars"]), int(spec["exit"]["sensitivity_max_hold_bars"])]
    bps_list = [float(b) for b in spec["costs_bps_round_trip"]["sensitivity"]]
    primary_bps = float(spec["costs_bps_round_trip"]["primary"])
    variant = _variant(spec)
    counts: Counter = Counter()
    has_parent = bool(spec["event"].get("extra"))
    KINDS_RUN = (SIGNAL, PARENT, ALL_REPORTS, RANDOM) if has_parent else (SIGNAL, ALL_REPORTS, RANDOM)
    rows: List[Dict[str, Any]] = []          # one per accounted position: kind, direction, cap, split, signal_date, exit month, trade
    syms = sorted(symbols) if symbols is not None else sorted(panel.bars)
    for sym in syms:
        bars = panel.bars.get(sym)
        reps = panel.reports.get(sym)
        if bars is None or not reps or len(bars.c) < 120:
            continue
        counts["symbols_with_reports"] += 1
        atr = atr14(bars.h, bars.l, bars.c)
        sigs = detect(spec, sym, bars, reps, counts, atr)
        rep_idx = [i for i in (_report_session(bars.dates, r.report_date, spec["event"]["report_session_max_gap_days"]) for r in reps) if i is not None]
        sigs += random_controls(spec, sym, bars, rep_idx, sigs, seed)
        for kind in KINDS_RUN:
            ks = [s for s in sigs if s.kind == kind and math.isfinite(float(atr[s.signal_idx])) and atr[s.signal_idx] > 0]
            if not ks:
                continue
            by_key = {(s.signal_idx, s.direction): s for s in ks}
            for cap in caps:
                for direction in (1, -1):
                    ds = [(s.signal_idx, s.direction) for s in ks if s.direction == direction]
                    if not ds:
                        continue
                    _, positions = ER.simulate_symbol([variant], [cap], sym, bars.o, bars.h, bars.l, bars.c, bars.dates, atr, ds)
                    for p in positions:
                        if p.signal_date < train_start:
                            continue
                        split = ER.assign_split(p.signal_date, p.exit_date, train_end, test_start)
                        rows.append({"kind": kind, "direction": direction, "cap": cap, "split": split, "censored": p.censored, "pos": p, "symbol": sym,
                                     "month": p.exit_date.strftime("%Y-%m"), "year": p.signal_date.year, "signal_date": p.signal_date,
                                     "features": by_key[(p.signal_idx, p.direction)].features})

    def pick(kind, split, cap, direction=None):
        return [r for r in rows if r["kind"] == kind and r["split"] == split and r["cap"] == cap and (direction is None or r["direction"] == direction)]

    def stats(sel, bps):
        pos = [r["pos"] for r in sel]
        s = ER.summarize(pos, "std", bps)
        vals, months = ER.series(pos, "std", bps)
        s["ci"] = block_ci(vals, months, n_boot, seed + 1)
        s["expectancy_without_top_1pct"] = without_top(vals)
        s["n_censored_excluded"] = sum(1 for r in sel if r["censored"])
        return s, vals, months

    results: Dict[str, Any] = {"cells": {}, "by_year": {}, "controls_diff": {}}
    for kind in KINDS_RUN:
        for split in ("train", "test"):
            for cap in caps:
                for dname, d in (("both", None), ("long", 1), ("short", -1)):
                    for bps in bps_list:
                        sel = pick(kind, split, cap, d)
                        if sel:
                            results["cells"][f"{kind}|{split}|hold{cap}|{dname}|{int(bps)}bps"] = stats(sel, bps)[0]
    for split in ("train", "test"):
        for cap in caps:
            sv, sm = ER.series([r["pos"] for r in pick(SIGNAL, split, cap)], "std", primary_bps)
            rv, rm = ER.series([r["pos"] for r in pick(RANDOM, split, cap)], "std", primary_bps)
            av, am = ER.series([r["pos"] for r in pick(ALL_REPORTS, split, cap)], "std", primary_bps)
            results["controls_diff"][f"{split}|hold{cap}|signal_minus_random"] = block_diff_ci(sv, sm, rv, rm, n_boot, seed + 2)
            results["controls_diff"][f"{split}|hold{cap}|signal_minus_all_reports"] = block_diff_ci(sv, sm, av, am, n_boot, seed + 3)
            if has_parent:
                pv, pm = ER.series([r["pos"] for r in pick(PARENT, split, cap)], "std", primary_bps)
                results["controls_diff"][f"{split}|hold{cap}|signal_minus_parent"] = block_diff_ci(sv, sm, pv, pm, n_boot, seed + 4)
    prim_cap = caps[0]
    by_year: Dict[int, List[float]] = {}
    for r in pick(SIGNAL, "train", prim_cap) + pick(SIGNAL, "test", prim_cap):
        if r["censored"]:
            continue
        v = ER.net_r(r["pos"].trade, "std", primary_bps)
        if math.isfinite(v):
            by_year.setdefault(r["year"], []).append(v)
    results["by_year"] = {str(y): {"n": len(v), "expectancy_r": round(float(np.mean(v)), 4)} for y, v in sorted(by_year.items())}
    if rows_out is not None:
        for r in rows:
            if r["censored"] or r["cap"] != prim_cap:
                continue
            v = ER.net_r(r["pos"].trade, "std", primary_bps)
            if math.isfinite(v):
                rows_out.append({"kind": r["kind"], "split": r["split"], "direction": r["direction"], "symbol": r["symbol"], "signal_date": r["signal_date"], "month": r["month"],
                                 "year": r["year"], "features": dict(r["features"]), "net_r": float(v)})
    results["event_accounting"] = dict(sorted(counts.items()))
    results["n_positions_by_kind_split"] = {f"{k}|{s}": len(pick(k, s, prim_cap)) for k in KINDS_RUN for s in ("train", "test", "embargo", "purged")}
    results["verdict"], results["decision_checks"] = decide(spec, results, primary_bps, prim_cap)
    return results


def decide(spec: Mapping[str, Any], results: Mapping[str, Any], primary_bps: float, cap: int) -> Tuple[str, List[Dict[str, Any]]]:
    """The pre-declared decision rule (specs.py), applied mechanically to the primary cell. Nothing else can change the verdict."""
    cells, cd = results["cells"], results["controls_diff"]
    test = cells.get(f"signal|test|hold{cap}|both|{int(primary_bps)}bps", {})
    train = cells.get(f"signal|train|hold{cap}|both|{int(primary_bps)}bps", {})
    diff = cd.get(f"test|hold{cap}|signal_minus_random", {})
    pdiff = cd.get(f"test|hold{cap}|signal_minus_parent", {})
    n_test = int(test.get("n", 0))
    if n_test < 150:
        return "INSUFFICIENT_DATA", [{"check": "n_test >= 150", "value": n_test, "ok": False}]
    ci = test.get("ci", {})
    checks = [
        {"check": "n_test >= 150", "value": n_test, "ok": True},
        {"check": "test net expectancy 95% interval entirely above 0", "value": ci.get("ci95"), "ok": bool(ci.get("ci95") and ci["ci95"][0] > 0)},
        {"check": "signal minus random-date control, 95% interval entirely above 0", "value": diff.get("ci95"), "ok": bool(diff.get("ci95") and diff["ci95"][0] > 0)},
        {"check": "train net expectancy > 0", "value": train.get("expectancy_r"), "ok": bool(train.get("expectancy_r") is not None and train["expectancy_r"] > 0)},
        {"check": "test expectancy without the top 1% of winners > 0", "value": test.get("expectancy_without_top_1pct"),
         "ok": bool(test.get("expectancy_without_top_1pct") is not None and test["expectancy_without_top_1pct"] > 0)},
    ]
    if spec["event"].get("extra"):
        checks.append({"check": "signal minus its parent strategy (the added conditions must improve on it), 95% interval entirely above 0", "value": pdiff.get("ci95"),
                       "ok": bool(pdiff.get("ci95") and pdiff["ci95"][0] > 0)})
    return ("PASS_FOR_FORWARD_SHADOW" if all(c["ok"] for c in checks) else "NO_EDGE_DEMONSTRATED"), checks


def run_hash(spec: Mapping[str, Any], data_hash: str, code_ref: str, seed: int) -> str:
    return canonical_hash({"spec_hash": spec_hash(spec), "data_hash": data_hash, "code_ref": code_ref, "split": spec["split"], "seed": seed, "decision_rule": spec["decision_rule"]})


def current_candidates(spec: Mapping[str, Any], panel: Panel, as_of_sessions: int = 3) -> List[Dict[str, Any]]:
    """Signals whose signal bar is among the last `as_of_sessions` sessions of the data: what the strategy would be looking at NOW (research output only; nothing is written or sent).
    A signal needs its entry bar, so the newest possible signal bar is the second to last; the list is informational and says whether the strategy passed."""
    out = []
    for sym, bars in sorted(panel.bars.items()):
        reps = panel.reports.get(sym) or []
        if not reps or len(bars.c) < 120:
            continue
        n = len(bars.c)
        counts: Counter = Counter()
        # the entry bar does not exist yet: pad one synthetic bar only for the structural check; no price from it is ever used
        pad = Bars(bars.dates + [date.max], *(np.append(a, a[-1]) for a in (bars.o, bars.h, bars.l, bars.c, bars.v)))
        for s in detect(spec, sym, pad, reps, counts):
            if s.kind == SIGNAL and s.signal_idx >= n - as_of_sessions:
                out.append({"symbol": sym, "signal_date": bars.dates[s.signal_idx].isoformat(), "direction": "long" if s.direction > 0 else "short",
                            "report_date": s.report_date.isoformat(), "surprise_pct": s.surprise_pct, "reaction_pct": round(s.reaction_pct, 2),
                            "last_close": float(bars.c[s.signal_idx])})
    return out
