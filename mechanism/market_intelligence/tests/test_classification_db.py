"""Migration 28 + classification_store against real Postgres: a classification pins the immutable fact_hash, is append-only, supersedes in a
linear chain, is read AS OF a time, and can never touch the source fact."""
import os
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.errors
import pytest

import mi_fixtures
from mi_fixtures import ROOT, conn, connect, mi_env  # noqa: F401
from market_intelligence import classification_store as cs
from market_intelligence import filing_contract as F
from market_intelligence import store

UTC = timezone.utc
RULE_ID = {"rule_id": "items_rule.v1"}
MODEL_ID = {"model_id": "m-1", "prompt_hash": "a" * 64}


def _fact(acc="0001234567-26-000123", items="2.02,9.01"):
    return F.build_fact(dict(accession=acc, cik=1234567, form_type="8-K", filing_date=date(2026, 9, 18),
                             accepted_at=datetime(2026, 9, 18, 20, 5, 11, tzinfo=UTC), items=items))


def _event(cur, fact):
    w = store.append_event_revision(cur, F.to_event_draft(fact, symbol="AAA", symbol_basis="test"))
    return w.id, f"sec|{fact.accession}"


def _fails(cur, fn, exc=psycopg2.Error, match=None):
    cur.execute("SAVEPOINT s")
    with pytest.raises(exc) as e:
        fn()
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(e.value), str(e.value)


def _cls(fact, **kw):
    base = dict(fact_accession=fact.accession, fact_hash=fact.content_hash(), classifier="items_rule", classifier_version="v1",
                method="rule", label="earnings_release", evidence={"items": ["2.02"]})
    base.update(kw)
    return F.CatalystClassification(**base)


def test_append_is_idempotent_on_revision_classifier_version_and_stamped_by_the_database(conn):
    cur = conn.cursor()
    f = _fact()
    rid, ek = _event(cur, f)
    a = cs.append_classification(cur, _cls(f), RULE_ID, rid, ek, "ref")
    b = cs.append_classification(cur, _cls(f), RULE_ID, rid, ek, "ref")
    assert a.created and not b.created and a.id == b.id
    (row,) = cs.history(cur, rid)
    assert row["classified_at"] > datetime(2020, 1, 1, tzinfo=UTC) and row["fact_hash"] == f.content_hash() and row["confidence"] is None


def test_a_classification_cannot_be_pinned_to_a_different_fact_or_event(conn):
    cur = conn.cursor()
    f, g = _fact(), _fact(acc="0001234567-26-000999", items="5.02")
    rid, ek = _event(cur, f)
    _fails(cur, lambda: cs.append_classification(cur, _cls(f, fact_hash=g.content_hash()), RULE_ID, rid, ek, "ref"),
           psycopg2.errors.IntegrityConstraintViolation, "do not match")
    _fails(cur, lambda: cs.append_classification(cur, _cls(f), RULE_ID, rid, "sec|other", "ref"),
           psycopg2.errors.IntegrityConstraintViolation, "do not match")
    _fails(cur, lambda: cs.append_classification(cur, _cls(f), RULE_ID, 999999, ek, "ref"), psycopg2.Error)        # no such revision


def test_a_revision_without_a_fact_hash_cannot_be_classified(conn):
    cur = conn.cursor()
    from market_intelligence import events as ev
    d = ev.EventDraft(event_key="manual|1", symbol="AAA", event_type="earnings_scheduled", event_time=date(2026, 10, 30), status="scheduled",
                      source="manual", source_ref="r", known_at_basis="ingested", pit_grade="B", provenance="observed")
    w = store.append_event_revision(cur, d)
    f = _fact()
    _fails(cur, lambda: cs.append_classification(cur, _cls(f), RULE_ID, w.id, "manual|1", "ref"),
           psycopg2.errors.IntegrityConstraintViolation, "no fact_hash")


def test_the_classifier_identity_is_typed_by_method(conn):
    cur = conn.cursor()
    f = _fact()
    rid, ek = _event(cur, f)
    with pytest.raises(F.FilingContractError):
        cs.append_classification(cur, _cls(f), {}, rid, ek, "ref")
    _fails(cur, lambda: cs.append_classification(cur, _cls(f), {"model_id": "m"}, rid, ek, "ref"), psycopg2.errors.CheckViolation)      # rule needs rule_id
    _fails(cur, lambda: cs.append_classification(cur, _cls(f, classifier="llm", method="model", confidence=0.7), {"model_id": "m"}, rid, ek, "ref"),
           psycopg2.errors.CheckViolation)                                                                                                # model needs prompt_hash
    cs.append_classification(cur, _cls(f, classifier="llm", method="model", confidence=0.7), MODEL_ID, rid, ek, "ref")
    cs.append_classification(cur, _cls(f, classifier="analyst", method="human", confidence=1.0), {"reviewer": "dp"}, rid, ek, "ref")


def test_supersession_is_a_linear_chain_inside_one_revision(conn):
    cur = conn.cursor()
    f, g = _fact(), _fact(acc="0001234567-26-000999", items="5.02")
    rid, ek = _event(cur, f)
    rid2, ek2 = _event(cur, g)
    v1 = cs.append_classification(cur, _cls(f), RULE_ID, rid, ek, "ref")
    v2 = cs.append_classification(cur, _cls(f, classifier_version="v2", label="results_of_operations", supersedes=v1.id), RULE_ID, rid, ek, "ref")
    assert v2.created
    _fails(cur, lambda: cs.append_classification(cur, _cls(f, classifier_version="v3", supersedes=v1.id), RULE_ID, rid, ek, "ref"),
           psycopg2.errors.UniqueViolation)                                                       # v1 already has a successor: no fork
    other = cs.append_classification(cur, _cls(g), RULE_ID, rid2, ek2, "ref")
    _fails(cur, lambda: cs.append_classification(cur, _cls(f, classifier_version="v4", supersedes=other.id), RULE_ID, rid, ek, "ref"),
           psycopg2.errors.IntegrityConstraintViolation, "not a classification of revision")      # cannot supersede another revision's row
    assert [r["classifier_version"] for r in cs.history(cur, rid)] == ["v1", "v2"]


def test_as_of_shows_only_what_was_believed_then_and_the_superseded_row_stays_current_until_replaced(conn):
    cur = conn.cursor()
    f = _fact()
    rid, ek = _event(cur, f)
    v1 = cs.append_classification(cur, _cls(f), RULE_ID, rid, ek, "ref")
    cur.execute("SELECT classified_at FROM catalyst_classification WHERE id = %s", (v1.id,))
    t1 = cur.fetchone()[0]
    v2 = cs.append_classification(cur, _cls(f, classifier_version="v2", label="other", supersedes=v1.id), RULE_ID, rid, ek, "ref")
    assert cs.classifications_as_of(cur, t1 - timedelta(seconds=1)) == []                          # nothing existed yet
    assert [r["id"] for r in cs.classifications_as_of(cur, t1)] == [v1.id]                         # v2 did not exist yet at t1
    assert [r["id"] for r in cs.classifications_as_of(cur, t1 + timedelta(days=1))] == [v2.id]
    with pytest.raises(ValueError):
        cs.classifications_as_of(cur, datetime(2026, 1, 1))


def test_classifications_are_immutable_and_do_not_alter_the_fact(conn):
    cur = conn.cursor()
    f = _fact()
    rid, ek = _event(cur, f)
    cs.append_classification(cur, _cls(f), RULE_ID, rid, ek, "ref")
    cur.execute("SELECT payload_hash, payload FROM market_event_revision WHERE id = %s", (rid,))
    before = cur.fetchone()
    for sql in ("UPDATE catalyst_classification SET label = 'x'", "DELETE FROM catalyst_classification", "TRUNCATE catalyst_classification"):
        _fails(cur, lambda s=sql: cur.execute(s), psycopg2.errors.IntegrityConstraintViolation, "append-only")
    cur.execute("SELECT payload_hash, payload FROM market_event_revision WHERE id = %s", (rid,))
    assert cur.fetchone() == before and before[1]["fact_hash"] == f.content_hash()


def test_migration_28_is_reapplyable_and_refuses_a_foreign_table(conn):
    cur = conn.cursor()
    with open(os.path.join(ROOT, "mechanism", "add_catalyst_classification_table.sql"), encoding="utf-8") as fh:
        sql = fh.read()
    cur.execute(sql)
    cur.execute("DROP TABLE catalyst_classification")
    cur.execute("CREATE TABLE catalyst_classification (x int)")
    _fails(cur, lambda: cur.execute(sql), match="migration 28 refused")
