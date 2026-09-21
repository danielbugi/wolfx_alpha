# mechanism/alerts/levels.py
"""
ATR risk framework: the arithmetic behind the bot's /levels reply. Pure functions, no I/O.

It is the SAME formula the dashboard's Strategy page uses (backend/services/strategy_calc.py + the screener): the risk level
is the price minus 2 x ATR(14) and the three reference levels are the price plus 2 / 4 / 6 x ATR, i.e. 1R / 2R / 3R where
R = the distance from the price to the risk level. test_levels.py asserts the numbers equal strategy_calc's, so the bot and the
UI cannot drift apart.

Framing (public, educational audience -- see MILESTONES.md 6C): these are reference distances computed identically for every
stock. They are arithmetic, not a forecast and not a recommendation; nothing here says price will reach any level.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

RISK_ATR = 2.0                         # risk level = close - RISK_ATR x ATR   (== 1R)
LEVEL_ATRS = (2.0, 4.0, 6.0)           # reference levels = close + k x ATR     (== 1R, 2R, 3R)
MIN_RISK_LEVEL = 0.0                   # a risk level at or below zero means the ATR is too large for the price


def risk_framework(close: Optional[float], atr: Optional[float]) -> Optional[Dict]:
    """Levels for a long-side geometry from the last close and ATR(14). None when they cannot be computed honestly
    (missing / non-positive inputs, or 2 x ATR would put the risk level at or below zero)."""
    if close is None or atr is None:
        return None
    if not (math.isfinite(close) and math.isfinite(atr)) or close <= 0 or atr <= 0:
        return None
    risk = RISK_ATR * atr
    risk_level = close - risk
    if risk_level <= MIN_RISK_LEVEL:
        return None
    levels: List[Dict] = []
    for k, mult in enumerate(LEVEL_ATRS, 1):
        dist = mult * atr
        levels.append({"r": k, "price": close + dist, "pct": dist / close * 100, "atr_multiple": mult,
                       "reward_to_risk": dist / risk})
    return {"close": close, "atr": atr, "atr_pct": atr / close * 100, "risk_level": risk_level,
            "risk_pct": risk / close * 100, "levels": levels}
