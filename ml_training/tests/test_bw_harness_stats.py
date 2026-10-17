"""Big Winner harness, statistics: matched controls, within-date estimate, bootstrap, permutation, Holm, the hypotheses and their verdict rules, survivorship sensitivity. Everything is checked on synthetic frames
with a KNOWN planted effect (or none), so the machinery is shown to find an effect when there is one, to stay quiet when there is none, and not to mistake a market-time effect for a stock-level one."""
import os
import re
import sys
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))

from bw_harness import hypotheses as H  # noqa: E402
from bw_harness import inference as I  # noqa: E402
from bw_harness import matching as M  # noqa: E402
from bw_harness import survivorship as SV  # noqa: E402
from bw_harness import synthetic as syn  # noqa: E402
from bw_harness.config import DEFAULT  # noqa: E402

CFG = replace(DEFAULT, n_boot=300, n_perm=300)
PRIM = "BW5@120 (primary, R-based)"


def prepared(n=24000, seed=7, **kw):
    df = syn.planted_dataset(n=n, seed=seed, **kw)
    return H.prepare(df, CFG)


def test_the_configuration_matches_the_pre_registered_values_and_is_hashed():
    c = DEFAULT
    assert (c.unit_atr, c.horizons, c.tiers_r, c.primary_horizon, c.primary_tier_r, c.secondary_tier_r) == (2.0, (20, 60, 120), (3.0, 5.0, 10.0), 120, 5.0, 10.0)
    assert (c.rs_top_pct, c.rs_control_below_pct, c.near_high_pct, c.far_high_pct, c.squeeze_pctile_max, c.sector_top_n, c.group_breakouts_min, c.group_lookback_sessions) == (0.80, 0.60, -5.0, -20.0, 20.0, 3, 3, 10)
    assert (c.controls_per_treated, c.match_window_sessions, c.min_rr, c.min_years_same_sign, c.min_sets_per_year, c.episode_gap_bars) == (3, 5, 1.25, 6, 500, 120)
    assert (c.train_end, c.validation_start, c.validation_end, c.test_start) == ("2021-06-30", "2022-01-01", "2023-06-30", "2024-01-01")
    assert replace(c, min_rr=1.3).hash() != c.hash() and c.hash() == DEFAULT.hash()
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.min_rr = 1.0


def test_the_documentation_states_the_same_definitions():
    doc = open(os.path.join(ROOT, "docs", "research", "BIG_WINNER_DISCOVERY.md"), encoding="utf-8").read()
    for phrase in ("top quintile", "52-week high", "squeeze percentile <= 20", "top 3 of 11", "at least 3 other same-sector breakouts", "within 5%", "more than 20% below", "1.25", "6 of the 8"):
        assert phrase in doc, phrase


# ---------------------------------------------------------------- matching
def test_matched_controls_obey_every_matching_rule():
    df, thr, _ = prepared()
    pool = H._pool(df, 1, ("test",), CFG, False)
    t = H.h1_t(pool, thr["long"], CFG)
    c = H.h1_c(pool, thr["long"], CFG) & ~t
    m = M.match(pool, t, c, CFG, np.random.default_rng(0))
    assert m.n_sets + m.n_unmatched == int(t.sum()) and m.n_sets > 300
    cp = m.control_pos
    used = cp[cp >= 0]
    assert len(used) == len(set(used)), "a control was reused"
    assert c[used].all() and not t[used].any()
    for k, ti in enumerate(m.treated_pos):
        for ci in cp[k][cp[k] >= 0]:
            r, q = pool.iloc[ti], pool.iloc[ci]
            assert abs(r.date_index - q.date_index) <= CFG.match_window_sessions and r.symbol != q.symbol and r.direction == q.direction
            assert (r.atr_decile, r.dv_tercile, r.ret60_tercile) == (q.atr_decile, q.dv_tercile, q.ret60_tercile)


def test_matching_removes_a_covariate_imbalance_that_the_raw_comparison_has():
    df = syn.planted_dataset(n=24000, seed=8)
    treated_like = df["rs60_pct"] >= 0.85
    df["atr_pct"] = df["atr_pct"] * np.where(treated_like, 1.6, 1.0)             # treated names are far more volatile
    df, thr, _ = H.prepare(df, CFG)
    pool = H._pool(df, 1, ("test",), CFG, False)
    t = H.h1_t(pool, thr["long"], CFG)
    c = H.h1_c(pool, thr["long"], CFG)
    raw = (pool.atr_pct[t].mean() - pool.atr_pct[c].mean()) / np.sqrt((pool.atr_pct[t].var() + pool.atr_pct[c].var()) / 2)
    m = M.match(pool, t, c & ~t, CFG, np.random.default_rng(1))
    smd = M.balance(pool, m)["atr_pct"]["smd"]
    assert abs(raw) > 0.8 and abs(smd) < 0.15


def test_matching_is_deterministic_for_a_seed_and_never_looks_at_an_outcome():
    df, thr, _ = prepared(n=12000)
    pool = H._pool(df, 1, ("validation",), CFG, False)
    t, c = H.h3_t(pool, thr["long"], CFG), H.h3_c(pool, thr["long"], CFG)
    a = M.match(pool, t, c, CFG, np.random.default_rng(5))
    b = M.match(pool, t, c, CFG, np.random.default_rng(5))
    assert (a.treated_pos == b.treated_pos).all() and (a.control_pos == b.control_pos).all()
    flipped = pool.copy(); flipped["ge5r_120"] = 1.0 - flipped["ge5r_120"]       # changing every outcome cannot change who is matched with whom
    c2 = M.match(flipped, t, c, CFG, np.random.default_rng(5))
    assert (a.treated_pos == c2.treated_pos).all() and (a.control_pos == c2.control_pos).all()


def test_a_reconstructed_sector_row_never_matches_an_observed_sector_row():
    df, thr, _ = prepared(n=12000)
    df.loc[df.index % 2 == 0, "sector_provenance"] = "RECONSTRUCTED"
    df.loc[df.index % 2 == 0, "sector"] = "Other"
    pool = H._pool(df, 1, ("test",), CFG, False)
    t, c = H.h4_t(pool, thr["long"], CFG), H.h4_c(pool, thr["long"], CFG) & ~H.h4_t(pool, thr["long"], CFG)
    m = M.match(pool, t, c, CFG, np.random.default_rng(2))
    obs = (pool.sector_provenance == "OBSERVED_PIT").to_numpy()
    for k, ti in enumerate(m.treated_pos):
        assert all(obs[ci] == obs[ti] for ci in m.control_pos[k] if ci >= 0)


# ---------------------------------------------------------------- inference
def test_under_no_effect_the_test_rejects_about_as_often_as_the_chosen_level():
    """Calibration on 80 null datasets. Measured on 150: rejection at 0.05 = 6.0% (nominal 5%), at 0.10 = 13.3%, month-block percentile interval coverage = 90.7% (nominal 95%: with about 24 months the percentile bootstrap is
    a little narrow). The harness therefore never relies on the interval alone: support also needs the permutation test (Holm-corrected) and the within-date estimate."""
    ps, cover = [], 0
    cfg = replace(CFG, n_boot=150, n_perm=120)
    for seed in range(1000, 1080):
        df, thr, _ = H.prepare(syn.planted_dataset(n=14000, seed=seed, effect=1.0), cfg)
        pool = H._pool(df, 1, ("test",), cfg, False)
        r = H.evaluate(pool, H.LONG_SPECS[0], thr["long"], cfg, seed=seed, realised=False)
        lab = r["labels"][PRIM]
        ps.append(lab["permutation"]["p_two_sided"])
        cover += int(lab["ci95"][0] <= 1.0 <= lab["ci95"][1])
    ps = np.array(ps)
    assert (ps < 0.05).mean() <= 0.12 and cover / len(ps) >= 0.84, ((ps < 0.05).mean(), cover / len(ps))
    assert 0.35 < ps.mean() < 0.65                                              # p-values spread over (0, 1), not piled near 0


def test_a_planted_effect_is_found_with_an_interval_above_one_and_a_small_p_value():
    df, thr, _ = prepared(n=24000, seed=7, effect=2.5)
    pool = H._pool(df, 1, ("test",), CFG, False)
    r = H.evaluate(pool, H.LONG_SPECS[0], thr["long"], CFG, seed=1)
    lab = r["labels"][PRIM]
    assert lab["rr"] > 1.5 and lab["ci95"][0] > 1.3 and lab["permutation"]["p_two_sided"] < 0.01
    assert r["within_date_mh"]["ci95"][0] > 1.3


def test_a_pure_market_time_effect_is_not_mistaken_for_a_stock_level_one():
    df, thr, _ = prepared(n=24000, seed=7, effect=1.0, date_confound=1.5)
    pool = H._pool(df, 1, ("test",), CFG, False)
    t, c = H.h1_t(pool, thr["long"], CFG), H.h1_c(pool, thr["long"], CFG)
    y = pool.ge5r_120.to_numpy()
    assert y[t].mean() / y[c].mean() > 1.5                                      # the raw comparison is badly fooled
    r = H.evaluate(pool, H.LONG_SPECS[0], thr["long"], CFG, seed=1)
    mh = r["within_date_mh"]
    assert mh["ci95"][0] <= 1.0 <= mh["ci95"][1] and 0.85 < mh["rr"] < 1.1


def test_permutation_p_is_one_when_the_outcome_carries_no_information_and_is_reproducible():
    df, thr, _ = prepared(n=12000)
    pool = H._pool(df, 1, ("test",), CFG, False)
    t, c = H.h3_t(pool, thr["long"], CFG), H.h3_c(pool, thr["long"], CFG)
    m = M.match(pool, t, c, CFG, np.random.default_rng(3))
    block = pd.factorize(pool.month)[0]
    const = np.ones(len(pool))
    assert I.permutation_p(const, block, m, 1.0, 100, np.random.default_rng(1))["p_two_sided"] == 1.0
    y = pool.ge5r_120.to_numpy()
    yt, ym, _ = I.set_arrays(y, m)
    rr = I.rate_ratio(yt, ym)
    a = I.permutation_p(y, block, m, rr, 100, np.random.default_rng(9))
    b = I.permutation_p(y, block, m, rr, 100, np.random.default_rng(9))
    assert a == b


def test_holm_matches_the_textbook_step_down():
    h = I.holm({"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04}, 0.05)
    assert [h[k]["p_holm"] for k in "abcd"] == [0.04, 0.06, 0.06, 0.06] and [h[k]["reject"] for k in "abcd"] == [True, False, False, False]
    assert I.holm({"x": None, "y": 0.001})["x"]["p_holm"] == 1.0


def test_the_month_block_bootstrap_contains_the_estimate_and_is_reproducible():
    rng = np.random.default_rng(0)
    months = np.repeat(np.array([f"2022-{m:02d}" for m in range(1, 13)]), 80)
    yt = (rng.random(len(months)) < 0.4).astype(float)
    ym = (rng.random(len(months)) < 0.3).astype(float)
    a = I.month_block_bootstrap(months, yt, ym, 400, np.random.default_rng(4))
    b = I.month_block_bootstrap(months, yt, ym, 400, np.random.default_rng(4))
    rr = I.rate_ratio(yt, ym)
    assert a == b and a["ci95"][0] < rr < a["ci95"][1] and a["n_months"] == 12


def test_year_consistency_counts_only_years_with_enough_sets():
    years = np.repeat([2018, 2019], [600, 100])
    yt = np.ones(700); ym = np.full(700, 0.5)
    yc = I.year_consistency(years, yt, ym, (2018, 2019, 2020), 500)
    assert yc["2018"]["counts"] and yc["2018"]["rr"] == 2.0 and not yc["2019"]["counts"] and yc["2020"]["sets"] == 0


# ---------------------------------------------------------------- hypotheses end to end
def both_sides(effect=1.0, seed=11, n=24000, **kw):
    a = syn.planted_dataset(n=n, seed=seed, effect=effect, **kw)
    b = syn.planted_dataset(n=n // 2, seed=seed + 1, effect=1.0)
    b["direction"] = -1
    b["symbol"] = "Y" + b["symbol"]
    return pd.concat([a, b], ignore_index=True)


def test_a_planted_leadership_effect_is_supported_and_the_negative_control_stays_null():
    cfg = replace(CFG, n_boot=200, n_perm=400)
    df, thr, _ = H.prepare(both_sides(effect=2.2, n=70000), cfg)           # large enough that every year has at least 500 matched sets (criterion b)
    res = H.run_all(df, thr, cfg)
    v = res["verdicts"]
    assert v["H1"]["verdict"] == "SUPPORTED_HISTORICAL", v["H1"]["criteria"]
    assert all(v[h]["verdict"] == "NOT_SUPPORTED" for h in ("H2", "H3", "H4", "H5"))
    for seg in ("validation", "test"):
        lab = res["long"]["H0"][seg]["labels"][PRIM]
        assert lab["ci95"][0] <= 1.0 <= lab["ci95"][1]
    assert res["short"]["H1s"]["test"]["status"] in ("ok", "no_matches", "no_treated_or_no_controls", "empty_pool")


def test_no_effect_means_no_hypothesis_is_supported():
    df, thr, _ = H.prepare(both_sides(effect=1.0, seed=31), CFG)
    v = H.run_all(df, thr, CFG)["verdicts"]
    assert all(x["verdict"] == "NOT_SUPPORTED" for x in v.values()), {k: x["verdict"] for k, x in v.items()}


def test_h5_cannot_be_confirmed_where_no_observed_sector_exists():
    raw = both_sides(effect=1.0, seed=41)
    raw["sector_provenance"] = np.where(raw["signal_date"] >= "2023-07-11", "OBSERVED_PIT", "RECONSTRUCTED")
    df, thr, _ = H.prepare(raw, CFG)
    res = H.run_all(df, thr, CFG)
    assert res["verdicts"]["H5"]["verdict"] == "NOT_EVALUABLE_PROVENANCE"
    assert "validation_reconstructed_replication" in res["long"]["H5"]               # reported, but never used for the verdict


def verdict_inputs(rr=1.5, lo=1.2, p=0.001, ret_lo=1.1, mh_lo=1.1, h0_ok=True, years_ok=True):
    def seg(extra=None):
        lab = {"rr": rr, "ci95": [lo, rr + 0.3], "permutation": {"p_two_sided": p}}
        d = {"status": "ok", "labels": {PRIM: lab, "return-based": {"rr": 1.4, "ci95": [ret_lo, 1.7]}}, "within_date_mh": {"rr": 1.3, "ci95": [mh_lo, 1.5]}, "holm": {"reject": p < 0.01, "p_holm": p}}
        return d
    yrs = {str(y): {"sets": 800, "rr": 1.3 if years_ok else 0.9, "counts": True} for y in range(2018, 2026)}
    res = {"validation": seg(), "test": seg(), "all_years": {"year_consistency_in_pool": yrs}}
    h0 = {s: {"labels": {PRIM: {"ci95": [0.9, 1.1] if h0_ok else [1.2, 1.5]}}} for s in ("validation", "test")}
    return res, h0


@pytest.mark.parametrize("kw,expected", [
    ({}, "SUPPORTED_HISTORICAL"),
    ({"ret_lo": 0.9}, "SUPPORTED_HISTORICAL_R_LABEL_ONLY"),
    ({"lo": 0.95}, "NOT_SUPPORTED"),                      # interval includes 1
    ({"rr": 1.2, "lo": 1.1}, "NOT_SUPPORTED"),            # ratio under 1.25
    ({"p": 0.2}, "NOT_SUPPORTED"),                        # does not survive Holm
    ({"mh_lo": 0.97}, "NOT_SUPPORTED"),                   # does not survive the within-date test
    ({"h0_ok": False}, "NOT_SUPPORTED"),                  # the negative control is not null: the machinery is not trusted
    ({"years_ok": False}, "NOT_SUPPORTED"),               # the sign does not hold in enough years
])
def test_the_verdict_rules_apply_section_5_1_mechanically(kw, expected):
    res, h0 = verdict_inputs(**kw)
    assert H.verdict(res, h0, CFG, H.LONG_SPECS[0])["verdict"] == expected


# ---------------------------------------------------------------- survivorship sensitivity
def test_equal_missing_mass_in_both_groups_leaves_the_ratio_unchanged_and_uneven_mass_moves_it():
    assert SV.adjusted_rr(0.40, 0.25, 0.1, 0.1, 0.0) == pytest.approx(0.40 / 0.25)
    assert SV.adjusted_rr(0.40, 0.25, 0.2, 0.0, 0.0) == pytest.approx(0.32 / 0.25)            # failed treated-type names missing: the ratio shrinks
    assert SV.adjusted_rr(0.40, 0.25, 0.0, 0.2, 0.0) > 1.6                                     # failed control-type names missing: the ratio grows


def test_tipping_points_reproduce_the_target_ratio():
    for target in (1.0, 1.25):
        phi = SV.tipping_phi_treated(0.40, 0.25, target, 0.0)
        assert SV.adjusted_rr(0.40, 0.25, phi, 0.0, 0.0) == pytest.approx(target)
    assert SV.tipping_phi_treated(0.40, 0.25, 1.25, 0.05) == pytest.approx((0.40 - 1.25 * 0.25) / (0.40 - 0.05))
    assert SV.tipping_phi_treated(0.20, 0.25, 1.0, 0.0) == 0.0                                   # already at or below the target
    assert SV.tipping_phi_treated(0.40, 0.25, 1.25, 0.9) is None or 0 <= SV.tipping_phi_treated(0.40, 0.25, 1.25, 0.9) <= 1


def test_the_sensitivity_report_has_the_full_grid_for_both_directions():
    r = SV.sensitivity(0.35, 0.25, CFG, "long")
    assert len(r["grid"]) == len(CFG.surv_missing_winner_rate_long) * len(CFG.surv_phi_treated) * len(CFG.surv_delta) and r["observed_rr"] == 1.4
    assert set(r["tipping"]) == {f"pm={p}" for p in CFG.surv_missing_winner_rate_long}
    s = SV.sensitivity(0.15, 0.10, CFG, "short")
    assert set(s["tipping"]) == {f"pm={p}" for p in CFG.surv_missing_winner_rate_short}
