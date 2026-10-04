"""Reproducibility and tamper proofs for the dataset builder, against a real throwaway Postgres.

  * the same manifest + the same data give the same dataset_hash / audit / baselines / report, also across two independent databases;
  * a later append (after the knowledge cutoff) changes nothing;
  * a meaningful mutation of ANY verified input is caught by hash verification (fail closed), and re-authoring the manifest on the
    mutated data yields a different dataset_hash (so the fingerprint really reflects the values).
"""
import copy
import json
from datetime import timedelta

import pytest

from dataset_world import CAL, CUTOFF, author_manifest, config, denv, fresh_denv, make_spec  # noqa: F401
from research.lab import dataset_contract as C
from research.lab import dataset_runner as R
from research.lab import manifest as M
from research.lab import registry_store as RS
from lab_samples import NOW


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)


def mutate(env, table, sql, params=()):
    """Test-only: rewrite history inside the throwaway schema (the immutability triggers are disabled for the statement, then restored)."""
    cur = env.conn.cursor()
    cur.execute("SELECT t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relname = %s "
                "AND c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal", (table,))
    saved = cur.fetchall()
    cur.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER USER')
    cur.execute(sql, params)
    assert cur.rowcount > 0, "the mutation touched no row"
    for name, enabled in saved:
        cur.execute(f'ALTER TABLE "{table}" ENABLE {"ALWAYS " if enabled == "A" else ""}TRIGGER "{name}"')
    env.conn.commit()


# ------------------------------------------------------------------ same manifest, same inputs -> same everything
def test_repeated_builds_are_identical(denv):
    a, b = denv.build(), denv.build()
    assert a.dataset_hash == b.dataset_hash and len(a.dataset_hash) == 64
    assert a.rows == b.rows
    assert canon(a.baselines) == canon(b.baselines)
    assert a.report == b.report and a.report["report_hash"] == b.report["report_hash"]
    assert a.audit.audit_hash == b.audit.audit_hash and a.audit.text == b.audit.text
    assert a.report_text == b.report_text and a.inputs_hash == b.inputs_hash
    assert a.artifact_hashes == b.artifact_hashes


def test_two_independent_databases_give_the_same_dataset(denv, fresh_denv):
    assert denv.manifest_hash == fresh_denv.manifest_hash, "the same data must author the same manifest (input hashes included)"
    a, b = denv.build(), fresh_denv.build()
    assert a.dataset_hash == b.dataset_hash
    assert canon(a.baselines) == canon(b.baselines)
    assert a.report["report_hash"] == b.report["report_hash"]


def test_report_hash_verifies_and_detects_edits(denv):
    from research.lab import dataset_report as RP
    rep = denv.build().report
    assert RP.verify_report_hash(rep)
    edited = copy.deepcopy(rep)
    edited["row_counts"]["rows_final"] += 1
    assert not RP.verify_report_hash(edited)


# ------------------------------------------------------------------ the dataset fingerprint contract
def test_fingerprint_ignores_row_and_column_order_but_not_values(denv):
    b = denv.build()
    m = b.manifest
    h = C.dataset_hash(m, b.rows)
    assert C.dataset_hash(m, list(reversed(b.rows))) == h
    assert C.dataset_hash(m, [dict(reversed(list(r.items()))) for r in b.rows]) == h
    changed = [dict(r) for r in b.rows]
    final = next(i for i, r in enumerate(changed) if r["label_status"] == "final")
    changed[final]["raw_return"] = changed[final]["raw_return"] + 1e-6
    assert C.dataset_hash(m, changed) != h
    assert C.dataset_hash(m, b.rows[:-1]) != h


def test_fingerprint_distinguishes_null_from_zero_and_empty(denv):
    b = denv.build()
    base = C.dataset_hash(b.manifest, b.rows)
    i = next(i for i, r in enumerate(b.rows) if r["regime_score"] is None)
    for fill in (0, 0.0):
        rows = [dict(r) for r in b.rows]
        rows[i]["regime_score"] = fill
        assert C.dataset_hash(b.manifest, rows) != base, repr(fill)
    j = next(j for j, r in enumerate(b.rows) if r["void_reason"] is None)
    rows = [dict(r) for r in b.rows]
    rows[j]["void_reason"] = ""
    assert C.dataset_hash(b.manifest, rows) != base
    for fill in ("", "null", "0", False, float("nan"), float("inf")):       # wrong-typed or non-finite values are rejected, never coerced
        rows = [dict(r) for r in b.rows]
        rows[i]["regime_score"] = fill
        with pytest.raises(M.LabError):
            C.dataset_hash(b.manifest, rows)


def test_fingerprint_binds_methodology_and_version_identity(denv):
    b = denv.build()
    base = C.dataset_hash(b.manifest, b.rows)
    doc = copy.deepcopy(b.manifest.document)
    for key, value in (("dataset_version", "v2"), ("label_methodology_version", "fwd_v1.m2"), ("code_sha", "d" * 40)):
        spec_doc = dict(doc)
        spec_doc[key] = value
        other = M.Manifest(document=spec_doc, manifest_hash=canonical_hash_of(spec_doc), calendar=b.manifest.calendar)
        assert C.dataset_hash(other, b.rows) != base, key


def canonical_hash_of(doc):
    return M.canonical_hash(doc)


# ------------------------------------------------------------------ a later append changes nothing
def test_rows_appended_after_the_cutoff_are_invisible(fresh_denv):
    env = fresh_denv
    before = env.build()
    late = CUTOFF + timedelta(days=1)
    w = env.world
    oid = w.candidate("A", 301, captured_at=late, with_labels=False)
    for h in (5, 20, 60):
        w.label("A", 301, h, oid, 1, computed_at=late)
    w.market(301, captured_at=late)
    env.conn.commit()
    after = env.build()
    assert after.dataset_hash == before.dataset_hash
    assert after.report["report_hash"] == before.report["report_hash"]
    assert after.verification.ok
    for source, added in (("candidates", 1), ("labels", 3), ("market", 1)):
        assert after.post_cutoff_counts[source] == before.post_cutoff_counts[source] + added, source
    assert "post_cutoff_counts" not in json.dumps(after.report)


# ------------------------------------------------------------------ a meaningful mutation of any verified input is caught
MUTATIONS = [
    ("forward_return_label", "UPDATE forward_return_label SET raw_return = raw_return + 0.05, directional_return = direction * (raw_return + 0.05), "
     "excess_return = excess_return + 0.05, directional_excess_return = directional_excess_return + direction * 0.05 WHERE id = (SELECT min(id) FROM forward_return_label "
     "WHERE label_status = 'final' AND horizon_sessions = 20)", "db.forward_return_label"),
    ("forward_return_label", "UPDATE forward_return_label SET mfe = mfe + 0.01 WHERE id = (SELECT min(id) FROM forward_return_label "
     "WHERE label_status = 'final')", "db.forward_return_label"),
    ("candidate_observation", "UPDATE candidate_observation SET quality_grade = 'F' WHERE id = (SELECT min(id) FROM candidate_observation "
     "WHERE quality_grade <> 'F')", "db.candidate_observation"),
    ("candidate_observation", "UPDATE candidate_observation SET alignment_score = alignment_score + 1 WHERE id = (SELECT min(id) FROM candidate_observation)",
     "db.candidate_observation"),
    ("market_snapshot", "UPDATE market_snapshot SET regime_score = 0.99 WHERE id = (SELECT min(id) FROM market_snapshot "
     "WHERE provenance = 'observed' AND regime_score IS NOT NULL)", "db.market_snapshot"),
    ("sector_snapshot", "UPDATE sector_snapshot SET sec_ret_20 = sec_ret_20 + 1 WHERE id = (SELECT min(id) FROM sector_snapshot "
     "WHERE provenance = 'observed')", "db.sector_snapshot"),
    ("stock_relative_strength", "UPDATE stock_relative_strength SET rs_percentile = 99.5 WHERE id = (SELECT min(id) FROM stock_relative_strength "
     "WHERE rs_percentile <> 99.5)", "db.stock_relative_strength"),
    ("market_event_revision", "UPDATE market_event_revision SET event_time = event_time + 1 WHERE id = (SELECT min(id) FROM market_event_revision)",
     "db.market_event"),
    ("catalyst_classification", "UPDATE catalyst_classification SET label = 'guidance_cut' WHERE id = (SELECT min(id) FROM catalyst_classification "
     "WHERE label <> 'guidance_cut')", "db.catalyst_classification"),
    ("source_observation", "UPDATE source_observation SET value = '{\"date\":\"1999-01-01\"}'::jsonb WHERE id = (SELECT min(id) FROM source_observation)",
     "db.source_observation"),
]


@pytest.mark.parametrize("table,sql,key", MUTATIONS, ids=[f"{m[0]}-{i}" for i, m in enumerate(MUTATIONS)])
def test_mutated_input_fails_hash_verification(fresh_denv, table, sql, key):
    env = fresh_denv
    assert env.build().verification.ok
    mutate(env, table, sql)
    with pytest.raises(R.BuildFailed) as e:
        env.build()
    failed = [c.key for c in e.value.verification.failures()]
    assert failed == [key], failed
    assert any("input_hash_mismatch" in p for p in e.value.problems)
    assert "MISMATCH" in e.value.audit.text


def test_mutated_history_authored_afresh_changes_the_dataset_hash(fresh_denv):
    env = fresh_denv
    before = env.build()
    mutate(env, "forward_return_label", MUTATIONS[0][1])
    mutated = author_manifest(env.conn, dataset_version="v-mutated")
    assert mutated.manifest_hash != env.manifest_hash
    assert mutated.document["input_hashes"]["db.forward_return_label"] != env.manifest.document["input_hashes"]["db.forward_return_label"]
    RS.insert_manifest(env.conn.cursor(), mutated)
    env.conn.commit()
    after = R.build_dataset(env.conn, mutated.manifest_hash, CAL, code=env.code)
    assert after.verification.ok
    assert after.dataset_hash != before.dataset_hash
    assert len(after.rows) == len(before.rows)
    changed = [(a, b) for a, b in zip(sorted(before.rows, key=lambda r: (r["observation_key"], r["horizon_sessions"])),
                                      sorted(after.rows, key=lambda r: (r["observation_key"], r["horizon_sessions"]))) if a != b]
    assert len(changed) == 1 and changed[0][0]["raw_return"] != changed[0][1]["raw_return"]


def test_unrelated_manifest_edits_change_the_dataset_identity_not_the_inputs(denv):
    base = denv.build()
    hashes = dict(denv.manifest.document["input_hashes"])
    other = M.build_manifest(make_spec(hashes, config(min_sample=7), dataset_version="v-min7"), now=NOW)
    cur = denv.conn.cursor()
    RS.insert_manifest(cur, other)
    denv.conn.commit()
    b = R.build_dataset(denv.conn, other.manifest_hash, CAL, code=denv.code)
    assert b.dataset_hash != base.dataset_hash
    assert b.verification.ok and b.rows == base.rows
    assert b.report["methodology"]["min_sample"] == 7
