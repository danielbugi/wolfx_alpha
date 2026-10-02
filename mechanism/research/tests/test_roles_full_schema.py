"""The Release B role model against the WHOLE production-shaped schema (not just the research tables).

test_roles.py proves the research-layer guarantees on a throwaway schema holding migrations 19-22 only. That cannot
catch a runtime object the role script forgot, because those schemas contain none of them -- and that is exactly how
the three production views were missed. This module bootstraps a throwaway DATABASE from docker-compose.yml's init
list (the same 01..N files production was built from), applies the role script under throwaway role names, and proves:

  * the verify script passes on the full schema and notices the drift classes it claims to notice;
  * the runtime role can do what the services do (read every view, advance every sequence, DML on every baseline
    table, call every function, run the real model-registry code);
  * the runtime role cannot do schema/maintenance operations (the reason migration 23 exists).

Needs a SUPERUSER DB role (CREATE DATABASE / CREATE ROLE); skips otherwise. CI's trading_user is one.
"""
import os
import re
import uuid

import psycopg2
import psycopg2.errors
import pytest

from conftest import (ROOT, ROLES_ROLLBACK_SQL, ROLES_SQL, ROLES_VERIFY_SQL, _connect_args, read_sql, strip_psql_meta)

RESEARCH = {"feature_set_registry", "feature_snapshot", "candidate_observation", "candidate_capture_run",
            "research_capture_activation", "research_maintenance_log", "research_maintenance_session",
            "research_maintenance_audit",
            # Market Intelligence (migrations 24/25): append-only, runtime INSERT/SELECT -- not the DML baseline
            "universe_snapshot", "market_snapshot", "sector_snapshot", "market_event", "market_event_revision"}
APP_PASSWORD = "rb-full-schema-test-only"


def _init_files():
    """The compose init list, in apply order -- production's own bootstrap, not a hand-maintained copy."""
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
        text = fh.read()
    pairs = re.findall(r"\./(mechanism/[\w./-]+\.sql):/docker-entrypoint-initdb\.d/(\d+)_", text)
    assert pairs, "no init mounts found in docker-compose.yml"
    return [os.path.join(ROOT, p) for p, _ in sorted(pairs, key=lambda x: int(x[1]))]


class FullEnv:
    def __init__(self, dbname, prefix, args):
        self.dbname, self.args, self.prefix = dbname, args, prefix
        self.names = {"donchian_owner": f"{prefix}_owner", "donchian_app": f"{prefix}_app",
                      "donchian_research_admin": f"{prefix}_admin"}
        self.owner, self.app, self.group = (self.names[k] for k in ("donchian_owner", "donchian_app",
                                                                    "donchian_research_admin"))
        self.db_args = dict(args, dbname=dbname)

    def admin(self):
        conn = psycopg2.connect(**self.db_args)
        conn.cursor().execute("SET search_path = public")
        conn.commit()
        return conn

    def sql(self, path):
        return strip_psql_meta(read_sql(path, self.names))

    def run_script(self, path, **gucs):
        conn = self.admin()
        try:
            cur = conn.cursor()
            for k, v in gucs.items():
                cur.execute("SELECT set_config(%s, %s, false)", (k.replace("__", "."), v))
            conn.commit()
            cur.execute(self.sql(path))
            conn.commit()
        finally:
            conn.close()

    def app_conn(self):
        """A real password login as the runtime role -- what the services do once DB_USER is switched."""
        return psycopg2.connect(**dict(self.db_args, user=self.app, password=APP_PASSWORD))

    def exec_admin(self, sql, *params):
        conn = self.admin()
        try:
            conn.cursor().execute(sql, params or None)
            conn.commit()
        finally:
            conn.close()


@pytest.fixture(scope="module")
def full():
    args = _connect_args()
    try:
        boot = psycopg2.connect(**dict(args, dbname=args["dbname"]))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    cur = boot.cursor()
    cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    if not cur.fetchone()[0]:
        boot.close()
        pytest.skip("needs a superuser DB role (CREATE DATABASE/ROLE); CI's trading_user is one")
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'trading_user'")
    if not cur.fetchone():
        boot.close()
        pytest.skip("the baseline init files assume a 'trading_user' role (production's bootstrap identity)")
    boot.rollback()
    boot.autocommit = True
    prefix = "rbf_" + uuid.uuid4().hex[:8]
    dbname = prefix + "_db"
    bcur = boot.cursor()
    bcur.execute(f'CREATE DATABASE "{dbname}"')
    env = FullEnv(dbname, prefix, args)
    try:
        conn = env.admin()
        cur = conn.cursor()
        for path in _init_files():
            with open(path, encoding="utf-8") as fh:
                cur.execute(fh.read())
            conn.commit()
        conn.close()
        env.run_script(ROLES_SQL)
        env.exec_admin(f"ALTER ROLE \"{env.app}\" PASSWORD '{APP_PASSWORD}'")
        yield env
    finally:
        try:
            bcur.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{dbname}' "
                        "AND pid <> pg_backend_pid()")
            bcur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            for r in (env.app, env.group, env.owner):
                bcur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,))
                if bcur.fetchone():
                    bcur.execute(f'DROP ROLE "{r}"')
        finally:
            boot.close()


@pytest.fixture
def snapshot_state(full):
    """Tests that deliberately break a privilege restore it through the role script itself (idempotent)."""
    yield
    full.run_script(ROLES_SQL)


def _catalog(full, sql, *params):
    conn = full.admin()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or None)
        return cur.fetchall()
    finally:
        conn.close()


def _baseline_tables(full):
    return [r[0] for r in _catalog(
        full, "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r','p') "
              "AND relname <> ALL(%s) ORDER BY 1", sorted(RESEARCH))]


def _refused(conn, sql):
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute(sql)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    return exc.value


# ------------------------------------------------------------------------------------------------ the schema shape
def test_the_bootstrap_contains_views_sequences_and_functions(full):
    """Guards the rehearsal itself: if the init list stopped producing these object kinds the coverage tests below
    would pass vacuously."""
    kinds = dict(_catalog(full, "SELECT relkind, count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                                "AND relkind IN ('r','v','m','S') GROUP BY 1"))
    assert kinds.get("r", 0) >= 40 and kinds.get("v", 0) >= 3 and kinds.get("S", 0) >= 10, kinds
    assert _catalog(full, "SELECT count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
                          "AND proname NOT LIKE 'research\\_%%'")[0][0] >= 1


def test_verify_passes_on_the_full_schema_and_is_idempotent(full):
    full.run_script(ROLES_SQL)
    full.run_script(ROLES_VERIFY_SQL)
    full.run_script(ROLES_VERIFY_SQL)


# --------------------------------------------------------------------------------------- the runtime role can work
def test_app_can_select_every_view(full):
    views = [r[0] for r in _catalog(full, "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                                          "AND relkind IN ('v','m') ORDER BY 1")]
    assert views
    conn = full.app_conn()
    try:
        cur = conn.cursor()
        for v in views:
            cur.execute(f'SELECT * FROM "{v}" LIMIT 0')
    finally:
        conn.close()


def test_app_can_advance_every_non_research_sequence(full):
    seqs = [r[0] for r in _catalog(
        full, "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'S' "
              "AND NOT EXISTS (SELECT 1 FROM pg_depend d JOIN pg_class t ON t.oid = d.refobjid WHERE d.objid = c.oid "
              "AND d.deptype IN ('a','i') AND t.relname = ANY(%s)) ORDER BY 1", sorted(RESEARCH))]
    assert seqs
    conn = full.app_conn()
    try:
        cur = conn.cursor()
        for s in seqs:
            cur.execute("SELECT nextval(%s::regclass)", (s,))
            cur.execute("SELECT last_value FROM " + f'"{s}"')
    finally:
        conn.rollback()
        conn.close()


def test_app_has_dml_on_every_baseline_table(full):
    tables = _baseline_tables(full)
    assert len(tables) >= 40
    conn = full.app_conn()
    try:
        cur = conn.cursor()
        for t in tables:
            cur.execute(f'SELECT 1 FROM "{t}" WHERE false')
            cur.execute("SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 "
                        "AND NOT attisdropped ORDER BY attnum LIMIT 1", (t,))
            col = cur.fetchone()[0]
            cur.execute(f'UPDATE "{t}" SET "{col}" = "{col}" WHERE false')
            cur.execute(f'DELETE FROM "{t}" WHERE false')
            cur.execute(f'INSERT INTO "{t}" SELECT * FROM "{t}" WHERE false')
            cur.execute(f'SELECT 1 FROM "{t}" WHERE false FOR UPDATE')
        conn.rollback()
    finally:
        conn.close()


def test_app_can_execute_the_non_research_functions(full):
    sigs = [r[0] for r in _catalog(full, "SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace = "
                                         "'public'::regnamespace AND p.prokind IN ('f','p') "
                                         "AND p.proname NOT LIKE 'research\\_%%'")]
    assert sigs
    conn = full.app_conn()
    try:
        cur = conn.cursor()
        for sig in sigs:
            cur.execute("SELECT has_function_privilege(current_user, %s::regprocedure, 'EXECUTE')", (sig,))
            assert cur.fetchone()[0], sig
    finally:
        conn.close()


def test_app_cannot_execute_the_research_maintenance_functions(full):
    conn = full.app_conn()
    try:
        for fn in ("research_maintenance_open(text, text, int)", "research_maintenance_approve(bigint)",
                   "research_maintenance_begin(bigint)", "research_maintenance_close(bigint)"):
            cur = conn.cursor()
            cur.execute("SELECT has_function_privilege(current_user, %s::regprocedure, 'EXECUTE')", (fn,))
            assert cur.fetchone()[0] is False, fn
    finally:
        conn.close()


def test_the_real_model_registry_code_runs_as_the_runtime_role(full):
    """Not a hand-written statement: the actual momentum_predictor read check and register_model, over a real
    password login as the non-owner runtime role. This is the path that used to need table ownership."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "ml_training", "models"))
    import momentum_predictor as mp

    conn = full.app_conn()
    try:
        assert mp.missing_registry_columns(conn) == []
    finally:
        conn.close()

    p = mp.MomentumBreakoutPredictor("momentum")
    p.db_config = dict(host=full.args["host"], port=full.args["port"], dbname=full.dbname, user=full.app,
                       password=APP_PASSWORD)
    p.model_version = "rbf_test_" + uuid.uuid4().hex[:6]
    report = {"n_train": 10, "holdout": {"auc": 0.5}}
    p.register_model(report, "2020-01-01", "2020-12-31", "x.json")
    rows = _catalog(full, "SELECT is_active, target FROM ml_models WHERE version = %s", p.model_version)
    assert rows == [(True, "momentum")]


def test_the_registry_check_fails_closed_when_migration_23_is_absent(full):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "ml_training", "models"))
    import momentum_predictor as mp
    full.exec_admin("ALTER TABLE ml_models DROP COLUMN evaluation")
    try:
        conn = full.app_conn()
        try:
            assert mp.missing_registry_columns(conn) == ["evaluation"]
        finally:
            conn.close()
    finally:
        full.exec_admin("ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS evaluation JSONB")


# ------------------------------------------------------------------------------------- the runtime role cannot DDL
@pytest.mark.parametrize("sql", [
    "ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS evaluation JSONB",  # the exact statement the old runtime DDL ran
    "ALTER TABLE ml_models ADD COLUMN zz_probe INT",
    "ALTER TABLE ml_models DROP COLUMN target",
    "ALTER TABLE ml_models RENAME TO zz_probe",
    "DROP TABLE ml_models",
    "TRUNCATE ml_models",
    "CREATE TABLE zz_probe (x INT)",
    "CREATE VIEW zz_probe AS SELECT 1",
    "CREATE INDEX zz_probe ON ml_models (id)",
    "CREATE SCHEMA zz_probe",
    "CREATE ROLE zz_probe",
    "ALTER ROLE CURRENT_USER SUPERUSER",
    "COMMENT ON TABLE ml_models IS 'x'",
    "ALTER SEQUENCE ml_models_id_seq RESTART",
    "SET session_replication_role = replica",
    "COPY (SELECT 1) TO PROGRAM 'true'",
])
def test_app_is_refused_schema_and_maintenance_operations(full, sql):
    conn = full.app_conn()
    try:
        err = _refused(conn, sql)
        assert isinstance(err, psycopg2.errors.InsufficientPrivilege), (sql, err)
    finally:
        conn.close()


def test_app_owns_nothing_and_has_no_dangerous_attributes(full):
    assert _catalog(full, "SELECT count(*) FROM pg_class WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = %s)",
                    full.app)[0][0] == 0
    assert _catalog(full, "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles "
                          "WHERE rolname = %s", full.app) == [(False, False, False, False, False)]


# ------------------------------------------------------------------------------------------------ verify catches drift
def _verify_fails(full, expected):
    with pytest.raises(psycopg2.Error) as exc:
        full.run_script(ROLES_VERIFY_SQL)
    msg = str(exc.value)
    for fixed, throwaway in full.names.items():
        msg = msg.replace(throwaway, fixed)
    assert "FAIL" in msg and expected in msg, msg


@pytest.mark.parametrize("break_sql,expected", [
    ("REVOKE SELECT ON {view} FROM {app}", "cannot SELECT from view"),
    ("REVOKE USAGE ON SEQUENCE {seq} FROM {app}", "lacks USAGE/SELECT/UPDATE on sequence"),
    ("REVOKE DELETE ON {table} FROM {app}", "lacks DELETE on table"),
    ("GRANT TRUNCATE ON {table} TO {app}", "has TRUNCATE on table"),
    ("GRANT INSERT ON {view} TO {app}", "has INSERT on view"),
    ("GRANT CREATE ON SCHEMA public TO PUBLIC", "may CREATE in schema"),
    ("GRANT CREATE ON DATABASE {db} TO {app}", "may CREATE schemas in database"),
    ("REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC, {app}", "cannot EXECUTE"),
])
def test_verify_detects_runtime_coverage_drift(full, snapshot_state, break_sql, expected):
    view = _catalog(full, "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'v' "
                          "ORDER BY 1 LIMIT 1")[0][0]
    seq = _catalog(full, "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'S' "
                         "AND c.relname NOT LIKE 'candidate\\_%%' AND c.relname NOT LIKE 'research\\_%%' "
                         "AND c.relname NOT LIKE 'feature\\_%%' ORDER BY 1 LIMIT 1")[0][0]
    fn = _catalog(full, "SELECT p.oid::regprocedure::text FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace "
                        "AND p.prokind IN ('f','p') AND p.proname NOT LIKE 'research\\_%%' LIMIT 1")[0][0]
    table = "ml_models"
    full.exec_admin(break_sql.format(view=f'"{view}"', seq=f'"{seq}"', table=table, fn=fn, app=f'"{full.app}"',
                                     db=f'"{full.dbname}"'))
    _verify_fails(full, expected)


def test_verify_detects_a_table_added_after_the_role_script(full, snapshot_state):
    """A later migration's table is NOT covered until the (idempotent) role script is re-run -- by design, there are no
    default privileges -- and verify must say so rather than letting the runtime role discover it in production."""
    full.exec_admin("CREATE TABLE zz_added_later (id SERIAL PRIMARY KEY, x INT)")
    try:
        _verify_fails(full, "lacks")
        full.run_script(ROLES_SQL)
        full.run_script(ROLES_VERIFY_SQL)
    finally:
        full.exec_admin("DROP TABLE IF EXISTS zz_added_later")


def test_rollback_strips_views_sequences_and_functions_too(full):
    original = _catalog(full, "SELECT current_user")[0][0]
    full.run_script(ROLES_ROLLBACK_SQL, research_roles__original_owner=original)
    try:
        view = _catalog(full, "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                              "AND relkind = 'v' ORDER BY 1 LIMIT 1")[0][0]
        assert _catalog(full, "SELECT has_table_privilege(%s, %s, 'SELECT')", full.app, view)[0][0] is False
        assert _catalog(full, "SELECT has_table_privilege(%s, 'ml_models', 'SELECT')", full.app)[0][0] is False
        assert _catalog(full, "SELECT has_sequence_privilege(%s, 'ml_models_id_seq', 'USAGE')", full.app)[0][0] is False
        assert _catalog(full, "SELECT count(*) FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace "
                              "AND p.prokind IN ('f','p') AND p.proname NOT LIKE 'research\\_%%' "
                              "AND has_function_privilege(%s, p.oid, 'EXECUTE') "
                              "AND NOT EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) a "
                              "WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE')", full.app)[0][0] == 0
    finally:
        full.run_script(ROLES_SQL)
        full.run_script(ROLES_VERIFY_SQL)


# ------------------------------------------------------------------ Market Intelligence tables (migrations 24 / 25)
MI_TABLES = ["universe_snapshot", "market_snapshot", "sector_snapshot", "market_event", "market_event_revision"]


def test_mi_tables_are_append_only_for_the_runtime_role(full):
    """INSERT + SELECT only: no UPDATE / DELETE / TRUNCATE for the runtime role, and no write of any kind for the admin group
    (there is no maintenance hatch for these tables)."""
    for t in MI_TABLES:
        assert _catalog(full, "SELECT has_table_privilege(%s, %s, 'SELECT'), has_table_privilege(%s, %s, 'INSERT')",
                        full.app, t, full.app, t) == [(True, True)], t
        for priv in ("UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
            assert _catalog(full, "SELECT has_table_privilege(%s, %s, %s)", full.app, t, priv)[0][0] is False, (t, priv)
        for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert _catalog(full, "SELECT has_table_privilege(%s, %s, %s)", full.group, t, priv)[0][0] is False, (t, priv)
        assert _catalog(full, "SELECT has_table_privilege('public', %s, 'SELECT')", t)[0][0] is False, t
        assert _catalog(full, "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = %s::regclass", t)[0][0] == full.owner, t


def test_mi_runtime_role_can_append_events_and_the_database_stamps_ingestion(full):
    key = "pytest:" + uuid.uuid4().hex
    conn = full.app_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO market_event (event_key, symbol, event_type) VALUES (%s, 'AAA', 'earnings_scheduled')", (key,))
        cur.execute("INSERT INTO market_event_revision (event_key, revision, event_time, known_at_basis, source, source_ref, "
                    "pit_grade, status, payload_hash, provenance) VALUES (%s, 1, DATE '2099-01-15', 'ingested', 'fake', 'r1', "
                    "'B', 'scheduled', %s, 'observed') RETURNING known_at = ingested_at, ingested_at > NOW() - INTERVAL '1 minute'",
                    (key, "0" * 64))
        assert cur.fetchone() == (True, True)
        for sql in ("UPDATE market_event_revision SET status = 'confirmed'", "DELETE FROM market_event_revision",
                    "TRUNCATE market_event_revision", "UPDATE market_event SET symbol = 'ZZZ'"):
            err = _refused(conn, sql)
            assert isinstance(err, psycopg2.errors.InsufficientPrivilege), (sql, err)
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("break_sql,expected", [
    ("GRANT UPDATE ON market_snapshot TO {app}", "has UPDATE on market_snapshot"),
    ("GRANT DELETE ON universe_snapshot TO {app}", "has DELETE on universe_snapshot"),
    ("GRANT TRUNCATE ON sector_snapshot TO {app}", "has TRUNCATE on sector_snapshot"),
    ("REVOKE INSERT ON market_event_revision FROM {app}", "lacks INSERT/SELECT on market_event_revision"),
    ("GRANT INSERT ON market_event TO {group}", "may write market_event"),
    ("ALTER TABLE market_snapshot DISABLE TRIGGER market_snapshot_immutable_row", "is not ENABLE ALWAYS"),
])
def test_verify_detects_mi_drift(full, snapshot_state, break_sql, expected):
    full.exec_admin(break_sql.format(app=f'"{full.app}"', group=f'"{full.group}"'))
    try:
        _verify_fails(full, expected)
    finally:
        full.exec_admin("ALTER TABLE market_snapshot ENABLE ALWAYS TRIGGER market_snapshot_immutable_row")


def test_rollback_hands_the_mi_objects_back(full):
    original = _catalog(full, "SELECT current_user")[0][0]
    full.run_script(ROLES_ROLLBACK_SQL, research_roles__original_owner=original)
    try:
        for t in MI_TABLES:
            assert _catalog(full, "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = %s::regclass", t)[0][0] == original, t
            assert _catalog(full, "SELECT has_table_privilege(%s, %s, 'INSERT')", full.app, t)[0][0] is False, t
        assert _catalog(full, "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname = 'research_market_guard'")[0][0] == original
    finally:
        full.run_script(ROLES_SQL)
        full.run_script(ROLES_VERIFY_SQL)
