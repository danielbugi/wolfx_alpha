"""The rollback of migration 32 (deploy/db/rollback_32.sql), run for real against a throwaway production-shaped database: it removes exactly the migration-32 objects
while the evidence table is empty, refuses (changing nothing) once a row exists unless the loss is approved in writing, is a no-op the second time, never touches
price_discontinuities / migrations 22-31 / the ledger, never uses CASCADE, and the migration re-applies afterwards."""
import os
import re

import psycopg2
import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
import full_env as FE
from forward_collection import activation as A

SQL = open(os.path.join(FE.ROOT, "deploy", "db", "rollback_32.sql"), encoding="utf-8").read()
MIGRATION = os.path.join(FE.ROOT, "mechanism", "add_price_discontinuity_scan.sql")
FUNCTIONS = {"research_discontinuity_scan_stamp", "research_discontinuity_scan_guard", "research_price_input_fingerprint", "research_discontinuity_result_fingerprint"}


def test_the_rollback_lists_exactly_what_migration_32_creates():
    cat = A.migration_catalog()[32]
    assert cat["tables"] == ["price_discontinuity_scan"] and set(cat["functions"]) == FUNCTIONS
    code = re.sub(r"--[^\n]*", "", SQL)
    assert not re.search(r"\bCASCADE\b", code, re.I)
    for forbidden in ("price_discontinuities", "candidate_observation", "sector_poll", "daily_fundamentals", "stock_prices"):
        assert forbidden not in code, forbidden


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env("rb32")
    try:
        yield e
    finally:
        teardown()


def present(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('price_discontinuity_scan') IS NOT NULL")
        table = cur.fetchone()[0]
        cur.execute("SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND proname = ANY(%s)", (sorted(FUNCTIONS),))
        return table, {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def run_rollback(env, *, approved=False, search_path="public"):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute(f"SET search_path = {search_path}")
        if approved:
            cur.execute("SET research.rollback_data_loss_approved = 'yes'")
        cur.execute(SQL)
        conn.commit()
        return list(conn.notices)
    finally:
        conn.close()


@pytest.fixture
def restored(env):
    yield
    env.exec_admin(open(MIGRATION, encoding="utf-8").read())


def test_an_empty_table_is_dropped_with_its_functions_and_nothing_else_is_touched(env, restored):
    assert present(env) == (True, FUNCTIONS)
    notices = run_rollback(env)
    assert present(env) == (False, set())
    assert any("migration 32 rolled back (0 row(s) removed)" in n for n in notices), notices
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relname IN ('price_discontinuities', 'signal_ledger', 'sector_poll', 'candidate_observation')")
        assert cur.fetchone()[0] == 4
    finally:
        conn.close()


def test_a_second_run_is_a_no_op_and_the_wrong_schema_is_refused(env, restored):
    run_rollback(env)
    assert any("0 row(s) removed" in n for n in run_rollback(env))
    with pytest.raises(psycopg2.Error, match="wrong search_path"):
        run_rollback(env, search_path="pg_catalog")


def test_a_row_of_evidence_blocks_the_rollback_unless_approved_and_a_refusal_changes_nothing(env, restored):
    env.exec_admin("INSERT INTO price_discontinuity_scan (session_date, status, failure_reason, started_at, run_id, writer, code_ref) "
                   "VALUES (DATE '2026-01-05', 'failed', 'scan_exception', clock_timestamp(), 'rb-1', 'test', 'test')")
    with pytest.raises(psycopg2.Error, match="holds 1 row"):
        run_rollback(env)
    assert present(env) == (True, FUNCTIONS)                                       # nothing was dropped
    notices = run_rollback(env, approved=True)
    assert present(env) == (False, set()) and any("1 row(s) removed" in n for n in notices), notices


def test_an_object_that_is_not_ours_is_never_dropped(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("COMMENT ON TABLE price_discontinuity_scan IS 'something else'")
        conn.commit()
        with pytest.raises(psycopg2.Error, match="not a migration-32 object"):
            run_rollback(env)
        cur.execute("COMMENT ON TABLE price_discontinuity_scan IS 'discontinuity_scan_migration_32'")
        conn.commit()
    finally:
        conn.close()
    assert present(env) == (True, FUNCTIONS)


def test_the_migration_is_idempotent_and_refuses_a_foreign_table_of_the_same_name(env):
    env.exec_admin(open(MIGRATION, encoding="utf-8").read())                        # re-applying over its own objects is a no-op
    assert present(env) == (True, FUNCTIONS)
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("COMMENT ON TABLE price_discontinuity_scan IS NULL")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(psycopg2.Error, match="migration 32 refused"):
        env.exec_admin(open(MIGRATION, encoding="utf-8").read())
    env.exec_admin("COMMENT ON TABLE price_discontinuity_scan IS 'discontinuity_scan_migration_32'")
