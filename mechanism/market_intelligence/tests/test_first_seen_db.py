"""Migration 27 + first_seen_store against real Postgres (throwaway schema): DB-stamped time, hash-chain triggers, change-only writes,
as-of reads, immutability (also for the owner), and idempotent / collision-safe application."""
import os
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.errors
import pytest

import mi_fixtures
from mi_fixtures import ROOT, conn, connect, mi_env  # noqa: F401  (explicit fixtures: no top-level `conftest` name)
from market_intelligence import first_seen as fs
from market_intelligence import first_seen_store as store

MIG = "add_source_observation_tables.sql"
INS = ("INSERT INTO source_observation (series_key, source, dataset, subject_type, subject_id, period_key, seq, prev_value_hash, value, "
       "value_hash, run_key, code_ref) VALUES (%s,'s','d','symbol','A',NULL,%s,%s,%s::jsonb,%s,'r','c')")


def _sql():
    with open(os.path.join(ROOT, "mechanism", MIG), encoding="utf-8") as fh:
        return fh.read()


def _fails(cur, sql, params=None, exc=psycopg2.Error, match=None):
    cur.execute("SAVEPOINT s")
    with pytest.raises(exc) as e:
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(e.value), str(e.value)


def O(v, subject="AAPL", period="2026-10-30", dataset="earnings_row"):
    return fs.Observation("prov_a", dataset, "symbol", subject, v, period_key=period)


def _raw_insert(cur, key, seq, prev, value):
    return INS, (key, seq, prev, '{"x": %s}' % value, fs.value_hash({"x": value}))


def test_migration_is_reapplyable_and_refuses_a_foreign_object(conn):
    cur = conn.cursor()
    cur.execute(_sql())
    cur.execute(_sql())
    cur.execute("DROP TABLE source_poll; DROP TABLE source_observation")
    cur.execute("CREATE TABLE source_observation (x int)")
    _fails(cur, _sql(), match="migration 27 refused")


def test_the_database_stamps_observed_at_and_the_caller_cannot_choose_it(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO source_observation (series_key, source, dataset, subject_type, subject_id, seq, value, value_hash, observed_at, "
                "run_key, code_ref) VALUES ('k','s','d','symbol','A',1,'{\"x\":1}'::jsonb,%s,'1999-01-01+00','r','c') RETURNING observed_at, now()",
                (fs.value_hash({"x": 1}),))
    stamped, now = cur.fetchone()
    assert stamped > datetime(2020, 1, 1, tzinfo=timezone.utc) and abs((stamped - now).total_seconds()) < 60


def test_the_chain_trigger_refuses_gaps_forks_wrong_predecessors_and_repeats(conn):
    cur = conn.cursor()
    h1, h2 = fs.value_hash({"x": 1}), fs.value_hash({"x": 2})
    cur.execute(*_raw_insert(cur, "k", 1, None, 1))
    _fails(cur, *_raw_insert(cur, "k", 1, None, 2), exc=psycopg2.errors.IntegrityConstraintViolation, match="next must be 2")   # fork at seq 1
    _fails(cur, *_raw_insert(cur, "k", 3, h1, 2), match="next must be 2")                           # gap
    _fails(cur, *_raw_insert(cur, "k", 2, h2, 2), match="predecessor")                              # wrong prev hash
    _fails(cur, *_raw_insert(cur, "k", 2, h1, 1), exc=psycopg2.errors.CheckViolation)               # repeats its predecessor
    _fails(cur, *_raw_insert(cur, "fresh", 2, h1, 2), match="seq must be 1")                        # a series cannot start at 2
    _fails(cur, *_raw_insert(cur, "fresh", 1, h1, 2), exc=psycopg2.errors.CheckViolation)           # seq 1 with a predecessor
    cur.execute(*_raw_insert(cur, "k", 2, h1, 2))                                                   # the honest append works


def test_value_and_hash_shape_checks(conn):
    cur = conn.cursor()
    _fails(cur, "INSERT INTO source_observation (series_key, source, dataset, subject_type, subject_id, seq, value, value_hash, run_key, code_ref) "
                "VALUES ('k','s','d','symbol','A',1,'[1]'::jsonb,%s,'r','c')", (fs.value_hash({"x": 1}),), exc=psycopg2.errors.CheckViolation)
    _fails(cur, "INSERT INTO source_observation (series_key, source, dataset, subject_type, subject_id, seq, value, value_hash, run_key, code_ref) "
                "VALUES ('k','s','d','ticker','A',1,'{\"x\":1}'::jsonb,%s,'r','c')", (fs.value_hash({"x": 1}),), exc=psycopg2.errors.CheckViolation)
    _fails(cur, "INSERT INTO source_observation (series_key, source, dataset, subject_type, subject_id, seq, value, value_hash, run_key, code_ref) "
                "VALUES ('k','s','d','symbol','A',1,'{\"x\":1}'::jsonb,'nothex','r','c')", exc=psycopg2.errors.CheckViolation)


@pytest.mark.parametrize("sql", ["UPDATE source_observation SET dataset = 'z'", "DELETE FROM source_observation", "TRUNCATE source_observation",
                                 "UPDATE source_poll SET status = 'failed'", "DELETE FROM source_poll", "TRUNCATE source_poll"])
def test_both_tables_are_immutable_even_for_the_owner(conn, sql):
    cur = conn.cursor()
    cur.execute(*_raw_insert(cur, "k", 1, None, 1))
    cur.execute("INSERT INTO source_poll (run_key, source, dataset, status, subjects_polled, n_new_observations, detail, code_ref) "
                "VALUES ('r','s','d','complete',ARRAY['symbol:A'],1,'{}'::jsonb,'c')")
    _fails(cur, sql, exc=psycopg2.errors.IntegrityConstraintViolation, match="append-only")


def test_poll_checks_and_database_stamp(conn):
    cur = conn.cursor()
    base = "INSERT INTO source_poll (run_key, source, dataset, status, subjects_polled, n_new_observations, detail, code_ref) VALUES "
    _fails(cur, base + "('r','s','d','failed',ARRAY['symbol:A'],0,'{}'::jsonb,'c')", exc=psycopg2.errors.CheckViolation)      # failed => nothing answered
    _fails(cur, base + "('r','s','d','weird',ARRAY[]::text[],0,'{}'::jsonb,'c')", exc=psycopg2.errors.CheckViolation)
    cur.execute(base + "('r','s','d','failed',ARRAY[]::text[],0,'{\"err\":\"timeout\"}'::jsonb,'c') RETURNING polled_at")
    assert cur.fetchone()[0] is not None
    _fails(cur, base + "('r','s','d','complete',ARRAY[]::text[],0,'{}'::jsonb,'c')", exc=psycopg2.errors.UniqueViolation)    # one poll per run/source/dataset


# ---------------------------------------------------------------- the store
def test_a_dry_run_reads_and_plans_but_writes_nothing(conn):
    cur = conn.cursor()
    r = store.record_run(cur, [O({"eps_estimate": 1.0})], "run1", "ref")
    assert r.applied is False and len(r.plan.appends) == 1 and r.written == 0
    cur.execute("SELECT count(*) FROM source_observation")
    assert cur.fetchone()[0] == 0


def test_only_changes_are_written_and_the_chain_is_intact_and_verifiable(conn):
    cur = conn.cursor()
    v1, v2, v3 = {"eps_estimate": 1.0}, {"eps_estimate": 1.1}, {"eps_estimate": 1.0}
    r1 = store.record_run(cur, [O(v1)], "run1", "ref", apply=True,
                          poll={"source": "prov_a", "dataset": "earnings_row", "status": "complete", "subjects_polled": ["symbol:AAPL"]})
    r2 = store.record_run(cur, [O(v1)], "run2", "ref", apply=True)             # unchanged: nothing
    r3 = store.record_run(cur, [O(v2)], "run3", "ref", apply=True)
    r4 = store.record_run(cur, [O(v3)], "run4", "ref", apply=True)             # reversion: a row
    assert (r1.written, r2.written, r3.written, r4.written) == (1, 0, 1, 1) and r2.plan.unchanged == 1 and r1.poll_id
    chain = store.read_chain(cur, O(v1).key)
    assert [c["seq"] for c in chain] == [1, 2, 3] and fs.verify_chain(chain) == []
    assert chain[2]["value"] == v3 and chain[2]["prev_value_hash"] == chain[1]["value_hash"]
    cur.execute("SELECT n_new_observations, subjects_polled FROM source_poll WHERE run_key = 'run1'")
    assert cur.fetchone() == (1, ["symbol:AAPL"])


def test_a_stale_writer_loses_the_race_instead_of_forking(conn, connect):
    cur = conn.cursor()
    o_old, o_a, o_b = O({"e": 1.0}), O({"e": 2.0}), O({"e": 3.0})
    store.record_run(cur, [o_old], "run1", "ref", apply=True)
    conn.commit()
    heads = store.load_heads(cur, [o_a.key])                                        # writer A reads the head (seq 1) ...
    with connect() as other:                                                         # ... writer B commits seq 2 first
        store.record_run(other.cursor(), [o_b], "runB", "ref", apply=True)
        other.commit()
    stale = fs.plan_appends([o_a], heads)                                            # A now writes its stale plan (seq 2, prev = hash of seq 1)
    assert stale.appends[0].seq == 2
    real = store.load_heads
    store.load_heads = lambda cur_, keys: heads
    try:
        r = store.record_run(cur, [o_a], "runA", "ref", apply=True)
    finally:
        store.load_heads = real
    assert (r.written, r.lost_race) == (0, 1)
    chain = store.read_chain(cur, o_a.key)
    assert [c["value"] for c in chain] == [{"e": 1.0}, {"e": 3.0}] and fs.verify_chain(chain) == []
    r2 = store.record_run(cur, [o_a], "runA2", "ref", apply=True)                    # the next run re-plans from the new head and appends seq 3
    assert (r2.written, r2.lost_race) == (1, 0) and [c["seq"] for c in store.read_chain(cur, o_a.key)] == [1, 2, 3]


def test_read_as_of_never_returns_a_value_first_seen_after_the_cutoff(conn):
    cur = conn.cursor()
    store.record_run(cur, [O({"e": 1.0}), O({"e": 5.0}, subject="MSFT")], "run1", "ref", apply=True)
    cur.execute("SELECT max(observed_at) FROM source_observation")
    t1 = cur.fetchone()[0]
    store.record_run(cur, [O({"e": 2.0}), O({"e": 9.0}, subject="NVDA")], "run2", "ref", apply=True)
    known = {r["subject_id"]: r["value"]["e"] for r in store.read_as_of(cur, t1, dataset="earnings_row")}
    assert known == {"AAPL": 1.0, "MSFT": 5.0}                                       # the revision and the new series are invisible at t1
    later = {r["subject_id"]: r["value"]["e"] for r in store.read_as_of(cur, t1 + timedelta(days=1), dataset="earnings_row")}
    assert later == {"AAPL": 2.0, "MSFT": 5.0, "NVDA": 9.0}
    assert store.read_as_of(cur, t1 - timedelta(days=1)) == []
    with pytest.raises(fs.FirstSeenError):
        store.read_as_of(cur, datetime(2026, 1, 1))


def test_a_bad_poll_status_is_refused_before_anything_is_written(conn):
    cur = conn.cursor()
    with pytest.raises(fs.FirstSeenError):
        store.record_run(cur, [O({"e": 1.0})], "run1", "ref", apply=True,
                         poll={"source": "p", "dataset": "d", "status": "great", "subjects_polled": []})
    cur.execute("SELECT count(*) FROM source_observation")
    assert cur.fetchone()[0] == 0
