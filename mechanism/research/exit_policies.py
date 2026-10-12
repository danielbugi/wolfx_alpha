"""Versioned exit policies for breakout trades, with independent simulated position accounting. PURE: numpy only; no database, clock, network or environment.

A *strategy variant* is (entry rule, exit policy). The entry rule is not this module's business (the breakout events come from the dataset / the screener). Each call to `simulate`
accounts ONE position of ONE policy completely on its own, so several policies (or several strategies) may hold the same symbol on the same day without ever sharing state.
Nothing here changes live signal selection; it only measures what an exit rule would have done.

Conventions (identical for every policy, so the comparison is fair)
  * The signal is the close of bar `s`; the position is entered at the OPEN of bar `s + 1` (the screener runs after the close).
  * 1R = `STOP_ATR` x ATR(14) at the signal bar (the same unit the existing trade plan uses); every result is expressed in that unit.
  * The entry bar's own range counts (a stop can be hit on the entry day).
  * Stops / channels used on bar `j` are computed from bars < j only (no lookahead). A stop fills at its level, or at the open when the bar opens beyond it (a gap is not
    flattered). A target fills at its level, or at the open when the bar opens beyond it. If a stop and a target are both inside one bar, the stop is taken first.
  * `max_hold` bars cap every policy: a position still open then exits at that bar's close (reason `time`).
  * `cost_bps` is a ROUND-TRIP cost (commission + slippage) charged in R: bps/1e4 x entry price / 1R.
  * Shorts are the mirror image of longs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

STOP_ATR = 2.0                    # 1R, as in ml_training/features/price_features.py
DEFAULT_MAX_HOLD = 60
CHANNEL = 10                      # exit channel length, a priori (half the 20-bar entry channel)
ATR_TRAIL_MULT = 3.0              # a priori
POLICY_VERSION = "v1"


@dataclass(frozen=True)
class PolicySpec:
    key: str
    params: Mapping[str, Any]
    version: str = POLICY_VERSION

    @property
    def identity(self) -> str:
        return f"{self.key}@{self.version}:" + ",".join(f"{k}={self.params[k]}" for k in sorted(self.params))


POLICIES: Dict[str, PolicySpec] = {
    "A_atr_stop_fixed_2R": PolicySpec("atr_stop_fixed_r", {"target_r": 2.0}),
    "A_atr_stop_fixed_3R": PolicySpec("atr_stop_fixed_r", {"target_r": 3.0}),
    "B_donchian_trail_10": PolicySpec("donchian_trail", {"channel": CHANNEL}),
    "C_atr_trail_3x": PolicySpec("atr_trail", {"mult": ATR_TRAIL_MULT}),
    "D_hybrid_atr_stop_to_donchian_10": PolicySpec("hybrid_atr_to_donchian", {"switch_r": 1.0, "channel": CHANNEL}),
}


@dataclass(frozen=True)
class Trade:
    policy: str
    direction: int
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    r_gross: float
    r_net: float
    duration: int
    reason: str            # stop | target | channel | trail | time
    mfe_r: float
    mae_r: float


def _fill_stop(d: int, level: float, o: float, h: float, l: float) -> Optional[float]:
    """Price at which a protective level is hit on this bar, or None. Long: stop below; short: stop above."""
    if d > 0:
        if o <= level:
            return o
        return level if l <= level else None
    if o >= level:
        return o
    return level if h >= level else None


def _fill_target(d: int, level: float, o: float, h: float, l: float) -> Optional[float]:
    if d > 0:
        if o >= level:
            return o
        return level if h >= level else None
    if o <= level:
        return o
    return level if l <= level else None


def _channel_level(d: int, hs: np.ndarray, ls: np.ndarray, j: int, n: int) -> float:
    """Trailing channel through bar j-1: long exits below the lowest low of the previous n bars, short above the highest high."""
    lo = max(0, j - n)
    return float(ls[lo:j].min()) if d > 0 else float(hs[lo:j].max())


def simulate(policy: str, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, signal_bar: int, direction: int, atr: float, *,
             max_hold: int = DEFAULT_MAX_HOLD, cost_bps: float = 0.0) -> Optional[Trade]:
    """One position under one policy. Returns None when it cannot be entered (no next bar, non-finite or non-positive ATR/price)."""
    spec = POLICIES[policy]
    d = 1 if direction > 0 else -1
    e_i = signal_bar + 1
    n = len(c)
    if e_i >= n or not math.isfinite(atr) or atr <= 0 or not math.isfinite(o[e_i]) or o[e_i] <= 0:
        return None
    entry = float(o[e_i])
    unit = STOP_ATR * atr
    init_stop = entry - d * unit
    last = min(n - 1, e_i + max_hold - 1)
    p = spec.params
    key = spec.key
    stop = init_stop
    if key == "atr_trail":
        stop = entry - d * p["mult"] * atr          # the trail starts at its own distance and only ever tightens
    best = entry                                     # most favourable price seen BEFORE the current bar
    switched = False
    mfe = mae = 0.0
    exit_price, reason, exit_i = float(c[last]), "time", last
    for j in range(e_i, last + 1):
        oj, hj, lj = float(o[j]), float(h[j]), float(l[j])
        # the level in force during bar j, from information before bar j only
        if key == "donchian_trail":
            stop = _channel_level(d, h, l, j, p["channel"])
        elif key == "hybrid_atr_to_donchian" and switched:
            ch = _channel_level(d, h, l, j, p["channel"])
            stop = max(init_stop, ch) if d > 0 else min(init_stop, ch)
        fav = (hj - entry) if d > 0 else (entry - lj)
        adv = (entry - lj) if d > 0 else (hj - entry)
        mfe, mae = max(mfe, fav / unit), max(mae, adv / unit)
        hit = _fill_stop(d, stop, oj, hj, lj)            # stop first when both a stop and a target are inside the bar
        if hit is not None:
            exit_price, exit_i = hit, j
            reason = {"atr_stop_fixed_r": "stop", "donchian_trail": "channel", "atr_trail": "trail"}.get(key, "channel" if switched else "stop")
            break
        if key == "atr_stop_fixed_r":
            tgt = _fill_target(d, entry + d * p["target_r"] * unit, oj, hj, lj)
            if tgt is not None:
                exit_price, exit_i, reason = tgt, j, "target"
                break
        # state for the NEXT bar
        best = max(best, hj) if d > 0 else min(best, lj)
        if key == "atr_trail":
            cand = best - d * p["mult"] * atr
            stop = max(stop, cand) if d > 0 else min(stop, cand)
        elif key == "hybrid_atr_to_donchian" and not switched and fav >= p["switch_r"] * unit:
            switched = True
    r_gross = d * (exit_price - entry) / unit
    cost_r = (cost_bps / 1e4) * entry / unit
    return Trade(policy, d, e_i, exit_i, entry, float(exit_price), float(r_gross), float(r_gross - cost_r), exit_i - e_i + 1, reason, float(mfe), float(mae))


def aggregate(trades: Sequence[Trade], exit_dates: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """Expectancy, win rate, profit factor, duration, MFE/MAE, extreme-winner share and drawdown of the cumulative-R curve (trades ordered by exit date when given)."""
    if not trades:
        return {"n": 0}
    r = np.array([t.r_net for t in trades], dtype=float)
    order = np.argsort(np.array(exit_dates, dtype="datetime64[ns]"), kind="stable") if exit_dates is not None else np.arange(len(r))
    curve = np.cumsum(r[order])
    dd = float((np.maximum.accumulate(np.r_[0.0, curve])[1:] - curve).max())
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    pos = np.sort(r[r > 0])[::-1]
    total_pos = pos.sum()
    k1, k5 = max(1, int(len(r) * 0.01)), max(1, int(len(r) * 0.05))
    return {
        "n": int(len(r)), "expectancy_r": round(float(r.mean()), 4), "median_r": round(float(np.median(r)), 4), "win_rate": round(float((r > 0).mean()), 4),
        "profit_factor": round(float(wins / losses), 3) if losses > 0 else None,
        "avg_duration_bars": round(float(np.mean([t.duration for t in trades])), 2), "median_duration_bars": float(np.median([t.duration for t in trades])),
        "avg_mfe_r": round(float(np.mean([t.mfe_r for t in trades])), 3), "avg_mae_r": round(float(np.mean([t.mae_r for t in trades])), 3),
        "max_win_r": round(float(r.max()), 3), "max_loss_r": round(float(r.min()), 3),
        "top1pct_share_of_gross_profit": round(float(pos[:k1].sum() / total_pos), 3) if total_pos > 0 else None,
        "top5pct_share_of_gross_profit": round(float(pos[:k5].sum() / total_pos), 3) if total_pos > 0 else None,
        "max_drawdown_r_cumulative": round(dd, 2), "total_r": round(float(r.sum()), 1),
        "exit_reasons": {k: int(sum(1 for t in trades if t.reason == k)) for k in sorted({t.reason for t in trades})},
    }
