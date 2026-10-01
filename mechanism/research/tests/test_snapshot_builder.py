"""t0_v1 snapshot builder: parity with price_features, missing-is-not-zero, session integrity. Pure -- no database."""
import copy
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from conftest import ROOT  # noqa: F401  (sys.path setup)
from ml_training.features import price_features as pf
from research import registry, snapshot_builder as sb

START = date(2024, 1, 2)


def make_rows(n=320, seed=7, start_price=50.0):
    rng = np.random.default_rng(seed)
    close = start_price * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
    rows = []
    for i in range(n):
        c = float(close[i])
        o = c * (1 + rng.normal(0, 0.004))
        rows.append({"date": START + timedelta(days=i), "open": round(o, 4),
                     "high": round(max(o, c) * (1 + abs(rng.normal(0, 0.006))), 4),
                     "low": round(min(o, c) * (1 - abs(rng.normal(0, 0.006))), 4),
                     "close": round(c, 4), "volume": int(rng.integers(400_000, 2_000_000))})
    return rows


def session_of(rows):
    return rows[-1]["date"]


def test_values_equal_price_features_for_the_same_bar():
    rows = make_rows()
    snap = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology")
    px = sb.prepare_frame(rows)
    ind = pf.compute_indicators(px)
    expected = pf.build_breakout_features(ind, np.array([len(px) - 1]), np.array([1])).iloc[0]
    shared = [n for n in registry.FEATURE_NAMES if n in expected.index]
    assert len(shared) == 20
    for name in shared:
        want = None if pd.isna(expected[name]) else float(expected[name])
        assert snap.features[name] == pytest.approx(want), name
    assert snap.features["atr_14"] == pytest.approx(float(ind["atr"].iloc[-1]))
    assert snap.features["dollar_vol_20"] == pytest.approx(float(ind["dollar_vol_20"].iloc[-1]))


def test_direction_independent_block_does_not_depend_on_direction():
    rows = make_rows()
    px = sb.prepare_frame(rows)
    ind = pf.compute_indicators(px)
    pos = np.array([len(px) - 1])
    up = pf.build_breakout_features(ind, pos, np.array([1])).iloc[0]
    dn = pf.build_breakout_features(ind, pos, np.array([-1])).iloc[0]
    for name in registry.FEATURE_NAMES:
        if name in up.index:
            a, b = up[name], dn[name]
            assert (pd.isna(a) and pd.isna(b)) or a == b, name


def test_causal_truncation_equals_full_history_at_that_bar():
    rows = make_rows()
    k = 250
    truncated = sb.build_t0_v1("AAA", rows[k]["date"], rows[:k + 1])
    px = sb.prepare_frame(rows)
    ind = pf.compute_indicators(px)
    expected = pf.build_breakout_features(ind, np.array([k]), np.array([1])).iloc[0]
    for name in registry.FEATURE_NAMES:
        if name in expected.index:
            want = None if pd.isna(expected[name]) else float(expected[name])
            assert truncated.features[name] == pytest.approx(want), name


def test_feature_keys_match_the_manifest_in_order():
    rows = make_rows()
    snap = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology")
    manifest_names = [f["name"] for f in registry.build_manifest()["features"]]
    assert list(snap.features) == manifest_names
    assert len(registry.FEATURE_NAMES) == 22 and manifest_names[-1] == registry.DISCONTINUITY_FLAG


def test_complete_snapshot_with_sector_has_nothing_missing():
    rows = make_rows()
    snap = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology", sector_source="daily_fundamentals",
                          sector_asof=date(2024, 11, 1))
    assert snap.missing_features == [] and snap.status == "complete"
    assert snap.sector == "Technology" and snap.sector_source == "daily_fundamentals"
    assert snap.bars_available == 320 and snap.bar_date == session_of(rows)
    assert snap.volume == rows[-1]["volume"] and snap.prev_close == rows[-2]["close"]


def test_short_history_is_partial_with_nulls_never_zero():
    rows = make_rows(n=30)
    snap = sb.build_t0_v1("NEW", session_of(rows), rows, sector="Technology")
    assert snap.status == "partial"
    for name in ("dist_sma200_pct", "dist_sma50_pct", "ret_60d", "pct_from_52w_high", "pct_from_52w_low",
                 "vol_ratio_50"):
        assert snap.features[name] is None and name in snap.missing_features, name
    for name in ("rsi_14", "atr_14", "macd_norm", "ret_20d", "dist_sma50_pct" if False else "sma10_vs_20"):
        assert snap.features[name] is not None, name  # enough bars for these: measured, not nulled
    assert sorted(snap.missing_features) == sorted(set(snap.missing_features))
    assert not [n for n in snap.missing_features if snap.features.get(n) is not None]


def test_one_bar_history_has_no_prev_close_and_says_so():
    rows = make_rows(n=1)
    snap = sb.build_t0_v1("IPO", session_of(rows), rows, sector="Technology")
    assert snap.prev_close is None and "prev_close" in snap.missing_features
    assert snap.features["gap_pct"] is None and snap.features["atr_14"] is None


def test_missing_sector_is_null_and_named_never_unknown():
    rows = make_rows()
    snap = sb.build_t0_v1("AAA", session_of(rows), rows, sector=None)
    assert snap.sector is None and snap.sector_source is None and snap.sector_asof is None
    assert "sector" in snap.missing_features and snap.status == "partial"
    snap2 = sb.build_t0_v1("AAA", session_of(rows), rows, sector="")
    assert snap2.sector is None


def test_null_volume_at_t0_is_null_not_zero():
    rows = make_rows()
    rows[-1]["volume"] = None
    snap = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology")
    assert snap.volume is None and "volume" in snap.missing_features
    assert snap.features["vol_ratio_10"] is None and snap.features["vol_ratio_50"] is None
    assert snap.features["rsi_14"] is not None


def test_zero_range_bar_leaves_close_location_null():
    rows = make_rows()
    c = rows[-1]["close"]
    rows[-1].update(open=c, high=c, low=c)
    snap = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology")
    assert snap.features["close_location"] is None and "close_location" in snap.missing_features
    assert snap.features["bar_range_atr"] == 0.0  # a measured zero range, not a default


def test_constant_prices_leave_bollinger_null():
    rows = make_rows(n=60)
    for r in rows:
        r.update(open=10.0, high=10.0, low=10.0, close=10.0)
    snap = sb.build_t0_v1("FLAT", session_of(rows), rows, sector="Technology")
    assert snap.features["bollinger_pos"] is None and "bollinger_pos" in snap.missing_features


def test_stale_newest_bar_is_skipped_not_relabelled():
    rows = make_rows()
    out = sb.build_t0_v1("AAA", session_of(rows) + timedelta(days=1), rows)
    assert isinstance(out, sb.SnapshotSkip) and out.reason == "stale_bar"


def test_a_bar_from_after_the_session_is_not_used():
    rows = make_rows()
    out = sb.build_t0_v1("AAA", rows[-2]["date"], rows)  # newest bar is later than the session
    assert isinstance(out, sb.SnapshotSkip) and out.reason == "stale_bar"


def test_empty_history_is_skipped():
    assert sb.build_t0_v1("AAA", START, []).reason == "no_price_history"
    rows = make_rows(n=5)
    for r in rows:
        r["close"] = None
    assert sb.build_t0_v1("AAA", session_of(rows), rows).reason == "no_price_history"


def test_incomplete_t0_ohlc_is_skipped():
    rows = make_rows()
    rows[-1]["open"] = None
    out = sb.build_t0_v1("AAA", session_of(rows), rows)
    assert isinstance(out, sb.SnapshotSkip) and out.reason == "incomplete_bar"


def test_timestamp_dates_are_accepted():
    rows = make_rows()
    for r in rows:
        r["date"] = pd.Timestamp(r["date"])
    snap = sb.build_t0_v1("AAA", session_of(rows).date(), rows, sector="Technology")
    assert isinstance(snap, sb.Snapshot) and snap.bar_date == session_of(rows).date()


def test_discontinuity_flag_inside_and_outside_the_window():
    rows = make_rows()
    jumped = copy.deepcopy(rows)
    for r in jumped[200:]:  # an unadjusted 5x step 120 bars before T0
        for k in ("open", "high", "low", "close"):
            r[k] = r[k] * 5
    s_in = sb.build_t0_v1("SPL", session_of(jumped), jumped, sector="Technology")
    assert s_in.features[registry.DISCONTINUITY_FLAG] is True
    clean = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology")
    assert clean.features[registry.DISCONTINUITY_FLAG] is False
    old = make_rows(n=400)
    for r in old[50:]:
        for k in ("open", "high", "low", "close"):
            r[k] = r[k] * 5
    s_out = sb.build_t0_v1("OLD", session_of(old), old, sector="Technology")
    assert s_out.features[registry.DISCONTINUITY_FLAG] is False  # 350 bars back: outside the 253 window


def test_payload_is_strict_json_and_input_is_untouched():
    rows = make_rows()
    rows[-1]["volume"] = None
    before = copy.deepcopy(rows)
    snap = sb.build_t0_v1("AAA", session_of(rows), rows)
    json.dumps(snap.features, allow_nan=False)
    assert rows == before


def test_content_hash_is_deterministic_and_value_sensitive():
    rows = make_rows()
    a = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology")
    b = sb.build_t0_v1("AAA", session_of(rows), copy.deepcopy(rows), sector="Technology")
    assert a.content_hash == b.content_hash and len(a.content_hash) == 64
    rows[-1]["close"] = rows[-1]["close"] * 1.01
    rows[-1]["high"] = max(rows[-1]["high"], rows[-1]["close"])
    c = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Technology")
    assert c.content_hash != a.content_hash
    d = sb.build_t0_v1("AAA", session_of(rows), rows, sector="Energy")
    assert d.content_hash != c.content_hash


def test_snapshot_records_manifest_hash_and_real_code_ref():
    rows = make_rows()
    snap = sb.build_t0_v1("AAA", session_of(rows), rows)
    assert snap.manifest_hash == registry.manifest_hash(registry.build_manifest())
    assert snap.code_ref.startswith("t0_v1:impl=") and len(snap.code_ref.split("=")[1]) == 12
    assert len(snap.manifest_hash) == 64
