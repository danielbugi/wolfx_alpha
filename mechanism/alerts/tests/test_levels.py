"""Unit tests for the ATR risk framework (levels.py) -- including a consistency check against the dashboard's own strategy math.

Run:  python -m pytest mechanism/alerts/tests -q      (from the repo root)
"""
import os
import sys
import warnings

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from alerts import levels  # noqa: E402
from ml_training.features import price_features as pf  # noqa: E402


def test_levels_are_the_2_2_4_6_atr_geometry():
    fw = levels.risk_framework(153.92, 9.352)
    assert fw["risk_level"] == pytest.approx(153.92 - 2 * 9.352)
    assert [lv["price"] for lv in fw["levels"]] == pytest.approx([153.92 + 2 * 9.352, 153.92 + 4 * 9.352, 153.92 + 6 * 9.352])
    assert [lv["r"] for lv in fw["levels"]] == [1, 2, 3]
    assert [lv["reward_to_risk"] for lv in fw["levels"]] == pytest.approx([1.0, 2.0, 3.0])          # geometry: 2/2, 4/2, 6/2
    assert fw["risk_pct"] == pytest.approx(2 * 9.352 / 153.92 * 100) and fw["atr_pct"] == pytest.approx(9.352 / 153.92 * 100)


def test_the_risk_multiple_matches_the_feature_module_used_for_training_and_alerts():
    assert levels.RISK_ATR == pf.STOP_ATR == 2.0


def test_bot_numbers_equal_the_dashboards_strategy_plan_to_the_cent():
    """backend/services/strategy_calc.py feeds the UI's Strategy page. Same signal in -> same numbers out, so they cannot drift."""
    strategy_calc = pytest.importorskip("services.strategy_calc")
    close, atr = 153.92, 9.352
    signal = {"signal_type": "bullish_breakout", "current_price": close, "atr_14": atr,
              "stop_loss_price": close - 2 * atr, "target_price": close + 6 * atr}      # exactly what the screener writes
    ui = strategy_calc.compute_strategy_plan(signal)
    fw = levels.risk_framework(close, atr)
    assert round(fw["risk_level"], 2) == ui["stop_loss_price"] == 135.22
    assert [round(lv["price"], 2) for lv in fw["levels"]] == [ui["tp1"], ui["tp2"], ui["tp3"]] == [172.62, 191.33, 210.03]
    assert round(fw["risk_pct"], 2) == ui["risk_pct"]
    # Prices match to the cent. The UI derives its reward % from the ALREADY ROUNDED prices (36.45), the bot from the exact ones
    # (36.46): a 0.01-point rounding-order artifact of strategy_calc.py, not a difference in the levels.
    assert [lv["pct"] for lv in fw["levels"]] == pytest.approx(
        [ui["reward_pct_tp1"], ui["reward_pct_tp2"], ui["reward_pct_tp3"]], abs=0.011)


@pytest.mark.parametrize("close,atr", [(None, 1.0), (10.0, None), (0, 1.0), (-5.0, 1.0), (10.0, 0), (10.0, -1.0),
                                       (float("nan"), 1.0), (10.0, float("nan")), (10.0, float("inf")),
                                       (10.0, 5.0), (10.0, 6.0)])                        # the last two: 2 x ATR >= price
def test_no_levels_when_inputs_are_missing_or_the_risk_level_would_not_be_positive(close, atr):
    assert levels.risk_framework(close, atr) is None


def test_a_volatile_but_valid_stock_still_gets_levels():
    fw = levels.risk_framework(5.81, 0.469)                                              # GEMI on 2026-09-18: ATR = 8.1% of price
    assert fw["risk_level"] == pytest.approx(4.872) and fw["levels"][2]["price"] == pytest.approx(8.624)
