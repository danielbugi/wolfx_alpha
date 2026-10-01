"""deploy/db/rollback22.sql in a throwaway schema: apply 22 -> verify -> rollback -> verify absence and restoration of
migrations 19/20/21 (Release A rows intact) -> reapply -> verify. Plus the data-loss gate and the collision refusals.
Needs a role that owns the schema's objects (the default dev role does, so does CI's superuser)."""
import os

import psycopg2
import pytest

from conftest import ROOT, SESSION
from research import schema_fingerprint as fp
from screeners.signal_ledger_writer import ObservationLink, StrategyRef, write_signals

ROLLBACK = os.path.join(ROOT, "deploy", "db", "rollback22.sql")
MIGRATION = os.path.join(ROOT, "mechanism", "add_research_observation_tables.sql")
RESEARCH_TABLES = ["feature_set_registry", "feature_snapshot", "candidate_observation", "candidate_capture_run",
                   "research_maintenance_log", "research_maintenance_audit", "research_maintenance_session",
                   "research_capture_activation"]
LINEAGE_CONSTRAINTS = ["signal_ledger_observation_fk", "signal_ledger_snapshot_fk", "signal_ledger_feature_set_fk",
                       "signal_ledger_lineage_all_or_none"]


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_script(connect, path, approved=False):
    """Apply a script atomically, exactly as `psql -v ON_ERROR_STOP=1 -1` would: one transaction, any error rolls back."""
    with connect() as c:
        cur = c.cursor()
        if approved:
            cur.execute("SELECT set_config('research.rollback_data_loss_approved', 'yes', true)")
        try:
            cur.execute(read(path))
            c.commit()
        except Exception:
            c.rollback()
            raise


def state(connect):
    with connect() as c:
        return fp.classify(fp.fingerprint(c))


def names(c, sql):
    cur = c.cursor()
    cur.execute(sql)
    return {r[0] for r in cur.fetchall()}


def research_tables(c):
    return names(c, "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()") \
        & set(RESEARCH_TABLES)


def research_functions(c):
    return names(c, "SELECT p.proname FROM pg_proc p WHERE p.pronamespace = current_schema()::regnamespace "
                    "AND p.proname LIKE 'research\\_%'")


def ledger_constraints(c):
    return names(c, "SELECT conname FROM pg_constraint WHERE conrelid = 'signal_ledger'::regclass")


def ledger_columns(c):
    return names(c, "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'signal_ledger'")


class Db:
    def __init__(self, connect):
        self.connect = connect

    def execute_dict_query(self, sql, params=None):
        from psycopg2.extras import RealDictCursor
        with self.connect() as c:
            cur = c.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
            c.commit()
            return [dict(r) for r in rows]

    def execute_insert(self, sql, params=None):
        with self.connect() as c:
            c.cursor().execute(sql, params)
            c.commit()
            return True


def sig(symbol):
    return {"symbol": symbol, "signal_type": "bullish_breakout", "screening_date": SESSION, "current_price": 100.0,
            "atr_14": 2.0, "sector": "Technology", "quality_grade": "B", "screener_defaults": []}


def seed_release_a_ledger(connect, seed):
    """One Release A ledger row (NULL lineage) -- it must survive apply/rollback/reapply untouched."""
    ref = StrategyRef(seed.strategy_id(), "donchian_breakout", "v1")
    seed.conn.commit()  # release the read locks: the script under test takes ACCESS EXCLUSIVE locks
    assert write_signals(Db(connect), [sig("OLD")], SESSION, ref) == 1
    return ref


def ledger_rows(connect):
    return Db(connect).execute_dict_query(
        "SELECT symbol, direction, observation_id, feature_snapshot_id, feature_set_version FROM signal_ledger "
        "ORDER BY symbol")


def test_apply_verify_rollback_verify_reapply_verify(connect, seed):
    ref = seed_release_a_ledger(connect, seed)
    before = ledger_rows(connect)
    assert state(connect)[0] == fp.EXACT                        # apply + verify
    with connect() as c:
        assert research_tables(c) == set(RESEARCH_TABLES) and len(research_functions(c)) == 8
        assert set(LINEAGE_CONSTRAINTS) <= ledger_constraints(c)

    run_script(connect, ROLLBACK)                               # rollback: empty tables need no approval
    assert state(connect) == (fp.ABSENT, [])                    # verify absence
    with connect() as c:
        assert research_tables(c) == set() and research_functions(c) == set()
        assert not set(LINEAGE_CONSTRAINTS) & ledger_constraints(c)
        assert {"observation_id", "feature_snapshot_id", "feature_set_version"} <= ledger_columns(c)  # migration 21 kept
        assert names(c, "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()") \
            >= {"strategies", "signal_ledger"}
    assert ledger_rows(connect) == before                       # Release A data restored/untouched

    run_script(connect, MIGRATION)                              # reapply
    assert state(connect)[0] == fp.EXACT                        # verify
    assert ledger_rows(connect) == before
    del ref


def test_rollback_is_idempotent_only_in_the_sense_of_refusing_a_second_run(connect):
    run_script(connect, ROLLBACK)
    with pytest.raises(psycopg2.Error, match="nothing to roll back"):
        run_script(connect, ROLLBACK)
    assert state(connect) == (fp.ABSENT, [])


def test_refuses_to_destroy_data_without_explicit_approval(connect, seed):
    seed.observation()
    seed.conn.commit()
    with pytest.raises(psycopg2.Error, match="rollback_data_loss_approved"):
        run_script(connect, ROLLBACK)
    with connect() as c:                                        # atomic: nothing was dropped
        assert research_tables(c) == set(RESEARCH_TABLES)
        cur = c.cursor()
        cur.execute("SELECT count(*) FROM candidate_observation")
        assert cur.fetchone()[0] == 1
    assert state(connect)[0] == fp.EXACT


def test_approved_rollback_with_data_clears_lineage_and_keeps_release_a_rows(connect, seed):
    ref = seed_release_a_ledger(connect, seed)
    snap = seed.snapshot("AAA", SESSION)
    obs = seed.observation(snapshot_id=snap, symbol="AAA")
    seed.conn.commit()
    link = ObservationLink(obs, snap, "t0_v1")
    assert write_signals(Db(connect), [sig("AAA")], SESSION, ref, {("AAA", 1): link}) == 1
    assert {r["symbol"]: r["observation_id"] for r in ledger_rows(connect)} == {"AAA": obs, "OLD": None}

    with pytest.raises(psycopg2.Error, match="signal_ledger rows with lineage"):
        run_script(connect, ROLLBACK)                           # ledger lineage alone is enough to refuse (and the tables hold rows)

    run_script(connect, ROLLBACK, approved=True)
    assert state(connect) == (fp.ABSENT, [])
    rows = {r["symbol"]: r for r in ledger_rows(connect)}
    assert set(rows) == {"AAA", "OLD"}                          # no ledger row is ever deleted
    assert all(r["observation_id"] is None and r["feature_snapshot_id"] is None and r["feature_set_version"] is None
               for r in rows.values())

    run_script(connect, MIGRATION)
    assert state(connect)[0] == fp.EXACT


def test_refuses_a_lookalike_table_without_the_marker(connect):
    with connect() as c:
        cur = c.cursor()
        cur.execute("COMMENT ON TABLE candidate_observation IS NULL")
        c.commit()
    with pytest.raises(psycopg2.Error, match="not a migration-22 object"):
        run_script(connect, ROLLBACK)
    with connect() as c:
        assert research_tables(c) == set(RESEARCH_TABLES)


def test_refuses_a_partial_state(connect):
    with connect() as c:
        c.cursor().execute("DROP TABLE research_maintenance_audit")
        c.commit()
    with pytest.raises(psycopg2.Error, match="partial state"):
        run_script(connect, ROLLBACK)


def test_refuses_when_nothing_of_22_is_present(pre22_env):
    _, connect = pre22_env
    with pytest.raises(psycopg2.Error, match="nothing to roll back"):
        run_script(connect, ROLLBACK)


def test_script_states_its_safety_boundary_and_never_cascades():
    text = read(ROLLBACK)
    assert "SAFE ONLY BEFORE MEANINGFUL RELEASE B DATA EXISTS" in text
    assert "rollback_data_loss_approved" in text
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    assert "CASCADE" not in code.upper().replace("NO CASCADE", "")
    assert "TRUNCATE" not in code.upper() and "DELETE FROM" not in code.upper()
    assert "DROP ROLE" not in code.upper()
