"""Strategy experiment runner on hand-built paths (pure; no database): identities, manifest, per-variant independent position accounting with the one-open-position rule, censoring, chronological
splits with purge, actual vs standardised risk, paired comparisons, selection baselines fitted on train only, and point-in-time features / catalysts."""
import math
from datetime import date, timedelta

import numpy as np
import pytest

from research import exit_policies as X
from research import experiment_runner as E
from research import strategy_variants as SV

D0 = date(2020, 1, 6)


def days(n):
    out, d = [], D0
    while len(out) < n:
        if d.isoweekday() < 6:
            out.append(d)
        d += timedelta(days=1)
    return out


def path(n, *, drift=0.0, noise=0.2, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    c = start + np.cumsum(np.full(n, drift) + rng.normal(0, noise, n))
    o = np.r_[start, c[:-1]]
    h = np.maximum(o, c) + 0.3
    l = np.minimum(o, c) - 0.3
    return o, h, l, c


def run(variants, caps, o, h, l, c, signals, symbol="TST"):
    n = len(c)
    atr = np.full(n, 1.0)
    return E.simulate_symbol(variants, caps, symbol, o, h, l, c, days(n), atr, signals)


# ---------------------------------------------------------------------------------------------------------------------------------- identities and manifest
def test_the_variants_are_exactly_the_registered_identities():
    vs = E.registered_variants()
    specs = SV.build_specs()
    assert [(v.key, v.version, v.definition_hash) for v in vs] == [(s.key, s.version, s.definition_hash) for s in specs]
    assert all(v.registered_max_hold == 60 and v.policy_name in X.POLICIES for v in vs) and len(vs) == 5


def test_identity_mismatches_report_a_missing_or_a_different_hash():
    vs = E.registered_variants()
    stored = [(v.key, v.version, v.definition_hash) for v in vs]
    assert E.identity_mismatches(vs, stored) == []
    assert "not in the registry" in E.identity_mismatches(vs, stored[1:])[0]
    tampered = [(k, v, "0" * 64) if i == 2 else (k, v, h) for i, (k, v, h) in enumerate(stored)]
    assert "differs" in E.identity_mismatches(vs, tampered)[0]


def manifest(**over):
    kw = dict(variants=E.registered_variants(), hold_caps=[60, 120], cost_bps=[0.0, 10.0], primary_cost_bps=10.0, train_end=date(2022, 1, 1), test_start=date(2022, 2, 15),
              universe={"symbols": 391}, signal_source={"table": "ml_breakout_dataset_v2"}, code_ref="abc1234")
    kw.update(over)
    return E.build_manifest(**kw)


def test_the_manifest_is_deterministic_and_every_input_changes_its_hash():
    base = manifest()["manifest_hash"]
    assert base == manifest()["manifest_hash"] and len(base) == 64
    for over in (dict(hold_caps=[60]), dict(cost_bps=[0.0]), dict(primary_cost_bps=25.0), dict(train_end=date(2021, 12, 1)), dict(code_ref="zzz"), dict(universe={"symbols": 390}),
                 dict(variants=E.registered_variants()[:4])):
        assert manifest(**over)["manifest_hash"] != base, over


def test_the_manifest_refuses_an_inverted_split_and_non_finite_numbers():
    with pytest.raises(ValueError):
        manifest(train_end=date(2022, 3, 1))
    with pytest.raises(Exception):
        manifest(cost_bps=[float("nan")])


# ---------------------------------------------------------------------------------------------------------------------------------- independent accounting, one open position
def trending(n=400):
    return path(n, drift=0.25, noise=0.15, seed=3)


def test_a_signal_while_the_variants_position_is_open_is_skipped_but_a_signal_after_the_exit_is_taken():
    o, h, l, c = trending()
    v = [x for x in E.registered_variants() if x.policy_name == "C_atr_trail_3x"]
    standalone, pos = run(v, [60], o, h, l, c, [(30, 1), (31, 1), (32, 1), (200, 1)])
    taken = [(p.signal_idx) for p in pos]
    first = pos[0]
    assert taken[0] == 30 and 31 not in taken and 32 not in taken                        # open since bar 31, still open on bars 31 and 32
    assert len(standalone[(v[0].key, 60)]) == 4 and all(t is not None for t in standalone[(v[0].key, 60)])    # standalone counts every signal
    assert 200 in taken and first.trade.exit_bar < 200


def test_a_signal_on_the_exit_bar_is_skipped_and_one_bar_later_is_taken():
    o, h, l, c = trending()
    v = [x for x in E.registered_variants() if x.policy_name == "A_atr_stop_fixed_2R"]
    _, pos0 = run(v, [60], o, h, l, c, [(40, 1)])
    ex = pos0[0].trade.exit_bar
    _, same = run(v, [60], o, h, l, c, [(40, 1), (ex, 1)])
    _, after = run(v, [60], o, h, l, c, [(40, 1), (ex + 1, 1)])
    assert [p.signal_idx for p in same] == [40] and [p.signal_idx for p in after] == [40, ex + 1]


def test_the_two_directions_and_the_two_hold_caps_keep_separate_accounts():
    o, h, l, c = path(300, drift=0.0, noise=0.4, seed=8)
    v = [E.registered_variants()[2]]
    _, pos = run(v, [60, 120], o, h, l, c, [(50, 1), (51, -1), (52, 1)])
    by = {(p.hold_cap, p.direction): [q.signal_idx for q in pos if (q.hold_cap, q.direction) == (p.hold_cap, p.direction)] for p in pos}
    assert (60, -1) in by and (60, 1) in by and 51 in by[(60, -1)]                       # the short opened while the long was open
    assert any(p.hold_cap == 120 for p in pos) and any(p.hold_cap == 60 for p in pos)


def test_a_variants_results_do_not_depend_on_which_other_variants_ran():
    o, h, l, c = path(500, drift=0.05, noise=0.5, seed=11)
    sig = [(i, 1 if i % 2 else -1) for i in range(30, 400, 7)]
    vs = E.registered_variants()
    _, together = run(vs, [60, 250], o, h, l, c, sig)
    for v in vs:
        _, alone = run([v], [60, 250], o, h, l, c, sig)
        mine = [p for p in together if p.variant.key == v.key]
        assert [(p.hold_cap, p.signal_idx, p.trade) for p in alone] == [(p.hold_cap, p.signal_idx, p.trade) for p in mine]


def test_signals_may_arrive_in_any_order():
    o, h, l, c = path(300, drift=0.1, noise=0.4, seed=2)
    v = [E.registered_variants()[0]]
    sig = [(60, 1), (30, 1), (200, -1), (31, 1)]
    _, a = run(v, [60], o, h, l, c, sig)
    _, b = run(v, [60], o, h, l, c, sorted(sig))
    assert [(p.signal_idx, p.trade) for p in a] == [(p.signal_idx, p.trade) for p in b]


def test_a_position_that_cannot_complete_its_cap_is_censored_and_excluded_from_the_metrics():
    o, h, l, c = path(130, drift=0.03, noise=0.1, seed=4)
    v = [x for x in E.registered_variants() if x.policy_name == "B_donchian_trail_10"]
    _, pos = run(v, [60, 250], o, h, l, c, [(100, 1)])
    cap250 = [p for p in pos if p.hold_cap == 250][0]
    assert cap250.censored and cap250.trade.reason == "time" and cap250.trade.duration < 250
    s = E.summarize([cap250], "std", 10.0)
    assert s["n_positions"] == 0 and s["n_censored"] == 1


# ---------------------------------------------------------------------------------------------------------------------------------- splits
def test_chronological_split_with_embargo_and_purge():
    tr_end, te_start = date(2022, 1, 1), date(2022, 2, 15)
    assert E.assign_split(date(2021, 6, 1), date(2021, 8, 1), tr_end, te_start) == "train"
    assert E.assign_split(date(2021, 12, 20), date(2022, 2, 15), tr_end, te_start) == "purged"     # still open when the test period starts
    assert E.assign_split(date(2021, 12, 20), date(2022, 2, 14), tr_end, te_start) == "train"
    assert E.assign_split(date(2022, 1, 1), date(2022, 1, 9), tr_end, te_start) == "embargo"
    assert E.assign_split(date(2022, 2, 14), date(2022, 2, 20), tr_end, te_start) == "embargo"
    assert E.assign_split(date(2022, 2, 15), date(2022, 2, 20), tr_end, te_start) == "test"


# ---------------------------------------------------------------------------------------------------------------------------------- risk bases and metrics
def test_net_r_charges_the_cost_in_the_chosen_risk_unit():
    rows = [(100, 100.4, 99.6, 100)] * 12 + [(100.0, 100.5, 99.5, 100.0), (100.0, 106.5, 99.9, 106.0)]
    a = np.array(rows, float)
    t = X.simulate("C_atr_trail_3x", a[:, 0], a[:, 1], a[:, 2], a[:, 3], 11, 1, 1.0, max_hold=2)
    assert t.initial_risk == pytest.approx(3.0) and t.unit == pytest.approx(2.0)
    assert E.net_r(t, "std", 10.0) == pytest.approx(t.r_gross - 10e-4 * 100.0 / 2.0)
    assert E.net_r(t, "actual", 10.0) == pytest.approx(t.r_actual_gross - 10e-4 * 100.0 / 3.0)
    with pytest.raises(ValueError):
        E.net_r(t, "bogus", 0.0)


def test_summaries_on_the_actual_basis_exclude_invalid_risk_and_report_the_initial_risk_distribution():
    o, h, l, c = path(400, drift=0.1, noise=0.6, seed=5)
    v = E.registered_variants()
    _, pos = run(v, [60], o, h, l, c, [(i, 1) for i in range(30, 300, 9)])
    for variant in v:
        mine = [p for p in pos if p.variant.key == variant.key]
        std = E.summarize(mine, "std", 10.0)
        act = E.summarize(mine, "actual", 10.0)
        assert std["n_positions"] == act["n_positions"] + act["n_invalid_risk"]
        assert "winsorised_mean_r" in act and set(act["initial_risk_atr"]) >= {"median", "share_below_0_5_atr"}
        if variant.policy_name.startswith(("A_", "D_")):
            assert act["initial_risk_atr"]["median"] == pytest.approx(2.0)               # the 1R stop is 2 ATR
        if variant.policy_name.startswith("C_"):
            assert act["initial_risk_atr"]["median"] == pytest.approx(3.0)


def test_paired_differences_use_only_signals_both_sides_could_take_and_that_are_final():
    o, h, l, c = path(300, drift=0.05, noise=0.5, seed=6)
    v = [x for x in E.registered_variants() if x.policy_name in ("A_atr_stop_fixed_3R", "C_atr_trail_3x")]
    sig = [(50, 1), (120, 1), (290, 1)]
    st, _ = run(v, [60], o, h, l, c, sig)
    months = [days(300)[i].strftime("%Y-%m") for i, _ in sig]
    d, m = E.paired_differences(st[(v[0].key, 60)], st[(v[1].key, 60)], 60, 60, "std", 10.0, months)
    assert len(d) == 2 and len(m) == 2                                                  # the signal at bar 290 has no room for a 60-bar cap: censored on both sides
    r0 = [E.net_r(t, "std", 10.0) for t in st[(v[0].key, 60)][:2]]
    r1 = [E.net_r(t, "std", 10.0) for t in st[(v[1].key, 60)][:2]]
    assert d == pytest.approx(np.array(r0) - np.array(r1))


def test_a_longer_hold_cap_lets_a_trend_run_while_the_60_bar_cap_closes_it_at_the_cap():
    o, h, l, c = path(400, drift=0.35, noise=0.05, seed=7)
    v = [x for x in E.registered_variants() if x.policy_name == "C_atr_trail_3x"]
    st, pos = run(v, [60, 120, 250], o, h, l, c, [(20, 1)])
    t60, t120, t250 = (st[(v[0].key, c_)][0] for c_ in (60, 120, 250))
    assert t60.reason == "time" and t60.duration == 60 and t120.duration == 120 and t250.duration == 250
    assert t250.r_gross > t120.r_gross > t60.r_gross
    assert [p.registered_cap for p in pos if p.hold_cap == 60] == [True] and not any(p.registered_cap for p in pos if p.hold_cap != 60)


# ---------------------------------------------------------------------------------------------------------------------------------- selection baselines
def test_selection_rules_are_fitted_on_train_only_and_judged_on_test():
    rng = np.random.default_rng(0)
    n = 600
    split = np.array(["train"] * 300 + ["test"] * 300)
    groups = np.array((["a", "b"] * 150) * 2)
    R = rng.normal(0, 0.1, (n, 3))
    R[:300, 0] += 0.5                      # variant 0 is the best in TRAIN ...
    R[300:, 1] += 0.5                      # ... but variant 1 is the best in TEST: a rule that peeked at test would pick 1
    out = E.selection_baselines(R, groups, split, ["v0", "v1", "v2"])
    assert out["best_single_variant_on_train"] == "v0"
    assert out["rules_test_mean"]["best_single_variant"] == pytest.approx(R[300:, 0].mean(), abs=1e-4)
    assert out["rules_test_mean"]["per_signal_oracle_UPPER_BOUND"] >= max(out["rules_test_mean"].values())
    assert out["fixed_variant_test_mean"]["v1"] > out["fixed_variant_test_mean"]["v0"]


def test_a_group_with_too_few_train_rows_falls_back_to_the_global_best():
    rng = np.random.default_rng(1)
    n = 400
    split = np.array(["train"] * 200 + ["test"] * 200)
    groups = np.array(["big"] * 190 + ["tiny"] * 10 + ["big"] * 200)
    R = rng.normal(0, 0.1, (n, 2))
    R[:200, 0] += 0.3
    R[190:200, 1] += 5.0                   # in the tiny group variant 1 looks great on 10 rows only
    out = E.selection_baselines(R, groups, split, ["a", "b"], min_group_train=50)
    assert "tiny" not in out["group_best_pick_on_train"]


def test_unavailable_outcomes_are_dropped_not_filled():
    R = np.array([[0.1, 0.2], [float("nan"), 0.3], [0.0, 0.1], [0.2, 0.0]])
    out = E.selection_baselines(R, ["g"] * 4, ["train", "train", "test", "test"], ["a", "b"], min_group_train=1)
    assert out["n_train"] == 1 and out["n_test"] == 2


# ---------------------------------------------------------------------------------------------------------------------------------- point in time
def test_signal_features_use_only_bars_up_to_the_signal_bar():
    o, h, l, c = path(120, drift=0.1, noise=0.5, seed=9)
    v = np.abs(np.random.default_rng(9).normal(1e6, 1e5, 120))
    atr = np.full(120, 1.2)
    f1 = E.signal_features(o, h, l, c, v, atr, 60)
    c2, v2 = c.copy(), v.copy()
    c2[61:] *= 3.0                      # rewrite the FUTURE
    v2[61:] *= 50.0
    assert E.signal_features(o, h, l, c2, v2, atr, 60) == f1
    assert f1["ret20_to_signal"] == pytest.approx(c[60] / c[40] - 1) and f1["relvol_signal_bar"] == pytest.approx(v[60] / v[40:60].mean())
    assert math.isnan(E.signal_features(o, h, l, c, v, atr, 10)["relvol_signal_bar"])        # fewer than 20 prior bars: unavailable, never filled


def ev(react, nature, accepted, acc="0000000001-25-000001"):
    return {"reaction_date": react, "nature": nature, "decision_accepted_at": accepted, "release_accession": acc}


def test_the_catalyst_is_the_latest_published_and_reacted_event_before_the_signal_session():
    events = [ev(date(2024, 5, 1), "none", "2024-04-30T20:10:00+00:00"), ev(date(2024, 5, 20), "recurring_operating", "2024-05-20T11:00:00+00:00"), ev(date(2024, 5, 28), "one_time_suspected", "2024-05-27T20:05:00+00:00")]
    got = E.catalyst_as_of(events, date(2024, 5, 30))
    assert got["nature"] == "one_time_suspected" and got["days_since"] == 2
    # a signal ON the reaction date or before it cannot see that event (the reaction session had not completed)
    assert E.catalyst_as_of(events, date(2024, 5, 28))["nature"] == "recurring_operating"
    assert E.catalyst_as_of(events, date(2024, 5, 10))["nature"] == "none"                 # 9 days after the 2024-05-01 reaction; the 05-20 event is still in the future
    assert E.catalyst_as_of(events, date(2024, 5, 20)) is None                            # the 05-01 event is 19 days old: outside the 15-day window
    assert E.catalyst_as_of(events, date(2024, 4, 30)) is None


def test_the_catalyst_window_and_an_inconsistent_timestamp():
    events = [ev(date(2024, 5, 1), "recurring_operating", "2024-04-30T20:10:00+00:00")]
    assert E.catalyst_as_of(events, date(2024, 5, 16))["days_since"] == 15
    assert E.catalyst_as_of(events, date(2024, 5, 17)) is None                              # more than 15 days
    bad = [ev(date(2024, 5, 1), "recurring_operating", "2024-05-03T20:10:00+00:00")]         # accepted AFTER its own reaction date: never used
    assert E.catalyst_as_of(bad, date(2024, 5, 10)) is None
