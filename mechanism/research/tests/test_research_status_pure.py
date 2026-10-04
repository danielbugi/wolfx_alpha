"""Pure (database-free) tests of the Slice 6 research status: the per-session state machine, the integrity catalogue, the readiness failures, the
remaining-history estimate and the canonical hash. Every input is a hand-built reader output, so each scenario states exactly which sessions are
observed, late, reconstructed, back-dated or missing and the document must say the same."""
import copy
import json
import math
import re
from datetime import timezone
from pathlib import Path

import research.lab.dataset_contract as C
import research.lab.research_status as ST
from dataset_world import CAL, HORIZONS, MAT, TE, TR, VA, config

UTC = timezone.utc
CFG = C.parse_config({k: v for k, v in config().items() if k not in ("catalyst", "first_seen")}, HORIZONS)
N_DUE = 40
CALS = CAL[:60]
CUTOFF = C.decision_deadline(CAL[N_DUE - 1], 1)            # the last due session is exactly CAL[39]
NO_CONTRACT = {"evaluated": False, "reason": "no primary-horizon dataset row exists yet (no observed candidate with a known context); nothing to evaluate"}
LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"


def prows(idxs, prov="observed", n=3, late=(), back=(), **extra):
    out = []
    for i in idxs:
        nl = n if i in late else 0
        out.append(dict(session=CAL[i], provenance=prov, n=n, n_in_time=n - nl, n_late=nl, n_backdated=n if i in back else 0, **extra))
    return out


def crows(idxs, n=3, late=(), back=(), before=()):
    return [dict(session=CAL[i], n=n, n_in_time=0 if i in late else n, n_late=n if i in late else 0, n_backdated=n if i in back else 0,
                 n_before_activation=n if i in before else 0) for i in idxs]


def runs(idxs, status="complete", **kw):
    return [dict(session=CAL[i], status=status, n=1, n_in_time=1 if status == "complete" else 0, hash_drift=0, snapshot_drift=0, **kw) for i in idxs]


def make(cand=range(N_DUE), market=range(N_DUE), sector=range(N_DUE), rs=range(N_DUE), activation=0, contract=None, **over):
    inp = {"cutoff": CUTOFF, "calendar": CALS, "calendar_source": "explicit_calendar_file", "config": CFG, "maturity_session": MAT,
           "plan": {"embargo_sessions": 60, "purge_sessions": 0, "windows": {"train": list(TR), "validation": list(VA), "test": list(TE)}},
           "universe": {"rule": "rule", "n_symbols": 3, "hash": "h" * 64, "from_database": True},
           "candidates": cand if isinstance(cand, dict) else {"rows": crows(cand), "runs": runs(cand), "activation": [] if activation is None else
                          [{"state": "enabled", "effective_from_session": CAL[activation]}]},
           "market": market if isinstance(market, dict) else {"rows": prows(market)},
           "sector": sector if isinstance(sector, dict) else {"rows": prows(sector, n_sectors=2)},
           "stock_rs": rs if isinstance(rs, dict) else {"rows": prows(rs, n_run_hashes=1, n_ok_no_sector=0, n_ok_sector_unsafe=0)},
           "labels": {"rows": [], "n_computed_before_horizon": 0, "computed_before_horizon_examples": []},
           "catalyst": {"n": 0}, "first_seen": {"n": 0}, "contract": contract or NO_CONTRACT}
    inp.update(over)
    return inp


def doc_of(**kw):
    return ST.build_status(make(**kw))


def src(doc, name):
    return next(s for s in doc["sources"] if s["source"] == name)


def integ(doc, cid):
    return next(f for f in doc["integrity"] if f["id"] == cid)


def failure_ids(doc):
    return [f["id"] for f in doc["readiness_failures"]]


def keys_of(o, acc=None):
    acc = set() if acc is None else acc
    if isinstance(o, dict):
        for k, v in o.items():
            acc.add(k)
            keys_of(v, acc)
    elif isinstance(o, list):
        for v in o:
            keys_of(v, acc)
    return acc


# ------------------------------------------------------------------ empty / new history
def test_an_empty_history_is_reported_not_estimated():
    d = ST.build_status(make(cand=(), market=(), sector=(), rs=(), activation=None))
    for name in ("candidates", "market", "sector", "stock_rs"):
        s = src(d, name)
        assert s["capture_state"] == "never_captured" and s["dormant"] is True and s["observed_sessions"] == 0
        assert s["first_observed_session"] is None and s["latest_observed_session"] is None
        assert s["expected_sessions"] is None and s["coverage_since_first_observed"] is None       # nothing to count from: not zero, not 100%
        assert f"{name}_capture_never_captured" in failure_ids(d)
    assert d["remaining_history_estimate"]["remaining_sessions"] is None and "not estimable" in d["remaining_history_estimate"]["reason"]
    assert d["history"]["jointly_observed_sessions"] == 0 and d["history"]["joint_observed_floor_session"] is None
    assert d["contract"]["evaluated"] is False and "slice5_data_checks_not_evaluable" in failure_ids(d)
    assert ST.verify_status_hash(d) and d["predictive_edge_claim"] == "none"


def test_the_calendar_scope_counts_due_and_pending_sessions():
    sc = doc_of()["scope"]["calendar"]
    assert sc["sessions"] == 60 and sc["due_sessions"] == N_DUE and sc["latest_due_session"] == CAL[N_DUE - 1].isoformat()
    assert sc["pending_sessions_within_grace"] >= 0


# ------------------------------------------------------------------ complete observed history
def test_a_complete_observed_history_is_fully_covered_and_active():
    d = doc_of()
    for name in ("candidates", "market", "sector", "stock_rs"):
        s = src(d, name)
        assert s["capture_state"] == "active" and s["dormant"] is False
        assert s["observed_sessions"] == N_DUE == s["expected_sessions"] and s["coverage_since_first_observed"] == 1.0
        assert s["missing_sessions"] == {"count": 0, "ranges": [], "ranges_truncated": False}
        assert s["first_observed_session"] == CAL[0].isoformat() and s["latest_observed_session"] == CAL[N_DUE - 1].isoformat()
        assert s["consecutive_observed_sessions"] == {"current": N_DUE, "longest": N_DUE}
    h = d["history"]
    assert h["jointly_observed_sessions"] == N_DUE and h["joint_coverage_since_floor"] == 1.0 and h["joint_observed_floor_session"] == CAL[0].isoformat()
    assert not any(f["count"] for f in d["integrity"] if f["severity"] == "blocker")


# ------------------------------------------------------------------ partial coverage / missing sessions / source-specific gaps
def test_missing_sessions_are_listed_as_exact_ranges():
    gone = {10, 11, 12, 25}
    d = doc_of(market=[i for i in range(N_DUE) if i not in gone])
    s = src(d, "market")
    assert s["observed_sessions"] == N_DUE - 4 and s["missing_sessions"]["count"] == 4
    assert s["missing_sessions"]["ranges"] == [[CAL[10].isoformat(), CAL[12].isoformat()], [CAL[25].isoformat(), CAL[25].isoformat()]]
    assert s["coverage_since_first_observed"] == round((N_DUE - 4) / N_DUE, 6)
    assert s["consecutive_observed_sessions"] == {"current": N_DUE - 26, "longest": N_DUE - 26}       # runs: 0..9, 13..24, 26..39
    assert s["session_breakdown_since_first"] == {"missing": 4, "observed": N_DUE - 4}
    # the gap is the market's alone: every other source stays whole, and the joint history loses exactly those sessions
    for other in ("candidates", "sector", "stock_rs"):
        assert src(d, other)["missing_sessions"]["count"] == 0
    assert d["history"]["jointly_observed_sessions"] == N_DUE - 4 and d["history"]["consecutive_jointly_observed_sessions"]["longest"] == N_DUE - 26


def test_history_that_starts_late_is_counted_from_its_own_first_session():
    d = doc_of(rs=range(15, N_DUE))
    s = src(d, "stock_rs")
    assert s["first_observed_session"] == CAL[15].isoformat() and s["expected_sessions"] == N_DUE - 15 and s["coverage_since_first_observed"] == 1.0
    assert s["coverage_of_due_calendar"] == round((N_DUE - 15) / N_DUE, 6)       # both views are reported; neither hides the late start
    assert d["history"]["joint_observed_floor_session"] == CAL[15].isoformat() and d["history"]["jointly_observed_sessions"] == N_DUE - 15


def test_a_stalled_source_is_not_active():
    d = doc_of(sector=range(N_DUE - 6))
    s = src(d, "sector")
    assert s["capture_state"] == "stalled" and s["dormant"] is True and "sector_capture_stalled" in failure_ids(d)
    assert s["missing_sessions"]["count"] == 6


# ------------------------------------------------------------------ reconstructed / unknown provenance / late / back-dated
def test_reconstructed_rows_never_count_as_observed():
    d = doc_of(market=())
    d2 = ST.build_status(make(market={"rows": prows(range(N_DUE), prov="reconstructed")}))
    s = src(d2, "market")
    assert s["observed_sessions"] == 0 and s["capture_state"] == "never_captured" and s["reconstructed_rows"] == 3 * N_DUE
    assert integ(d2, "market_reconstructed_rows")["count"] == 3 * N_DUE and integ(d2, "market_reconstructed_rows")["severity"] == "info"
    assert "market_capture_never_captured" in failure_ids(d2) and d["history"]["jointly_observed_sessions"] == 0


def test_an_observed_row_shadows_a_reconstructed_one_and_the_overlap_is_reported():
    rows = prows(range(N_DUE)) + prows([5, 6], prov="reconstructed")
    d = ST.build_status(make(market={"rows": rows}))
    s = src(d, "market")
    assert s["observed_sessions"] == N_DUE and s["reconstructed_rows"] == 6
    assert integ(d, "market_sessions_observed_and_reconstructed")["count"] == 2


def test_unknown_provenance_is_a_blocker_and_never_observed():
    rows = prows(range(N_DUE - 1)) + prows([N_DUE - 1], prov="mystery")
    d = ST.build_status(make(sector={"rows": [dict(r, n_sectors=2) for r in rows]}))
    s = src(d, "sector")
    assert s["unknown_provenance_rows"] == 3 and s["observed_sessions"] == N_DUE - 1
    f = integ(d, "sector_unknown_provenance_rows")
    assert f["severity"] == "blocker" and f["count"] == 3 and f["example_sessions"] == [CAL[N_DUE - 1].isoformat()]
    assert "sector_unknown_provenance_rows" in failure_ids(d)


def test_late_rows_are_not_observed_history_and_are_counted_separately():
    d = ST.build_status(make(market={"rows": prows(range(N_DUE), late={5, 6, 7})}))
    s = src(d, "market")
    assert s["observed_sessions"] == N_DUE - 3 and s["late_observed_rows"] == 9 and s["session_breakdown_since_first"]["late"] == 3
    assert s["missing_sessions"]["count"] == 3                                      # a late session is a gap in FORWARD history
    f = integ(d, "market_rows_late")
    assert f["count"] == 9 and f["severity"] == "warning"


def test_backdated_rows_are_impossible_availability_and_block():
    d = ST.build_status(make(rs={"rows": prows(range(N_DUE), back={3}, n_run_hashes=1, n_ok_no_sector=0, n_ok_sector_unsafe=0)}))
    f = integ(d, "stock_rs_rows_backdated")
    assert f["count"] == 3 and f["severity"] == "blocker" and "stock_rs_rows_backdated" in failure_ids(d)
    assert src(d, "stock_rs")["session_breakdown_since_first"]["backdated"] == 1 and src(d, "stock_rs")["observed_sessions"] == N_DUE - 1


def test_candidate_late_backdated_and_pre_activation_rows_are_reported():
    d = ST.build_status(make(cand={"rows": crows(range(N_DUE), late={4}, back={6}, before={0, 1}), "runs": runs(range(N_DUE)),
                                "activation": [{"state": "enabled", "effective_from_session": CAL[2]}]}))
    assert integ(d, "candidate_rows_late")["count"] == 3 and integ(d, "candidate_rows_backdated")["count"] == 3
    assert integ(d, "candidate_rows_before_activation")["count"] == 6
    assert {"candidate_rows_backdated", "candidate_rows_before_activation"} <= set(failure_ids(d))
    assert "candidate_rows_late" not in failure_ids(d)                                # a warning is reported, not a readiness failure


def test_off_calendar_rows_are_reported():
    off = dict(session=CAL[100], provenance="observed", n=3, n_in_time=3, n_late=0, n_backdated=0)
    d = ST.build_status(make(market={"rows": prows(range(N_DUE)) + [off]}))
    assert integ(d, "market_rows_off_calendar")["count"] == 3


# ------------------------------------------------------------------ malformed / conflicting observations
def test_capture_run_outcomes_partial_failed_running_and_a_clearing_retry():
    cand_runs = runs(range(N_DUE - 4)) + runs([N_DUE - 4], "partial") + runs([N_DUE - 3], "failed") + runs([N_DUE - 2], "running")
    cand_runs += runs([N_DUE - 1], "partial") + runs([N_DUE - 1])                     # partial then a complete retry: cleared
    d = ST.build_status(make(cand={"rows": crows(range(N_DUE)), "runs": cand_runs, "activation": [{"state": "enabled", "effective_from_session": CAL[0]}]}))
    s = src(d, "candidates")
    assert s["session_breakdown_since_first"]["partial"] == 1 and s["session_breakdown_since_first"]["failed"] == 1
    assert s["session_breakdown_since_first"]["running"] == 1 and s["session_breakdown_since_first"]["observed"] == N_DUE - 3
    assert integ(d, "candidate_run_not_complete")["count"] == 3                       # counted per SESSION: the cleared retry is not in it
    assert integ(d, "candidate_run_not_complete")["example_sessions"] == [CAL[i].isoformat() for i in (N_DUE - 4, N_DUE - 3, N_DUE - 2)]
    assert "candidate_run_not_complete" in failure_ids(d)


def test_hash_and_snapshot_drift_are_conflicting_observations_and_block():
    cand_runs = runs(range(N_DUE))
    cand_runs[7]["hash_drift"] = 2
    cand_runs[9]["snapshot_drift"] = 1
    d = ST.build_status(make(cand={"rows": crows(range(N_DUE)), "runs": cand_runs, "activation": [{"state": "enabled", "effective_from_session": CAL[0]}]}))
    assert integ(d, "candidate_run_hash_drift")["count"] == 2 and integ(d, "candidate_snapshot_drift")["count"] == 1
    assert {"candidate_run_hash_drift", "candidate_snapshot_drift"} <= set(failure_ids(d))


def test_relative_strength_conflicts_and_sector_gaps_are_reported():
    rs_rows = prows(range(N_DUE), n_run_hashes=1, n_ok_no_sector=0, n_ok_sector_unsafe=0)
    rs_rows[4]["n_run_hashes"] = 2
    rs_rows[5]["n_ok_no_sector"] = 1
    rs_rows[6]["n_ok_sector_unsafe"] = 2
    sec_rows = prows(range(N_DUE), n_sectors=2)
    sec_rows[8]["n_sectors"] = 1
    d = ST.build_status(make(rs={"rows": rs_rows}, sector={"rows": sec_rows}))
    assert integ(d, "stock_rs_sessions_with_multiple_runs")["count"] == 1 and "stock_rs_sessions_with_multiple_runs" in failure_ids(d)
    assert integ(d, "stock_rs_ok_cells_without_sector")["count"] == 1 and integ(d, "stock_rs_ok_cells_without_sector")["severity"] == "warning"
    assert integ(d, "stock_rs_ok_cells_sector_map_unsafe")["count"] == 2 and "stock_rs_ok_cells_sector_map_unsafe" in failure_ids(d)
    assert integ(d, "sector_observed_sector_count_varies")["count"] == 1


def test_labels_computed_before_their_horizon_are_impossible_availability():
    lab = {"rows": [], "n_computed_before_horizon": 2, "computed_before_horizon_examples": [CAL[3], CAL[4]]}
    d = ST.build_status(make(labels=lab))
    f = integ(d, "labels_computed_before_horizon")
    assert f["count"] == 2 and f["severity"] == "blocker" and f["example_sessions"] == [CAL[3].isoformat(), CAL[4].isoformat()]
    assert "labels_computed_before_horizon" in failure_ids(d)


def test_the_catalogue_is_unique_and_every_check_is_always_reported():
    ids = [c[0] for c in ST.INTEGRITY_CATALOGUE]
    assert len(ids) == len(set(ids)) == 31
    assert [f["id"] for f in doc_of()["integrity"]] == ids
    assert {c[2] for c in ST.INTEGRITY_CATALOGUE} == {"blocker", "warning", "info"}


# ------------------------------------------------------------------ dormancy / structural findings
def test_capture_dormancy_follows_the_observations_not_the_activation_row():
    d = ST.build_status(make(cand=(), activation=0))
    s = src(d, "candidates")
    assert s["capture_state"] == "never_captured" and s["dormant"] is True and s["activation"][0]["state"] == "enabled"
    assert s["capture_runs"] == {}


def test_a_disabled_activation_makes_no_session_expected():
    d = ST.build_status(make(cand={"rows": crows(range(10)), "runs": runs(range(10)), "activation": [
        {"state": "enabled", "effective_from_session": CAL[0]}, {"state": "disabled", "effective_from_session": CAL[10]}]}))
    s = src(d, "candidates")
    assert s["expected_sessions"] == 10 and s["capture_state"] == "stalled" and s["missing_sessions"]["count"] == 0


def test_unscheduled_sources_are_structural_failures_until_observed_then_a_note():
    d = doc_of(market=(), sector=(), rs=(), cand=range(N_DUE))
    assert {"market_no_scheduled_collector", "sector_no_scheduled_collector", "stock_rs_no_scheduled_collector",
            "labels_no_scheduled_collector"} <= set(failure_ids(d)) and d["structural_notes"] == []
    ok = doc_of()
    assert "market_no_scheduled_collector" not in failure_ids(ok) and "market_no_scheduled_collector" in [n["id"] for n in ok["structural_notes"]]
    assert ST.COLLECTORS["candidates"]["scheduled_forward_collector"] is True
    assert all(ST.COLLECTORS[s]["scheduled_forward_collector"] is False for s in ("market", "sector", "stock_rs", "catalyst", "first_seen", "labels"))


def test_event_sources_have_no_session_expectation():
    d = doc_of()
    for name in ("catalyst", "first_seen"):
        s = src(d, name)
        assert s["session_based"] is False and s["capture_state"] == "no_scheduled_collector" and s["dormant"] is True and "expected_sessions" not in s


# ------------------------------------------------------------------ the Slice 5 relationship
def evaluated_contract(passed=False, final=(20, 10, 10)):
    checks = [{"id": i, "passed": passed, "detail": f"detail {i}"} for i in (
        "no_reconstructed_value_in_dataset", "relative_strength_sector_pit_safe", "observed_coverage_sufficient", "trusted_pit_date_determined",
        "labels_present", "final_label_samples_sufficient")]
    return {"evaluated": True, "provisional_rows": 30, "data_checks": checks, "earliest_trustworthy_pit_date": CAL[0].isoformat(), "coverage": {},
            "label_maturity": {}, "sample": {s: {"final_labelled_rows": n, "required": r} for s, n, r in zip(("train", "validation", "test"), final, (300, 100, 100))},
            "sector_unsafe_cells": 0}


def test_failing_slice5_data_checks_are_listed_with_their_own_ids_and_detail():
    d = doc_of(contract=evaluated_contract(False))
    fs = [f for f in d["readiness_failures"] if f["scope"] == "slice5_data_check"]
    assert [f["id"] for f in fs] == [c["id"] for c in evaluated_contract()["data_checks"]] and fs[0]["detail"] == "detail no_reconstructed_value_in_dataset"
    assert d["contract"]["data_checks_all_pass"] is False and d["history"]["authoritative_earliest_trustworthy_pit_date"] == CAL[0].isoformat()


def test_the_status_never_states_eligibility_or_an_edge():
    for d in (doc_of(), doc_of(contract=evaluated_contract(True)), ST.build_status(make(cand=(), market=(), sector=(), rs=(), activation=None))):
        assert "model_research_eligible" not in keys_of(d) and "eligible" not in keys_of(d)
        text = ST.render_text(d)
        assert "ELIGIBLE: YES" not in text and "ELIGIBLE: NO" not in text and "predictive edge claimed: none" in text
        assert d["predictive_edge_claim"] == "none"
        assert set(d["contract"]["build_level_checks"]["not_evaluated_here_need_a_real_build"]) == {"audit_passed", "inputs_verified", "code_identity_exact", "test_split_unevaluated"}


def test_a_non_exclude_reconstructed_policy_is_a_readiness_failure():
    cfg = C.parse_config({**{k: v for k, v in config().items() if k not in ("catalyst", "first_seen")}, "reconstructed_policy": "include_flagged"}, HORIZONS) \
        if "include_flagged" in getattr(C, "RECONSTRUCTED_POLICIES", ("include_flagged",)) else None
    if cfg is None:
        return
    d = ST.build_status(make(config=cfg))
    assert "reconstructed_policy_is_exclude" in failure_ids(d)


# ------------------------------------------------------------------ the remaining-history estimate
def test_the_estimate_is_in_sessions_never_a_date_and_refuses_a_thin_rate():
    thin = doc_of(cand=range(N_DUE - 5, N_DUE), market=range(N_DUE - 5, N_DUE), sector=range(N_DUE - 5, N_DUE), rs=range(N_DUE - 5, N_DUE),
                  activation=N_DUE - 5)
    e = thin["remaining_history_estimate"]
    assert e["unit"] == "trading sessions" and e["remaining_sessions"] is None and "need 20" in e["reason"]
    assert not any({"date", "eta", "days"} & set(k.split("_")) for k in e)


def test_the_estimate_is_a_lower_bound_derived_from_the_observed_rate():
    d = doc_of(contract=evaluated_contract(False))
    e = d["remaining_history_estimate"]
    required = 300 + 100 + 100
    rate = 3 * N_DUE / N_DUE
    expected = math.ceil(max(0, required - 3 * N_DUE) / rate) + CFG.primary_horizon + 2 * (60 + 0)
    assert e["remaining_sessions"] == expected and e["rate_candidate_rows_per_session"] == rate
    assert e["components"]["label_maturity_lag_sessions"] == CFG.primary_horizon and "never a calendar date" in e["reason"]


def test_the_estimate_is_zero_only_when_every_data_check_already_passes():
    d = doc_of(contract=evaluated_contract(True, final=(300, 100, 100)))
    assert d["remaining_history_estimate"]["remaining_sessions"] == 0 and d["contract"]["data_checks_all_pass"] is True
    assert not [f for f in d["readiness_failures"] if f["scope"] == "slice5_data_check"]


def test_no_estimate_while_a_required_source_is_not_accumulating():
    d = doc_of(rs=(), contract=evaluated_contract(False))
    e = d["remaining_history_estimate"]
    assert e["remaining_sessions"] is None and "stock_rs" in e["reason"] and "waiting does not change that" in e["reason"]


# ------------------------------------------------------------------ canonical hash
def test_the_status_is_deterministic_and_order_independent():
    a, b = make(), make()
    for k in ("market", "sector", "stock_rs"):
        b[k] = {"rows": list(reversed(b[k]["rows"]))}
    b["candidates"] = {"rows": list(reversed(b["candidates"]["rows"])), "runs": list(reversed(b["candidates"]["runs"])), "activation": b["candidates"]["activation"]}
    da, db = ST.build_status(a), ST.build_status(copy.deepcopy(b))
    assert da["status_hash"] == db["status_hash"] == ST.build_status(copy.deepcopy(a))["status_hash"]
    assert json.dumps(da, sort_keys=True, default=str) == json.dumps(db, sort_keys=True, default=str)


def test_any_change_to_the_history_changes_the_hash_and_a_tampered_document_fails_verification():
    base = doc_of()
    assert doc_of(market=range(N_DUE - 1))["status_hash"] != base["status_hash"]
    assert ST.verify_status_hash(base)
    tampered = copy.deepcopy(base)
    tampered["sources"][1]["observed_sessions"] += 1
    assert not ST.verify_status_hash(tampered)
    assert not ST.verify_status_hash({k: v for k, v in base.items() if k != "status_hash"})


def test_the_document_carries_no_volatile_execution_metadata():
    blob = json.dumps(doc_of(), default=str)
    for volatile in ("database_time", "hostname", "elapsed", "generated_at", "run_at", "pid", "C:\\\\", "/home/"):
        assert volatile not in blob


# ------------------------------------------------------------------ source-level guards
def sql_strings(path):
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_the_reader_contains_no_write_ddl_or_locking_sql():
    bad = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE|CREATE\s+(TABLE|INDEX|FUNCTION|ROLE)|DROP\s+|GRANT\s|REVOKE\s|"
                     r"FOR\s+UPDATE|LOCK\s+TABLE|NEXTVAL|SET_CONFIG|COPY\s)\b", re.I)
    offenders = [s[:80] for s in sql_strings(LAB_ROOT / "research_status_reader.py") if bad.search(s)]
    assert not offenders, offenders
    src_text = (LAB_ROOT / "research_status_reader.py").read_text(encoding="utf-8")
    assert "commit(" not in src_text and "execute(\"SELECT research_capture_set_state" not in src_text and "research_capture_set_state" not in src_text


def test_the_pure_status_module_is_pure():
    text = (LAB_ROOT / "research_status.py").read_text(encoding="utf-8")
    for forbidden in ("import psycopg2", "import random", "datetime.now", "datetime.utcnow", "time.time", "os.environ", "import subprocess", "open("):
        assert forbidden not in text, forbidden
    assert "import research.labels" not in text and "from research.labels" not in text and "dataset_reader" not in text
