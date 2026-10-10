"""Pure tests of the readiness / eligibility verdict (no database): every check fails CLOSED, and a dataset that merely builds and audits clean is
not called research-ready."""
import copy
from datetime import timedelta
from types import SimpleNamespace

import pytest

import research.lab.dataset_contract as C
import research.lab.dataset_readiness as RY
from dataset_world import CAL, HORIZONS, TE, TR, VA, config, make_spec
from lab_samples import NOW
from research.lab import manifest as M

MANIFEST = M.build_manifest(make_spec({"labels": "b" * 64}), now=NOW)
CFG = C.parse_config(config(), HORIZONS)
H = "h" * 64
OK_CHECK = {"status": "match", "tree_clean": True, "running_code_sha": "a" * 40, "manifest_code_sha": "a" * 40}
OK_AUDIT = {"verdict": "PASS", "fatal": [], "limitations": [{"note": "a limitation the audit itself reports"}]}
OK_VERIF = SimpleNamespace(ok=True, by_kind=lambda kind: [])


def row(split, day, label="final", **over):
    r = {"horizon_sessions": 20, "split": split, "t0_session": day, "horizon_session": day + timedelta(days=28), "label_status": label,
         "market__state": C.OK, "market__provenance": "observed", "sector__state": C.OK, "sector__provenance": "observed",
         "rs__state": C.OK, "rs__provenance": "observed", "rs_sector_pit_safe": True, "breadth__state": C.OK,
         "rs_vs_sector_pp": 1.5, "rs_vs_sector__state": C.OK, "rs_sector": "Technology",
         "catalyst__state": C.CATALYST_NONE_OBSERVED, "first_seen__state": C.OK}
    r.update(over)
    return r


def good_rows():
    rows = []
    for split, window, n in (("train", TR, 300), ("validation", VA, 100), ("test", TE, 100)):
        span = CAL.index(window[1]) - CAL.index(window[0]) + 1
        rows += [row(split, CAL[CAL.index(window[0]) + i % span]) for i in range(n)]
    return rows


def assess(rows=None, cfg=CFG, audit=OK_AUDIT, verification=OK_VERIF, code_check=OK_CHECK, include_test=False):
    return RY.assess(manifest=MANIFEST, cfg=cfg, rows=good_rows() if rows is None else rows, audit_document=audit, audit_hash="a" * 64,
                     verification=verification, code_check=code_check, dataset_hash="d" * 64, report_hash="r" * 64, inputs_hash="i" * 64,
                     include_test=include_test)


def failed(doc):
    return {c["id"] for c in doc["eligibility"]["checks"] if not c["passed"]}


def test_a_hygienic_observed_dataset_is_eligible_and_says_what_that_means():
    d = assess()
    assert failed(d) == set() and d["eligibility"]["model_research_eligible"] is True
    assert d["eligibility"]["predictive_edge_claim"] == "none" and d["distinction"] == RY.DISTINCTION
    assert "reproducible != point-in-time correct != vendor correct != predictive edge" in RY.render_text(d)
    assert RY.verify_readiness_hash(d)
    assert d["coverage"]["earliest_trustworthy_pit_date"]["all_sources"] == TR[0].isoformat()
    assert d["identity"]["dataset_hash"] == "d" * 64 and d["identity"]["manifest_hash"] == MANIFEST.manifest_hash


def test_the_verdict_and_hash_are_deterministic_and_detect_edits():
    a, b = assess(), assess()
    assert a == b and a["readiness_hash"] == b["readiness_hash"]
    edited = copy.deepcopy(a)
    edited["eligibility"]["model_research_eligible"] = False
    assert not RY.verify_readiness_hash(edited)
    assert not RY.verify_readiness_hash({k: v for k, v in a.items() if k != "readiness_hash"})


def test_a_dataset_that_builds_and_audits_clean_is_not_thereby_eligible():
    rows = good_rows()[:50]
    d = assess(rows)
    assert not d["eligibility"]["model_research_eligible"] and "final_label_samples_sufficient" in failed(d)
    assert d["eligibility"]["blocking_reasons"]


@pytest.mark.parametrize("name,kw,expected", [
    ("audit_failed", {"audit": {"verdict": "FAIL", "fatal": ["x"], "limitations": []}}, "audit_passed"),
    ("audit_verdict_missing", {"audit": {"limitations": []}}, "audit_passed"),
    ("input_mismatch", {"verification": SimpleNamespace(ok=False, by_kind=lambda kind: [])}, "inputs_verified"),
    ("input_unverifiable", {"verification": SimpleNamespace(ok=True, by_kind=lambda kind: [1] if kind == "unverifiable" else [])}, "inputs_verified"),
    ("code_drift", {"code_check": dict(OK_CHECK, status="drift_allowed")}, "code_identity_exact"),
    ("code_dirty", {"code_check": dict(OK_CHECK, tree_clean=False)}, "code_identity_exact"),
    ("code_check_empty", {"code_check": {}}, "code_identity_exact"),
    ("test_revealed", {"include_test": True}, "test_split_unevaluated"),
    ("policy_not_exclude", {"cfg": C.parse_config(config(reconstructed_policy="include_flagged"), HORIZONS)}, "reconstructed_policy_is_exclude"),
])
def test_each_hygiene_check_fails_closed(name, kw, expected):
    d = assess(**kw)
    assert expected in failed(d) and not d["eligibility"]["model_research_eligible"], name


def _tweak(pred, **over):
    rows = good_rows()
    for r in rows:
        if pred(r):
            r.update(over)
    return rows


def test_reconstructed_values_in_the_dataset_block_eligibility():
    d = assess(_tweak(lambda r: r["split"] == "train" and r["t0_session"] == TR[0], market__provenance="reconstructed"))
    assert "no_reconstructed_value_in_dataset" in failed(d) and not d["eligibility"]["model_research_eligible"]
    assert d["coverage"]["by_source"]["market"]["reconstructed_used"] > 0


def test_unknown_provenance_counts_as_reconstructed_never_as_observed():
    d = assess(_tweak(lambda r: True, rs__provenance="mystery"))
    assert {"no_reconstructed_value_in_dataset", "observed_coverage_sufficient"} <= failed(d)


NO_SECTOR_CELL = dict(sector__state=C.NO_SECTOR, sector__provenance=None, rs_sector_pit_safe=False, rs_vs_sector_pp=None,
                      rs_vs_sector__state=C.NO_SECTOR, rs_sector=None)


def test_a_sector_relative_value_without_a_pit_safe_sector_blocks_eligibility():
    d = assess(_tweak(lambda r: r["split"] == "train", rs_sector_pit_safe=False))     # a non-NULL value, but its sector map is not PIT-safe
    assert "relative_strength_sector_pit_safe" in failed(d)


@pytest.mark.parametrize("name,over", [
    ("value_without_a_sector", dict(rs_sector=None)),
    ("value_not_in_state_ok", dict(rs_vs_sector__state=C.SECTOR_STALE)),
    ("value_under_state_no_sector", dict(rs_vs_sector__state=C.NO_SECTOR)),
    ("value_under_reconstructed_rs_row", dict(rs__provenance="reconstructed")),
    ("value_under_unknown_provenance", dict(rs__provenance="mystery")),
    ("value_flagged_unsafe", dict(rs_vs_sector__state=C.UNSAFE_VALUE)),
])
def test_every_way_a_non_null_sector_relative_value_can_be_unproven_blocks_eligibility(name, over):
    d = assess(_tweak(lambda r: r["t0_session"] == TR[0] and r["split"] == "train", **over))
    assert "relative_strength_sector_pit_safe" in failed(d), name


def test_option_b_a_few_no_sector_candidates_stay_and_do_not_fail_readiness():
    rows = good_rows()
    for r in rows[:25]:                                   # 25 of 500 = 5% legitimately have no sector
        r.update(NO_SECTOR_CELL)
    d = assess(rows)
    assert "relative_strength_sector_pit_safe" not in failed(d)
    assert failed(d) == set() and d["eligibility"]["model_research_eligible"] is True
    sr = d["coverage"]["sector_relative"]
    assert sr["states"][C.NO_SECTOR] == 25 and sr["observed_sector_relative_rows"] == 475 and sr["null_not_zero"] == 25
    assert sr["threshold"] is None and "never 0" in sr["note"]


def test_option_b_many_no_sector_candidates_are_reported_by_the_sector_context_coverage_not_by_the_rs_check():
    rows = good_rows()
    for r in rows[:100]:                                  # 20% have no sector: the sector CONTEXT is sparse, which is its own (deliberate) check
        r.update(NO_SECTOR_CELL)
    d = assess(rows)
    assert "relative_strength_sector_pit_safe" not in failed(d)
    assert failed(d) == {"observed_coverage_sufficient"}


def test_a_null_sector_relative_value_is_never_unsafe_whatever_its_state():
    for st in (C.NO_SECTOR, C.SECTOR_NOT_PIT_SAFE, C.SECTOR_STALE, C.SECTOR_UNCONFIRMED, C.SECTOR_IDENTITY_CONFLICT, C.SECTOR_VALUE_UNAVAILABLE):
        r = row("train", TR[0], rs_vs_sector_pp=None, rs_vs_sector__state=st, rs_sector=None, rs_sector_pit_safe=False)
        assert RY.sector_relative_unsafe(r) is False, st


def test_an_unknown_provenance_sector_is_never_quietly_null():
    d = assess(_tweak(lambda r: r["t0_session"] == TR[0] and r["split"] == "train", sector__state=C.SECTOR_UNKNOWN_PROVENANCE, sector__provenance=None))
    assert "no_reconstructed_value_in_dataset" in failed(d)
    assert d["coverage"]["by_source"]["sector"]["unknown_provenance_used"] > 0


def test_weak_observed_coverage_blocks_eligibility_even_when_nothing_is_reconstructed():
    rows = good_rows()
    for r in rows[:60]:
        r.update(sector__state=C.ABSENT)            # 60 of 500 rows -> 88% observed
    d = assess(rows)
    assert "observed_coverage_sufficient" in failed(d)
    assert d["coverage"]["by_source"]["sector"]["absent"] == 60
    assert d["coverage"]["by_source"]["market"]["observed_fraction"] == 1.0


def test_late_and_unknown_availability_are_counted_not_hidden():
    rows = good_rows()
    rows[0].update(market__state=C.LATE)
    rows[1].update(market__state=C.UNKNOWN_AVAILABILITY)
    rows[2].update(market__state=C.RECONSTRUCTED_EXCLUDED)
    m = assess(rows)["coverage"]["by_source"]["market"]
    assert (m["late"], m["unknown_availability"], m["reconstructed_excluded"], m["observed"]) == (1, 1, 1, 497)


def test_the_earliest_trustworthy_date_is_a_coverage_suffix_not_the_first_observation():
    rows = good_rows()
    cutoff_day = CAL[100]
    for r in rows:
        if r["t0_session"] < cutoff_day:
            r.update(market__state=C.ABSENT)         # observation of market "starts" at CAL[0]... for a few rows only, then nothing
    first_obs = min(r["t0_session"] for r in rows if r["market__state"] == C.OK)
    d = assess(rows)
    t = d["coverage"]["earliest_trustworthy_pit_date"]
    assert t["per_source_first_observed"]["market"] == first_obs.isoformat()
    assert t["all_sources"] is not None and t["all_sources"] >= first_obs.isoformat()
    since = t["observed_fraction_since"]
    assert all(v >= RY.MIN_OBSERVED_COVERAGE for v in since.values())
    scan = [r for r in rows if r["t0_session"].isoformat() >= t["all_sources"]]
    assert t["rows_since"] == len(scan)


def test_no_trustworthy_date_when_a_source_never_reaches_the_coverage_bar():
    d = assess(_tweak(lambda r: True, rs__state=C.ABSENT))
    t = d["coverage"]["earliest_trustworthy_pit_date"]
    assert t["all_sources"] is None and t["reason"] and "trusted_pit_date_determined" in failed(d)


def test_a_trusted_date_after_the_train_window_is_not_enough():
    rows = [r for r in good_rows() if r["split"] != "train"] + [row("train", TR[0], market__state=C.ABSENT) for _ in range(300)]
    d = assess(rows)
    assert d["coverage"]["earliest_trustworthy_pit_date"]["all_sources"] is not None
    assert "trusted_pit_date_determined" in failed(d) or "observed_coverage_sufficient" in failed(d)


def test_missing_labels_and_label_shortfalls_block_eligibility():
    rows = good_rows()
    for r in [x for x in rows if x["split"] == "train"][:40]:
        r.update(label_status=C.MISSING)
    d = assess(rows)
    assert {"labels_present", "final_label_samples_sufficient"} <= failed(d)
    assert d["label_maturity"]["by_split"]["train"]["missing"] == 40 and d["sample"]["train"] == {"final_labelled_rows": 260, "required": 300}


def test_no_rows_at_all_is_not_eligible_and_does_not_crash():
    d = assess([])
    assert not d["eligibility"]["model_research_eligible"] and d["coverage"]["earliest_trustworthy_pit_date"]["all_sources"] is None
    assert RY.verify_readiness_hash(d) and RY.render_text(d)


def test_the_thresholds_are_declared_in_the_document_and_every_limitation_is_listed():
    d = assess()
    assert d["thresholds"] == RY.THRESHOLDS and d["thresholds"]["min_observed_coverage"] == 0.9
    assert d["pit_limitations"][0] == "a limitation the audit itself reports"
    assert set(RY.RESIDUAL_LIMITATIONS) <= set(d["pit_limitations"])
    text = RY.render_text(d)
    for needle in ("MODEL RESEARCH ELIGIBLE: YES", "EARLIEST TRUSTWORTHY PIT DATE", "LABEL MATURITY", "KNOWN POINT-IN-TIME LIMITATIONS", "readiness"):
        assert needle in text
