# mechanism/shared/trade_plan.py
"""
The trade-plan convention the signal ledger evaluates against: stop = 2xATR (=1R),
targets at 2/4/6xATR (=1R/2R/3R), stop-wins-ties.

This is a deliberate duplicate of the constants in
ml_training/features/price_features.py::plan_outcomes(), not an import from it.
CLAUDE.md draws a hard line between `mechanism/` (the live pipeline) and
`ml_training/` (model training only) -- nothing in `mechanism/` imports from
`ml_training/` today, and plan_outcomes() is shaped for a full historical array
(it needs `pos + horizon < n` already materialized), not day-by-day incremental
evaluation of rows that are still open. Keeping this module self-contained
respects that boundary.

The two definitions are pinned together by
mechanism/shared/tests/test_trade_plan.py, which runs the same synthetic price
path through both and asserts identical levels and tie-break outcome -- so they
can be found and fixed together the moment they'd otherwise silently drift.
"""
from __future__ import annotations

from typing import Dict

STOP_ATR_MULT = 2.0                       # 1R = STOP_ATR_MULT * ATR
TARGET_ATR_MULTS = (2.0, 4.0, 6.0)        # 1R / 2R / 3R, matching plan_outcomes()' three tranches
PLAN_HORIZON_BARS = 20                    # time exit, identical to ml_training's PLAN_HORIZON


def compute_levels(entry_price: float, atr: float, direction: int) -> Dict[str, float]:
    """Stop and three target prices for a breakout entered at `entry_price` with the given
    `atr`. `direction` is +1 (bullish) or -1 (bearish). Raises ValueError on a non-positive
    atr or an invalid direction -- callers must not write a ledger row for a signal that
    doesn't have a real ATR."""
    if atr is None or atr <= 0:
        raise ValueError(f"atr must be positive, got {atr!r}")
    if direction not in (1, -1):
        raise ValueError(f"direction must be 1 or -1, got {direction!r}")

    r = STOP_ATR_MULT * atr
    stop_price = entry_price - direction * r
    targets = [entry_price + direction * mult * atr for mult in TARGET_ATR_MULTS]

    return {
        "stop_price": stop_price,
        "target1_price": targets[0],
        "target2_price": targets[1],
        "target3_price": targets[2],
    }
