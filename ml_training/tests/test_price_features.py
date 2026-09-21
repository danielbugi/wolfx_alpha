"""Unit tests for ml_training/features/price_features.py (synthetic data, no DB needed).

Run:  python -m pytest ml_training/tests -q      (from the repo root)
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from ml_training.features import price_features as pf  # noqa: E402


def synth(n=600, seed=0, start="2022-01-03"):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0004, 0.02, n)
    close = 50 * np.exp(np.cumsum(r))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(200_000, 2_000_000, n).astype(float)
    return pd.DataFrame({"date": pd.bdate_range(start, periods=n), "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


def features_at(px, sector="Technology"):
    ind = pf.compute_indicators(px)
    d = pf.detect_breakouts(ind)
    pos = np.flatnonzero(d != 0)
    return ind, pos, d[pos], pf.build_breakout_features(ind, pos, d[pos], sector)


def test_features_are_causal_truncation_parity():
    """Features for a breakout bar must be identical whether or not later bars exist."""
    px = synth()
    _, pos, dirs, full = features_at(px)
    assert len(pos) > 10
    for k in (3, len(pos) // 2, len(pos) - 1):
        t = pos[k]
        ind_t = pf.compute_indicators(px.iloc[: t + 1].reset_index(drop=True))
        d_t = pf.detect_breakouts(ind_t)
        assert d_t[-1] == dirs[k]
        trunc = pf.build_breakout_features(ind_t, np.array([t]), np.array([dirs[k]]), "Technology")
        pd.testing.assert_series_equal(trunc.iloc[0], full.iloc[k], check_names=False, rtol=1e-9, atol=1e-9)


def test_feature_columns_match_contract_and_unknown_sector_is_nan():
    px = synth()
    ind = pf.compute_indicators(px)
    pos = np.flatnonzero(pf.detect_breakouts(ind) != 0)
    df = pf.build_breakout_features(ind, pos, pf.detect_breakouts(ind)[pos], None)
    assert list(df.columns) == pf.FEATURE_NAMES
    assert df[[c for c in df.columns if c.startswith("sector_")]].isna().all().all()  # missing, not "none"
    assert not np.isinf(df.to_numpy(dtype=float)).any()


def test_breakout_detection_rule():
    """Rule == live screener: close above the PREVIOUS bar's 20-bar channel high. (The screener's extra
    'prev_close <= prev_channel_high' clause is always true because the channel includes the previous
    bar's own high, so consecutive new-high closes each count as a breakout.)"""
    n = 40
    px = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=n), "open": 100.0, "high": 101.0,
                       "low": 99.0, "close": 100.0, "volume": 1e6})
    px.loc[30, ["close", "high"]] = [103.0, 103.5]   # above prior channel high (101)
    px.loc[31, ["close", "high"]] = [103.4, 103.5]   # inside the channel that now includes bar 30 -> no breakout
    px.loc[32, ["close", "high"]] = [104.0, 104.5]   # above prior channel high (103.5) again
    d = pf.detect_breakouts(pf.compute_indicators(px))
    assert (d[30], d[31], d[32]) == (1, 0, 1)
    px.loc[35, ["close", "low"]] = [80.0, 79.0]
    assert pf.detect_breakouts(pf.compute_indicators(px))[35] == -1


def _path(highs, lows, closes, atr_seed=2.0):
    """Frame with entry at bar 30 and a controlled 20-bar future; ATR forced via flat pre-history."""
    n = 60
    px = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=n), "open": 100.0, "high": 100 + atr_seed / 2,
                       "low": 100 - atr_seed / 2, "close": 100.0, "volume": 1e6})
    for i, (h, l, c) in enumerate(zip(highs, lows, closes), start=31):
        px.loc[i, ["high", "low", "close"]] = [h, l, c]
    return px


def test_plan_outcomes_target_stop_tiebreak_and_timeout():
    fl = [100.0] * 20
    ind = pf.compute_indicators(_path([100.0] * 20, [100.0] * 20, fl))
    atr = ind["atr"].iloc[30]
    R = 2 * atr
    # long: tp3 (3R) reached on day 1, stop never -> +3R on every tranche
    hi = [100 + 3 * R + 0.01] + [100.0] * 19
    out = pf.plan_outcomes(pf.compute_indicators(_path(hi, [100.0] * 20, fl)), np.array([30]), np.array([1]))
    assert out.plan_r[0] == pytest.approx(2.0) and out.tp3_hit[0] == 1 and out.stopped[0] == 0
    # same bar touches both stop and 3R -> stop first, -1R on all tranches
    lo = [100 - R - 0.01] + [100.0] * 19
    out = pf.plan_outcomes(pf.compute_indicators(_path(hi, lo, fl)), np.array([30]), np.array([1]))
    assert out.plan_r[0] == pytest.approx(-1.0) and out.tp3_hit[0] == 0 and out.stopped[0] == 1
    # tp1 then stop later: tranche1 +1R, others -1R
    hi2 = [100 + R + 0.01] + [100.0] * 19
    lo2 = [100.0] + [100 - R - 0.01] + [100.0] * 18
    out = pf.plan_outcomes(pf.compute_indicators(_path(hi2, lo2, fl)), np.array([30]), np.array([1]))
    assert out.plan_r[0] == pytest.approx((1 - 1 - 1) / 3)
    # short mirror: price falls 3R
    lo3 = [100 - 3 * R - 0.01] + [100.0] * 19
    out = pf.plan_outcomes(pf.compute_indicators(_path([100.0] * 20, lo3, fl)), np.array([30]), np.array([-1]))
    assert out.plan_r[0] == pytest.approx(2.0)
    # timeout marks to market: closes at +1R on the last bar, no barrier touched
    cl = [100.0] * 19 + [100 + 0.9 * R]
    out = pf.plan_outcomes(pf.compute_indicators(_path([100.0] * 19 + [100 + 0.9 * R], [100.0] * 20, cl)),
                           np.array([30]), np.array([1]))
    assert out.plan_r[0] == pytest.approx(0.9) and out.stopped[0] == 0
    # not enough forward bars -> NaN, never a fabricated number
    out = pf.plan_outcomes(pf.compute_indicators(synth(60)), np.array([55]), np.array([1]))
    assert np.isnan(out.plan_r[0])


def test_discontinuities_and_contamination_window():
    px = synth(800)
    px.loc[200:, ["open", "high", "low", "close"]] *= 0.1        # unadjusted 10:1 reverse split, ratio 0.1
    disc = pf.find_discontinuities(px)
    assert list(disc["pos"]) == [200] and disc["kind"].iloc[0] == "jump_down"
    m = pf.contaminated_mask(len(px), disc["pos"])
    assert m[200 - pf.PLAN_HORIZON] and not m[200 - pf.PLAN_HORIZON - 1]      # label window would cross it
    assert m[200 + pf.LOOKBACK_BARS - 1] and not m[200 + pf.LOOKBACK_BARS]    # lookback would include it
    px2 = synth(100)
    px2.loc[50, "close"] = 0.0
    assert pf.find_discontinuities(px2)["kind"].tolist().count("nonpositive") == 1


def test_legacy_score_matches_original_labeler():
    """The extracted composite must equal momentum_labeler.calculate_momentum_score exactly."""
    from ml_training.data_preparation.momentum_labeler import MomentumLabeler
    rng = np.random.default_rng(1)
    for trial in range(200):
        n = int(rng.integers(6, 20))
        c = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        h, l = c * 1.01, c * 0.99
        v = rng.integers(1e5, 5e6, n).astype(float)
        bull = bool(trial % 2)
        df = pd.DataFrame({"close": c, "high": h, "low": l, "volume": v})
        expected = MomentumLabeler.calculate_momentum_score(None, df, c[0], "bullish" if bull else "bearish")
        assert pf.legacy_momentum_score(c, h, l, v, c[0], bull) == expected
