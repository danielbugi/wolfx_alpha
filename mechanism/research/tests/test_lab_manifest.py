"""Dataset manifest + experiment registration (pure): reproducibility identity and every leakage control."""
from datetime import date, datetime, timedelta, timezone

import pytest

from lab_samples import CAL, H, MAT, NOW, SHA, TE, TR, VA, exp, manifest, spec
from research.lab import manifest as M


def bad(match, **o):
    with pytest.raises(M.LabError) as e:
        M.build_manifest(spec(**o), now=NOW)
    assert any(match in p for p in e.value.problems), e.value.problems


def test_a_valid_manifest_has_a_deterministic_identity_that_covers_every_field():
    a, b = manifest(), manifest()
    assert a.manifest_hash == b.manifest_hash and len(a.manifest_hash) == 64
    d = a.document
    for k in ("code_sha", "dataset_version", "label_version", "label_methodology_version", "label_horizons", "feature_versions", "universe_id",
              "universe_hash", "knowledge_cutoff_at", "windows", "embargo_sessions", "purge_sessions", "calendar_source", "calendar_hash",
              "input_hashes", "label_maturity_session"):
        assert k in d and d[k] not in (None, "", {}, [])
    assert "created_at" not in d                                   # the database stamps it; it is not part of the identity


@pytest.mark.parametrize("o", [dict(dataset_version="v2"), dict(code_sha="c" * 40), dict(feature_versions={"mi_v2": "2"}),
                               dict(label_horizons=(5, 20)), dict(input_hashes={"labels": "d" * 64}), dict(config={"x": 1}),
                               dict(universe_hash=M.universe_hash(["A", "B", "C"], "rule")), dict(calendar_source="other")])
def test_changing_any_input_changes_the_identity(o):
    assert manifest(**o).manifest_hash != manifest().manifest_hash


def _wide(**o):
    """Same layout with 70 sessions between windows, so embargo/purge can be raised without touching the windows."""
    va, te = (CAL[270], CAL[369]), (CAL[440], CAL[539])
    base = dict(windows=M.Windows(TR, va, te), label_maturity_session=CAL[602],
                knowledge_cutoff_at=datetime(CAL[602].year, CAL[602].month, CAL[602].day, 12, tzinfo=timezone.utc))
    base.update(o)
    return manifest(**base)


def test_embargo_and_purge_are_part_of_the_identity():
    h = {_wide().manifest_hash, _wide(embargo_sessions=61).manifest_hash, _wide(purge_sessions=2, embargo_sessions=62).manifest_hash}
    assert len(h) == 3


def test_the_universe_hash_depends_on_members_and_rule_but_not_order_or_duplicates():
    h = M.universe_hash(["B", "A", "A"], "r")
    assert h == M.universe_hash(["A", "B"], "r") and h != M.universe_hash(["A", "B"], "r2") and h != M.universe_hash(["A"], "r")
    with pytest.raises(M.LabError):
        M.universe_hash([], "r")


def test_a_dirty_tree_or_a_non_sha_cannot_produce_a_manifest():
    bad("not clean", code_tree_clean=False)
    for s in ("abc", "A" * 40, "a" * 39, ""):
        bad("40-hex", code_sha=s)


def test_the_embargo_must_cover_the_sixty_session_label_window_plus_purge():
    bad("embargo_sessions 59", embargo_sessions=59)
    bad("embargo_sessions 61 < required 62", purge_sessions=2, embargo_sessions=61)
    assert manifest(embargo_sessions=60).document["embargo_sessions"] == 60


def test_the_embargo_is_counted_in_sessions_of_the_calendar_not_in_calendar_days():
    short_va = (CAL[240], CAL[339])                                      # only 39 sessions after the train end
    bad("sessions between train end and validation start", windows=M.Windows(TR, short_va, TE))
    bad("sessions between validation end and test start", windows=M.Windows(TR, VA, (CAL[400], CAL[499])))


def test_windows_must_be_ordered_and_must_sit_on_sessions_of_the_calendar():
    bad("start <= end", windows=M.Windows((CAL[10], CAL[5]), VA, TE))
    bad("not a session", windows=M.Windows((date(2023, 1, 7), TR[1]), VA, TE))                 # a Saturday
    bad("sessions between", windows=M.Windows(TR, (CAL[150], CAL[300]), TE))                   # overlaps train
    bad("sessions between", windows=M.Windows(VA, TR, TE))                                     # reversed


def test_label_maturity_must_follow_the_test_window_by_horizon_plus_void_grace():
    late = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bad("labels need 60 + 3", label_maturity_session=CAL[519 + 62], knowledge_cutoff_at=late)
    assert manifest(label_maturity_session=CAL[519 + 63]).document["label_maturity_session"] == CAL[582].isoformat()
    short = manifest(label_horizons=(5,), label_maturity_session=CAL[519 + 8], knowledge_cutoff_at=late)
    assert short.document["label_horizons"] == [5]
    bad("embargo_sessions", label_horizons=(5,), embargo_sessions=10)       # the embargo stays the 60-session one even for short horizons


def test_the_cutoff_cannot_precede_maturity_or_lie_in_the_future_or_be_naive():
    bad("precedes", knowledge_cutoff_at=datetime(MAT.year, MAT.month, MAT.day, tzinfo=timezone.utc) - timedelta(seconds=1))
    bad("future", knowledge_cutoff_at=NOW + timedelta(seconds=1))
    bad("timezone-aware", knowledge_cutoff_at=datetime(2025, 6, 1))
    with pytest.raises(M.LabError):
        M.build_manifest(spec(), now=datetime(2026, 1, 1))


def test_labels_horizons_features_inputs_and_universe_are_validated():
    bad("known label methodology", label_methodology_version="fwd_v1.m2")
    bad("known label methodology", label_version="fwd_v2")
    for hz in ((), (7,), (5, 5), (5, 120)):
        bad("label_horizons", label_horizons=hz)
    bad("feature_versions", feature_versions={})
    bad("feature_versions", feature_versions={"x": ""})
    bad("input_hashes", input_hashes={})
    bad("input_hashes", input_hashes={"x": "short"})
    bad("universe_hash", universe_hash="zz")


def test_every_violation_is_reported_together():
    with pytest.raises(M.LabError) as e:
        M.build_manifest(spec(code_tree_clean=False, code_sha="x", embargo_sessions=1, input_hashes={}), now=NOW)
    assert len(e.value.problems) >= 3


def test_nan_and_naive_values_never_reach_the_identity():
    for cfg in ({"x": float("nan")}, {"when": datetime(2025, 1, 1)}, {"o": object()}):
        with pytest.raises(M.LabError):
            manifest(config=cfg)


def test_the_calendar_must_be_explicit_and_strictly_increasing():
    with pytest.raises(M.LabError):
        M.build_manifest(spec(calendar=()), now=NOW)
    with pytest.raises(M.LabError):
        M.build_manifest(spec(calendar=(CAL[1], CAL[0]) + CAL[2:]), now=NOW)
    bad("not a session", calendar=tuple(d for d in CAL if d != TR[0]))


# ---------------------------------------------------------------- row assignment: the leakage guard for dataset construction
def test_a_row_is_usable_only_if_its_whole_label_window_stays_inside_its_window():
    m = manifest()
    assert M.assign_split(m, CAL[0], 20) == "train"
    assert M.assign_split(m, CAL[199 - 20], 20) == "train"                  # label ends exactly on the last train session
    assert M.assign_split(m, CAL[199 - 19], 20) == "purged"                 # label ends in the embargo
    assert M.assign_split(m, CAL[199], 5) == "purged"
    assert M.assign_split(m, CAL[199], 60) == "purged"
    assert M.assign_split(m, CAL[230], 5) == "embargo"
    assert M.assign_split(m, CAL[260], 5) == "validation" and M.assign_split(m, CAL[359 - 5], 5) == "validation"
    assert M.assign_split(m, CAL[359 - 4], 5) == "purged"
    assert M.assign_split(m, CAL[420], 60) == "test" and M.assign_split(m, CAL[519 - 59], 60) == "purged"
    assert M.assign_split(m, CAL[600], 5) == "outside"
    assert M.assign_split(m, CAL[799], 5) == "purged"                       # the label would read past the known calendar


def test_no_train_label_can_see_a_session_inside_the_validation_or_test_window():
    m = manifest(label_horizons=(1, 3, 5, 10, 20, 60))
    for h in m.document["label_horizons"]:
        for i in range(0, 200):
            if M.assign_split(m, CAL[i], h) == "train":
                assert CAL[i + h] <= TR[1] < VA[0]
        for i in range(260, 360):
            if M.assign_split(m, CAL[i], h) == "validation":
                assert CAL[i + h] <= VA[1] < TE[0]
    assert list(CAL).index(VA[0]) - list(CAL).index(TR[1]) - 1 >= 60


def test_assign_split_refuses_unknown_horizons_and_non_sessions():
    m = manifest(label_horizons=(5,))
    with pytest.raises(M.LabError):
        M.assign_split(m, CAL[0], 20)
    with pytest.raises(M.LabError):
        M.assign_split(m, date(2023, 1, 7), 5)


# ---------------------------------------------------------------- experiment registration
def test_a_registration_is_deterministic_bound_to_its_manifest_and_hashes_its_plan():
    a, b = M.build_registration(exp()), M.build_registration(exp())
    assert a.registration_hash == b.registration_hash and a.document["manifest_hash"] == manifest().manifest_hash
    assert a.document["label_version"] == "fwd_v1"
    for o in (dict(seed=8), dict(search_budget=4), dict(hypothesis="other"), dict(model_spec={"family": "x"}),
              dict(evaluation_plan={"metrics": ["auc"], "primary_metric": "auc", "decision_rule": "r"})):
        assert M.build_registration(exp(**o)).registration_hash != a.registration_hash
    assert M.build_registration(exp(manifest(dataset_version="v2"))).registration_hash != a.registration_hash


@pytest.mark.parametrize("o,match", [
    (dict(code_tree_clean=False), "not clean"), (dict(code_sha="x"), "40-hex"), (dict(feature_versions={"other": "1"}), "subset"),
    (dict(feature_versions={"mi_v2": "9"}), "subset"), (dict(feature_versions={}), "subset"), (dict(model_spec={}), "model_spec"),
    (dict(search_budget=0), "search_budget"), (dict(search_budget=True), "search_budget"), (dict(seed=1.5), "seed"),
    (dict(evaluation_plan={"metrics": [], "primary_metric": "auc", "decision_rule": "r"}), "metrics"),
    (dict(evaluation_plan={"metrics": ["auc"], "primary_metric": "f1", "decision_rule": "r"}), "primary_metric"),
    (dict(evaluation_plan={"metrics": ["auc"], "primary_metric": "auc"}), "decision_rule"),
    (dict(hypothesis="  "), "hypothesis"), (dict(experiment_name=""), "experiment_name")])
def test_an_incomplete_or_dishonest_registration_is_refused(o, match):
    with pytest.raises(M.LabError) as e:
        M.build_registration(exp(**o))
    assert any(match in p for p in e.value.problems), e.value.problems


def _res(reg, kind="validation", **o):
    base = dict(registration_hash=reg.registration_hash, result_kind=kind,
                metrics={"auc": 0.51, "brier": 0.25} if kind in ("validation", "test") else {}, n_configs_tried=2,
                artifact_hashes={"model": H}, code_sha=SHA)
    base.update(o)
    return M.ResultSpec(**base)


def test_result_rules_budget_test_once_validation_first_no_closed_experiments_and_no_stand_in_metrics():
    reg = M.build_registration(exp())
    M.validate_result(_res(reg), reg, [])
    cases = [
        (_res(reg, "test"), [], "prior validation"), (_res(reg, "test"), ["validation", "test"], "once"),
        (_res(reg, n_configs_tried=4), [], "search_budget"), (_res(reg, metrics={"auc": 0.5}), [], "every registered metric"),
        (_res(reg, metrics={"auc": float("nan"), "brier": 0.2}), [], "finite"), (_res(reg, metrics={"auc": True, "brier": 1}), [], "finite"),
        (_res(reg, "failed", metrics={"auc": 0.5}), [], "metrics"), (_res(reg, metrics={}), [], "metrics"),
        (_res(reg), ["abandoned"], "closed"), (_res(reg, artifact_hashes={"m": "x"}), [], "sha256"),
        (_res(reg, registration_hash="e" * 64), [], "different registration")]
    for result, prior, match in cases:
        with pytest.raises(M.LabError) as e:
            M.validate_result(result, reg, prior)
        assert any(match in p for p in e.value.problems), (match, e.value.problems)
    M.validate_result(_res(reg, "failed", n_configs_tried=3), reg, ["validation"])
    M.validate_result(_res(reg, "test"), reg, ["validation"])


def test_the_lab_package_trains_nothing_and_reads_no_clock_or_driver():
    import inspect
    src = inspect.getsource(M)
    for w in ("psycopg2", "datetime.now", "date.today", "utcnow", "time.time(", "sklearn", "xgboost", "torch", "os.environ", "open("):
        assert w not in src, w


def test_the_mirrored_label_constants_equal_the_label_engine_they_describe():
    from research.labels import fwd_v1
    assert (M.HORIZONS, M.LABEL_VERSION, M.METHODOLOGY_VERSION, M.VOID_GRACE_SESSIONS) == (
        fwd_v1.HORIZONS, fwd_v1.LABEL_VERSION, fwd_v1.METHODOLOGY_VERSION, fwd_v1.VOID_GRACE_SESSIONS)
