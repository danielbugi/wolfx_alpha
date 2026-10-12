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
from dataclasses import dataclass, field
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
    r_gross: float         # in the STANDARDISED unit: 1R = STOP_ATR x ATR at the signal bar, identical for every policy
    r_net: float
    duration: int
    reason: str            # stop | target | channel | trail | time
    mfe_r: float           # standardised units
    mae_r: float
    # ACTUAL initial risk at entry: where this policy's protective level really sat when the position opened. For A and D that is the 1R ATR stop; for C it is mult x ATR; for B it is the
    # lowest low (short: highest high) of the channel bars before the entry bar, which can be far wider (or already beyond the entry price). NaN on trades built by older callers.
    initial_stop: float = float("nan")
    initial_risk: float = float("nan")      # price distance entry -> initial stop; <= 0 means the entry already sat on or beyond the stop
    atr: float = float("nan")               # ATR at the signal bar (the unit is STOP_ATR x atr)

    @property
    def unit(self) -> float:
        return STOP_ATR * self.atr

    @property
    def risk_valid(self) -> bool:
        return math.isfinite(self.initial_risk) and self.initial_risk > 0

    @property
    def risk_atr_multiple(self) -> float:
        return self.initial_risk / self.atr if self.risk_valid and self.atr > 0 else float("nan")

    @property
    def r_actual_gross(self) -> float:
        """Gross result in units of the ACTUAL initial risk (NaN when that risk is not positive)."""
        return self.direction * (self.exit_price - self.entry_price) / self.initial_risk if self.risk_valid else float("nan")

    def r_actual_net(self, cost_bps: float) -> float:
        return self.r_actual_gross - (cost_bps / 1e4) * self.entry_price / self.initial_risk if self.risk_valid else float("nan")

    @property
    def mfe_actual_r(self) -> float:
        return self.mfe_r * self.unit / self.initial_risk if self.risk_valid else float("nan")

    @property
    def mae_actual_r(self) -> float:
        return self.mae_r * self.unit / self.initial_risk if self.risk_valid else float("nan")


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


def _channel_from_window(d: int, hist_h: Sequence[float], hist_l: Sequence[float]) -> float:
    """The same level, from the window of bars before the bar being processed (what a forward tracker holds): lowest low (long) / highest high (short) of those bars."""
    vals = hist_l if d > 0 else hist_h
    if sum(vals) != sum(vals):                 # a non-finite bar anywhere in the window makes the level NaN (what numpy's min/max did in the original simulation), so nothing is stopped by it
        return float("nan")
    return float(min(vals)) if d > 0 else float(max(vals))


def channel_length(policy: str) -> int:
    """How many bars before the current one the policy looks at (0 when it uses no channel)."""
    return int(POLICIES[policy].params.get("channel", 0))


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# One position, one bar at a time. `simulate` (batch research) and the forward paper tracker both run THIS code, so they cannot disagree.
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
@dataclass
class OpenPosition:
    policy: str
    direction: int
    entry_price: float
    atr: float
    unit: float
    initial_stop: float          # where the protective level really sat at entry (see Trade)
    clip_floor: float            # the ATR stop D never loosens below after its switch (A and D: initial_stop; irrelevant elsewhere)
    stop: float                  # the level carried into the next bar (ratcheted for C)
    best: float                  # most favourable price seen BEFORE the current bar
    switched: bool
    mfe: float
    mae: float
    bars_held: int
    max_hold: int
    hist_h: List[float] = field(default_factory=list)       # highs / lows of the bars BEFORE the next bar to process (only as many as the policy's channel needs)
    hist_l: List[float] = field(default_factory=list)


@dataclass(frozen=True)
class Exit:
    price: float
    reason: str


def open_position(policy: str, direction: int, entry_open: float, atr: float, hist_h: Sequence[float] = (), hist_l: Sequence[float] = (), *,
                  max_hold: int = DEFAULT_MAX_HOLD) -> Optional[OpenPosition]:
    """Open one position at `entry_open` (the open of the bar after the signal). `hist_*` are the bars before the entry bar, oldest first (only the last `channel` bars are used).
    None when it cannot be entered (non-finite or non-positive ATR / entry price, or a channel policy with no earlier bar)."""
    spec = POLICIES[policy]
    d = 1 if direction > 0 else -1
    if not math.isfinite(atr) or atr <= 0 or not math.isfinite(entry_open) or entry_open <= 0:
        return None
    entry = float(entry_open)
    unit = STOP_ATR * atr
    clip_floor = entry - d * unit
    key, p = spec.key, spec.params
    n = int(p.get("channel", 0))
    hh = [float(x) for x in list(hist_h)[-n:]] if n else []
    hl = [float(x) for x in list(hist_l)[-n:]] if n else []
    stop = clip_floor
    initial = clip_floor
    if key == "atr_trail":
        stop = entry - d * p["mult"] * atr           # the trail starts at its own distance and only ever tightens
        initial = stop
    elif key == "donchian_trail":
        if not hl:
            return None
        stop = _channel_from_window(d, hh, hl)
        initial = stop
    return OpenPosition(policy, d, entry, float(atr), float(unit), float(initial), float(clip_floor), float(stop), entry, False, 0.0, 0.0, 0, int(max_hold), hh, hl)


def advance(pos: OpenPosition, o: float, h: float, l: float, c: float) -> Optional[Exit]:
    """Process ONE bar for an open position (the entry bar is the first). Returns the exit if the position closed on this bar, else None and the state is ready for the next bar.
    The level in force during the bar uses only information from before it (no lookahead); a stop is taken before a target when both are inside the bar."""
    spec = POLICIES[pos.policy]
    key, p, d = spec.key, spec.params, pos.direction
    n = int(p.get("channel", 0))
    stop = pos.stop
    if key == "donchian_trail":
        stop = _channel_from_window(d, pos.hist_h, pos.hist_l)
    elif key == "hybrid_atr_to_donchian" and pos.switched:
        ch = _channel_from_window(d, pos.hist_h, pos.hist_l)
        stop = max(pos.clip_floor, ch) if d > 0 else min(pos.clip_floor, ch)
    pos.stop = stop
    fav = (h - pos.entry_price) if d > 0 else (pos.entry_price - l)
    adv = (pos.entry_price - l) if d > 0 else (h - pos.entry_price)
    pos.mfe, pos.mae = max(pos.mfe, fav / pos.unit), max(pos.mae, adv / pos.unit)
    hit = _fill_stop(d, stop, o, h, l)                  # stop first when both a stop and a target are inside the bar
    if hit is not None:
        pos.bars_held += 1
        reason = {"atr_stop_fixed_r": "stop", "donchian_trail": "channel", "atr_trail": "trail"}.get(key, "channel" if pos.switched else "stop")
        return Exit(hit, reason)
    if key == "atr_stop_fixed_r":
        tgt = _fill_target(d, pos.entry_price + d * p["target_r"] * pos.unit, o, h, l)
        if tgt is not None:
            pos.bars_held += 1
            return Exit(tgt, "target")
    # state for the NEXT bar
    pos.best = max(pos.best, h) if d > 0 else min(pos.best, l)
    if key == "atr_trail":
        cand = pos.best - d * p["mult"] * pos.atr
        pos.stop = max(pos.stop, cand) if d > 0 else min(pos.stop, cand)
    elif key == "hybrid_atr_to_donchian" and not pos.switched and fav >= p["switch_r"] * pos.unit:
        pos.switched = True
    if n:
        pos.hist_h.append(float(h))
        pos.hist_l.append(float(l))
        del pos.hist_h[:-n], pos.hist_l[:-n]
    pos.bars_held += 1
    if pos.bars_held >= pos.max_hold:
        return Exit(float(c), "time")                   # a position still open at the cap exits at that bar's close
    return None


def make_trade(pos: OpenPosition, ex: Exit, entry_bar: int, exit_bar: int, cost_bps: float = 0.0) -> Trade:
    r_gross = pos.direction * (ex.price - pos.entry_price) / pos.unit
    cost_r = (cost_bps / 1e4) * pos.entry_price / pos.unit
    return Trade(pos.policy, pos.direction, entry_bar, exit_bar, pos.entry_price, float(ex.price), float(r_gross), float(r_gross - cost_r), pos.bars_held, ex.reason, float(pos.mfe), float(pos.mae),
                 pos.initial_stop, pos.direction * (pos.entry_price - pos.initial_stop), pos.atr)    # signed: positive when the protective level sits on the losing side of the entry


def simulate(policy: str, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, signal_bar: int, direction: int, atr: float, *,
             max_hold: int = DEFAULT_MAX_HOLD, cost_bps: float = 0.0) -> Optional[Trade]:
    """One position under one policy. Returns None when it cannot be entered (no next bar, non-finite or non-positive ATR/price)."""
    e_i = signal_bar + 1
    n = len(c)
    if e_i >= n:
        return None
    ch = channel_length(policy)
    lo = max(0, e_i - ch)
    pos = open_position(policy, direction, float(o[e_i]), atr, h[lo:e_i], l[lo:e_i], max_hold=max_hold)
    if pos is None:
        return None
    last = min(n - 1, e_i + max_hold - 1)
    for j in range(e_i, last + 1):
        ex = advance(pos, float(o[j]), float(h[j]), float(l[j]), float(c[j]))
        if ex is not None:
            return make_trade(pos, ex, e_i, j, cost_bps)
    # the data ends before the cap: close at the last available bar (batch research only; a forward tracker simply stays open)
    pos.bars_held = last - e_i + 1
    return make_trade(pos, Exit(float(c[last]), "time"), e_i, last, cost_bps)


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
