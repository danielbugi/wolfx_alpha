"""The leakage audit must catch what it claims to: each test corrupts an otherwise valid assembly (or the raw inputs) and checks that the
INDEPENDENT audit raises the specific fatal finding. A clean assembly of the same world must pass, so these are real positives."""
import dataclasses
from datetime import timedelta

import pytest

from dataset_world import CAL, CUTOFF, MAT, denv  # noqa: F401
from research.lab import dataset_assemble as A
from research.lab import dataset_audit as AU
from research.lab import dataset_contract as C


@pytest.fixture(scope="module")
def parts(denv):
    manifest, cfg, raw = denv.parts()
    assembly = A.assemble(manifest, cfg, raw)
    verification = C.verify_inputs(manifest.document, cfg, manifest.calendar, cfg.universe_members,
                                   {s: C.fingerprint(s, raw[s], CUTOFF) for s in C.enabled_db_sources(cfg)})
    return manifest, cfg, raw, assembly, verification


def run(parts, rows=None, raw=None, assembly=None, verification=None):
    manifest, cfg, raw0, asm, ver = parts
    a = assembly or (dataclasses.replace(asm, rows=tuple(rows)) if rows is not None else asm)
    return AU.audit(manifest, cfg, raw if raw is not None else raw0, a, verification or ver)


def codes(audit):
    return {f["code"] for f in audit.document["fatal"]}


def find(parts, pred):
    rows = [dict(r) for r in parts[3].rows]
    i = next(i for i, r in enumerate(rows) if pred(r))
    return rows, i


def test_the_untampered_assembly_passes(parts):
    audit = run(parts)
    assert audit.ok and codes(audit) == set()


def test_candidate_stamped_after_its_deadline_is_a_pit_violation(parts):
    rows, i = find(parts, lambda r: True)
    rows[i]["candidate_available_at"] = C.decision_deadline(rows[i]["t0_session"], parts[1].availability_grace_days)
    assert "pit_violation" in codes(run(parts, rows))


def test_any_stamp_after_the_cutoff_is_a_future_observation(parts):
    rows, i = find(parts, lambda r: r["label_available_at"] is not None)
    rows[i]["label_available_at"] = CUTOFF + timedelta(days=1)
    assert "future_observation" in codes(run(parts, rows))


def test_a_dated_fact_after_t0_is_a_future_observation(parts):
    rows, i = find(parts, lambda r: r["catalyst__state"] == "catalyst_known")
    rows[i]["catalyst_latest_event_time"] = rows[i]["t0_session"] + timedelta(days=3)
    assert "future_observation" in codes(run(parts, rows))


def test_an_immature_or_inconsistent_label_is_a_maturity_violation(parts):
    rows, i = find(parts, lambda r: r["label_status"] == "final")
    rows[i]["horizon_session"] = CAL[700]
    assert codes(run(parts, rows)) & {"label_maturity_violation", "label_invalid"}
    rows, i = find(parts, lambda r: r["label_status"] == "void")
    rows[i]["directional_return"] = 0.01
    assert codes(run(parts, rows)) & {"label_maturity_violation", "label_invalid", "masked_value_leak"}


def test_a_row_in_the_wrong_split_is_a_boundary_violation(parts):
    rows, i = find(parts, lambda r: r["split"] == "train")
    rows[i]["split"] = "test"
    assert "split_boundary_violation" in codes(run(parts, rows))
    rows, i = find(parts, lambda r: r["split"] == "validation")
    rows[i]["split"] = "train"
    assert "split_boundary_violation" in codes(run(parts, rows))


def test_a_label_window_crossing_the_window_end_is_a_boundary_violation(parts):
    rows, i = find(parts, lambda r: r["split"] == "train" and r["horizon_sessions"] == 5)
    rows[i]["t0_session"] = CAL[198]
    rows[i]["horizon_session"] = CAL[203]
    assert codes(run(parts, rows)) & {"split_boundary_violation", "embargo_violation", "label_invalid"}


def test_a_masked_state_that_still_carries_a_value_is_a_leak(parts):
    rows, i = find(parts, lambda r: r["market__state"] == "late")
    rows[i]["regime_state"] = "RISK_ON"
    assert "masked_value_leak" in codes(run(parts, rows))
    rows, i = find(parts, lambda r: r["rs__state"] == "absent")
    rows[i]["rs_percentile"] = 12.0
    assert "masked_value_leak" in codes(run(parts, rows))


def test_a_reconstructed_input_under_the_exclude_policy_is_fatal(parts):
    rows, i = find(parts, lambda r: r["market__state"] == "ok")
    rows[i]["market__provenance"] = "reconstructed"
    assert codes(run(parts, rows)) & {"reconstructed_policy_violation", "selection_mismatch"}


def test_a_value_that_is_not_what_was_known_is_a_selection_mismatch(parts):
    rows, i = find(parts, lambda r: r["market__state"] == "ok")
    rows[i]["regime_score"] = 123.0
    assert "selection_mismatch" in codes(run(parts, rows))
    rows, i = find(parts, lambda r: r["rs__state"] == "ok")
    rows[i]["rs_percentile"] = (rows[i]["rs_percentile"] + 1.0) % 100
    assert "selection_mismatch" in codes(run(parts, rows))
    rows, i = find(parts, lambda r: r["catalyst__state"] == "catalyst_known")
    rows[i]["catalyst__state"] = "none_observed"
    assert "selection_mismatch" in codes(run(parts, rows))


def test_tampered_breadth_sector_and_catalyst_values_are_caught(parts):
    rows, i = find(parts, lambda r: r["breadth_sma50_pct"] is not None)
    rows[i]["breadth_sma50_pct"] += 1.0
    assert "selection_mismatch" in codes(run(parts, rows))
    rows, i = find(parts, lambda r: r["sector_ret_20"] is not None)
    rows[i]["sector_ret_20"] += 0.01
    assert "selection_mismatch" in codes(run(parts, rows))
    rows, i = find(parts, lambda r: r["catalyst_labels"] is not None)
    rows[i]["catalyst_labels"] = "guidance_cut|invented"
    assert "selection_mismatch" in codes(run(parts, rows))
    rows, i = find(parts, lambda r: r["catalyst__state"] == "none_observed")
    rows[i]["catalyst_n_events"] = 2
    assert "selection_mismatch" in codes(run(parts, rows))


def test_a_wrong_sector_name_state_is_caught_from_the_raw_candidate(parts):
    rows, i = find(parts, lambda r: r["sector"] is not None)
    rows[i]["sector"] = "Utilities"
    assert codes(run(parts, rows)) & {"selection_mismatch", "masked_value_leak"}
    # the raw candidate's feature snapshot was stamped after the deadline: the assembled row must not carry a sector name
    manifest, cfg, raw, asm, ver = parts
    row = next(r for r in asm.rows if r["sector"] is not None)
    tainted = {k: [dict(r) for r in v] for k, v in raw.items()}
    cand = next(c for c in tainted["candidates"] if (c["symbol"], c["session_date"], c["direction"]) == (row["symbol"], row["t0_session"], row["direction"]))
    cand["fs_captured_at"] = C.decision_deadline(row["t0_session"], cfg.availability_grace_days)
    assert "masked_value_leak" in codes(run(parts, raw=tainted))


def test_row_accounting_catches_lost_and_duplicated_rows(parts):
    assert "row_accounting" in codes(run(parts, list(parts[3].rows)[:-1]))
    dup = [dict(r) for r in parts[3].rows]
    dup[-1] = dict(dup[0])
    assert "row_accounting" in codes(run(parts, dup))


def test_a_hash_mismatch_is_fatal_in_the_audit_too(parts):
    manifest, cfg, raw, asm, ver = parts
    bad = C.InputVerification(tuple(dataclasses.replace(c, status="MISMATCH", actual="0" * 64) if c.key == "db.market_snapshot" else c for c in ver.checks))
    audit = run(parts, verification=bad)
    assert "input_hash_mismatch" in codes(audit) and audit.document["verdict"] == "FAIL" and "MISMATCH" in audit.text


def test_raw_rows_with_a_stamp_after_the_cutoff_are_rejected_by_the_audit(parts):
    manifest, cfg, raw, asm, ver = parts
    tainted = {k: [dict(r) for r in v] for k, v in raw.items()}
    tainted["market"][0]["captured_at"] = CUTOFF + timedelta(days=1)
    assert not run(parts, raw=tainted).ok


def test_fatal_findings_are_listed_with_counts_and_examples(parts):
    rows, i = find(parts, lambda r: r["market__state"] == "late")
    rows[i]["regime_state"] = "RISK_ON"
    audit = run(parts, rows)
    leak = next(f for f in audit.document["fatal"] if f["code"] == "masked_value_leak")
    assert leak["count"] >= 1 and leak["examples"]
    assert "FATAL VIOLATIONS" in audit.text and "verdict FAIL" in audit.text
    assert audit.document["verdict"] == "FAIL"
    assert audit.audit_hash != run(parts).audit_hash
