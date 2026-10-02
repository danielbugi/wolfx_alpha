"""Migrations 24 / 25 against real Postgres: idempotent, atomic collision refusal, CHECKs, ENABLE ALWAYS immutability (even for the
owner and with replication role set), and the database-stamped ingestion time."""
import os

import psycopg2
import psycopg2.errors
import pytest

import mi_samples as S
from conftest import MIGRATIONS, ROOT
from market_intelligence import store

TABLES = ["universe_snapshot", "market_snapshot", "sector_snapshot", "market_event", "market_event_revision"]


def _sql(name):
    with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
        return fh.read()


def _fails(cur, sql, params=None, exc=psycopg2.Error, match=None):
    cur.execute("SAVEPOINT s")
    with pytest.raises(exc) as e:
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(e.value), str(e.value)
    return e.value


def test_tables_exist_and_both_migrations_are_reapplyable(conn):
    cur = conn.cursor()
    for name in MIGRATIONS:
        cur.execute(_sql(name))                         # second application: no error, no change
    cur.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE c.relname = ANY(%s) AND c.relnamespace = current_schema()::regnamespace AND NOT t.tgisinternal AND t.tgenabled = 'A'",
                (TABLES,))
    assert cur.fetchone()[0] == 11                      # 3x2 snapshot guards + 2x2 event guards + 1 stamp


def _replica_if_allowed(cur):
    """Also try with session_replication_role=replica (an ordinary trigger is skipped by it, an ENABLE ALWAYS one is not). Needs superuser;
    where the test role is not one the plain statement is still checked."""
    cur.execute("SAVEPOINT r")
    try:
        cur.execute("SET LOCAL session_replication_role = replica")
    except psycopg2.errors.InsufficientPrivilege:
        cur.execute("ROLLBACK TO SAVEPOINT r")


@pytest.mark.parametrize("sql", [
    "UPDATE universe_snapshot SET n_symbols = n_symbols",
    "DELETE FROM universe_snapshot",
    "TRUNCATE universe_snapshot, market_snapshot, sector_snapshot",
    "UPDATE market_snapshot SET regime_state = 'NEUTRAL'",
    "DELETE FROM market_snapshot",
    "TRUNCATE market_snapshot, sector_snapshot",
    "UPDATE sector_snapshot SET sector = 'x'",
    "DELETE FROM sector_snapshot",
    "TRUNCATE sector_snapshot",
])
def test_snapshots_are_immutable_even_for_the_owner_and_with_replication_role(conn, sql):
    cur = conn.cursor()
    out = store.write_session(cur, S.regime(), S.relative_strength(), "observed", "unit-test", "test@abc")
    assert out["sectors_written"] == 3
    _replica_if_allowed(cur)
    _fails(cur, sql, exc=psycopg2.errors.IntegrityConstraintViolation, match="append-only")


def test_events_are_immutable_but_appendable(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO market_event (event_key, symbol, event_type) VALUES ('k1','AAA','earnings_scheduled')")
    _replica_if_allowed(cur)
    for sql in ("UPDATE market_event SET symbol = 'BBB'", "DELETE FROM market_event", "TRUNCATE market_event, market_event_revision"):
        _fails(cur, sql, exc=psycopg2.errors.IntegrityConstraintViolation, match="append-only")


def _rev(**kw):
    row = dict(event_key="k1", revision=1, event_time="2026-10-20", known_at_basis="ingested", source="s", source_ref="r1",
               pit_grade="B", status="scheduled", payload_hash="0" * 64, provenance="observed")
    row.update(kw)
    cols = ", ".join(row)
    return f"INSERT INTO market_event_revision ({cols}) VALUES ({', '.join(['%s'] * len(row))})", list(row.values())


def _ev(cur):
    cur.execute("INSERT INTO market_event (event_key, symbol, event_type) VALUES ('k1','AAA','earnings_scheduled')")


def test_database_stamps_ingested_at_and_a_caller_cannot_backdate_it(conn):
    cur = conn.cursor()
    _ev(cur)
    sql, args = _rev(ingested_at="2000-01-01T00:00:00Z", known_at="2000-01-01T00:00:00Z")
    cur.execute(sql + " RETURNING ingested_at, known_at, now()", args)
    ingested, known, now = cur.fetchone()
    assert ingested >= now and known == ingested        # clock_timestamp at insert, not the caller's 2000-01-01


def test_known_at_is_null_for_an_unknown_basis_and_never_fabricated(conn):
    cur = conn.cursor()
    _ev(cur)
    sql, args = _rev(known_at_basis="unknown", pit_grade="X")
    cur.execute(sql + " RETURNING known_at", args)
    assert cur.fetchone()[0] is None
    for kw in (dict(known_at_basis="unknown", pit_grade="X", known_at="2026-10-01T00:00:00Z", revision=2),
               dict(known_at_basis="unknown", pit_grade="B", revision=3),                    # grade X <=> unknown
               dict(known_at_basis="ingested", pit_grade="X", revision=4)):
        s, a = _rev(**kw)
        _fails(cur, s, a, exc=psycopg2.errors.CheckViolation)


def test_vendor_published_requires_published_at_equal_to_known_at(conn):
    cur = conn.cursor()
    _ev(cur)
    s, a = _rev(known_at_basis="vendor_published", pit_grade="A")
    _fails(cur, s, a, exc=psycopg2.errors.CheckViolation)
    s, a = _rev(known_at_basis="vendor_published", pit_grade="A", published_at="2026-10-01T00:00:00Z", known_at="2026-10-02T00:00:00Z")
    _fails(cur, s, a, exc=psycopg2.errors.CheckViolation)
    s, a = _rev(known_at_basis="vendor_published", pit_grade="A", published_at="2026-10-01T00:00:00Z", known_at="2026-10-01T00:00:00Z")
    cur.execute(s, a)


def test_known_at_cannot_be_in_the_future_of_ingestion(conn):
    cur = conn.cursor()
    _ev(cur)
    s, a = _rev(known_at_basis="vendor_published", pit_grade="A", published_at="2999-01-01T00:00:00Z", known_at="2999-01-01T00:00:00Z")
    _fails(cur, s, a, exc=psycopg2.errors.CheckViolation)


def test_actuals_need_a_reported_status_and_revisions_are_unique_and_immutable(conn):
    cur = conn.cursor()
    _ev(cur)
    s, a = _rev(eps_actual=1.2)
    _fails(cur, s, a, exc=psycopg2.errors.CheckViolation)
    s, a = _rev(status="reported", eps_actual=1.2, eps_estimate=1.0)
    cur.execute(s, a)
    s, a = _rev(status="revised", eps_actual=1.3)
    _fails(cur, s, a, exc=psycopg2.errors.UniqueViolation)           # (event_key, revision) already exists
    _fails(cur, "UPDATE market_event_revision SET status = 'revised'", exc=psycopg2.errors.IntegrityConstraintViolation)


def test_a_pre_existing_foreign_relation_makes_the_migration_refuse_atomically(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE market_event_revision; DROP TABLE market_event; DROP FUNCTION research_market_event_stamp()")
    cur.execute("CREATE TABLE market_event (x int)")
    with pytest.raises(psycopg2.Error, match="not a migration-25 object"):
        cur.execute(_sql("add_market_event_tables.sql"))
    conn.rollback()
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('market_event_revision') IS NOT NULL")      # the rollback restored the pre-test state: nothing half-created
    assert cur.fetchone()[0] is True


def test_migration_25_refuses_without_migration_24(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE market_event_revision; DROP TABLE market_event; DROP FUNCTION research_market_event_stamp()")
    cur.execute("DROP TABLE sector_snapshot; DROP TABLE market_snapshot; DROP TABLE universe_snapshot")
    cur.execute("DROP FUNCTION research_market_guard()")
    with pytest.raises(psycopg2.Error, match="requires migration 24"):
        cur.execute(_sql("add_market_event_tables.sql"))


def test_a_weakened_guard_trigger_makes_the_migration_refuse(conn):
    cur = conn.cursor()
    cur.execute("ALTER TABLE market_snapshot DISABLE TRIGGER market_snapshot_immutable_row")
    with pytest.raises(psycopg2.Error, match="ENABLE ALWAYS"):
        cur.execute(_sql("add_market_snapshot_tables.sql"))


def test_unavailable_regime_cannot_carry_a_score_and_nulls_are_not_zeroes(conn):
    cur = conn.cursor()
    out = store.write_session(cur, S.regime({}), S.relative_strength(), "observed", "unit-test", "test@abc")
    cur.execute("SELECT regime_state, regime_score, regime_strength, regime_agreement FROM market_snapshot WHERE id = %s", (out["market"].id,))
    assert cur.fetchone() == ("UNAVAILABLE", None, None, None)
    cur.execute("SELECT universe_snapshot_id FROM market_snapshot WHERE id = %s", (out["market"].id,))
    uid = cur.fetchone()[0]
    _fails(cur, "INSERT INTO market_snapshot (session_date, provenance, feature_set_version, regime_model_version, rs_model_version, "
                "regime_state, regime_score, regime_present_weight, regime_components, coverage, source, universe_snapshot_id, content_hash, "
                "code_ref) VALUES (%s,'observed','z','a','b','UNAVAILABLE',0.1,0.5,'{}','{}','s',%s,%s,'c')",
           (S.T, uid, "0" * 64), exc=psycopg2.errors.CheckViolation)


def test_sector_checks_refuse_a_return_on_too_few_members_and_a_rank_without_a_return(conn):
    cur = conn.cursor()
    out = store.write_session(cur, S.regime(), S.relative_strength(), "observed", "unit-test", "test@abc")
    mid = out["market"].id
    base = ("INSERT INTO sector_snapshot (session_date, provenance, feature_set_version, sector, n_members, n_valid_5, n_valid_20, n_valid_60, "
            "n_excluded_5, n_excluded_20, n_excluded_60, sec_ret_20, rank_20, market_snapshot_id) "
            "VALUES (%s,'observed','mi_v1','Tiny',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
    _fails(cur, base, (S.T, 4, 4, 4, 4, 0, 0, 0, 1.0, None, mid), exc=psycopg2.errors.CheckViolation)      # return on 4 members
    _fails(cur, base, (S.T, 6, 6, 6, 6, 0, 0, 0, None, 1, mid), exc=psycopg2.errors.CheckViolation)        # rank without a return
    _fails(cur, base, (S.T, 6, 6, 6, 6, 1, 0, 0, None, None, mid), exc=psycopg2.errors.CheckViolation)     # excluded != members - valid


def test_a_correction_is_a_new_feature_set_version_not_an_edit(conn):
    cur = conn.cursor()
    a = store.write_session(cur, S.regime(), S.relative_strength(), "observed", "unit-test", "test@abc")
    b = store.write_session(cur, S.regime(), S.relative_strength(), "observed", "unit-test", "test@abc", feature_set_version="mi_v2")
    assert a["market"].created and b["market"].created and a["market"].id != b["market"].id
    assert b["universe"].created is False and b["universe"].id == a["universe"].id    # the observed universe is reused, never rewritten
