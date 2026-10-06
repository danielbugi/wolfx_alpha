"""Migration 31 + the authoritative recorder against real Postgres (throwaway schema): DB-stamped time, the hash-chain triggers, immutability (also
for the owner / a superuser), poll semantics, the recorder's atomic unit, and concurrency / idempotency.

Every historical-time test lives in `test_sector_history_temporal_db.py`; this file proves the STORE."""
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
import pytest

from conftest import ROOT, _connect_args, sconn, sector_env  # noqa: F401  (explicit fixtures: no top-level `conftest` name)
from data_updaters import sector_history_recorder as R
from research.lab import sector_history as SH
from research.lab.dataset_contract import HISTORY_COLUMNS

MIG = "add_sector_history_tables.sql"
SRC = "yfinance_info"
INTEGRITY = (psycopg2.IntegrityError, psycopg2.errors.IntegrityConstraintViolation)   # a CHECK or a trigger refusal
SECTOR_OUTCOME = lambda s, src=SRC: R.classify(src, {"sector": s, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5})   # noqa: E731
NONE_OUTCOME = lambda src=SRC: R.classify(src, {"sector": None, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5})        # noqa: E731


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


OBS = ("INSERT INTO sector_observation (symbol, source, seq, sector, sector_raw, no_sector_reason, change_kind, provenance, raw_payload_hash, "
       "run_id, writer, code_ref, prev_value_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,'observed_forward',%s,%s,'w','c',%s) RETURNING id, value_hash")


def raw_obs(cur, symbol, seq, sector, kind, prev, *, run="r", source=SRC, reason=None):
    cur.execute(OBS, (symbol, source, seq, sector, sector, reason if sector is None else None, kind, R.payload_hash(sector), run, prev))
    return cur.fetchone()


def chain_rows(conn, symbol="AAA", source=SRC):
    cur = conn.cursor()
    cur.execute("SELECT 'observation', symbol, source, seq, sector, sector_raw, no_sector_reason, change_kind, captured_at, effective_session, "
                "source_asof, provenance, raw_payload_hash, prev_value_hash, value_hash, run_id FROM sector_observation "
                "WHERE symbol = %s AND source = %s ORDER BY seq", (symbol, source))
    return [dict(zip(HISTORY_COLUMNS, r)) for r in cur.fetchall()]


def polls(conn, symbol="AAA"):
    cur = conn.cursor()
    cur.execute("SELECT run_id, response_state, chain_effect, observation_id, failure_reason FROM sector_poll WHERE symbol = %s ORDER BY id", (symbol,))
    return cur.fetchall()


def count(conn, table):
    cur = conn.cursor()
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


# ================================================================== migration
def test_migration_reapplies_and_refuses_a_foreign_object(sconn):
    cur = sconn.cursor()
    cur.execute(_sql())                                                                  # a second application is a no-op
    cur.execute("SELECT count(*) FROM pg_trigger t WHERE NOT t.tgisinternal AND t.tgrelid::regclass::text LIKE 'sector_%%'")
    assert cur.fetchone()[0] == 9
    cur.execute("SELECT count(*) FROM pg_trigger t WHERE NOT t.tgisinternal AND t.tgrelid::regclass::text LIKE 'sector_%%' AND t.tgenabled = 'A'")
    assert cur.fetchone()[0] == 9                                                        # every one ENABLE ALWAYS (a superuser cannot skip it)
    cur.execute("DROP TABLE sector_poll; DROP TABLE sector_observation; DROP TABLE sector_reconstruction")
    cur.execute("CREATE TABLE sector_observation (x int)")
    _fails(cur, _sql(), match="migration 31 refused")


def test_a_foreign_trigger_function_is_refused(sconn):
    cur = sconn.cursor()
    cur.execute("ALTER TABLE sector_observation DISABLE TRIGGER sector_observation_stamp")
    cur.execute("DROP TRIGGER sector_observation_stamp ON sector_observation")
    cur.execute("CREATE TRIGGER sector_observation_stamp BEFORE INSERT ON sector_observation FOR EACH ROW EXECUTE FUNCTION research_sector_guard()")
    _fails(cur, _sql(), match="migration 31 refused")


# ================================================================== the database stamps time and hash
def test_the_database_stamps_time_session_and_hash_and_the_caller_cannot_choose_them(sconn):
    cur = sconn.cursor()
    cur.execute("INSERT INTO sector_observation (symbol, source, seq, sector, sector_raw, change_kind, provenance, raw_payload_hash, run_id, writer, "
                "code_ref, captured_at, effective_session, value_hash) VALUES ('AAA',%s,1,'Tech','Tech','first','observed_forward',%s,'r','w','c',"
                "'1999-01-01+00','1999-01-01',%s) RETURNING captured_at, effective_session, value_hash, now()",
                (SRC, R.payload_hash("Tech"), "f" * 64))
    at, session, vh, now = cur.fetchone()
    assert at > datetime(2020, 1, 1, tzinfo=timezone.utc) and abs((at - now).total_seconds()) < 60
    assert session == at.astimezone(timezone.utc).date() and vh != "f" * 64


def test_the_python_row_hash_is_the_databases_row_hash(sconn):
    cur = sconn.cursor()
    a = raw_obs(cur, "AAA", 1, "Tech", "first", None)
    raw_obs(cur, "AAA", 2, None, "became_none", a[1], reason="vendor_blank")
    rows = chain_rows(sconn)
    assert [SH.row_hash(r) for r in rows] == [r["value_hash"] for r in rows]
    assert SH.verify_chain(rows) == []
    cur.execute("SELECT research_sector_row_hash('AAA', %s, 1, 'Tech', NULL, 'Tech', %s, 'observed_forward', %s, NULL)", (SRC, rows[0]["stamp"], rows[0]["raw_payload_hash"]))
    assert cur.fetchone()[0] == rows[0]["value_hash"]


def test_the_first_row_anchors_and_the_next_references_the_prior(sconn):
    cur = sconn.cursor()
    first = raw_obs(cur, "AAA", 1, "Tech", "first", None)
    second = raw_obs(cur, "AAA", 2, "Health Care", "changed", first[1])
    rows = chain_rows(sconn)
    assert rows[0]["prev_value_hash"] is None and rows[1]["prev_value_hash"] == first[1] == rows[0]["value_hash"] and second[1] == rows[1]["value_hash"]


# ================================================================== the chain trigger refuses contradictory history
def test_the_chain_trigger_refuses_gaps_forks_wrong_predecessors_repeats_and_wrong_kinds(sconn):
    cur = sconn.cursor()
    first = raw_obs(cur, "AAA", 1, "Tech", "first", None)
    _fails(cur, OBS, ("AAA", SRC, 1, "Energy", "Energy", None, "first", R.payload_hash("Energy"), "r2", None), match="next must be 2")  # fork at 1
    _fails(cur, OBS, ("AAA", SRC, 3, "Energy", "Energy", None, "changed", R.payload_hash("Energy"), "r2", first[1]), match="next must be 2")           # gap
    _fails(cur, OBS, ("AAA", SRC, 2, "Energy", "Energy", None, "changed", R.payload_hash("Energy"), "r2", "0" * 64), match="predecessor")             # wrong prev
    _fails(cur, OBS, ("AAA", SRC, 2, "Energy", "Energy", None, "changed", R.payload_hash("Energy"), "r2", None), exc=psycopg2.Error)                  # seq>1 needs a prev
    _fails(cur, OBS, ("AAA", SRC, 2, "Tech", "Tech", None, "changed", R.payload_hash("Tech"), "r2", first[1]), match="confirm poll")                  # a repeat
    _fails(cur, OBS, ("AAA", SRC, 2, "Energy", "Energy", None, "became_none", R.payload_hash("Energy"), "r2", first[1]), exc=psycopg2.Error)         # wrong kind
    _fails(cur, OBS, ("AAA", SRC, 2, "Energy", "Energy", None, "first", R.payload_hash("Energy"), "r2", first[1]), exc=psycopg2.Error)
    _fails(cur, OBS, ("BBB", SRC, 2, "Energy", "Energy", None, "changed", R.payload_hash("Energy"), "r2", first[1]), match="so seq must be 1")       # another chain has no head
    assert count(sconn, "sector_observation") == 1


def test_chains_are_independent_per_symbol_and_per_source(sconn):
    cur = sconn.cursor()
    raw_obs(cur, "AAA", 1, "Tech", "first", None)
    raw_obs(cur, "BBB", 1, "Tech", "first", None)
    raw_obs(cur, "AAA", 1, "Energy", "first", None, source="tiingo_meta")
    assert count(sconn, "sector_observation") == 3


def test_the_check_constraints_refuse_malformed_observations(sconn):
    cur = sconn.cursor()
    bad = [("AAA", SRC, 1, None, None, None, "first", "h", "r", None),                                        # neither a sector nor a reason
           ("AAA", SRC, 1, "Tech", "Tech", "vendor_null", "first", "h", "r", None),                           # both
           ("AAA", SRC, 1, " Tech", "Tech", None, "first", "h", "r", None),                                   # not cleaned
           ("AAA", SRC, 1, "Unknown", "Unknown", None, "first", "h", "r", None),                              # the unknown label is never a sector
           ("AAA", SRC, 1, None, None, "made_up", "first", "h", "r", None),                                  # unknown reason
           ("AAA", SRC, 1, "Tech", "Tech", None, "first", "zz", "r", None),                                   # malformed payload hash
           ("AAA", SRC, 1, "Tech", "Tech", None, "first", "h", "", None)]                                     # empty run id
    for p in bad:
        p = tuple(R.payload_hash("x") if v == "h" else v for v in p)
        _fails(cur, OBS, p, exc=psycopg2.errors.CheckViolation)
    _fails(cur, OBS.replace("'observed_forward'", "'reconstructed'"), ("AAA", SRC, 1, "Tech", "Tech", None, "first", R.payload_hash("Tech"), "r", None),
           exc=psycopg2.errors.CheckViolation)                                                                # an observation is never reconstructed
    _fails(cur, OBS, ("AAA", SRC, 1, "x" * 101, "x", None, "first", R.payload_hash("x"), "r", None))        # oversized
    assert count(sconn, "sector_observation") == 0


# ================================================================== immutability (the runtime-role model; the triggers hold even for the owner)
def test_observations_cannot_be_updated_deleted_or_truncated(sconn):
    cur = sconn.cursor()
    raw_obs(cur, "AAA", 1, "Tech", "first", None)
    for stmt in ("UPDATE sector_observation SET sector = 'Energy'",                                   # value rewrite
                 "UPDATE sector_observation SET captured_at = captured_at - interval '30 days'",     # timestamp rewrite
                 "UPDATE sector_observation SET effective_session = effective_session - 30",
                 "UPDATE sector_observation SET provenance = 'reconstructed'",                         # provenance rewrite
                 "UPDATE sector_observation SET prev_value_hash = repeat('a', 64)",                    # chain rewrite
                 "UPDATE sector_observation SET value_hash = repeat('a', 64)",
                 "UPDATE sector_observation SET seq = 7", "UPDATE sector_observation SET source_asof = now()",
                 "UPDATE sector_observation SET raw_payload_hash = repeat('b', 64)", "UPDATE sector_observation SET run_id = 'x'",
                 "DELETE FROM sector_observation", "TRUNCATE sector_observation, sector_poll", "TRUNCATE sector_observation CASCADE"):
        _fails(cur, stmt, match="append-only")
    cur.execute("SELECT sector FROM sector_observation")
    assert cur.fetchall() == [("Tech",)]


def test_polls_and_reconstruction_rows_are_immutable_too(sconn):
    cur = sconn.cursor()
    R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    cur.execute("INSERT INTO sector_reconstruction (symbol, sector, reconstructed_from, row_date, method, import_batch, provenance, code_ref) "
                "VALUES ('AAA','Tech','daily_fundamentals.sector','2020-01-02','m','b','reconstructed','c')")
    for stmt in ("UPDATE sector_poll SET chain_effect = 'none'", "UPDATE sector_poll SET attempted_at = now() - interval '9 days'",
                 "UPDATE sector_poll SET response_state = 'request_failed'", "DELETE FROM sector_poll", "TRUNCATE sector_poll, sector_observation",
                 "UPDATE sector_reconstruction SET sector = 'Energy'", "UPDATE sector_reconstruction SET imported_at = now() - interval '9 days'",
                 "DELETE FROM sector_reconstruction", "TRUNCATE sector_reconstruction"):
        _fails(cur, stmt, match="append-only")
    assert (count(sconn, "sector_poll"), count(sconn, "sector_reconstruction")) == (1, 1)


def test_there_is_no_maintenance_hatch_in_migration_31():
    text = " ".join(line.split("--")[0] for line in _sql().lower().splitlines())            # code only: the header may say "no maintenance hatch"
    for word in ("maintenance", "ticket", "research_audit_append_only", "set_state", "bypass", "session_replication_role"):
        assert word not in text, word


# ================================================================== poll semantics (the recorder against the real schema)
def test_first_answer_creates_an_observation_and_a_poll(sconn):
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA") == "created_observation"
    rows = chain_rows(sconn)
    assert [(r["seq"], r["sector"], r["change_kind"], r["provenance"], r["source_asof"]) for r in rows] == [(1, "Tech", "first", "observed_forward", None)]
    assert [(p[0], p[1], p[2]) for p in polls(sconn)] == [("r1", "sector", "created_observation")]


def test_a_same_value_refresh_records_a_poll_and_no_second_observation(sconn):
    R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r2", symbol="AAA") == "confirmed_head"
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r3", symbol="AAA") == "confirmed_head"
    assert len(chain_rows(sconn)) == 1                                                  # no fake change
    assert [p[2] for p in polls(sconn)] == ["created_observation", "confirmed_head", "confirmed_head"]
    assert len({p[3] for p in polls(sconn)}) == 1                                        # every poll points at the same observation


def test_a_changed_value_is_a_chained_row_and_the_old_one_stays(sconn):
    R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    assert R.record_poll(sconn, SECTOR_OUTCOME("Health Care"), run_id="r2", symbol="AAA") == "created_observation"
    rows = chain_rows(sconn)
    assert [(r["seq"], r["sector"], r["change_kind"]) for r in rows] == [(1, "Tech", "first"), (2, "Health Care", "changed")]
    assert rows[1]["prev_value_hash"] == rows[0]["value_hash"] and SH.verify_chain(rows) == []


def test_explicit_no_sector_is_an_observation_and_is_not_a_vendor_failure(sconn):
    R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    assert R.record_poll(sconn, NONE_OUTCOME(), run_id="r2", symbol="AAA") == "created_observation"
    assert R.record_poll(sconn, NONE_OUTCOME(), run_id="r3", symbol="AAA") == "confirmed_head"             # still no sector: a confirmation
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r4", symbol="AAA") == "created_observation"
    rows = chain_rows(sconn)
    assert [(r["sector"], r["no_sector_reason"], r["change_kind"]) for r in rows] == [("Tech", None, "first"), (None, "vendor_null", "became_none"),
                                                                                     ("Tech", None, "became_set")]
    assert SH.verify_chain(rows) == []


def test_a_vendor_failure_polls_but_never_touches_the_chain(sconn):
    R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    before = chain_rows(sconn)
    for i, o in enumerate((R.classify(SRC, None, TimeoutError()), R.classify(SRC, None), R.classify(SRC, {"symbol": "AAA", "a": 1}),
                           R.classify("tiingo_meta", {"sector": None}), R.classify(SRC, {"sector": 5}))):
        assert R.record_poll(sconn, o, run_id=f"f{i}", symbol="AAA") == "none"
    assert chain_rows(sconn) == before                                                  # not erased, not rewritten, not extended
    states = [(p[1], p[2], p[3]) for p in polls(sconn)][1:]
    assert [s[:2] for s in states] == [("request_failed", "none")] * 2 + [("invalid_response", "none")] * 3 and all(s[2] is None for s in states)
    assert [p[4] for p in polls(sconn)][1:] == ["timeout", "no_company_info", "sector_field_missing", "ambiguous_source_none", "non_string_sector"]


def test_a_failure_before_any_answer_leaves_no_chain_and_does_not_count_as_no_sector(sconn):
    assert R.record_poll(sconn, R.classify(SRC, None, TimeoutError()), run_id="r1", symbol="AAA") == "none"
    assert count(sconn, "sector_observation") == 0 and count(sconn, "sector_poll") == 1


def test_the_poll_table_refuses_inconsistent_polls(sconn):
    cur = sconn.cursor()
    ins = ("INSERT INTO sector_poll (run_id, symbol, source, response_state, chain_effect, response_sector, no_sector_reason, failure_reason, "
           "raw_payload_hash, observation_id, writer, code_ref) VALUES (%s,'AAA',%s,%s,%s,%s,%s,%s,%s,%s,'w','c')")
    h = R.payload_hash("Tech")
    first = R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    cur.execute("SELECT id FROM sector_observation")
    oid = cur.fetchone()[0]
    _fails(cur, ins, ("r2", SRC, "request_failed", "confirmed_head", None, None, "timeout", None, oid), exc=INTEGRITY)    # failed polls cannot touch the chain
    _fails(cur, ins, ("r2", SRC, "request_failed", "none", None, None, None, None, None), exc=INTEGRITY)                  # a failure needs a reason
    _fails(cur, ins, ("r2", SRC, "request_failed", "none", None, None, "Bad Reason!", None, None), exc=INTEGRITY)
    _fails(cur, ins, ("r2", SRC, "sector", "none", "Tech", None, None, h, None), exc=INTEGRITY)                          # a sector answer has an effect
    _fails(cur, ins, ("r2", SRC, "sector", "confirmed_head", "Energy", None, None, h, oid), match="holds")                                       # answered != head
    _fails(cur, ins, ("r1", SRC, "sector", "confirmed_head", "Tech", None, None, h, oid), match="itself")                                         # a run cannot confirm its own row
    _fails(cur, ins, ("r1", SRC, "request_failed", "none", None, None, "timeout", None, None), exc=psycopg2.errors.UniqueViolation)           # one poll per run/symbol/source
    _fails(cur, ins, ("r2", SRC, "sector", "confirmed_head", "Tech", None, None, h, 9999), exc=psycopg2.Error)                                  # dangling observation
    assert first == "created_observation" and count(sconn, "sector_poll") == 1


def test_a_run_cannot_confirm_the_observation_it_created_and_a_stale_head_cannot_be_confirmed(sconn):
    cur = sconn.cursor()
    ins = ("INSERT INTO sector_poll (run_id, symbol, source, response_state, chain_effect, response_sector, raw_payload_hash, observation_id, writer, code_ref) "
           "VALUES (%s,'AAA',%s,'sector','confirmed_head',%s,%s,%s,'w','c')")
    R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    cur.execute("SELECT id FROM sector_observation")
    old = cur.fetchone()[0]
    _fails(cur, ins, ("r1", SRC, "Tech", R.payload_hash("Tech"), old), match="cannot confirm the observation it created itself")
    R.record_poll(sconn, SECTOR_OUTCOME("Energy"), run_id="r2", symbol="AAA")
    _fails(cur, ins, ("r3", SRC, "Tech", R.payload_hash("Tech"), old), match="not the current head")                                             # superseded head


# ================================================================== the recorder's atomic unit and failure isolation
def test_the_observation_and_its_poll_are_one_unit_a_failing_poll_rolls_the_observation_back(sconn, monkeypatch):
    real = R._insert_poll

    def failing(cur, o, run_id, symbol, effect, observation_id):
        if effect == "created_observation":
            raise RuntimeError("crash between the observation and its poll")
        return real(cur, o, run_id, symbol, effect, observation_id)
    monkeypatch.setattr(R, "_insert_poll", failing)
    with pytest.raises(RuntimeError):
        R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA")
    assert count(sconn, "sector_observation") == 0 and count(sconn, "sector_poll") == 0      # all-or-nothing
    monkeypatch.setattr(R, "_insert_poll", real)
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA") == "created_observation"      # the retry succeeds
    assert len(chain_rows(sconn)) == 1 and SH.verify_chain(chain_rows(sconn)) == []


def test_a_retry_after_a_transient_error_is_idempotent(sconn):
    bad = R.Outcome(source=SRC, response_state="request_failed", failure_reason="Not Valid!")
    with pytest.raises(psycopg2.Error):
        R.record_poll(sconn, bad, run_id="r1", symbol="AAA")                                  # transient/constraint error
    assert count(sconn, "sector_poll") == 0
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA") == "created_observation"
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA") == "duplicate_poll"      # the same attempt twice: nothing new
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA") == "duplicate_poll"
    assert (count(sconn, "sector_observation"), count(sconn, "sector_poll")) == (1, 1)


def test_an_observation_committed_without_its_poll_is_completed_by_the_retry(sconn):
    """Not reachable through the recorder (one savepoint), but a hand-made partial state must still heal without a fake change or a fake confirmation."""
    cur = sconn.cursor()
    raw_obs(cur, "AAA", 1, "Tech", "first", None, run="r1")
    sconn.commit()
    assert R.record_poll(sconn, SECTOR_OUTCOME("Tech"), run_id="r1", symbol="AAA") == "created_observation"
    assert [p[2] for p in polls(sconn)] == ["created_observation"] and len(chain_rows(sconn)) == 1


def test_the_recorder_isolates_every_failure_and_counts_it(sector_env, caplog):
    _, connect = sector_env

    @contextmanager
    def failing():
        raise ConnectionError("pool exhausted")
        yield
    rec = R.SectorRecorder(run_id="r1", connect=failing, is_enabled=lambda: True)
    with caplog.at_level("ERROR"):
        assert rec.record("AAA", SRC, {"sector": "Tech", "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}) is None
    assert rec.counters == {"attempted": 1, "recorded": 0, "duplicate": 0, "failed": 1}
    assert any("sector history NOT recorded for AAA" in m and "pool exhausted" in m for m in caplog.messages)
    rec2 = R.SectorRecorder(run_id="r1", connect=connect, is_enabled=lambda: True)
    info = {"sector": "Tech", "a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
    assert (rec2.record("AAA", SRC, info), rec2.record("AAA", SRC, info)) == ("created_observation", "duplicate_poll")
    assert rec2.record("BBB", SRC, None, TimeoutError()) == "none"
    assert rec2.counters == {"attempted": 3, "recorded": 2, "duplicate": 1, "failed": 0}


# ================================================================== concurrency
def _run_threads(connect, jobs):
    """jobs = [(run_id, symbol, outcome)]. Each runs in its own thread on its own connection, released together by a barrier."""
    barrier, results, errors = threading.Barrier(len(jobs)), [None] * len(jobs), []

    def work(i, run_id, symbol, outcome):
        try:
            with connect() as c:
                barrier.wait(timeout=20)
                results[i] = R.record_poll(c, outcome, run_id=run_id, symbol=symbol)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=work, args=(i, *j)) for i, j in enumerate(jobs)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    return results, errors


def test_concurrent_same_symbol_same_value_refreshes_create_one_observation(sector_env):
    _, connect = sector_env
    results, errors = _run_threads(connect, [(f"r{i}", "AAA", SECTOR_OUTCOME("Tech")) for i in range(6)])
    assert errors == []
    assert sorted(results) == ["confirmed_head"] * 5 + ["created_observation"]
    with connect() as c:
        rows = chain_rows(c)
        assert len(rows) == 1 and count(c, "sector_poll") == 6 and SH.verify_chain(rows) == []


def test_concurrent_changed_value_refreshes_leave_a_valid_chain(sector_env):
    _, connect = sector_env
    values = ["Tech", "Energy", "Health Care", "Utilities", "Tech", "Energy"]
    results, errors = _run_threads(connect, [(f"r{i}", "AAA", SECTOR_OUTCOME(v)) for i, v in enumerate(values)])
    assert errors == []
    with connect() as c:
        rows = chain_rows(c)
        assert SH.verify_chain(rows) == [] and [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
        assert all(rows[i]["sector"] != rows[i - 1]["sector"] for i in range(1, len(rows)))     # never a fake change
        assert all(rows[i]["stamp"] >= rows[i - 1]["stamp"] for i in range(1, len(rows)))
        assert count(c, "sector_poll") == len(values) and results.count("created_observation") == len(rows)


def test_concurrent_duplicate_attempts_of_the_same_poll_collapse_to_one(sector_env):
    _, connect = sector_env
    results, errors = _run_threads(connect, [("same-run", "AAA", SECTOR_OUTCOME("Tech"))] * 5)
    assert errors == [] and sorted(results) == ["created_observation"] + ["duplicate_poll"] * 4
    with connect() as c:
        assert (count(c, "sector_observation"), count(c, "sector_poll")) == (1, 1)


def test_concurrent_writers_on_different_symbols_do_not_block_each_other(sector_env):
    _, connect = sector_env
    results, errors = _run_threads(connect, [(f"r{i}", f"S{i}", SECTOR_OUTCOME("Tech")) for i in range(8)])
    assert errors == [] and results == ["created_observation"] * 8


def test_direct_inserts_racing_the_head_are_serialised_by_the_trigger_lock(sector_env):
    """Two writers that bypass the recorder and both claim seq 1 / the same predecessor: exactly one wins, the loser is refused, the chain stays valid."""
    _, connect = sector_env
    barrier, outcome = threading.Barrier(2), []

    def work(sector):
        with connect() as c:
            cur = c.cursor()
            barrier.wait(timeout=20)
            try:
                raw_obs(cur, "AAA", 1, sector, "first", None, run=f"r-{sector}")
                c.commit()
                outcome.append("ok")
            except psycopg2.Error:
                c.rollback()
                outcome.append("refused")
    ts = [threading.Thread(target=work, args=(s,)) for s in ("Tech", "Energy")]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(outcome) == ["ok", "refused"]
    with connect() as c:
        assert len(chain_rows(c)) == 1 and SH.verify_chain(chain_rows(c)) == []


# ================================================================== reconstruction can never be observed history
def test_reconstruction_is_a_separate_store_that_cannot_hold_an_observation_or_be_one(sconn):
    cur = sconn.cursor()
    ins = ("INSERT INTO sector_reconstruction (symbol, sector, reconstructed_from, row_date, method, import_batch, provenance, code_ref) "
           "VALUES ('AAA','Tech','daily_fundamentals.sector','2020-01-02','m','b',%s,'c')")
    _fails(cur, ins, ("observed_forward",), exc=psycopg2.errors.CheckViolation)                       # cannot claim to be observed
    cur.execute(ins, ("reconstructed",))
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'sector_reconstruction' AND table_schema = current_schema()")
    cols = {r[0] for r in cur.fetchall()}
    assert not ({"seq", "prev_value_hash", "value_hash", "captured_at", "effective_session", "source_asof", "raw_payload_hash"} & cols)
    assert count(sconn, "sector_observation") == 0
    cur.execute("SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid WHERE c.contype = 'f' AND t.relname = 'sector_reconstruction'")
    assert cur.fetchone()[0] == 0                                                                     # no link into the chain either way
