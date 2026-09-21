# mechanism/alerts/price_guard.py
"""
The one rule for "this close-to-close move looks like a split / price adjustment, not a price move", shared by the assistant's performance
maths (performance.py) and the channel's list scoreboard (scoreboard.py). Pure, no I/O.

A move is treated as a split / adjustment when it is beyond ~3x, or lands on a COMMON split factor: 1/3 (3-for-1), 1/2 (2-for-1), 2/3 (3-for-2),
2 and 3 (reverse splits) within SPLIT_TOLERANCE. Anything else stays a real move, because a wrongly-flagged crash only shows "n/a", whereas a
missed split shows a fabricated number.
"""
from __future__ import annotations

from decimal import Decimal

SPLIT_FACTORS = (Decimal(1) / 3, Decimal(1) / 2, Decimal(2) / 3, Decimal(2), Decimal(3))
SPLIT_TOLERANCE = Decimal("0.03")
HARD_JUMP_HIGH, HARD_JUMP_LOW = Decimal("3.09"), Decimal("0.323")


def is_split_like(ratio) -> bool:
    """True when a close / previous-close ratio looks like a split or an adjustment rather than a price move."""
    if ratio is None:
        return False
    r = ratio if isinstance(ratio, Decimal) else Decimal(str(ratio))
    if r <= 0:
        return False
    if r > HARD_JUMP_HIGH or r < HARD_JUMP_LOW:
        return True
    return any(abs(r / f - 1) <= SPLIT_TOLERANCE for f in SPLIT_FACTORS)
