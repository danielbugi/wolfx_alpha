# mechanism/alerts/alert_builder.py
"""
Builds the daily momentum shortlist: candidates -> enrichment -> plan. Pure logic; database and news access are
injected (see send_daily_alerts.py), so everything here is unit-testable.

What the numbers mean (see CLAUDE.md, "Alert design studies 2026-09-20"):
  * Candidates = the day's top gainers within OUR liquid universe (20-day avg dollar volume >= $1M), the same idea as
    the Finviz top-15 channel but without illiquid names.
  * The six "context flags" are DESCRIPTIVE facts for chart reading -- backtests did not show that they improve
    outcomes (weekly/monthly false-breakdown in particular did not concentrate the +100%/+300% tail). They are
    shown so you can judge quickly, not sold as a validated signal.
  * The trade plan is a risk template: initial stop = STOP_ATR x ATR below the last close (= 1R), partial sells at
    fixed R multiples, and a wide trailing stop on the rest.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from ml_training.features import price_features as pf  # noqa: E402

N_FLAGS = 6
FLAG_LABELS = ["volume >= 3x", "range >= 2 ATR", "weekly strong close", "weekly false-breakdown",
               "monthly false-breakdown", "above SMA200"]


@dataclass(frozen=True)
class PlanTemplate:
    stop_atr: float = 2.0                          # initial stop distance in ATRs == 1R
    tp_r: Tuple[float, ...] = (2.0, 5.0, 10.0)     # reference levels in R (always shown)
    tp_fractions: Tuple[float, ...] = (1 / 3, 0.0, 0.0)    # share SOLD at each level; 0 = "review level", no forced sale
    trail_pct: float = 0.40                        # runner exits on a close this far below the highest close
    breakeven_after_tp1: bool = True


# Backtested 2018-2025 on top-gainer alerts, 250 bars, R = 2xATR (mean R / win rate / P(R>=20)):
#   runner   pure runner, 40% trail            +0.42 / 20% / 1.00%   <- highest expectancy and tail, hardest to hold
#   balanced 1/3 at +2R then breakeven stop    +0.27 / 36% / 0.41%   <- default: TP2/TP3 are review levels only
#   ladder   25% at +2R, +5R, +10R, 25% runner +0.23 / 36% / 0.07%   <- smoothest, cuts almost all of the tail
# (survivor universe, bull-market heavy sample -- levels of R are optimistic; the ranking of the three is the point)
TEMPLATES = {
    "runner": PlanTemplate(tp_fractions=(0.0, 0.0, 0.0), breakeven_after_tp1=False),
    "balanced": PlanTemplate(),
    "ladder": PlanTemplate(tp_fractions=(0.25, 0.25, 0.25)),
}


def template_from_env(name: Optional[str] = None) -> PlanTemplate:
    import os
    key = (name or os.getenv("ALERTS_PLAN") or "balanced").strip().lower()
    if key not in TEMPLATES:
        raise ValueError(f"ALERTS_PLAN must be one of {sorted(TEMPLATES)}, got {key!r}")
    return TEMPLATES[key]


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def make_plan(entry: float, atr: float, low_10d: Optional[float], t: PlanTemplate = PlanTemplate()) -> Optional[Dict]:
    """Entry/stop/targets/trail from the last close. None when the ATR is unusable or the stop would be <= 0."""
    if not entry or entry <= 0 or not atr or not np.isfinite(atr) or atr <= 0:
        return None
    r = t.stop_atr * atr
    stop = entry - r
    if stop <= 0 or r / entry > 0.6:               # the stop distance alone would be >60% of the price
        return None
    tps = [{"r": lvl, "price": entry + lvl * r, "gain_pct": lvl * r / entry * 100, "sell_frac": frac}
           for lvl, frac in zip(t.tp_r, t.tp_fractions)]
    return {
        "entry": entry, "atr": atr, "stop": stop, "risk_pct": r / entry * 100, "tps": tps,
        "runner_frac": round(1 - sum(t.tp_fractions), 4), "trail_pct": t.trail_pct * 100,
        "breakeven_after_tp1": t.breakeven_after_tp1,
        "alt_stop_10d_low": low_10d if low_10d and 0 < low_10d < entry else None,
        "alt_stop_risk_pct": (entry - low_10d) / entry * 100 if low_10d and 0 < low_10d < entry else None,
    }


# ---------------------------------------------------------------------------
# Multi-timeframe context (weekly / monthly candle built from daily bars, as of the session close)
# ---------------------------------------------------------------------------
def _period_context(d: pd.DataFrame, col: str, nprior: int) -> Optional[Dict]:
    cur_key = d[col].iloc[-1]
    cur = d[d[col] == cur_key]
    prior = d[d[col] < cur_key].groupby(col)["low"].min().tail(nprior)
    if len(prior) < nprior:
        return None
    plow = float(prior.min())
    lo, hi = float(cur["low"].min()), float(cur["high"].max())
    op, cl = float(cur["open"].iloc[0]), float(cur["close"].iloc[-1])
    rng = hi - lo
    loc = (cl - lo) / rng if rng > 0 else float("nan")
    swept = lo < plow
    reclaimed = cl >= plow
    return {"open": op, "high": hi, "low": lo, "close": cl, "loc": loc, "days": int(len(cur)),
            "prior_low": plow, "swept": bool(swept), "reclaimed": bool(reclaimed),
            "tail_pct": (plow - lo) / plow * 100 if swept else 0.0,
            # "false breakdown": wick below the prior lows, closed back above them, close in the top 30% of the range
            "spring": bool(swept and reclaimed and loc >= 0.70)}


def mtf_context(px: pd.DataFrame) -> Dict:
    """Weekly (vs the prior 4 weeks' lows) and monthly (vs the prior 3 months' lows) candle facts."""
    d = px[["date", "open", "high", "low", "close"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    d["wk"] = d["date"].dt.to_period("W-FRI")
    d["mo"] = d["date"].dt.to_period("M")
    last = d["date"].iloc[-1]
    week_done = last.weekday() == 4
    month_done = (last + pd.offsets.BDay(1)).month != last.month
    w, m = _period_context(d, "wk", 4), _period_context(d, "mo", 3)
    if w: w["complete"] = bool(week_done)
    if m: m["complete"] = bool(month_done)
    return {"weekly": w, "monthly": m}


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
class Skip(Exception):
    """Candidate cannot be alerted on (reason in args[0]); the message is shown in the run summary."""


def enrich(px: pd.DataFrame) -> Dict:
    px = px.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    for c in pf.OHLCV:
        px[c] = pd.to_numeric(px[c], errors="coerce")
    n = len(px)
    if n < 130:
        raise Skip(f"only {n} bars of history")
    last = n - 1
    disc = pf.find_discontinuities(px)
    warnings: List[str] = []
    if len(disc):
        prior = disc[disc["pos"] < last]
        if (prior["pos"] >= last - pf.LOOKBACK_BARS).any():
            raise Skip("price discontinuity in the last 253 bars (unadjusted split / bad data)")
        if ((disc["pos"] == last) & (disc["kind"] == "jump_up")).any():
            warnings.append("one-day move above +200% - verify it is real (split/data error possible)")
        if ((disc["pos"] == last) & (disc["kind"] != "jump_up")).any():
            raise Skip("bad bar on the session date")
    ind = pf.compute_indicators(px)
    r = ind.iloc[-1]
    c, prev_c, atr = float(r["close"]), float(ind["close"].iloc[-2]), float(r["atr"]) if np.isfinite(r["atr"]) else float("nan")
    vol_med50 = float(pd.Series(ind["volume"]).iloc[-51:-1].median())
    prev_atr = float(ind["atr"].iloc[-2])
    tr = max(r["high"] - r["low"], abs(r["high"] - prev_c), abs(r["low"] - prev_c))
    rng = r["high"] - r["low"]
    facts = {
        "close": c, "prev_close": prev_c, "ret1_pct": (c / prev_c - 1) * 100,
        "gap_pct": (float(r["open"]) / prev_c - 1) * 100,
        "atr": atr, "atr_pct": atr / c * 100 if atr == atr else float("nan"),
        "rvol": float(r["volume"]) / vol_med50 if vol_med50 > 0 else float("nan"),
        "range_atr": float(tr) / prev_atr if prev_atr and prev_atr > 0 else float("nan"),
        "day_close_loc": float((c - r["low"]) / rng) if rng > 0 else float("nan"),
        "from_52w_high_pct": (c / float(r["hi252"]) - 1) * 100 if r["hi252"] == r["hi252"] else float("nan"),
        "from_52w_low_pct": (c / float(r["lo252"]) - 1) * 100 if r["lo252"] == r["lo252"] else float("nan"),
        "above_sma50": bool(c > r["sma50"]) if r["sma50"] == r["sma50"] else None,
        "above_sma200": bool(c > r["sma200"]) if r["sma200"] == r["sma200"] else None,
        "dollar_vol_20": float(pd.Series(ind["dollar_vol_20"]).iloc[-2]),
        "low_10d": float(px["low"].iloc[-10:].min()),
        "session_date": pd.Timestamp(px["date"].iloc[-1]).date(),
    }
    mtf = mtf_context(px)
    w, m = mtf["weekly"], mtf["monthly"]
    flags = [
        bool(facts["rvol"] >= 3),
        bool(facts["range_atr"] >= 2),
        bool(w and w["loc"] >= 0.70),
        bool(w and w["spring"]),
        bool(m and m["spring"]),
        bool(facts["above_sma200"]),
    ]
    return {"facts": facts, "mtf": mtf, "flags": flags, "flag_count": int(sum(flags)), "warnings": warnings}


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------
def rank_top_movers(prices: pd.DataFrame, session_date, pool: int = 45,
                    min_dollar_volume: float = pf.MIN_DOLLAR_VOLUME_20, max_gap_days: int = 5) -> pd.DataFrame:
    """Top gainers of `session_date` among symbols whose PRIOR-20-day average dollar volume >= the floor.
    `prices`: symbol, date, close, volume for at least ~25 sessions before `session_date`."""
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"])
    p = p.sort_values(["symbol", "date"])
    g = p.groupby("symbol", sort=False)
    p["prev_close"], p["prev_date"] = g["close"].shift(1), g["date"].shift(1)
    p["dv"] = p["close"].astype(float) * p["volume"].astype(float)
    p["dv20_prior"] = g["dv"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
    cur = p[p["date"] == pd.Timestamp(session_date)].copy()
    cur = cur[(cur["prev_close"] > 0) & ((cur["date"] - cur["prev_date"]).dt.days <= max_gap_days)
              & (cur["dv20_prior"] >= min_dollar_volume)]
    cur["ret1"] = cur["close"].astype(float) / cur["prev_close"].astype(float) - 1
    return cur.sort_values("ret1", ascending=False).head(pool).reset_index(drop=True)


def build_alerts(movers: pd.DataFrame, load_history: Callable[[str], pd.DataFrame],
                 sector_lookup: Optional[Callable[[str], Dict]] = None, top_n: int = 15,
                 template: PlanTemplate = PlanTemplate()) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """Enrich movers in order of daily gain until `top_n` valid alerts exist; return (alerts, skipped[(symbol, reason)]).
    Alerts are sorted by number of context flags (desc), then by daily gain."""
    alerts, skipped = [], []
    for row in movers.itertuples():
        if len(alerts) >= top_n:
            break
        sym = row.symbol
        try:
            e = enrich(load_history(sym))
        except Skip as s:
            skipped.append((sym, str(s.args[0])))
            continue
        except Exception as ex:                       # one bad symbol must not kill the run
            skipped.append((sym, f"error: {type(ex).__name__}: {ex}"))
            continue
        f = e["facts"]
        plan = make_plan(f["close"], f["atr"], f["low_10d"], template)
        if plan is None:
            skipped.append((sym, "no usable stop plan (ATR missing or stop distance > 60% of price)"))
            continue
        info = sector_lookup(sym) if sector_lookup else {}
        alerts.append({"symbol": sym, "sector": info.get("sector"), "market_cap": info.get("market_cap"),
                       "plan": plan, "news": [], **e})
    alerts.sort(key=lambda a: (-a["flag_count"], -a["facts"]["ret1_pct"]))
    return alerts, skipped
