"""The ORIGINAL (pre-refactor, 2026-10-11) exit simulation, frozen verbatim as a reference implementation. The refactored `exit_policies.simulate` (a loop over the stateful `advance()` that the forward paper tracker
shares) must reproduce it exactly on every path: tests/test_exit_policies_equivalence.py."""
import math
from typing import Optional

import numpy as np

from research import exit_policies as X

def reference_simulate(policy: str, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, signal_bar: int, direction: int, atr: float, *,
             max_hold: int = X.DEFAULT_MAX_HOLD, cost_bps: float = 0.0) -> Optional[X.Trade]:
    """One position under one policy. Returns None when it cannot be entered (no next bar, non-finite or non-positive ATR/price)."""
    spec = X.POLICIES[policy]
    d = 1 if direction > 0 else -1
    e_i = signal_bar + 1
    n = len(c)
    if e_i >= n or not math.isfinite(atr) or atr <= 0 or not math.isfinite(o[e_i]) or o[e_i] <= 0:
        return None
    entry = float(o[e_i])
    unit = X.STOP_ATR * atr
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
            stop = X._channel_level(d, h, l, j, p["channel"])
        elif key == "hybrid_atr_to_donchian" and switched:
            ch = X._channel_level(d, h, l, j, p["channel"])
            stop = max(init_stop, ch) if d > 0 else min(init_stop, ch)
        fav = (hj - entry) if d > 0 else (entry - lj)
        adv = (entry - lj) if d > 0 else (hj - entry)
        mfe, mae = max(mfe, fav / unit), max(mae, adv / unit)
        hit = X._fill_stop(d, stop, oj, hj, lj)            # stop first when both a stop and a target are inside the bar
        if hit is not None:
            exit_price, exit_i = hit, j
            reason = {"atr_stop_fixed_r": "stop", "donchian_trail": "channel", "atr_trail": "trail"}.get(key, "channel" if switched else "stop")
            break
        if key == "atr_stop_fixed_r":
            tgt = X._fill_target(d, entry + d * p["target_r"] * unit, oj, hj, lj)
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
    return X.Trade(policy, d, e_i, exit_i, entry, float(exit_price), float(r_gross), float(r_gross - cost_r), exit_i - e_i + 1, reason, float(mfe), float(mae))
