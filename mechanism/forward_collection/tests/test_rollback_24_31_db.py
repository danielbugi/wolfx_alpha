"""The rollback of migrations 24..31 (deploy/db/rollback_24_31.sql), run for real against a throwaway production-shaped database.

Proven: its table and function lists equal what the compose migration list creates (so a new table cannot be forgotten); an empty set is dropped cleanly and
migrations 22/23, signal_ledger and every pre-existing table are untouched; a table holding a row blocks it unless the loss is approved in writing, and a refusal
changes nothing; a second run is a no-op; it refuses on the wrong schema; and the migrations re-apply afterwards (the forward path is not damaged)."""
import os
import re

import psycopg2
import pytest

import full_env as FE
from forward_collection import activation as A

SQL_PATH = os.path.join(FE.ROOT, "deploy", "db", "rollback_24_31.sql")
SQL = open(SQL_PATH, encoding="utf-8").read()
MIGRATIONS = (24, 25, 26, 27, 28, 29, 30, 31)


def catalog():
    cat = A.migration_catalog()
    return ({t for n in MIGRATIONS for t in cat[n]["tables"]}, {f for n in MIGRATIONS for f in cat[n]["functions"]})


def listed(name):
    body = re.search(name + r"\s+TEXT\[\]\s*:=\s*ARRAY\[(.*?)\];", SQL, re.S).group(1)
    return set(re.findall(r"'([a-z_0-9]+)'", re.sub(r"--[^\n]*", "", body)))


def test_the_rollback_covers_exactly_what_migrations_24_to_31_create():
    tables, functions = catalog()
    assert listed("tbls") == tables and len(tables) == 16
    assert listed("fns") == functions and len(functions) == 20


def test_the_rollback_never_cascades_and_never_names_a_migration_22_object_or_the_ledger():
    code = re.sub(r"--[^\n]*", "", SQL)
    assert not re.search(r"\bCASCADE\b", code, re.I)
    for forbidden in ("candidate_observation", "feature_snapshot", "candidate_capture_run", "research_capture_activation", "daily_fundamentals", "stock_prices"):
        assert forbidden not in code, forbidden
    assert code.count("signal_ledger") == 2                       # only the wrong-schema guard (its test and its message)


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env("rb")
    try:
        yield e
    finally:
        teardown()


def apply_24_31(env):
    cat = A.migration_catalog()
    for n in MIGRATIONS:
        env.exec_admin(open(os.path.join(FE.ROOT, cat[n]["file"]), encoding="utf-8").read())


def present(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        tables, functions = catalog()
        cur.execute("SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r','p') AND relname = ANY(%s)", (sorted(tables),))
        got_t = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND proname = ANY(%s)", (sorted(functions),))
        got_f = {r[0] for r in cur.fetchall()}
        return got_t, got_f
    finally:
        conn.close()


def untouched(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' AND relname = ANY(%s) ORDER BY 1",
                    (["candidate_observation", "feature_snapshot", "candidate_capture_run", "research_capture_activation", "signal_ledger", "stock_prices",
                      "daily_fundamentals"],))
        return [r[0] for r in cur.fetchall()]
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
    apply_24_31(env)


def test_an_empty_set_is_dropped_cleanly_and_nothing_else_is_touched(env, restored):
    tables, functions = catalog()
    before = untouched(env)
    assert present(env) == (tables, functions)
    notices = run_rollback(env)
    assert present(env) == (set(), set())
    assert untouched(env) == before and "signal_ledger" in before
    assert any("rollback_24_31 OK" in n and "16 table(s) dropped" in n and "20 function(s) dropped" in n for n in notices), notices


def test_a_second_run_is_a_no_op(env, restored):
    run_rollback(env)
    notices = run_rollback(env)
    assert any("0 table(s) dropped" in n and "0 function(s) dropped" in n for n in notices), notices


def test_a_row_of_evidence_blocks_the_rollback_unless_the_loss_is_approved_and_a_refusal_changes_nothing(env, restored):
    tables, functions = catalog()
    env.exec_admin("INSERT INTO sector_poll (run_id, symbol, source, response_state, chain_effect, failure_reason, writer, code_ref) "
                   "VALUES ('rb-1', 'AAA', 'yfinance_info', 'request_failed', 'none', 'timeout', 'test', 'test')")
    with pytest.raises(psycopg2.Error) as e:
        run_rollback(env)
    assert "refused" in str(e.value) and "sector_poll=1" in str(e.value)
    assert present(env) == (tables, functions)                                              # nothing was dropped
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM sector_poll")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()
    notices = run_rollback(env, approved=True)
    assert present(env) == (set(), set())
    assert any("1 row(s) discarded" in n and "sector_poll=1" in n for n in notices), notices


def test_it_refuses_on_a_schema_that_has_no_ledger(env, restored):
    env.exec_admin("CREATE SCHEMA IF NOT EXISTS elsewhere")
    with pytest.raises(psycopg2.Error) as e:
        run_rollback(env, search_path="elsewhere")
    assert "wrong search_path" in str(e.value)
    assert present(env) == catalog()


def test_the_forward_migrations_apply_again_after_a_rollback(env):
    tables, functions = catalog()
    run_rollback(env)
    apply_24_31(env)
    assert present(env) == (tables, functions)
