"""risk_regime_v1: pure, no database. The worked example, the thresholds, the coverage rule, missing != 0, determinism."""
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from market_intelligence import risk_regime as R  # noqa: E402

D = date(2026, 9, 30)
EXAMPLE = {"c1_index_trend": 1 / 3, "c2_index_momentum": 0.50, "c3_breadth_sma50": 0.32, "c4_breadth_sma200": 0.08,
           "c5_net_highs_lows": 0.30, "c6_volatility": 0.22, "c7_risk_appetite": -1 / 3}


def test_worked_example_from_the_specification():
    r = R.from_scores(D, EXAMPLE)
    assert r.score == 0.2350 and r.state == R.NEUTRAL
    assert r.present_weight == 1.0
    assert r.strength == 0.2350 and r.strength_label == "mixed"
    assert r.agreement == 0.90
    assert r.model_version == "risk_regime_v1"
    assert r.reasons == []                                       # reasons are formatted from stored raw values; none were supplied


def test_weights_are_the_declared_ones_and_sum_to_one():
    assert R.WEIGHTS == {"c1_index_trend": 0.25, "c2_index_momentum": 0.10, "c3_breadth_sma50": 0.20, "c4_breadth_sma200": 0.10,
                         "c5_net_highs_lows": 0.10, "c6_volatility": 0.15, "c7_risk_appetite": 0.10}
    assert abs(sum(R.WEIGHTS.values()) - 1) < 1e-12


@pytest.mark.parametrize("s,state", [(0.30, R.RISK_ON), (0.2999, R.NEUTRAL), (-0.30, R.RISK_OFF), (-0.2999, R.NEUTRAL), (0.0, R.NEUTRAL),
                                     (1.0, R.RISK_ON), (-1.0, R.RISK_OFF)])
def test_thresholds_are_inclusive_at_four_decimals(s, state):
    r = R.from_scores(D, {k: s for k in R.WEIGHTS})
    assert r.score == pytest.approx(s) and r.state == state


def test_the_stored_four_dp_value_is_what_is_classified():
    # 0.29996 displays/stores as 0.3 -> RISK_ON; classification can never disagree with the stored number
    r = R.from_scores(D, {k: 0.29996 for k in R.WEIGHTS})
    assert r.score == 0.3 and r.state == R.RISK_ON


def test_strength_labels():
    assert R.strength_label(0.2999) == "mixed" and R.strength_label(0.30) == "moderate"
    assert R.strength_label(0.5999) == "moderate" and R.strength_label(0.60) == "strong" and R.strength_label(None) is None


def test_losing_c6_and_c7_still_computes_at_75_percent_coverage():
    sc = dict(EXAMPLE, c6_volatility=None, c7_risk_appetite=None)
    r = R.from_scores(D, sc)
    assert r.present_weight == 0.75 and r.state != R.UNAVAILABLE
    assert r.score == round((0.25 / 3 + 0.05 + 0.064 + 0.008 + 0.03) / 0.75, 4)


def test_losing_all_breadth_or_all_index_inputs_is_unavailable_with_null_score():
    no_breadth = dict(EXAMPLE, c3_breadth_sma50=None, c4_breadth_sma200=None, c5_net_highs_lows=None)
    r = R.from_scores(D, no_breadth)
    assert r.present_weight == 0.60 and r.state == R.UNAVAILABLE and r.score is None
    assert r.strength is None and r.strength_label is None and r.agreement is None and r.reasons == []
    no_index = dict(EXAMPLE, c1_index_trend=None, c2_index_momentum=None, c6_volatility=None, c7_risk_appetite=None)
    r2 = R.from_scores(D, no_index)
    assert r2.present_weight == 0.40 and r2.state == R.UNAVAILABLE and r2.score is None


def test_a_missing_component_is_not_a_zero():
    full = R.from_scores(D, dict(EXAMPLE, c2_index_momentum=0.0))
    miss = R.from_scores(D, dict(EXAMPLE, c2_index_momentum=None))
    assert full.score != miss.score
    assert [c for c in miss.components if c.key == "c2_index_momentum"][0].score is None


def test_agreement_distinguishes_flat_from_conflicted():
    flat = R.from_scores(D, {k: 0.0 for k in R.WEIGHTS})
    assert flat.state == R.NEUTRAL and flat.agreement is None
    conflicted = R.from_scores(D, dict({k: 1.0 for k in R.WEIGHTS}, c1_index_trend=-1.0, c3_breadth_sma50=-1.0, c4_breadth_sma200=-1.0))
    assert conflicted.agreement == round(0.55 / 1.0, 4)


# ------------------------------------------------------------------ real computations on synthetic data
def _series(n, start, step, end_date=D):
    idx = pd.bdate_range(end=pd.Timestamp(end_date), periods=n)
    return pd.Series(start + step * np.arange(n), index=idx, dtype=float)


def _panel(n_stocks, n_days, up_share, end_date=D):
    idx = pd.bdate_range(end=pd.Timestamp(end_date), periods=n_days)
    cols = [f"S{i:04d}" for i in range(n_stocks)]
    base = np.linspace(100, 100, n_days)
    close = pd.DataFrame(np.tile(base, (n_stocks, 1)).T, index=idx, columns=cols)
    n_up = int(round(n_stocks * up_share))
    ramp = np.linspace(0, 20, n_days)
    close.iloc[:, :n_up] += ramp[:, None]                      # these end above their averages
    close.iloc[:, n_up:] -= ramp[:, None]                      # these end below
    high, low = close * 1.001, close * 0.999
    return close, high, low


def _idx(spx_step=1.0, vix=17.8):
    spx = _series(260, 4000, spx_step)
    rut = _series(260, 2000, spx_step / 2)
    v = _series(260, vix, 0)
    return {"^GSPC": spx, "^RUT": rut, "^VIX": v}


def test_c1_levels_and_missing():
    t = pd.Timestamp(D)
    up = R.c1_index_trend(_series(260, 4000, 1.0), t)
    assert up.present and up.raw["n_conditions_true"] == 3 and up.score == 1.0
    down = R.c1_index_trend(_series(260, 5000, -1.0), t)
    assert down.raw["n_conditions_true"] == 0 and down.score == -1.0
    assert R.c1_index_trend(_series(199, 4000, 1.0), t).present is False                 # 199 bars
    stale = _series(260, 4000, 1.0, end_date=date(2026, 9, 29))
    c = R.c1_index_trend(stale, t)
    assert c.present is False and c.score is None                                       # no bar on t: never carried forward


def test_c1_includes_day_t_in_the_average_and_uses_strict_greater():
    t = pd.Timestamp(D)
    flat = _series(260, 100.0, 0.0)
    c = R.c1_index_trend(flat, t)
    assert c.raw["n_conditions_true"] == 0                                              # equal is not above


def test_c2_and_c7_formulas_and_clipping():
    t = pd.Timestamp(D)
    spx = _series(260, 1000, 0.0)
    spx.iloc[-21:] = np.linspace(1000, 1025, 21)                                       # +2.5% over 20 sessions
    c2 = R.c2_index_momentum(spx, t)
    assert c2.raw["ret20_pct"] == pytest.approx(2.5) and c2.score == pytest.approx(0.5)
    huge = spx.copy(); huge.iloc[-1] = 5000
    assert R.c2_index_momentum(huge, t).score == 1.0
    rut = _series(260, 1000, 0.0); rut.iloc[-21:] = np.linspace(1000, 1015, 21)         # +1.5% -> d = -1.0 pp
    c7 = R.c7_risk_appetite(rut, spx, t)
    assert c7.raw["difference_pp"] == pytest.approx(-1.0) and c7.score == pytest.approx(-1 / 3)
    assert R.c2_index_momentum(_series(20, 1, 1), t).present is False                  # < 21 bars


def test_c6_volatility_mapping():
    t = pd.Timestamp(D)
    for v, s in ((10, 1.0), (20, 0.0), (30, -1.0), (17.8, 0.22), (5, 1.0), (45, -1.0)):
        c = R.c6_volatility(_series(5, v, 0), t)
        assert c.score == pytest.approx(s)
    assert R.c6_volatility(None, t).present is False


def test_breadth_components_on_a_synthetic_panel():
    close, high, low = _panel(1200, 260, up_share=0.58)
    comps = {c.key: c for c in R.breadth_components(close, high, low, pd.Timestamp(D))}
    c3, c4 = comps["c3_breadth_sma50"], comps["c4_breadth_sma200"]
    assert c3.present and c3.raw["n_valid"] == 1200 and c3.raw["p50"] == pytest.approx(58.0)
    assert c3.score == pytest.approx(0.32) and c4.score == pytest.approx(0.32)
    c5 = comps["c5_net_highs_lows"]
    assert c5.present and c5.raw["n_range"] == 1200


def test_breadth_needs_at_least_1000_valid_stocks():
    close, high, low = _panel(999, 260, 0.6)
    for c in R.breadth_components(close, high, low, pd.Timestamp(D)):
        assert c.present is False and c.score is None and "999" in c.missing_reason
    close, high, low = _panel(1000, 260, 0.6)
    assert all(c.present for c in R.breadth_components(close, high, low, pd.Timestamp(D)))


def test_breadth_strictly_above_and_short_history_stocks_are_left_out():
    close, high, low = _panel(1100, 260, 0.5)
    close.iloc[:, :50] = np.nan                                                          # no history at all
    close.iloc[:150, 50:100] = np.nan                                                    # 110 bars: has an SMA50 but no SMA200
    comps = {c.key: c for c in R.breadth_components(close, high, low, pd.Timestamp(D))}
    assert comps["c3_breadth_sma50"].raw["n_valid"] == 1050
    assert comps["c4_breadth_sma200"].raw["n_valid"] == 1000
    flat = close.copy(); flat[:] = 50.0
    c = {c.key: c for c in R.breadth_components(flat, flat * 1.0, flat * 1.0, pd.Timestamp(D))}
    assert c["c3_breadth_sma50"].raw["n_above"] == 0                                     # equal to the average is not above


def test_a_partial_load_session_is_not_a_session_and_breadth_is_missing():
    close, high, low = _panel(1200, 260, 0.6)
    close.iloc[-1, 100:] = np.nan                                                        # today carried by 100 of 1200 symbols
    for c in R.breadth_components(close, high, low, pd.Timestamp(D)):
        assert c.present is False and "real session" in c.missing_reason


def test_compute_end_to_end_and_record_is_complete_and_json_safe():
    close, high, low = _panel(1200, 260, 0.58)
    r = R.compute(D, _idx(1.0), close, high, low)
    assert r.state in (R.RISK_ON, R.NEUTRAL) and r.present_weight == 1.0 and r.score is not None
    assert 1 <= len(r.reasons) <= 3 and all(isinstance(x, str) for x in r.reasons)
    rec = r.to_record()
    json.dumps(rec)
    assert set(rec["components"]) == set(R.WEIGHTS) and all(rec["available"].values())
    for k, c in rec["components"].items():
        assert c["weight"] == R.WEIGHTS[k] and c["raw"] and c["value"] is not None and -1 <= c["score"] <= 1
    for field in ("model_version", "session_date", "present_weight", "score", "state", "strength", "strength_label", "agreement", "reasons"):
        assert field in rec


def test_compute_is_deterministic_and_ignores_bars_after_the_session():
    close, high, low = _panel(1200, 262, 0.58, end_date=date(2026, 10, 2))
    idx = {k: _series(262, v.iloc[0], 1.0, end_date=date(2026, 10, 2)) for k, v in _idx().items()}
    a = R.compute(D, idx, close, high, low).to_record()
    b = R.compute(D, idx, close, high, low).to_record()
    assert a == b
    truncated = {k: v.loc[:pd.Timestamp(D)] for k, v in idx.items()}
    c = R.compute(D, truncated, close.loc[:pd.Timestamp(D)], high.loc[:pd.Timestamp(D)], low.loc[:pd.Timestamp(D)]).to_record()
    assert a == c                                                                        # future bars cannot change the answer


def test_no_breadth_panel_degrades_to_unavailable_not_to_zeros():
    r = R.compute(D, _idx(1.0))
    assert r.present_weight == 0.60 and r.state == R.UNAVAILABLE and r.score is None
    rec = r.to_record()
    assert rec["components"]["c3_breadth_sma50"]["score"] is None and rec["available"]["c3_breadth_sma50"] is False


def test_module_reads_no_clock():
    src = open(os.path.join(ROOT, "mechanism", "market_intelligence", "risk_regime.py"), encoding="utf-8").read()
    for banned in ("date.today", "datetime.now", "time.time", "utcnow"):
        assert banned not in src
