"""Migration 22 hardening against real Postgres: the collision guard (`IF NOT EXISTS` never blesses an unknown object),
the catalog fingerprint (preflight/verify judge REAL definitions), the added CHECKs, the activation-boundary writer and
the ledger lineage constraints. Everything runs in throwaway schemas -- nothing touches `public`."""
import copy
import os

import psycopg2
import psycopg2.errors
import pytest

from conftest import MIGRATIONS, ROOT, SESSION
from research import schema_fingerprint as fp

MIGRATION = os.path.join(ROOT, "mechanism", "add_research_observation_tables.sql")


def _sql():
    with open(MIGRATION, encoding="utf-8") as fh:
        return fh.read()


def _fails(conn, sql, params=None, match=None):
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(exc.value), str(exc.value)
    return exc.value


def _apply_refused(conn, match):
    cur = conn.cursor()
    cur.execute("SAVEPOINT m")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute(_sql())
    cur.execute("ROLLBACK TO SAVEPOINT m")
    assert match in str(exc.value), str(exc.value)


def _present(conn, name):
    cur = conn.cursor()
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    return cur.fetchone()[0]


@pytest.fixture
def pre(pre22_env):
    _, connect = pre22_env
    with connect() as c:
        yield c
        c.rollback()


# ------------------------------------------------------------------------------------------------ collision guard
def test_lookalike_table_is_refused_and_nothing_is_created(pre):
    cur = pre.cursor()
    cur.execute("CREATE TABLE candidate_observation (id SERIAL PRIMARY KEY, note TEXT)")
    _apply_refused(pre, "not a migration-22 object")
    assert not _present(pre, "feature_snapshot") and not _present(pre, "research_maintenance_log")


def test_lookalike_function_is_refused(pre):
    pre.cursor().execute("CREATE FUNCTION research_guard_immutable() RETURNS trigger LANGUAGE plpgsql "
                         "AS $$ BEGIN RETURN NEW; END $$")
    _apply_refused(pre, "function research_guard_immutable() already exists and is not a migration-22 object")
    assert not _present(pre, "candidate_observation")


def test_partially_applied_state_is_refused(pre):
    cur = pre.cursor()
    cur.execute("CREATE TABLE feature_set_registry (feature_set_version VARCHAR(30) PRIMARY KEY)")
    cur.execute("COMMENT ON TABLE feature_set_registry IS 'release_b_migration_22'")
    _apply_refused(pre, "1 of 8 research tables exist")


def test_same_named_index_on_another_relation_is_refused(pre):
    cur = pre.cursor()
    cur.execute("CREATE TABLE unrelated (a INT)")
    cur.execute("CREATE INDEX idx_feature_snapshot_session ON unrelated (a)")
    _apply_refused(pre, "already exists on a different relation")


def test_orphan_ledger_lineage_constraint_is_refused(pre):
    cur = pre.cursor()
    cur.execute("ALTER TABLE signal_ledger ADD CONSTRAINT signal_ledger_lineage_all_or_none CHECK (true)")
    _apply_refused(pre, "already carries a lineage constraint")


def test_missing_release_a_is_refused(pre):
    pre.cursor().execute("ALTER TABLE signal_ledger DROP COLUMN observation_id")
    _apply_refused(pre, "lineage columns from migration 21")


def test_apply_to_a_clean_database_then_reapply_is_a_noop(pre):
    cur = pre.cursor()
    cur.execute(_sql())
    before = fp.fingerprint(pre)
    cur.execute(_sql())
    assert fp.fingerprint(pre) == before
    assert fp.classify(before)[0] == fp.EXACT


# ------------------------------------------------------------------------------------------------ fingerprint
def test_fresh_apply_matches_the_committed_golden(conn):
    state, diffs = fp.classify(fp.fingerprint(conn))
    assert state == fp.EXACT, diffs[:10]


def test_pre22_is_absent(pre):
    assert fp.classify(fp.fingerprint(pre)) == (fp.ABSENT, [])


def test_lookalike_is_incompatible(pre):
    pre.cursor().execute("CREATE TABLE candidate_observation (id SERIAL PRIMARY KEY)")
    state, diffs = fp.classify(fp.fingerprint(pre))
    assert state == fp.INCOMPATIBLE and diffs


@pytest.mark.parametrize("tamper,needle", [
    ("ALTER TABLE candidate_observation DROP CONSTRAINT candidate_observation_signal_type_values", "signal_type_values"),
    ("ALTER TABLE candidate_observation DROP CONSTRAINT candidate_observation_atr_source_values", "atr_source_values"),
    ("ALTER TABLE candidate_observation ALTER COLUMN atr_source DROP NOT NULL", "atr_source"),
    ("ALTER TABLE candidate_observation ALTER COLUMN ml_status SET DEFAULT 'scored'", "ml_status"),
    ("ALTER TABLE candidate_observation ALTER COLUMN symbol TYPE VARCHAR(20)", "symbol"),
    ("ALTER TABLE candidate_observation ADD COLUMN sneaky INT", "sneaky"),
    ("ALTER TABLE feature_snapshot DROP CONSTRAINT feature_snapshot_identity_key", "identity_key"),
    ("ALTER TABLE candidate_observation DROP CONSTRAINT candidate_observation_snapshot_id_fkey", "snapshot_id_fkey"),
    ("DROP INDEX idx_candidate_observation_tracked", "idx_candidate_observation_tracked"),
    ("ALTER TABLE candidate_observation DISABLE TRIGGER candidate_observation_immutable_row", "immutable_row"),
    ("ALTER TABLE candidate_observation ENABLE REPLICA TRIGGER candidate_observation_immutable_row", "immutable_row"),
    ("DROP TRIGGER candidate_observation_immutable_truncate ON candidate_observation", "immutable_truncate"),
    ("ALTER TABLE signal_ledger DROP CONSTRAINT signal_ledger_observation_fk", "signal_ledger_observation_fk"),
    ("ALTER TABLE signal_ledger DROP CONSTRAINT signal_ledger_lineage_all_or_none", "all_or_none"),
    ("COMMENT ON TABLE candidate_observation IS NULL", "comment"),
    ("GRANT EXECUTE ON FUNCTION research_maintenance_begin(bigint) TO PUBLIC", "public_execute"),
    ("ALTER FUNCTION research_guard_immutable() SECURITY INVOKER", "security_definer"),
    ("ALTER FUNCTION research_guard_immutable() RESET search_path", "pins_search_path"),
    ("CREATE OR REPLACE FUNCTION research_audit_append_only() RETURNS trigger LANGUAGE plpgsql "
     "SET search_path = pg_temp AS $$ BEGIN RETURN NEW; END $$", "body_sha256"),
])
def test_fingerprint_detects_tampering(conn, tamper, needle):
    cur = conn.cursor()
    cur.execute(tamper)
    state, diffs = fp.classify(fp.fingerprint(conn))
    assert state == fp.INCOMPATIBLE, "tamper not detected: " + tamper
    assert any(needle in d for d in diffs), diffs[:5]


def test_function_body_normalises_line_endings():
    base = {"x": {"body_sha256": "a"}}
    assert fp.diff(base, copy.deepcopy(base)) == []


def test_preflight_and_verify_cli(conn, pre22_env, capsys):
    import check_research_migration_preflight as pf
    assert pf.run_verify(conn) == 0 and "VERIFIED" in capsys.readouterr().out
    assert pf.run_preflight(conn) == 0 and "EXACT" in capsys.readouterr().out
    _, connect = pre22_env
    with connect() as c:
        assert pf.run_preflight(c) == 0 and "ABSENT" in capsys.readouterr().out
        assert pf.run_verify(c) == 1 and "ABSENT" in capsys.readouterr().out
        c.cursor().execute("CREATE TABLE candidate_observation (id SERIAL PRIMARY KEY)")
        assert pf.run_preflight(c) == 1
        out = capsys.readouterr().out
        assert "INCOMPATIBLE" in out and "STOP" in out
        c.rollback()


def test_preflight_blocks_when_release_a_is_missing(pre22_env, capsys):
    import check_research_migration_preflight as pf
    _, connect = pre22_env
    with connect() as c:
        c.cursor().execute("DELETE FROM strategies")
        assert pf.run_preflight(c) == 1 and "BLOCKER" in capsys.readouterr().out
        c.rollback()


# ------------------------------------------------------------------------------------------------ CHECKs
@pytest.mark.parametrize("kw,constraint", [
    (dict(signal_type="mystery"), "candidate_observation_"),  # several CHECKs reject it; PG names the first
    (dict(signal_type="bullish_breakout", direction=-1), "direction_matches_signal"),
    (dict(signal_type="near_bearish", direction=1), "direction_matches_signal"),
    (dict(signal_type="bullish_breakout", triggered=False), "triggered_matches_signal"),
    (dict(signal_type="near_bullish", triggered=True), "triggered_matches_signal"),
    (dict(atr_source="guess"), "atr_source_values"),
    (dict(session_source="inferred"), "session_source"),
])
def test_observation_value_checks(seed, kw, constraint):
    snap, run = seed.snapshot("AAA"), seed.run()
    with pytest.raises(psycopg2.errors.CheckViolation) as exc:
        seed.observation(snap, run, "AAA", **kw)
    assert constraint in str(exc.value)


def test_observation_atr_source_is_required(seed):
    snap, run = seed.snapshot("AAA"), seed.run()
    with pytest.raises(psycopg2.errors.NotNullViolation):
        seed.observation(snap, run, "AAA", atr_source=None)


@pytest.mark.parametrize("signal_type,direction,triggered", [
    ("bullish_breakout", 1, True), ("near_bullish", 1, False),
    ("bearish_breakout", -1, True), ("near_bearish", -1, False)])
@pytest.mark.parametrize("atr_source", ["measured", "fallback", "missing"])
def test_every_real_class_and_atr_source_is_accepted(seed, signal_type, direction, triggered, atr_source):
    snap, run = seed.snapshot("AAA"), seed.run()
    seed.observation(snap, run, "AAA", signal_type=signal_type, direction=direction, triggered=triggered,
                     atr_source=atr_source)


def _finish(seed, run, **cols):
    base = dict(status="complete", run_finished_at="NOW()", candidates=3, captured=3, already_captured=0,
                stale_skipped=0, snapshot_skipped=0, invalid_skipped=0, guard_not_evaluated=0)
    base.update(cols)
    sets = ", ".join(f"{k} = {v}" if v == "NOW()" else f"{k} = %({k})s" for k, v in base.items())
    params = {k: v for k, v in base.items() if v != "NOW()"}
    params["id"] = run
    seed.execute(f"UPDATE candidate_capture_run SET {sets} WHERE id = %(id)s", params)


def test_run_status_values_and_accounting_checks(conn, seed):
    run = seed.run()
    _finish(seed, run)  # complete, fully accounted: accepted
    for kw, constraint in [
        (dict(status="disabled"), "status_values"),
        (dict(status="complete", snapshot_skipped=1, captured=2), "complete_is_accounted"),
        (dict(status="complete", invalid_skipped=1, captured=2), "complete_is_accounted"),
        (dict(status="complete", guard_not_evaluated=1), "complete_is_accounted"),
        (dict(status="complete", captured=2), "complete_is_accounted"),           # does not add up
        (dict(status="complete", error="'x'"), "complete_is_accounted"),
        (dict(status="complete", snapshot_skipped=None), "complete_is_accounted"),  # NULL must not slip through
        (dict(status="complete", candidates=None), "complete_is_accounted"),
        (dict(status="complete", invalid_skipped=None), "complete_is_accounted"),
        (dict(status="partial", captured=None), "partial_is_counted"),
        (dict(status="partial", error="x"), "partial_is_counted"),
        (dict(status="failed", error=None), "failed_has_error"),
        (dict(status="running"), "finished_iff_not_running"),                       # running but has a finish time
    ]:
        kw = {k: ("'" + v + "'" if k == "error" and isinstance(v, str) and not v.startswith("'") else v)
              for k, v in kw.items()}
        err_cols = {k: v for k, v in kw.items()}
        cur = conn.cursor()
        cur.execute("SAVEPOINT s")
        with pytest.raises(psycopg2.errors.CheckViolation) as exc:
            base = dict(status="complete", run_finished_at="NOW()", candidates=3, captured=3, already_captured=0,
                        stale_skipped=0, snapshot_skipped=0, invalid_skipped=0, guard_not_evaluated=0, error=None)
            base.update(err_cols)
            sets = ", ".join(f"{k} = {v}" if isinstance(v, str) and (v == "NOW()" or v.startswith("'")) else f"{k} = %({k})s"
                             for k, v in base.items())
            params = {k: v for k, v in base.items() if not (isinstance(v, str) and (v == "NOW()" or v.startswith("'")))}
            cur.execute(f"UPDATE candidate_capture_run SET {sets} WHERE id = %(id)s", dict(params, id=run))
        cur.execute("ROLLBACK TO SAVEPOINT s")
        assert constraint in str(exc.value), (kw, str(exc.value))


def test_partial_and_failed_runs_are_storable(seed):
    run = seed.run()
    _finish(seed, run, status="partial", snapshot_skipped=1, captured=2)
    run2 = seed.run(session=SESSION.replace(day=16))
    seed.execute("UPDATE candidate_capture_run SET status = 'failed', run_finished_at = NOW(), error = 'boom' WHERE id = %s", (run2,))


def test_a_run_can_only_ever_be_explicit(conn, seed):
    seed.registry()
    _fails(conn, "INSERT INTO candidate_capture_run (strategy_id, strategy_version, session_date, feature_set_version, "
                 "session_source) VALUES (%s, 'v1', %s, 't0_v1', 'wall_clock')", (seed.strategy_id(), SESSION),
           match="session_source_explicit")


# ------------------------------------------------------------------------------------------------ activation boundary
def _set(conn, sid, state, eff, note="activation under test"):
    cur = conn.cursor()
    cur.execute("SELECT research_capture_set_state(%s, %s, %s, %s)", (sid, state, eff, note))
    return cur.fetchone()[0]


def test_activation_boundary_rules(conn, seed):
    sid = seed.strategy_id()
    _fails(conn, "SELECT research_capture_set_state(%s, 'disabled', DATE '2099-02-01', 'first must be enabled')",
           (sid,), match="first boundary must be enabled")
    _fails(conn, "SELECT research_capture_set_state(%s, 'enabled', NULL, 'no date given at all')", (sid,),
           match="explicit effective_from")
    _fails(conn, "SELECT research_capture_set_state(999999, 'enabled', DATE '2099-02-01', 'unknown strategy id')",
           match="unknown strategy")
    _fails(conn, "SELECT research_capture_set_state(%s, 'enabled', DATE '2099-02-01', 'x')", (sid,),
           match="note_nonempty")
    _set(conn, sid, "enabled", "2099-02-01")
    _fails(conn, "SELECT research_capture_set_state(%s, 'enabled', DATE '2099-03-01', 'repeating the same state')",
           (sid,), match="no-op boundary")
    _fails(conn, "SELECT research_capture_set_state(%s, 'disabled', DATE '2099-02-01', 'same day as the boundary')",
           (sid,), match="must be after the latest boundary")
    _fails(conn, "SELECT research_capture_set_state(%s, 'disabled', DATE '2099-01-15', 'back-dated boundary')",
           (sid,), match="must be after the latest boundary")
    _set(conn, sid, "disabled", "2099-03-01")
    _set(conn, sid, "enabled", "2099-04-01")
    cur = conn.cursor()
    cur.execute("SELECT state, effective_from_session FROM research_capture_activation ORDER BY effective_from_session")
    assert [r[0] for r in cur.fetchall()] == ["enabled", "disabled", "enabled"]


def test_activation_cannot_postdate_an_already_captured_session(conn, seed):
    sid = seed.strategy_id()
    seed.run(session=SESSION)  # a capture already exists for 2099-01-15
    _fails(conn, "SELECT research_capture_set_state(%s, 'enabled', DATE '2099-01-15', 'covers a captured session')",
           (sid,), match="does not postdate the already-captured session")
    _set(conn, sid, "enabled", "2099-01-16")


def test_migration_seeds_no_activation_and_no_date(conn):
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM research_capture_activation")
    assert cur.fetchone()[0] == 0
    assert not any(ch.isdigit() and "2026" in line for line in _sql().splitlines()
                   if line.lstrip().startswith("INSERT INTO research_capture_activation") for ch in line[:1])


def test_activation_table_checks(conn, seed):
    sid = seed.strategy_id()
    _fails(conn, "INSERT INTO research_capture_activation (strategy_id, state, effective_from_session, note) "
                 "VALUES (%s, 'paused', '2099-02-01', 'a valid note')", (sid,), match="state_values")
    _fails(conn, "INSERT INTO research_capture_activation (strategy_id, state, effective_from_session, note) "
                 "VALUES (%s, 'enabled', NULL, 'a valid note')", (sid,))


# ------------------------------------------------------------------------------------------------ ledger lineage FKs
def _ledger_insert(seed, **cols):
    base = dict(symbol="AAA", signal_date=SESSION, direction=1, entry_price=10, atr=1, stop_price=8,
                target1_price=12, target2_price=14, target3_price=16, strategy_id=seed.strategy_id(),
                strategy_version="v1")
    base.update(cols)
    names = ", ".join(base)
    seed.execute(f"INSERT INTO signal_ledger ({names}) VALUES ({', '.join('%(' + k + ')s' for k in base)})", base)


def test_release_a_rows_without_lineage_are_valid(seed):
    _ledger_insert(seed)


def test_full_lineage_pointing_at_real_rows_is_valid(seed):
    obs = seed.observation(symbol="AAA")
    cur = seed.execute("SELECT snapshot_id FROM candidate_observation WHERE id = %s", (obs,))
    snap = cur.fetchone()[0]
    _ledger_insert(seed, observation_id=obs, feature_snapshot_id=snap, feature_set_version="t0_v1")


def test_dangling_lineage_is_refused(seed):
    seed.registry()
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        _ledger_insert(seed, observation_id=999999, feature_snapshot_id=999999, feature_set_version="t0_v1")


@pytest.mark.parametrize("cols", [
    dict(observation_id=1), dict(feature_snapshot_id=1), dict(feature_set_version="t0_v1"),
    dict(observation_id=1, feature_snapshot_id=1)])
def test_partial_lineage_is_refused(seed, cols):
    seed.registry()
    with pytest.raises((psycopg2.errors.CheckViolation, psycopg2.errors.ForeignKeyViolation)):
        _ledger_insert(seed, **cols)
