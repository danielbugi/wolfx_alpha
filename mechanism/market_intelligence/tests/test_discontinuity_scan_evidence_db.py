"""Migration 32: the append-only `price_discontinuity_scan` heartbeat, against real Postgres in a throwaway schema.

What a reader may rely on: a COMPLETE scan row exists only if the database itself recomputed the price-input fingerprint (the session's own bar is the newest, nothing
newer mixed in, prices unchanged during the scan) and the discontinuity-table fingerprint at insert, and stamped completion with ITS clock. The reader
(`inputs.discontinuity_scan_evidence`, used by every `observed` run) then accepts it only for the SAME session, the SAME prices as stored now, the SAME discontinuity
table as stored now, and a completion no later than the session's knowledge cutoff. Everything else fails closed with a distinct, coded reason.
Test-only forging (triggers off in the disposable schema) is used to construct states the legitimate path cannot produce, such as a scan of another session."""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest

import mi_fixtures  # noqa: F401  (path setup)
from mi_runner_fixtures import ROOT, SCAN_TRIGGERS, T, record_scan_on, runner_env  # noqa: F401
from market_intelligence import inputs, runner

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ml_training", "config"))
from ml_training.data_preparation import build_dataset as bd  # noqa: E402

UTC = timezone.utc
CUTOFF = inputs.knowledge_cutoff(T)


def forge(env, sql, params=()):
    """TEST ONLY: run a statement on the scan table with its immutability triggers briefly off (disposable schema), then ENABLE ALWAYS them again."""
    with env.connect() as c:
        cur = c.cursor()
        cur.execute("ALTER TABLE price_discontinuity_scan DISABLE TRIGGER USER")
        cur.execute(sql, params)
        for name in SCAN_TRIGGERS:
            cur.execute(f'ALTER TABLE price_discontinuity_scan ENABLE ALWAYS TRIGGER "{name}"')
        c.commit()


def evidence(env, session=T):
    with env.connect() as c:
        return inputs.discontinuity_scan_evidence(c, session)


def scan(env, session=T, **kw):
    with env.connect() as c:
        sid = record_scan_on(c.cursor(), session, **kw)
        c.commit()
    return sid


def n_scans(env):
    with env.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM price_discontinuity_scan")
        return cur.fetchone()[0]


def observed(env):
    return runner.run(env.connect, T, provenance="observed", apply=False, feature_set_version=env.fsv(), code_ref="t")


@pytest.fixture
def world(runner_env):
    """No discontinuities, no scan rows: each test builds exactly the state it needs. The baseline (a clean table + the builder's scan for T) is restored after."""
    def reset():
        forge(runner_env, "DELETE FROM price_discontinuity_scan")
        with runner_env.connect() as c:
            c.cursor().execute("DELETE FROM price_discontinuities")
            c.commit()
    reset()
    yield runner_env
    reset()
    scan(runner_env)


# ---------------------------------------------------------------------------------------------------- normal / skipped / failed
def test_a_normal_successful_scan_satisfies_the_observed_run(world):
    sid = scan(world)
    ev = evidence(world)
    assert ev["ok"] and ev["reason"] == inputs.SCAN_OK and ev["detail"]["scan_id"] == sid
    assert ev["detail"]["newest_price_bar"] == T.isoformat() and ev["detail"]["n_symbols"] == 1100
    assert observed(world).applied is False                                       # an observed run is not refused


def test_a_skipped_scan_leaves_no_evidence_and_the_observed_run_is_refused(world):
    ev = evidence(world)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_MISSING
    with pytest.raises(runner.ProvenanceRefused, match="scan_missing"):
        observed(world)
    rep = runner.run(world.connect, T, provenance="reconstructed", apply=False, feature_set_version=world.fsv(), code_ref="t")   # reconstruction never claimed live
    assert rep.applied is False


def test_a_failed_scan_cannot_satisfy_the_dependency(world):
    with world.connect() as c:
        c.cursor().execute("INSERT INTO price_discontinuity_scan (session_date, status, failure_reason, started_at, run_id, writer, code_ref) "
                           "VALUES (%s, 'failed', 'scan_exception', clock_timestamp(), 'r1', 'build_dataset', 'x')", (T,))
        c.commit()
    ev = evidence(world)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_FAILED and ev["detail"]["failure_reasons"] == ["scan_exception"]
    with pytest.raises(runner.ProvenanceRefused, match="scan_failed"):
        observed(world)
    scan(world)                                                                    # a later COMPLETE scan satisfies it; the failed row stays as history
    assert evidence(world)["ok"] and n_scans(world) == 2


def test_a_failed_row_cannot_claim_a_fingerprint_and_a_complete_row_must_carry_them(world):
    with world.connect() as c:
        with pytest.raises(psycopg2.IntegrityError):
            c.cursor().execute("INSERT INTO price_discontinuity_scan (session_date, status, failure_reason, input_fingerprint, started_at, run_id, writer, code_ref) "
                               "VALUES (%s, 'failed', 'x', repeat('a', 64), clock_timestamp(), 'r', 'w', 'c')", (T,))
    with world.connect() as c:
        with pytest.raises(psycopg2.IntegrityError):
            c.cursor().execute("INSERT INTO price_discontinuity_scan (session_date, status, newest_bar, started_at, run_id, writer, code_ref) "
                               "VALUES (%s, 'complete', %s, clock_timestamp(), 'r', 'w', 'c')", (T, T))


# ---------------------------------------------------------------------------------------------------- wrong session / wrong fingerprint / partial
def test_a_scan_of_another_session_is_never_evidence_and_the_database_refuses_a_session_that_is_not_the_newest_loaded_bar(world):
    """The detector reads the WHOLE price table, so a scan is only meaningful for the session whose bar is the newest in it."""
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT newest_bar, n_symbols, n_price_rows, fingerprint FROM research_price_input_fingerprint(%s)", (T,))
        newest, n_sym, n_rows, fp = cur.fetchone()
        cur.execute("SELECT n_rows, fingerprint FROM research_discontinuity_result_fingerprint()")
        n_found, rfp = cur.fetchone()
        c.commit()
    insert = ("INSERT INTO price_discontinuity_scan (session_date, status, newest_bar, n_symbols, n_price_rows, input_fingerprint, n_discontinuities, "
              "result_fingerprint, started_at, run_id, writer, code_ref) VALUES (%s, 'complete', %s, %s, %s, %s, %s, %s, clock_timestamp(), 'r', 'w', 'c')")
    ahead, behind = T + timedelta(days=1), T - timedelta(days=1)
    for other in (ahead, behind):                                                    # not loaded yet / an older session while a newer bar is stored
        with world.connect() as c:
            with pytest.raises(psycopg2.errors.IntegrityConstraintViolation, match="newest stored price bar"):
                c.cursor().execute(insert, (other, other, n_sym, n_rows, fp, n_found, rfp))
    with world.connect() as c:                                                      # forged (triggers off): a row for the previous day with the right numbers
        cur = c.cursor()
        cur.execute("SELECT newest_bar, n_symbols, n_price_rows, fingerprint FROM research_price_input_fingerprint(%s)", (behind,))
        b_newest, b_sym, b_rows, b_fp = cur.fetchone()
        c.commit()
    forge(world, "INSERT INTO price_discontinuity_scan (session_date, status, newest_bar, n_symbols, n_price_rows, input_fingerprint, n_discontinuities, "
                 "result_fingerprint, started_at, run_id, writer, code_ref, finished_at) VALUES (%s, 'complete', %s, %s, %s, %s, %s, %s, now(), 'r', 'w', 'c', now())",
          (behind, behind, b_sym, b_rows, b_fp, n_found, rfp))
    ev = evidence(world)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_MISSING and ev["detail"]["scans_for_session"] == 0      # not evidence for T
    ev = evidence(world, behind)                                                    # and the reader will not accept it for its own session either: T is newer
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_PRICES_NOT_AT_SESSION


def test_a_scan_with_a_wrong_price_fingerprint_is_refused_at_insert_and_ignored_if_forged(world):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT newest_bar, n_symbols, n_price_rows FROM research_price_input_fingerprint(%s)", (T,))
        newest, n_sym, n_rows = cur.fetchone()
        cur.execute("SELECT n_rows, fingerprint FROM research_discontinuity_result_fingerprint()")
        n_found, rfp = cur.fetchone()
        c.commit()
    args = (T, T, n_sym, n_rows, "b" * 64, n_found, rfp)
    sql = ("INSERT INTO price_discontinuity_scan (session_date, status, newest_bar, n_symbols, n_price_rows, input_fingerprint, n_discontinuities, result_fingerprint, "
           "started_at, run_id, writer, code_ref) VALUES (%s, 'complete', %s, %s, %s, %s, %s, %s, clock_timestamp(), 'r', 'w', 'c')")
    with world.connect() as c:
        with pytest.raises(psycopg2.errors.IntegrityConstraintViolation, match="claimed price input does not match"):
            c.cursor().execute(sql, args)
    forge(world, sql, args)
    ev = evidence(world)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_INPUT_MISMATCH
    with pytest.raises(runner.ProvenanceRefused, match="scan_input_mismatch"):
        observed(world)


def test_a_wrong_result_claim_is_refused_at_insert(world):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT newest_bar, n_symbols, n_price_rows, fingerprint FROM research_price_input_fingerprint(%s)", (T,))
        newest, n_sym, n_rows, fp = cur.fetchone()
        c.commit()
    with world.connect() as c:
        with pytest.raises(psycopg2.errors.IntegrityConstraintViolation, match="claimed result does not match"):
            c.cursor().execute(
                "INSERT INTO price_discontinuity_scan (session_date, status, newest_bar, n_symbols, n_price_rows, input_fingerprint, n_discontinuities, "
                "result_fingerprint, started_at, run_id, writer, code_ref) VALUES (%s, 'complete', %s, %s, %s, %s, 0, %s, clock_timestamp(), 'r', 'w', 'c')",
                (T, T, n_sym, n_rows, fp, "c" * 64))


def test_prices_that_change_after_a_partial_ingestion_scan_no_longer_match_it(world):
    """The scan describes the input it saw. If part of the session's bars were missing then and are loaded later, the evidence is for another price version."""
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("CREATE TEMP TABLE _held AS SELECT * FROM stock_prices WHERE date = %s AND symbol <= 'S0599'", (T,))
        cur.execute("DELETE FROM stock_prices WHERE date = %s AND symbol <= 'S0599'", (T,))
        c.commit()
        try:
            scan(world)                                                              # recorded over the partial load (the newest bar is still T)
            with pytest.raises((runner.SessionNotAvailable, runner.ProvenanceRefused)):
                observed(world)                                                      # the runner's own real-session guard refuses a partial load too
        finally:
            cur.execute("INSERT INTO stock_prices SELECT * FROM _held")
            c.commit()
    ev = evidence(world)                                                             # the rest of the session arrived after the scan
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_INPUT_MISMATCH


def test_changed_input_prices_need_a_new_scan_and_the_old_row_stays_as_history(world):
    scan(world)
    assert evidence(world)["ok"]
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT close FROM stock_prices WHERE symbol = 'S0005' AND date = %s", (T - timedelta(days=2),))
        original = cur.fetchone()[0]
        cur.execute("UPDATE stock_prices SET close = close + 0.01 WHERE symbol = 'S0005' AND date = %s", (T - timedelta(days=2),))   # a vendor correction
        c.commit()
    try:
        ev = evidence(world)
        assert not ev["ok"] and ev["reason"] == inputs.SCAN_INPUT_MISMATCH
        with pytest.raises(runner.ProvenanceRefused, match="scan_input_mismatch"):
            observed(world)
        assert scan(world) is not None and evidence(world)["ok"] and n_scans(world) == 2        # a new scan over the corrected prices
    finally:
        with world.connect() as c:
            c.cursor().execute("UPDATE stock_prices SET close = %s WHERE symbol = 'S0005' AND date = %s", (original, T - timedelta(days=2)))
            c.commit()
    assert evidence(world)["ok"]                                                    # the original scan describes the restored prices again


# ---------------------------------------------------------------------------------------------------- repeated / deletion / ratio / delay
def test_a_repeated_identical_scan_is_idempotent_and_keeps_the_first_completion_time(world):
    first = scan(world)
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT finished_at FROM price_discontinuity_scan WHERE id = %s", (first,))
        t1 = cur.fetchone()[0]
    for _ in range(3):
        assert scan(world) is None                                                  # identical prices and result: the same evidence, no new row
    assert n_scans(world) == 1
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT finished_at FROM price_discontinuity_scan")
        assert cur.fetchone()[0] == t1


def _row(sym="S0002", event=date(2026, 9, 10), ratio=1.5):
    return (sym, event, "jump_up", 100.0, 100.0 * ratio, ratio)


def _upsert(env, rows):
    with env.connect() as c:
        bd.upsert_discontinuities(c.cursor(), rows)
        c.commit()


def test_a_discontinuity_deleted_after_the_scan_breaks_the_match_and_a_reappearance_gets_a_new_stamp_and_needs_a_new_scan(world):
    _upsert(world, [_row()])
    scan(world)
    assert evidence(world)["ok"]
    with world.connect() as c:
        c.cursor().execute("DELETE FROM price_discontinuities")                     # the provider restated it away (the builder's prune), outside any scan
        c.commit()
    ev = evidence(world)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_RESULT_MISMATCH
    _upsert(world, [_row()])                                                         # it reappears: a NEW first-detected stamp, so not the table the old scan produced
    assert evidence(world)["reason"] == inputs.SCAN_RESULT_MISMATCH
    assert scan(world) is not None and evidence(world)["ok"]


def test_a_material_ratio_change_restamps_the_row_and_breaks_the_match_but_jitter_does_not(world):
    _upsert(world, [_row()])
    scan(world)
    _upsert(world, [_row(ratio=1.5 * (1 + 1e-9))])                                   # dividend-adjustment jitter: stamp (and so the result fingerprint) unchanged
    assert evidence(world)["ok"]
    _upsert(world, [_row(ratio=1.6)])                                                 # the bar was restated: a different fact, known from now on
    ev = evidence(world)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_RESULT_MISMATCH
    scan(world)
    assert evidence(world)["ok"]


def test_delayed_detection_a_scan_that_completed_after_the_cutoff_is_not_evidence_and_a_retry_cannot_launder_it(world):
    late = scan(world, finished=CUTOFF + timedelta(seconds=1))
    ev = evidence(world)
    assert not ev["ok"] and ev["reason"] == inputs.SCAN_AFTER_CUTOFF and ev["detail"]["scan_id"] == late
    with pytest.raises(runner.ProvenanceRefused, match="scan_after_cutoff"):
        observed(world)
    assert scan(world) is None                                                       # an identical retry writes nothing: the FIRST completion time stands
    assert evidence(world)["reason"] == inputs.SCAN_AFTER_CUTOFF


def test_the_cutoff_boundary_is_exact(world):
    scan(world, finished=CUTOFF)
    assert evidence(world)["ok"]                                                     # completed at the cutoff instant: eligible
    forge(world, "UPDATE price_discontinuity_scan SET finished_at = %s", (CUTOFF + timedelta(microseconds=1),))
    assert evidence(world)["reason"] == inputs.SCAN_AFTER_CUTOFF                      # one microsecond later: not


def test_the_earliest_matching_scan_decides_not_the_latest(world):
    scan(world, finished=CUTOFF + timedelta(hours=5))                                # first completion: late
    _upsert(world, [_row()])
    scan(world, finished=CUTOFF - timedelta(hours=1))                                # a different result (so a different row), completed in time
    assert evidence(world)["ok"]                                                     # the matching scan for the CURRENT table is the second one
    with world.connect() as c:
        c.cursor().execute("DELETE FROM price_discontinuities")
        c.commit()
    assert evidence(world)["reason"] == inputs.SCAN_AFTER_CUTOFF                      # the empty table matches only the late scan again


# ---------------------------------------------------------------------------------------------------- database-stamped time, immutability, availability
def test_completion_time_is_the_database_clock_whatever_the_caller_writes(world):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT newest_bar, n_symbols, n_price_rows, fingerprint FROM research_price_input_fingerprint(%s)", (T,))
        newest, n_sym, n_rows, fp = cur.fetchone()
        cur.execute("SELECT n_rows, fingerprint FROM research_discontinuity_result_fingerprint()")
        n_found, rfp = cur.fetchone()
        cur.execute("INSERT INTO price_discontinuity_scan (session_date, status, newest_bar, n_symbols, n_price_rows, input_fingerprint, n_discontinuities, "
                    "result_fingerprint, started_at, finished_at, run_id, writer, code_ref) VALUES (%s, 'complete', %s, %s, %s, %s, %s, %s, "
                    "clock_timestamp(), TIMESTAMPTZ '2000-01-01 00:00:00+00', 'r', 'w', 'c') RETURNING finished_at, clock_timestamp()", (T, T, n_sym, n_rows, fp, n_found, rfp))
        stored, now = cur.fetchone()
        c.commit()
    assert now - stored < timedelta(seconds=5)                                      # the caller's year-2000 value was overwritten
    with world.connect() as c:
        with pytest.raises(psycopg2.errors.IntegrityConstraintViolation, match="started_at"):
            c.cursor().execute("INSERT INTO price_discontinuity_scan (session_date, status, failure_reason, started_at, run_id, writer, code_ref) "
                               "VALUES (%s, 'failed', 'x', clock_timestamp() + interval '1 day', 'r', 'w', 'c')", (T,))


def test_scan_evidence_is_immutable_even_for_the_table_owner(world):
    sid = scan(world)
    for sql in ("UPDATE price_discontinuity_scan SET status = 'failed'", "DELETE FROM price_discontinuity_scan", "TRUNCATE price_discontinuity_scan"):
        with world.connect() as c:
            with pytest.raises(psycopg2.errors.IntegrityConstraintViolation, match="append-only"):
                c.cursor().execute(sql)
    assert n_scans(world) == 1 and evidence(world)["detail"]["scan_id"] == sid
    with world.connect() as c:
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'price_discontinuity_scan'::regclass AND NOT tgisinternal AND tgenabled = 'A'")
        assert cur.fetchone()[0] == 3                                                # all three triggers are ENABLE ALWAYS (a replica-role session cannot skip them)


def test_without_migration_32_there_is_no_evidence_and_observed_fails_closed(runner_env):
    import uuid
    from mi_runner_fixtures import _args
    schema = "mi_nomig32_" + uuid.uuid4().hex[:8]
    admin = psycopg2.connect(**_args())
    try:
        admin.cursor().execute(f'CREATE SCHEMA "{schema}"')
        admin.commit()
        conn = psycopg2.connect(options=f"-c search_path={schema}", **_args())
        try:
            ev = inputs.discontinuity_scan_evidence(conn, T)
        finally:
            conn.close()
        assert not ev["ok"] and ev["reason"] == inputs.SCAN_UNAVAILABLE and "migration 32" in ev["detail"]["hint"]
    finally:
        admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.commit()
        admin.close()


def test_the_price_fingerprint_is_deterministic_order_independent_and_sensitive_to_every_detection_column(world):
    def fp():
        with world.connect() as c:
            cur = c.cursor()
            cur.execute("SELECT fingerprint, n_price_rows FROM research_price_input_fingerprint(%s)", (T,))
            return cur.fetchone()
    base = fp()
    assert fp() == base
    for col, delta in (("close", "0.01"), ("open", "0.01"), ("high", "0.01"), ("low", "0.01")):
        with world.connect() as c:
            c.cursor().execute(f"UPDATE stock_prices SET {col} = {col} + {delta} WHERE symbol = 'S0007' AND date = %s", (T - timedelta(days=5),))
            c.commit()
        assert fp()[0] != base[0], col
        with world.connect() as c:
            c.cursor().execute(f"UPDATE stock_prices SET {col} = {col} - {delta} WHERE symbol = 'S0007' AND date = %s", (T - timedelta(days=5),))
            c.commit()
        assert fp() == base, col
    assert world.connect is not None
