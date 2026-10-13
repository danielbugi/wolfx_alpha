"""Migration 35 (strategy_variant) against a throwaway production-shaped DATABASE bootstrapped from docker-compose.yml's own init list with the real role script applied:

* fresh bootstrap creates exactly the intended objects (3 ENABLE ALWAYS triggers, marker comments) and the role script gives the runtime role SELECT + INSERT only;
* the registry works end to end as the LEAST-PRIVILEGE runtime role (foreign-key check, DB-stamped time, lineage trigger), and that role cannot update, delete or truncate;
* re-applying the migration (existing-schema compatibility, populated table) is a no-op that changes no row; a foreign table of the same name is refused;
* deploy/db/preflight_35.sql reports ABSENT / EXACT and stops on every other state, and writes nothing;
* deploy/db/rollback_35.sql removes exactly the migration-35 objects while empty, refuses (changing nothing) once a row exists unless the loss is approved in writing, is a no-op the
  second time, never touches strategies / signal_ledger, never uses CASCADE, and the migration re-applies afterwards."""
import os
import re

import psycopg2
import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
import full_env as FE
from forward_collection import activation as A
from research import strategy_variants as V

ROLLBACK = open(os.path.join(FE.ROOT, "deploy", "db", "rollback_35.sql"), encoding="utf-8").read()
PREFLIGHT = open(os.path.join(FE.ROOT, "deploy", "db", "preflight_35.sql"), encoding="utf-8").read()
MIGRATION_PATH = os.path.join(FE.ROOT, "mechanism", "add_strategy_variant_registry.sql")
MIGRATION = open(MIGRATION_PATH, encoding="utf-8").read()
ROLLBACK36 = open(os.path.join(FE.ROOT, "deploy", "db", "rollback_36.sql"), encoding="utf-8").read()
MIGRATION36 = open(os.path.join(FE.ROOT, "mechanism", "add_paper_tracker_tables.sql"), encoding="utf-8").read()
FUNCTIONS = {"research_strategy_variant_guard", "research_strategy_variant_stamp"}


def test_the_catalog_and_the_rollback_list_exactly_what_migration_35_creates():
    sql = re.sub(r"--[^\n]*", "", MIGRATION)                                      # the activation catalog is scoped to 24-32; same extraction rules, applied here
    assert sorted(set(A.TABLE_RE.findall(sql))) == ["strategy_variant"] and set(A.FUNC_RE.findall(sql)) == FUNCTIONS
    code = re.sub(r"--[^\n]*", "", ROLLBACK)
    assert not re.search(r"\bCASCADE\b", code, re.I)
    for forbidden in ("signal_ledger", "candidate_observation", "price_discontinuity", "stock_prices"):
        assert forbidden not in code, forbidden
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b", re.sub(r"--[^\n]*", "", PREFLIGHT), re.I)       # the preflight cannot write


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env("m35")
    try:
        yield e
    finally:
        teardown()


def scalar(env, sql, params=None):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]
    finally:
        conn.close()


def present(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('strategy_variant') IS NOT NULL")
        table = cur.fetchone()[0]
        cur.execute("SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND proname = ANY(%s)", (sorted(FUNCTIONS),))
        return table, {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def run_sql(env, sql, *, settings=(), search_path="public"):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute(f"SET search_path = {search_path}")
        for s in settings:
            cur.execute(s)
        cur.execute(sql)
        conn.commit()
        return list(conn.notices)
    finally:
        conn.close()


def rollback(env, *, approved=False, search_path="public"):
    """Migration 36 depends on strategy_variant, so its (empty) tables are rolled back first when present; rollback_35 itself refuses otherwise (see the dependency test)."""
    if search_path == "public" and scalar(env, "SELECT to_regclass('paper_observation') IS NOT NULL"):
        run_sql(env, ROLLBACK36)
    return run_sql(env, ROLLBACK, settings=["SET research.rollback_data_loss_approved = 'yes'"] if approved else (), search_path=search_path)


@pytest.fixture
def restored(env):
    yield
    env.exec_admin(MIGRATION)
    env.exec_admin(MIGRATION36)                                                   # 36 depends on 35: both are put back in order


# ---- fresh bootstrap + permissions --------------------------------------------------------------------------------------------------------------------------------

def test_a_fresh_bootstrap_has_exactly_the_intended_objects_and_least_privilege(env):
    assert present(env) == (True, FUNCTIONS)
    assert scalar(env, "SELECT obj_description(to_regclass('strategy_variant'), 'pg_class')") == "strategy_variant_migration_35"
    assert scalar(env, "SELECT count(*) FROM pg_trigger WHERE tgrelid = to_regclass('strategy_variant') AND NOT tgisinternal AND tgenabled = 'A'") == 3
    assert scalar(env, "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = to_regclass('strategy_variant')") == env.owner
    for priv, want in (("SELECT", True), ("INSERT", True), ("UPDATE", False), ("DELETE", False), ("TRUNCATE", False), ("REFERENCES", False), ("TRIGGER", False)):
        assert scalar(env, "SELECT has_table_privilege(%s, 'strategy_variant', %s)", (env.app, priv)) is want, priv
    assert scalar(env, "SELECT has_table_privilege('public', 'strategy_variant', 'SELECT')") is False
    assert scalar(env, "SELECT has_sequence_privilege(%s, pg_get_serial_sequence('strategy_variant', 'id'), 'USAGE')", (env.app,)) is True
    for fn in sorted(FUNCTIONS):
        assert scalar(env, f"SELECT has_function_privilege(%s, '{fn}()', 'EXECUTE')", (env.app,)) is False, fn


def test_the_registry_works_as_the_least_privilege_runtime_role_and_that_role_cannot_rewrite_it(env):
    conn = env.app_connect_factory()()
    try:
        cur = conn.cursor()
        cur.execute("SET search_path = public")
        out = V.register(cur, V.build_specs(policies=["A_atr_stop_fixed_3R"]))
        conn.commit()
        assert [o.action for o in out] == ["created"]
        assert [o.action for o in V.register(cur, V.build_specs(policies=["A_atr_stop_fixed_3R"]))] == ["unchanged"]
        conn.commit()
        for sql in ("UPDATE strategy_variant SET description = 'x'", "DELETE FROM strategy_variant", "TRUNCATE strategy_variant"):
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(sql)
            conn.rollback()
        cur.execute("SELECT count(*) FROM strategies")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()
    # a table owner / superuser is still stopped by the ENABLE ALWAYS triggers
    with pytest.raises(psycopg2.Error, match="append-only"):
        env.exec_admin("UPDATE strategy_variant SET description = 'x'")


# ---- existing-schema compatibility and idempotency ----------------------------------------------------------------------------------------------------------------

def test_reapplying_over_a_populated_table_is_a_no_op_and_the_live_identity_is_untouched(env):
    before = (scalar(env, "SELECT count(*) FROM strategy_variant"), scalar(env, "SELECT count(*) FROM strategies"), scalar(env, "SELECT count(*) FROM signal_ledger"),
              scalar(env, "SELECT md5(string_agg(id::text || definition_hash || created_at::text, ',' ORDER BY id)) FROM strategy_variant"))
    assert before[0] >= 1
    env.exec_admin(MIGRATION)
    env.exec_admin(MIGRATION)
    after = (scalar(env, "SELECT count(*) FROM strategy_variant"), scalar(env, "SELECT count(*) FROM strategies"), scalar(env, "SELECT count(*) FROM signal_ledger"),
             scalar(env, "SELECT md5(string_agg(id::text || definition_hash || created_at::text, ',' ORDER BY id)) FROM strategy_variant"))
    assert after == before
    assert scalar(env, "SELECT count(*) FROM pg_trigger WHERE tgrelid = to_regclass('strategy_variant') AND NOT tgisinternal") == 3


def test_a_foreign_table_or_function_of_the_same_name_is_refused(env):
    env.exec_admin("COMMENT ON TABLE strategy_variant IS NULL")
    try:
        with pytest.raises(psycopg2.Error, match="migration 35 refused"):
            env.exec_admin(MIGRATION)
        with pytest.raises(psycopg2.Error, match="not a migration-35 object"):
            rollback(env)
        with pytest.raises(psycopg2.Error, match="PREFLIGHT_35 STOP"):
            run_sql(env, PREFLIGHT)
    finally:
        env.exec_admin("COMMENT ON TABLE strategy_variant IS 'strategy_variant_migration_35'")
    assert present(env) == (True, FUNCTIONS)


def test_the_role_script_can_be_rerun_and_the_runtime_privileges_survive(env):
    env.exec_admin(env.roles_sql())
    assert scalar(env, "SELECT has_table_privilege(%s, 'strategy_variant', 'UPDATE')", (env.app,)) is False
    assert scalar(env, "SELECT has_table_privilege(%s, 'strategy_variant', 'INSERT')", (env.app,)) is True


# ---- preflight ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_preflight_reports_exact_when_applied_and_absent_before_and_writes_nothing(env, restored):
    assert any("PREFLIGHT_35 OK state=EXACT" in n for n in run_sql(env, PREFLIGHT, settings=["SET default_transaction_read_only = on"]))
    rollback(env, approved=True)
    notices = run_sql(env, PREFLIGHT, settings=["SET default_transaction_read_only = on"])
    assert any("PREFLIGHT_35 OK state=ABSENT" in n for n in notices), notices
    assert present(env) == (False, set())                                           # the preflight created nothing
    with pytest.raises(psycopg2.Error, match="roles are missing"):                 # the throwaway roles carry a prefix, so the production names are absent here
        run_sql(env, PREFLIGHT, settings=["SET research.preflight_require_roles = 'yes'"])


def test_the_preflight_stops_on_a_missing_parent_strategy(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT set_config('search_path', 'pg_catalog', false)")
        with pytest.raises(psycopg2.Error, match="strategies is missing"):
            cur.execute(PREFLIGHT)
    finally:
        conn.close()


def test_the_preflight_stops_when_a_trigger_is_not_enabled_always(env):
    env.exec_admin("ALTER TABLE strategy_variant DISABLE TRIGGER strategy_variant_stamp")
    try:
        with pytest.raises(psycopg2.Error, match="enabled ALWAYS"):
            run_sql(env, PREFLIGHT)
    finally:
        env.exec_admin("ALTER TABLE strategy_variant ENABLE ALWAYS TRIGGER strategy_variant_stamp")


# ---- rollback ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_row_of_identity_evidence_blocks_the_rollback_unless_approved_and_a_refusal_changes_nothing(env, restored):
    conn = env.admin()
    try:
        V.register(conn.cursor(), V.build_specs(policies=["B_donchian_trail_10"]))
        conn.commit()
    finally:
        conn.close()
    n = scalar(env, "SELECT count(*) FROM strategy_variant")
    assert n >= 1
    with pytest.raises(psycopg2.Error, match=f"holds {n} row"):
        rollback(env)
    assert present(env) == (True, FUNCTIONS) and scalar(env, "SELECT count(*) FROM strategy_variant") == n
    notices = rollback(env, approved=True)
    assert present(env) == (False, set()) and any(f"{n} row(s) removed" in x for x in notices), notices
    assert scalar(env, "SELECT count(*) FROM strategies") == 1 and scalar(env, "SELECT to_regclass('signal_ledger') IS NOT NULL") is True


def test_an_empty_table_is_dropped_a_second_run_is_a_no_op_and_the_wrong_schema_is_refused(env, restored):
    rollback(env, approved=True)                                                    # clear the rows of the previous tests
    env.exec_admin(MIGRATION)                                                       # re-create it empty
    assert present(env) == (True, FUNCTIONS)
    assert any("0 row(s) removed" in x for x in rollback(env))
    assert present(env) == (False, set())
    assert any("0 row(s) removed" in x for x in rollback(env))
    with pytest.raises(psycopg2.Error, match="wrong search_path"):
        rollback(env, search_path="pg_catalog")


def test_the_rollback_refuses_while_the_paper_tracker_tables_depend_on_the_registry(env, restored):
    assert scalar(env, "SELECT to_regclass('paper_observation') IS NOT NULL") is True
    with pytest.raises(psycopg2.Error, match="roll back 36 first"):
        run_sql(env, ROLLBACK, settings=["SET research.rollback_data_loss_approved = 'yes'"])
    assert present(env) == (True, FUNCTIONS) and scalar(env, "SELECT to_regclass('paper_observation') IS NOT NULL") is True       # nothing was dropped
