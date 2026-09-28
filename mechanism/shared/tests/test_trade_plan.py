"""Drift check: mechanism/shared/trade_plan.py deliberately duplicates the stop/target
convention in ml_training/features/price_features.py::plan_outcomes() instead of importing
it (see trade_plan.py's module docstring for why). This test is what keeps the two from
silently disagreeing -- it runs one synthetic price path through plan_outcomes() and
independently walks the same path against trade_plan.compute_levels()'s prices, and asserts
they agree on which target/stop fires and on the resulting R-multiple.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from shared import trade_plan  # noqa: E402
from ml_training.features.price_features import plan_outcomes, STOP_ATR  # noqa: E402


def test_constants_match_price_features():
    """The actual drift surface: if either side's numbers change without the other, this fails."""
    assert trade_plan.STOP_ATR_MULT == STOP_ATR
    assert trade_plan.TARGET_ATR_MULTS == tuple(lvl * STOP_ATR for lvl in (1, 2, 3))


# A bullish breakout at pos=5, entry close=100, atr=2.0 (R=4.0). Bars 6..25 (20 forward bars,
# matching PLAN_HORIZON): target1 (104) crosses at bar 8, target2 (108) crosses at bar 11,
# target3 (112) never reached, stop (96) never reached, final close 106.
_FILLER = dict(high=100.0, low=99.0, close=99.5, atr=2.0)
_ENTRY = dict(high=100.0, low=99.0, close=100.0, atr=2.0)
_FORWARD = [
    dict(high=101.0, low=99.0, close=100.0),   # 6
    dict(high=101.0, low=99.0, close=100.0),   # 7
    dict(high=105.0, low=99.0, close=104.0),   # 8  -- crosses target1 (104)
    dict(high=106.0, low=100.0, close=105.0),  # 9
    dict(high=106.0, low=100.0, close=105.0),  # 10
    dict(high=109.0, low=101.0, close=108.0),  # 11 -- crosses target2 (108)
] + [dict(high=107.0, low=101.0, close=106.0)] * 14  # 12..25, final close = 106


def _synthetic_ind() -> pd.DataFrame:
    rows = [_FILLER] * 5 + [_ENTRY] + _FORWARD
    assert len(rows) == 26
    return pd.DataFrame(rows)


def test_bullish_scenario_agrees_with_plan_outcomes():
    ind = _synthetic_ind()
    pos, direction = np.array([5]), np.array([1])

    out = plan_outcomes(ind, pos, direction, horizon=20)
    assert out["stopped"][0] == 0.0
    assert out["tp3_hit"][0] == 0.0
    # tranche 1 hit (+1), tranche 2 hit (+2), tranche 3 never hit -> mtm = (106-100)/4 = 1.5
    assert out["plan_r"][0] == pytest.approx((1.0 + 2.0 + 1.5) / 3.0)

    entry_price, atr = ind["close"][5], ind["atr"][5]
    levels = trade_plan.compute_levels(entry_price, atr, direction=1)
    assert levels["stop_price"] == 96.0
    assert levels["target1_price"] == 104.0
    assert levels["target2_price"] == 108.0
    assert levels["target3_price"] == 112.0

    # Independently walk the same forward bars against trade_plan's own price levels and
    # confirm they call the same outcome plan_outcomes did: target1 hit at bar 8 (before any
    # stop touch), target2 hit at bar 11, target3 never, stop never.
    highs = ind["high"].to_numpy()[6:26]
    lows = ind["low"].to_numpy()[6:26]
    stop_hit = np.any(lows <= levels["stop_price"])
    t1_hit_idx = np.argmax(highs >= levels["target1_price"]) if np.any(highs >= levels["target1_price"]) else None
    t2_hit_idx = np.argmax(highs >= levels["target2_price"]) if np.any(highs >= levels["target2_price"]) else None
    t3_hit = np.any(highs >= levels["target3_price"])

    assert not stop_hit
    assert t1_hit_idx == 2   # bar 8 is offset 2 into the forward window (0-indexed)
    assert t2_hit_idx == 5   # bar 11 is offset 5
    assert not t3_hit


def test_compute_levels_rejects_invalid_atr_and_direction():
    with pytest.raises(ValueError):
        trade_plan.compute_levels(100.0, atr=0.0, direction=1)
    with pytest.raises(ValueError):
        trade_plan.compute_levels(100.0, atr=2.0, direction=0)
