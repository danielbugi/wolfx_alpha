"""Big-winner intelligence (research only). PURE: numpy + stdlib; no database, clock, network or environment.

Everything here is a HINDSIGHT measurement: it needs bars that come AFTER the decision and must never be used as a feature. The decision-time information about a signal (volume, trend, catalyst, regime,
sector strength) is captured separately at registration (`experiment_runner.signal_features`, the forward `context`). Every payload this module returns carries `"decision_time_information": False`, and
datasets name the two kinds of column differently (`pit_*` known at decision time, `hs_*` hindsight).

For a position entered at bar `e` (the open of the bar after the signal) with entry price `p` and signal ATR `a` (1R, standardised = 2 x ATR):
  * the favourable excursion over the next H bars is the best price the position could have seen (highest high for a long, lowest low for a short) in % and in R, with the bar at which it peaked and the
    worst adverse excursion BEFORE that peak;
  * the share of that movement an exit policy captured = realised return / favourable excursion over a window that covers the whole holding period;
  * a premature exit is an exit followed, within the next K bars, by a further favourable move of at least `PREMATURE_R` beyond the exit price (a missed continuation);
  * big-winner tiers (>= 3R, 5R, 10R of favourable excursion within 120 bars) are hindsight labels for studying what preceded exceptional moves, never a trading rule.
A horizon whose window is not fully inside the data is reported as None (unknown), never truncated and passed off as complete.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

import numpy as np

HORIZONS = (20, 60, 120, 250)
UNIT_ATR = 2.0                    # 1R = 2 x ATR, the standardised unit of the experiments
PREMATURE_R = 2.0                 # continuation beyond the exit, in R, that makes an exit "premature"
PREMATURE_BARS = 60
TIERS = ((3.0, "ge_3r"), (5.0, "ge_5r"), (10.0, "ge_10r"))
TIER_HORIZON = 120


def favourable_excursion(direction: int, entry_price: float, atr: float, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, entry_idx: int,
                         horizons: Sequence[int] = HORIZONS) -> Dict[str, Any]:
    """Hindsight excursions of a position entered at the open of bar `entry_idx`, for every horizon whose window (entry bar included) lies fully inside the data."""
    d = 1 if direction > 0 else -1
    unit = UNIT_ATR * atr
    out: Dict[str, Any] = {"decision_time_information": False}
    n = len(c)
    ok = math.isfinite(entry_price) and entry_price > 0 and math.isfinite(atr) and atr > 0
    for H in horizons:
        key = f"h{H}"
        if not ok or entry_idx < 0 or entry_idx + H > n:
            out[key] = None
            continue
        hs, ls, cs = h[entry_idx:entry_idx + H], l[entry_idx:entry_idx + H], c[entry_idx:entry_idx + H]
        if not (np.isfinite(hs).all() and np.isfinite(ls).all() and np.isfinite(cs[-1])):
            out[key] = None
            continue
        if d > 0:
            k = int(np.argmax(hs))
            fav_px = float(hs[k])
            adverse_px = float(ls[:k + 1].min())
        else:
            k = int(np.argmin(ls))
            fav_px = float(ls[k])
            adverse_px = float(hs[:k + 1].max())
        out[key] = {"max_favourable_pct": round(d * (fav_px / entry_price - 1) * 100, 4), "max_favourable_r": round(d * (fav_px - entry_price) / unit, 4),
                    "bars_to_peak": k + 1, "max_adverse_before_peak_r": round(max(0.0, d * (entry_price - adverse_px) / unit), 4),
                    "close_return_pct": round(d * (float(cs[-1]) / entry_price - 1) * 100, 4)}
    return out


def tier_of(max_favourable_r_120: Optional[float]) -> Optional[str]:
    """The highest big-winner tier reached within 120 bars ('none' when below 3R); None when the 120-bar window is unknown."""
    if max_favourable_r_120 is None:
        return None
    best = "none"
    for thr, name in TIERS:
        if max_favourable_r_120 >= thr:
            best = name
    return best


def capture(direction: int, entry_price: float, exit_price: float, fav: Optional[Dict[str, Any]], holding_bars: int) -> Dict[str, Any]:
    """Share of the favourable movement a trade captured, measured over the shortest horizon that covers its whole holding period (so the denominator contains the exit). None when no such horizon is
    known, or when there was no favourable movement to capture."""
    d = 1 if direction > 0 else -1
    realised_pct = d * (exit_price / entry_price - 1) * 100
    out: Dict[str, Any] = {"decision_time_information": False, "realised_return_pct": round(realised_pct, 4)}
    if not fav:
        return {**out, "capture_ratio": None, "horizon": None}
    for H in HORIZONS:
        window = fav.get(f"h{H}")
        if H >= holding_bars and window is not None:
            denom = window["max_favourable_pct"]
            return {**out, "horizon": H, "max_favourable_pct": denom, "capture_ratio": round(realised_pct / denom, 4) if denom > 0 else None}
    return {**out, "capture_ratio": None, "horizon": None}


def missed_continuation(direction: int, exit_price: float, atr: float, h: np.ndarray, l: np.ndarray, exit_idx: int, bars: int = PREMATURE_BARS) -> Dict[str, Any]:
    """Further favourable movement, in R beyond the exit price, over the `bars` bars AFTER the exit bar (hindsight). `premature` when it reaches PREMATURE_R. None when fewer than `bars` bars exist."""
    d = 1 if direction > 0 else -1
    n = len(h)
    out: Dict[str, Any] = {"decision_time_information": False, "window_bars": bars}
    if exit_idx + 1 + bars > n or not (math.isfinite(atr) and atr > 0):
        return {**out, "missed_continuation_r": None, "premature": None, "bars_to_continuation_peak": None}
    hs, ls = h[exit_idx + 1:exit_idx + 1 + bars], l[exit_idx + 1:exit_idx + 1 + bars]
    if not (np.isfinite(hs).all() and np.isfinite(ls).all()):
        return {**out, "missed_continuation_r": None, "premature": None, "bars_to_continuation_peak": None}
    k = int(np.argmax(hs)) if d > 0 else int(np.argmin(ls))
    ext = float(hs[k]) if d > 0 else float(ls[k])
    cont = max(0.0, d * (ext - exit_price) / (UNIT_ATR * atr))
    return {**out, "missed_continuation_r": round(cont, 4), "premature": bool(cont >= PREMATURE_R), "bars_to_continuation_peak": k + 1}


def hindsight_payload(direction: int, entry_price: float, exit_price: float, atr: float, holding_bars: int, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                      entry_idx: int, exit_idx: int) -> Dict[str, Any]:
    """The complete hindsight record of one closed position (what a later enrichment event stores): excursions per horizon, capture, tier, missed continuation."""
    fav = favourable_excursion(direction, entry_price, atr, o, h, l, c, entry_idx)
    cap = capture(direction, entry_price, exit_price, fav, holding_bars)
    h120 = fav.get(f"h{TIER_HORIZON}")
    return {"decision_time_information": False, "excursions": {k: v for k, v in fav.items() if k != "decision_time_information"}, "capture": {k: v for k, v in cap.items() if k != "decision_time_information"},
            "big_winner_tier_120": tier_of(h120["max_favourable_r"] if h120 else None),
            "missed_continuation": {k: v for k, v in missed_continuation(direction, exit_price, atr, h, l, exit_idx).items() if k != "decision_time_information"}}


def tail_concentration(values: Sequence[float], fractions: Sequence[float] = (0.01, 0.05, 0.10)) -> Dict[str, Any]:
    """How concentrated the positive part of a payoff distribution is: the share of total positive R (or of total favourable excursion) contributed by the top x% of observations, plus skewness."""
    v = np.asarray([x for x in values if x is not None and math.isfinite(x)], dtype=float)
    if len(v) == 0:
        return {"n": 0}
    pos = np.sort(v[v > 0])[::-1]
    tot = float(pos.sum())
    out: Dict[str, Any] = {"n": int(len(v)), "mean": round(float(v.mean()), 4), "median": round(float(np.median(v)), 4), "share_positive": round(float((v > 0).mean()), 4)}
    for f in fractions:
        k = max(1, int(round(len(v) * f)))
        out[f"top_{int(f * 100)}pct_share_of_positive"] = round(float(pos[:k].sum() / tot), 4) if tot > 0 else None
    sd = float(v.std())
    out["skewness"] = round(float(((v - v.mean()) ** 3).mean() / sd ** 3), 3) if sd > 0 else None
    out["p95"] = round(float(np.percentile(v, 95)), 4)
    out["p99"] = round(float(np.percentile(v, 99)), 4)
    return out
