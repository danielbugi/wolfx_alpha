"""Migration 22 against real Postgres in a throwaway schema: tables, identity keys, session/shape CHECKs, DB-enforced
immutability, and the audited maintenance hatch."""
import os

import psycopg2
import psycopg2.errors
import pytest

from conftest import ROOT, SESSION

MIGRATION = os.path.join(ROOT, "mechanism", "add_research_observation_tables.sql")
IMMUTABLE = ["candidate_observation", "feature_snapshot", "feature_set_registry"]


def _fails(conn, sql, params=None, match=None):
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(exc.value), str(exc.value)
    return exc.value


def test_all_tables_exist_and_migration_is_reapplyable(conn):
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")
    names = {r[0] for r in cur.fetchall()}
    assert {"feature_set_registry", "feature_snapshot", "candidate_observation", "candidate_capture_run",
            "research_maintenance_log", "research_maintenance_audit", "research_maintenance_session",
            "research_capture_activation"} <= names
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    cur.execute(sql)  # IF NOT EXISTS / existence-checked: a second apply is a no-op
    # Scoped to this schema: on the CI database the migration is also applied in `public`.
    cur.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_proc p ON p.oid = t.tgfoid AND left(p.proname, 9) = 'research_' "
                "WHERE n.nspname = current_schema() AND NOT t.tgisinternal AND t.tgenabled = 'A'")
    assert cur.fetchone()[0] == 14  # 7 tables x (row/update-delete + truncate)


def test_migration_writes_no_rows(conn):
    cur = conn.cursor()
    for t in ("feature_set_registry", "feature_snapshot", "candidate_observation", "candidate_capture_run",
              "research_maintenance_log", "research_maintenance_audit", "research_maintenance_session",
              "research_capture_activation"):
        cur.execute(f"SELECT count(*) FROM {t}")
        assert cur.fetchone()[0] == 0, t


def test_identity_keys_reject_duplicates(conn, seed):
    snap = seed.snapshot("AAA")
    run = seed.run()
    seed.observation(snap, run, "AAA")
    _fails(conn, "INSERT INTO feature_snapshot (symbol, session_date, feature_set_version, bar_date, open, high, low, "
                 "close, bars_available, snapshot_status, features, manifest_hash, content_hash) "
                 "VALUES ('AAA', %s, 't0_v1', %s, 1, 1, 1, 1, 1, 'complete', '{}', %s, %s)",
           (SESSION, SESSION, "a" * 64, "b" * 64), match="feature_snapshot_identity_key")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        seed.observation(snap, run, "AAA")


def test_same_symbol_opposite_direction_is_a_distinct_candidate(seed):
    snap = seed.snapshot("AAA")
    run = seed.run()
    seed.observation(snap, run, "AAA", direction=1)
    seed.observation(snap, run, "AAA", direction=-1, signal_type="bearish_breakout")


def test_two_strategies_can_share_one_snapshot(conn, seed):
    snap = seed.snapshot("AAA")
    run = seed.run()
    cur = conn.cursor()
    cur.execute("INSERT INTO strategies (strategy_key, strategy_version, description) "
                "VALUES ('other_strategy', 'v1', 'Other') RETURNING id")
    other = cur.fetchone()[0]
    seed.observation(snap, run, "AAA")
    seed.observation(snap, run, "AAA", strategy_id=other)


def test_snapshot_bar_must_be_the_session(conn, seed):
    seed.registry()
    _fails(conn, "INSERT INTO feature_snapshot (symbol, session_date, feature_set_version, bar_date, open, high, low, "
                 "close, bars_available, snapshot_status, features, manifest_hash, content_hash) VALUES "
                 "('BAR', '2099-01-15', 't0_v1', '2099-01-14', 1, 1, 1, 1, 1, 'complete', '{}', %s, %s)",
           ("a" * 64, "b" * 64), match="feature_snapshot_bar_is_session")


def test_observation_bar_must_be_the_session(seed):
    snap, run = seed.snapshot("AAA"), seed.run()
    with pytest.raises(psycopg2.errors.CheckViolation):
        seed.observation(snap, run, "AAA", bar_date="2099-01-14")


def test_session_source_must_be_explicit(conn, seed):
    seed.registry()
    _fails(conn, "INSERT INTO candidate_capture_run (strategy_id, strategy_version, session_date, feature_set_version, "
                 "session_source) VALUES (%s, 'v1', %s, 't0_v1', 'inferred')", (seed.strategy_id(), SESSION))
    snap, run = seed.snapshot("BBB"), seed.run()
    with pytest.raises(psycopg2.errors.CheckViolation):
        seed.observation(snap, run, "BBB", session_source="inferred")


def test_guard_reasons_exactly_when_guard_failed(seed):
    snap, run = seed.snapshot("AAA"), seed.run()
    with pytest.raises(psycopg2.errors.CheckViolation):
        seed.observation(snap, run, "AAA", passed_guard=False, guard_reasons=None)


def test_guard_reasons_on_a_pass_is_rejected(seed):
    snap, run = seed.snapshot("BBB"), seed.run()
    with pytest.raises(psycopg2.errors.CheckViolation):
        seed.observation(snap, run, "BBB", passed_guard=True, guard_reasons=["illiquid_dollar_volume"])


def test_guard_rejection_with_reasons_is_accepted(seed):
    snap, run = seed.snapshot("AAA"), seed.run()
    seed.observation(snap, run, "AAA", passed_guard=False, guard_reasons=["illiquid_dollar_volume"])


def test_guard_not_evaluated_is_null_not_true(seed):
    snap, run = seed.snapshot("AAA"), seed.run()
    seed.observation(snap, run, "AAA", passed_guard=None, guard_reasons=None)


def test_ml_score_only_when_scored(seed):
    snap, run = seed.snapshot("AAA"), seed.run()
    with pytest.raises(psycopg2.errors.CheckViolation):
        seed.observation(snap, run, "AAA", ml_status="not_processed", ml_score=0)


def test_ml_score_accepted_when_scored(seed):
    snap, run = seed.snapshot("BBB"), seed.run()
    seed.observation(snap, run, "BBB", ml_status="scored", ml_score=61.5)


def test_snapshot_status_must_agree_with_missing_features(seed):
    with pytest.raises(psycopg2.errors.CheckViolation):
        seed.snapshot("AAA", snapshot_status="complete", missing_features=["rsi_14"])


def test_partial_snapshot_must_name_what_is_missing(seed):
    with pytest.raises(psycopg2.errors.CheckViolation):
        seed.snapshot("BBB", snapshot_status="partial", missing_features=[])


def test_partial_snapshot_with_named_gaps_is_accepted(seed):
    seed.snapshot("CCC", snapshot_status="partial", missing_features=["rsi_14"])


def test_observation_requires_a_snapshot_and_a_run(seed):
    run = seed.run()
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        seed.observation(snapshot_id=999999, run_id=run, symbol="AAA")


@pytest.mark.parametrize("table,col", [("candidate_observation", "symbol"), ("feature_snapshot", "symbol"),
                                       ("feature_set_registry", "description")])
def test_update_and_delete_are_rejected(conn, seed, table, col):
    seed.observation(symbol="AAA")
    _fails(conn, f"UPDATE {table} SET {col} = 'ZZZ'", match="immutable")
    _fails(conn, f"DELETE FROM {table}", match="immutable")
    cur = conn.cursor()
    cur.execute(f"SELECT count(*) FROM {table}")
    assert cur.fetchone()[0] == 1


def test_truncate_is_rejected(conn, seed):
    seed.observation(symbol="AAA")
    # the immutable tables are FK-referenced (signal_ledger lineage), so name every referrer in the statement
    _fails(conn, "TRUNCATE candidate_observation, signal_ledger", match="immutable")
    _fails(conn, "TRUNCATE feature_snapshot, candidate_observation, feature_set_registry, signal_ledger")
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM candidate_observation")
    assert cur.fetchone()[0] == 1


def test_capture_run_stays_mutable(conn, seed):
    run = seed.run()
    cur = conn.cursor()
    cur.execute("UPDATE candidate_capture_run SET status = 'complete', run_finished_at = NOW(), candidates = 3, "
                "captured = 3, already_captured = 0, stale_skipped = 0, snapshot_skipped = 0, invalid_skipped = 0, "
                "guard_not_evaluated = 0 WHERE id = %s", (run,))


def test_maintenance_log_cannot_be_updated_to_forge_an_approval(conn, seed):
    ticket = seed.ticket(approved=False)
    _fails(conn, "UPDATE research_maintenance_log SET approved_by = 'x', approved_at = NOW(), "
                 "expires_at = NOW() + INTERVAL '1 hour', opened_by = 'x' WHERE id = %s", (ticket,), match="write-once")
