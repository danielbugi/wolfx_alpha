"""The dataset builder end to end against a real throwaway Postgres: assembly, PIT, split/purge/embargo, input verification, the audit, the
one-shot test rule, and read-only enforcement. (Reproducibility and mutation proofs live in test_dataset_repro_db.py.)"""
import copy
import json

import psycopg2
import pytest

from dataset_world import CAL, CUTOFF, EDGE_IDX, HORIZONS, SHA, TE, TR, VA, config, denv, fresh_denv, make_spec  # noqa: F401
from lab_samples import NOW
from research.lab import dataset_contract as C
from research.lab import dataset_reader as RD
from research.lab import dataset_runner as R
from research.lab import manifest as M
from research.lab import registry_store as RS


@pytest.fixture(scope="module")
def build(denv):
    return denv.build()


# ------------------------------------------------------------------ a complete, audited build
def test_build_passes_every_check(build):
    assert build.audit.ok and build.audit.document["verdict"] == "PASS"
    assert build.verification.ok and not build.verification.failures()
    assert {c.kind for c in build.verification.checks} == {"cryptographic", "database_fingerprint"}
    assert {c.key for c in build.verification.by_kind("cryptographic")} >= {"calendar.sessions", "contract.dataset_schema", "universe.members"}
    assert {c.key for c in build.verification.by_kind("database_fingerprint")} == {C.DB_INPUTS[s] for s in C.enabled_db_sources(build.config)}
    assert all(c.status == "verified" for c in build.verification.checks)


def test_rows_follow_the_contract_and_reconcile(build):
    assert len(build.rows) > 100
    names = {n for n, _ in C.COLUMNS}
    assert all(set(r) == names for r in build.rows)
    a = build.assembly
    assert len(a.rows) + len(a.dropped) == a.candidates_seen * len(HORIZONS)
    counts = build.audit.document["counts"]
    assert counts["rows_final"] == len(build.rows)
    assert sum(counts["rows_by_split"].values()) == len(build.rows)
    assert all(counts["rows_by_split"][s] > 0 for s in M.SPLITS)


def test_split_assignment_matches_the_manifest_contract(build):
    windows = {"train": (CAL[0], CAL[199]), "validation": VA, "test": TE}
    for r in build.rows:
        assert M.assign_split(build.manifest, r["t0_session"], r["horizon_sessions"]) == r["split"]
        a, b = windows[r["split"]]
        assert a <= r["t0_session"] <= b and a <= r["horizon_session"] <= b


def test_edge_candidates_are_dropped_with_a_reason_never_used(build):
    reasons = {d.reason for d in build.assembly.dropped}
    assert {"split:purged", "split:embargo"} <= reasons
    used = {(r["t0_session"], r["horizon_sessions"]) for r in build.rows}
    for idx in EDGE_IDX:
        for h in (20, 60):
            assert (CAL[idx], h) not in used
    assert any(d.t0_session == CAL[185] and d.horizon_sessions == 20 and d.reason == "split:purged" for d in build.assembly.dropped)
    assert any(d.t0_session == CAL[230] and d.reason == "split:embargo" for d in build.assembly.dropped)


def test_windows_are_separated_by_the_embargo(build):
    assert build.manifest.required_gap() == 60
    starts = {s: min(CAL.index(r["t0_session"]) for r in build.rows if r["split"] == s) for s in M.SPLITS}
    ends = {s: max(CAL.index(r["horizon_session"]) for r in build.rows if r["split"] == s) for s in M.SPLITS}
    assert starts["validation"] - ends["train"] > 60 and starts["test"] - ends["validation"] > 60


def test_every_value_was_known_before_its_decision_deadline(build):
    for r in build.rows:
        deadline = C.decision_deadline(r["t0_session"], build.config.availability_grace_days)
        for _, col, _ in C.AVAILABILITY_COLUMNS:
            if r[col] is not None:
                assert r[col] <= CUTOFF
                if col != "label_available_at":
                    assert r[col] < deadline, (r["observation_key"], col)


def test_late_absent_and_reconstructed_inputs_are_masked_not_filled(build):
    rows = build.rows
    assert {"ok", "late", "absent", "unavailable", "reconstructed_excluded"} <= {r["market__state"] for r in rows}
    for r in rows:
        if r["market__state"] != "ok":
            assert r["regime_state"] is None and r["regime_score"] is None
        if r["market__state"] in ("late", "absent", "reconstructed_excluded"):
            assert r["market_available_at"] is None
        if r["market__state"] in ("late", "absent", "reconstructed_excluded"):
            assert r["breadth__state"] == r["market__state"] and r["breadth_sma50_pct"] is None
        if r["rs__state"] != "ok":
            assert r["rs_percentile"] is None and r["rs_ret_pct"] is None
        assert r["market__provenance"] in (None, "observed")
    assert any(r["market__state"] == "unavailable" and r["regime_score"] is None for r in rows)
    assert not any(r["regime_state"] == "UNAVAILABLE" for r in rows)


def test_catalyst_states_and_first_seen(build):
    assert {r["catalyst__state"] for r in build.rows} == {"catalyst_known", "event_unclassified", "none_observed", "unknown_availability"}
    for r in build.rows:
        if r["catalyst__state"] == "catalyst_known":
            assert r["catalyst_latest_event_time"] is not None and r["catalyst_latest_event_time"] <= r["t0_session"]
    assert {r["first_seen__state"] for r in build.rows} >= {"ok", "absent", "late"}
    ok = [r for r in build.rows if r["first_seen__state"] == "ok"]
    assert ok and all(json.loads(r["first_seen_json"]) for r in ok)


def test_sector_name_comes_from_the_candidate_not_the_snapshot(build):
    assert any(r["sector"] == "Tech" and r["sector__state"] in ("late", "absent", "reconstructed_excluded") for r in build.rows)
    assert all(r["sector"] is None for r in build.rows if r["sector__state"] in ("no_sector", "sector_asof_after_t0", "sector_name_late"))


def test_label_void_rows_carry_no_return(build):
    void = [r for r in build.rows if r["label_status"] == "void"]
    assert void and all(r["raw_return"] is None and r["directional_return"] is None and r["void_reason"] for r in void)
    assert not [r for r in build.rows if r["label_status"] == "final" and r["directional_return"] is None]


def test_long_and_short_directions_are_both_present(build):
    assert {r["direction"] for r in build.rows} == {1, -1}
    for r in build.rows:
        if r["label_status"] == "final":
            assert r["directional_return"] == pytest.approx(r["direction"] * r["raw_return"])


# ------------------------------------------------------------------ the audit is machine- AND human-readable, and says what it must
def test_audit_document_and_text_cover_the_required_findings(build):
    d = build.audit.document
    assert d["fatal"] == [] and d["verdict"] == "PASS"
    for k in ("rows_final", "rows_by_split", "rows_purged", "rows_embargoed"):
        assert k in d["counts"]
    codes = {x["code"] for x in d["limitations"]}
    assert {"availability_stamp_trust", "sector_history_reconstructed", "unavailable_features", "unknown_availability",
            "reconstructed_input_excluded", "late_value_masked", "catalyst_absence_is_not_evidence"} <= codes
    for needle in ("FATAL VIOLATIONS", "SOURCE STATES", "INPUT VERIFICATION", "LIMITATIONS", "purged", "embargoed", "verdict PASS"):
        assert needle in build.audit.text
    json.dumps(d)


def test_report_is_complete_and_never_zero_fills(build):
    rep = build.report
    i = rep["identity"]
    assert i["manifest_hash"] == build.manifest.manifest_hash and i["dataset_hash"] == build.dataset_hash and i["code_sha"] == SHA
    assert i["code_check"]["status"] == "match"
    for k in ("methodology", "universe", "time", "row_counts", "missingness", "outcome_distribution", "label_distribution", "leakage_audit",
              "baselines", "known_pit_limitations", "insufficient_sample_warnings"):
        assert k in rep
    assert rep["time"]["embargo_sessions"] == 60 and rep["time"]["windows"]["train"] == [TR[0].isoformat(), TR[1].isoformat()]
    assert rep["insufficient_sample_warnings"]
    assert all(w["n"] is not None and w["min_sample"] == 10 for w in rep["insufficient_sample_warnings"])
    assert build.report_text.startswith("DATASET DIAGNOSTIC REPORT")


def test_insufficient_samples_carry_n_and_no_number(build):
    found = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("status") == "insufficient_sample":
                found.append(o)
                assert set(o) <= {"n", "status", "min_sample", "n_a", "n_b"}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(build.baselines)
    assert found


def test_test_split_is_withheld_by_default(build):
    b = build.baselines["baselines"]
    assert b["null_unconditional"]["by_split"]["test"] == {"status": "withheld"}
    for name in ("donchian_signal", "donchian_x_regime", "donchian_x_rs", "donchian_x_catalyst"):
        assert b[name]["strata"]["test"]["status"] == "withheld"
    assert build.include_test is False


def test_all_required_baselines_present(build):
    assert set(build.baselines["baselines"]) == {"null_unconditional", "donchian_signal", "donchian_x_regime", "donchian_x_rs", "donchian_x_catalyst"}
    assert build.baselines["n_comparisons"] > 0 and "disclaimer" in build.baselines


# ------------------------------------------------------------------ read-only against the database
def test_reader_transaction_is_read_only_and_restored(denv):
    conn = denv.conn
    before = conn.readonly
    with RD.read_only_session(conn):
        cur = conn.cursor()
        with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
            cur.execute("CREATE TABLE lab_must_not_exist (a int)")
        conn.rollback()
        with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
            cur.execute("DELETE FROM forward_return_label")
        conn.rollback()
    assert conn.readonly == before
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM forward_return_label")
    assert cur.fetchone()[0] > 0


def test_build_leaves_the_database_unchanged(denv):
    cur = denv.conn.cursor()
    tables = ("candidate_observation", "forward_return_label", "market_snapshot", "sector_snapshot", "stock_relative_strength",
              "market_event_revision", "catalyst_classification", "source_observation", "dataset_manifest", "experiment_registration",
              "experiment_result")

    def snap():
        out = {}
        for t in tables:
            cur.execute(f"SELECT count(*) FROM {t}")
            out[t] = cur.fetchone()[0]
        return out
    before = snap()
    denv.build()
    denv.conn.rollback()
    assert snap() == before


# ------------------------------------------------------------------ fail closed
def test_unknown_manifest_hash_fails_closed(denv):
    with pytest.raises(M.LabError, match="no dataset manifest"):
        R.build_dataset(denv.conn, "f" * 64, CAL, code=denv.code)


def test_wrong_calendar_fails_closed(denv):
    with pytest.raises(M.LabError):
        R.build_dataset(denv.conn, denv.manifest_hash, CAL[:-1], code=denv.code)
    with pytest.raises(M.LabError):
        R.build_dataset(denv.conn, denv.manifest_hash, tuple(reversed(CAL)), code=denv.code)


def test_code_identity_is_enforced(denv):
    with pytest.raises(R.BuildFailed, match="not the manifest's code_sha"):
        denv.build(code=R.CodeIdentity("c" * 40, True))
    with pytest.raises(R.BuildFailed, match="not clean"):
        denv.build(code=R.CodeIdentity(SHA, False))
    with pytest.raises(R.BuildFailed, match="code identity was not supplied"):
        R.build_dataset(denv.conn, denv.manifest_hash, CAL, code=None)
    b = denv.build(code=R.CodeIdentity("c" * 40, True), allow_code_sha_drift=True)
    cc = b.report["identity"]["code_check"]
    assert cc["status"] == "drift_allowed" and cc["running_code_sha"] == "c" * 40 and cc["manifest_code_sha"] == SHA
    assert b.dataset_hash == denv.build().dataset_hash
    with pytest.raises(R.BuildFailed):
        denv.build(code=R.CodeIdentity("c" * 40, False), allow_code_sha_drift=True)


_VERSION = iter(range(1, 10_000))


def _register(env, hashes=None, cfg=None, **over):
    over.setdefault("dataset_version", f"v-test-{next(_VERSION)}")
    hashes = dict(env.manifest.document["input_hashes"] if hashes is None else hashes)
    m = M.build_manifest(make_spec(hashes, cfg if cfg is not None else config(), **over), now=NOW)
    RS.insert_manifest(env.conn.cursor(), m)
    env.conn.commit()
    return m.manifest_hash


def test_missing_required_input_hash_fails_closed(fresh_denv):
    hashes = dict(fresh_denv.manifest.document["input_hashes"])
    hashes.pop("db.market_snapshot")
    h = _register(fresh_denv, hashes)
    with pytest.raises(R.BuildFailed) as e:
        R.build_dataset(fresh_denv.conn, h, CAL, code=fresh_denv.code)
    assert any("input_hash_missing" in p for p in e.value.problems) and e.value.audit is not None
    assert any(c.key == "db.market_snapshot" and c.status == "MISSING" for c in e.value.verification.checks)


def test_wrong_input_hash_fails_closed(fresh_denv):
    hashes = dict(fresh_denv.manifest.document["input_hashes"])
    hashes["db.forward_return_label"] = "0" * 64
    h = _register(fresh_denv, hashes)
    with pytest.raises(R.BuildFailed) as e:
        R.build_dataset(fresh_denv.conn, h, CAL, code=fresh_denv.code)
    assert any("input_hash_mismatch" in p for p in e.value.problems)
    assert [c.key for c in e.value.verification.failures()] == ["db.forward_return_label"]
    assert "MISMATCH" in e.value.audit.text


@pytest.mark.parametrize("mutate,needle", [
    (lambda c: c.pop("min_sample"), "min_sample"),
    (lambda c: c.pop("reconstructed_policy"), "reconstructed_policy"),
    (lambda c: c.update(primary_horizon=7), "primary_horizon"),
    (lambda c: c.update(universe_members=["B", "A", "C"]), "universe_members"),
    (lambda c: c.update(market={"feature_set_version": "other_fsv"}), "feature_versions"),
    (lambda c: (c.pop("market"), c.update(sector={"feature_set_version": "mi_v2"})), "config.sector needs config.market"),
])
def test_inconsistent_config_fails_closed(fresh_denv, mutate, needle):
    cfg = config()
    mutate(cfg)
    h = _register(fresh_denv, cfg=cfg, universe_hash=M.universe_hash(["A", "B", "C"], "rule"))
    with pytest.raises(M.LabError) as e:
        R.build_dataset(fresh_denv.conn, h, CAL, code=fresh_denv.code)
    assert needle in " ".join(e.value.problems)


def test_universe_that_is_not_the_manifests_fails_closed(fresh_denv):
    h = _register(fresh_denv, cfg=config(universe_members=["A", "B"]), universe_hash=M.universe_hash(["A", "B", "C"], "rule"))
    with pytest.raises(R.BuildFailed) as e:
        R.build_dataset(fresh_denv.conn, h, CAL, code=fresh_denv.code)
    failed = {c.key for c in e.value.verification.failures()}
    assert {"manifest.universe_hash", "universe.members"} <= failed, failed


def test_tampered_stored_document_fails_closed(denv):
    got = RS.get_manifest(denv.conn.cursor(), denv.manifest_hash)
    for edit in (lambda d: d.update(embargo_sessions=5), lambda d: d["config"].update(min_sample=2)):
        doc = copy.deepcopy(got["manifest"])
        edit(doc)
        with pytest.raises(M.LabError):
            C.revalidate_manifest(doc, denv.manifest_hash, CAL, got["created_at"])


# ------------------------------------------------------------------ the one-shot test window, through the registry
def test_test_window_is_one_shot_through_the_registry(fresh_denv):
    env = fresh_denv
    b0 = env.build()
    reg = R.diagnostic_registration(env.manifest, env.code, "diag-1")
    cur = env.conn.cursor()
    RS.insert_registration(cur, reg)
    env.conn.commit()
    with pytest.raises(M.LabError, match="registration_hash"):
        env.build(include_test=True)
    with pytest.raises(M.LabError, match="validation result first"):
        env.build(include_test=True, registration_hash=reg.registration_hash)
    with pytest.raises(M.LabError, match="withheld"):
        R.record_test(cur, b0, reg, env.code)
    R.record_validation(cur, b0, reg, env.code)
    env.conn.commit()
    cur.execute("SELECT metrics, artifact_hashes FROM experiment_result WHERE registration_hash = %s", (reg.registration_hash,))
    metrics, artifacts = cur.fetchone()
    assert artifacts["dataset_hash"] == b0.dataset_hash and artifacts["report_hash"] == b0.report["report_hash"]
    assert set(metrics) == {"n_rows", "n_final"}
    b1 = env.build(include_test=True, registration_hash=reg.registration_hash)
    assert b1.include_test and b1.dataset_hash == b0.dataset_hash
    assert b1.baselines["baselines"]["null_unconditional"]["by_split"]["test"]["n_rows"] > 0
    R.record_test(cur, b1, reg, env.code)
    env.conn.commit()
    with pytest.raises(M.LabError, match="already evaluated"):
        env.build(include_test=True, registration_hash=reg.registration_hash)
    with pytest.raises(M.LabError):
        R.record_test(cur, b1, reg, env.code)
    env.conn.rollback()


def test_closed_experiment_keeps_its_test_window(fresh_denv):
    env = fresh_denv
    b0 = env.build()
    reg = R.diagnostic_registration(env.manifest, env.code, "diag-closed")
    cur = env.conn.cursor()
    RS.insert_registration(cur, reg)
    R.record_validation(cur, b0, reg, env.code)
    RS.append_result(cur, M.ResultSpec(reg.registration_hash, "failed", {}, 1, {}, SHA, "abandoned for test"), reg)
    env.conn.commit()
    with pytest.raises(M.LabError, match="closed"):
        env.build(include_test=True, registration_hash=reg.registration_hash)
