"""Big Winner harness, core: the canonical label (checked against the repository's own `opportunity.favourable_excursion`), maturity, episodes, splits, provenance and the dataset builder (causality, determinism,
entry at the next open, purge)."""
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from bw_harness import episodes as E  # noqa: E402
from bw_harness import features as F  # noqa: E402
from bw_harness import labels as L  # noqa: E402
from bw_harness import provenance as P  # noqa: E402
from bw_harness import splits as S  # noqa: E402
from bw_harness import synthetic as syn  # noqa: E402
from bw_harness.config import DEFAULT, Config  # noqa: E402
from research import opportunity as OPP  # noqa: E402

CFG = DEFAULT


def path(n=400, seed=0, sigma=0.02):
    rng = np.random.default_rng(seed)
    c = 50 * np.exp(np.cumsum(rng.normal(0.0003, sigma, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, sigma * 0.3, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, sigma * 0.4, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, sigma * 0.4, n)))
    atr = np.full(n, 50 * sigma * 0.9) * (c / 50)
    return o, h, l, c, atr


# ---------------------------------------------------------------- the canonical label
@pytest.mark.parametrize("direction", [1, -1])
def test_the_label_equals_the_repositorys_own_favourable_excursion_for_every_horizon(direction):
    o, h, l, c, atr = path(500, seed=3 if direction == 1 else 4)
    idx = np.arange(30, 330, 7)
    lab = L.signal_labels(o, h, l, c, atr, idx, np.full(len(idx), direction), CFG)
    for k, i in enumerate(idx):
        ref = OPP.favourable_excursion(direction, float(o[i + 1]), float(atr[i]), o, h, l, c, i + 1, horizons=CFG.horizons)
        for H in CFG.horizons:
            r = ref[f"h{H}"]
            if r is None:
                assert lab[f"status_{H}"][k] == "immature" and np.isnan(lab[f"mfe_r_{H}"][k])
            else:
                assert lab[f"status_{H}"][k] == "ok"
                assert abs(lab[f"mfe_r_{H}"][k] - r["max_favourable_r"]) < 1e-3 and abs(lab[f"mfe_pct_{H}"][k] - r["max_favourable_pct"]) < 1e-3


def test_entry_is_the_next_open_the_signal_bar_is_not_in_the_window_and_the_entry_bar_is():
    o, h, l, c, atr = path(300, seed=1)
    i = 100
    base = L.signal_labels(o, h, l, c, atr, np.array([i]), np.array([1]), CFG)
    assert base["entry_px"][0] == o[i + 1] and base["unit_r"][0] == 2.0 * atr[i]
    h2 = h.copy(); h2[i] *= 5                                          # a spike on the SIGNAL bar: not part of the favourable excursion
    assert L.signal_labels(o, h2, l, c, atr, np.array([i]), np.array([1]), CFG)["mfe_r_20"][0] == base["mfe_r_20"][0]
    h3 = h.copy(); h3[i + 1] *= 5                                      # a spike on the ENTRY bar: counts (window = bars i+1 .. i+H)
    assert L.signal_labels(o, h3, l, c, atr, np.array([i]), np.array([1]), CFG)["mfe_r_20"][0] > base["mfe_r_20"][0]
    c2 = c.copy(); c2[i] *= 3                                          # the signal close does not enter the label at all
    assert L.signal_labels(o, h, l, c2, atr, np.array([i]), np.array([1]), CFG)["mfe_r_20"][0] == base["mfe_r_20"][0]
    o2 = o.copy(); o2[i + 1] *= 1.05
    assert L.signal_labels(o2, h, l, c, atr, np.array([i]), np.array([1]), CFG)["entry_px"][0] == o2[i + 1]


def test_tiers_are_separate_indicators_of_the_same_excursion():
    n = 200
    o = np.full(n, 100.0); h = np.full(n, 100.5); l = np.full(n, 99.5); c = np.full(n, 100.0)
    atr = np.full(n, 1.0)                                              # 1R = 2.0
    h[50] = 100 + 2.0 * 6.2                                            # peak of 6.2R at bar 50
    lab = L.signal_labels(o, h, l, c, atr, np.array([10]), np.array([1]), CFG)
    assert abs(lab["mfe_r_60"][0] - 6.2) < 1e-9 and lab["ge3r_60"][0] == 1 and lab["ge5r_60"][0] == 1 and lab["ge10r_60"][0] == 0
    assert L.tier_of(6.2) == 2 and L.tier_of(2.9) == 0 and L.tier_of(10.0) == 3 and L.tier_of(float("nan")) == -1


def test_mfe_is_kept_apart_from_what_the_position_stood_at_the_end():
    n = 200
    o = np.full(n, 100.0); h = np.full(n, 100.2); l = np.full(n, 99.8); c = np.full(n, 100.0)
    atr = np.full(n, 1.0)
    h[40] = 100 + 2.0 * 7.0                                           # peaks at 7R ...
    c[10 + 60] = 98.0                                                 # ... and closes bar i+60 at -1R
    lab = L.signal_labels(o, h, l, c, atr, np.array([10]), np.array([1]), CFG)
    assert abs(lab["mfe_r_60"][0] - 7.0) < 1e-9 and abs(lab["terminal_r_60"][0] - (-1.0)) < 1e-9 and lab["persist_60"][0] == 0.0
    assert lab["ge5r_60"][0] == 1.0                                  # a winner by MFE even though it ended below its entry: MFE is opportunity, not a result


def test_an_incomplete_window_is_immature_not_negative_and_never_counted():
    o, h, l, c, atr = path(300, seed=2)
    n = len(c)
    i_ok, i_edge, i_late = n - 1 - 120, n - 120, n - 5
    lab = L.signal_labels(o, h, l, c, atr, np.array([i_ok, i_edge, i_late]), np.array([1, 1, 1]), CFG)
    assert list(lab["status_120"]) == ["ok", "immature", "immature"]          # the window i+1 .. i+120 must lie fully inside the data
    for k in ("mfe_r_120", "ge5r_120", "ge10r_120", "terminal_r_120", "ret_label_120", "vn_label_120"):
        assert np.isfinite(lab[k][0]) and np.isnan(lab[k][1]) and np.isnan(lab[k][2])
    assert list(lab["status_20"]) == ["ok", "ok", "immature"] or list(lab["status_20"])[0] == "ok"
    last = L.signal_labels(o, h, l, c, atr, np.array([n - 1]), np.array([1]), CFG)                # no next bar at all
    assert last["status_20"][0] == "immature" and np.isnan(last["entry_px"][0])


def test_a_window_that_touches_a_discontinuity_is_excluded_with_its_own_status():
    o, h, l, c, atr = path(400, seed=5)
    flag = {H: np.zeros(len(c), bool) for H in CFG.horizons}
    flag[120][100] = True
    lab = L.signal_labels(o, h, l, c, atr, np.array([100, 101]), np.array([1, 1]), CFG, flag)
    assert lab["status_120"][0] == "discontinuity" and np.isnan(lab["mfe_r_120"][0]) and lab["status_120"][1] == "ok" and lab["status_20"][0] == "ok"


def test_the_return_based_and_vol_neutral_labels_are_separate_columns_with_their_own_thresholds():
    n = 300
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) * 1.002; l = np.minimum(o, c) * 0.998
    atr = np.full(n, 1.0)
    h[160] = o[101] * 1.6                                             # +60% within 60 bars of the entry
    lab = L.signal_labels(o, h, l, c, atr, np.array([100]), np.array([1]), CFG)
    assert lab["ret_label_60"][0] == 1.0 and lab["ge10r_60"][0] == 1.0 and np.isfinite(lab["vn_z_60"][0])
    short = L.signal_labels(o, h, l, c, atr, np.array([100]), np.array([-1]), CFG)
    assert short["ret_label_60"][0] in (0.0, 1.0)


# ---------------------------------------------------------------- episodes and splits
def test_episodes_are_independent_kept_signals_at_least_the_gap_apart():
    sym = np.array(["A"] * 6 + ["B"] * 3 + ["A"] * 2)
    dr = np.array([1] * 6 + [1] * 3 + [-1] * 2)
    bi = np.array([10, 15, 100, 129, 130, 300, 10, 200, 250, 10, 20])
    ep, first = E.assign_episodes(sym, dr, bi, gap=120)
    a_long = [(b, f) for s, d, b, f in zip(sym, dr, bi, first) if s == "A" and d == 1]
    assert [b for b, f in a_long if f] == [10, 130, 300]                  # 15, 100, 129 belong to the episode of 10; 130 is 120 after 10; 300 starts a new one
    assert ep[0] == ep[1] == ep[2] == ep[3] and ep[4] != ep[3] and ep[5] != ep[4]
    assert len(set(ep)) == 6                                              # A long 3, B long 2, A short 1
    kept = pd.DataFrame({"s": sym, "d": dr, "b": bi, "f": first}).query("f")
    for (s, d), g in kept.groupby(["s", "d"]):
        assert (np.diff(np.sort(g.b.to_numpy())) >= 120).all()


def test_episode_ids_do_not_depend_on_the_input_order():
    rng = np.random.default_rng(1)
    sym = rng.choice(["A", "B", "C"], 300); dr = rng.choice([1, -1], 300); bi = rng.permutation(5000)[:300]       # bar positions are unique per symbol in real data
    ep1, f1 = E.assign_episodes(sym, dr, bi)
    p = rng.permutation(300)
    ep2, f2 = E.assign_episodes(sym[p], dr[p], bi[p])
    assert (ep1[p] == ep2).all() and (f1[p] == f2).all()


def test_segments_follow_the_pre_registered_dates_with_embargo_between():
    d = pd.to_datetime(["2017-12-31", "2018-01-01", "2021-06-30", "2021-07-01", "2021-12-31", "2022-01-01", "2023-06-30", "2023-07-01", "2023-12-31", "2024-01-01", "2026-04-01"])
    assert list(S.assign_segment(d, CFG)) == ["outside", "train", "train", "embargo", "embargo", "validation", "validation", "embargo", "embargo", "test", "test"]


# ---------------------------------------------------------------- provenance
def test_every_column_has_a_provenance_class_and_labels_are_never_features():
    prices = syn.make_prices(12, seed=11)
    df = F.build_dataset(prices, F.detect_signals(prices), syn.make_sector_snaps(list(prices)), CFG)
    assert P.check_dataset(df.columns) == []
    tab = P.provenance_table(df.columns)
    assert {v["class"] for v in tab.values()} <= set(P.CLASSES)
    for c in df.columns:
        if re.match(r"(status|mfe_r|mfe_pct|terminal_r|ge\dr|ret_label|vn_label|persist)_", c):
            assert tab[c]["class"] == P.LABEL_HINDSIGHT
    assert tab["rs60_pct"]["class"] == P.RECONSTRUCTED and tab["ret_20d"]["class"] == P.REVISED_PRICE_DERIVED and P.classify("industry")[0] == P.UNAVAILABLE
    assert tab["entry_px"]["class"] == P.LABEL_HINDSIGHT


def test_hypothesis_definitions_read_only_decision_time_columns():
    src = open(os.path.join(ROOT, "ml_training", "experiments", "bw_harness", "hypotheses.py"), encoding="utf-8").read()
    used = set(re.findall(r'_f\(d, "([a-z0-9_]+)"\)', src))
    assert used, "no columns found"
    for col in used:
        assert P.classify(col)[0] in P.FEATURE_CLASSES, col


# ---------------------------------------------------------------- the builder
def small_dataset(seed=21, n=14):
    prices = syn.make_prices(n, seed=seed)
    return prices, F.build_dataset(prices, F.detect_signals(prices), syn.make_sector_snaps(list(prices)), CFG)


def test_the_dataset_is_deterministic():
    prices, a = small_dataset()
    _, b = small_dataset()
    pd.testing.assert_frame_equal(a, b)


def test_features_computed_on_history_truncated_at_the_signal_equal_the_stored_ones():
    prices, df = small_dataset(seed=22, n=12)
    feats = ["breakout_dist_atr", "atr_pct", "vol_ratio_50", "ret_20d", "ret_60d", "pct_from_52w_high", "chan_squeeze_pctile", "dist_sma200_pct", "rs60_pct", "rs120_pct", "breadth_50", "index_above_200"]
    pick = df[df.eligible & (df.bar_index > 300)].sample(6, random_state=0)
    for _, r in pick.iterrows():
        cut = {s: p[p["date"] <= r.signal_date] for s, p in prices.items()}
        keys = pd.DataFrame({"symbol": [r.symbol], "date": [r.signal_date], "direction": [r.direction]})
        t = F.build_dataset(cut, keys, None, CFG).iloc[0]
        for f in feats:
            a, b = r[f], t[f]
            assert (pd.isna(a) and pd.isna(b)) or abs(a - b) <= 1e-9 * max(1, abs(a)), (f, a, b)


def test_entry_date_and_price_are_the_next_session_open_and_windows_end_inside_their_segment_gap():
    prices, df = small_dataset(seed=23, n=10)
    r = df[df.status_120 == "ok"].iloc[5]
    px = prices[r.symbol].reset_index(drop=True)
    i = int(np.flatnonzero(px.date == r.signal_date)[0])
    assert r.entry_date == px.date[i + 1] and abs(r.entry_px - px.open[i + 1]) < 1e-9 and r.window_end_date == px.date[i + 120]
    chk = S.purge_check(df, CFG)
    assert chk["ok"] and chk["train_windows_reaching_validation"] == 0


def test_shorts_and_longs_are_both_present_and_episode_firsts_are_a_minority():
    _, df = small_dataset(seed=24, n=10)
    assert set(df.direction.unique()) == {1, -1}
    assert 0 < df.is_episode_first.mean() < 0.5
    assert df.groupby(["symbol", "direction"]).bar_index.apply(lambda s: s.is_monotonic_increasing).all()


def test_sector_provenance_is_observed_only_from_the_first_dated_snapshot():
    _, df = small_dataset(seed=25, n=10)
    obs = df[df.sector_provenance == "OBSERVED_PIT"]
    assert len(obs) and obs.signal_date.min() >= pd.Timestamp("2023-07-11")
    assert (df[df.signal_date < "2023-07-11"].sector_provenance == "RECONSTRUCTED").all()
