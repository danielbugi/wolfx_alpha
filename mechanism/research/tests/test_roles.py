"""The least-privilege role model (deploy/db/research_roles*.sql) against a real Postgres: applied to a throwaway
schema with throwaway role names. Proves the runtime role can do exactly what capture needs and nothing that could
rewrite captured history, that the verify script really detects drift, and that the rollback script restores
ownership and strips privileges. Needs a SUPERUSER DB role (skips otherwise; CI's trading_user is one)."""
import psycopg2
import psycopg2.errors
import pytest

from conftest import ROLES_ROLLBACK_SQL, ROLES_SQL, ROLES_VERIFY_SQL, Seed

IMMUTABLE = ["candidate_observation", "feature_snapshot", "feature_set_registry"]


def _refused(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.Error) as exc:
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    return exc.value


def test_role_script_is_idempotent_and_verify_passes(roles):
    roles.run_script(ROLES_SQL)  # a second run changes nothing and does not fail
    roles.run_script(ROLES_VERIFY_SQL)


def test_runtime_role_can_do_everything_capture_needs(roles):
    with roles.connect() as conn:
        roles.as_user(conn, roles.app)
        seed = Seed(conn)
        snap = seed.snapshot("AAA")
        run = seed.run()
        obs = seed.observation(snap, run, "AAA")
        cur = conn.cursor()
        cur.execute("UPDATE candidate_capture_run SET status = 'partial', run_finished_at = NOW(), candidates = 1, "
                    "captured = 1, already_captured = 0, stale_skipped = 0, snapshot_skipped = 0, "
                    "invalid_skipped = 1, guard_not_evaluated = 0 WHERE id = %s", (run,))
        cur.execute("SELECT count(*) FROM candidate_observation WHERE id = %s", (obs,))
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM research_capture_activation")
        conn.rollback()


@pytest.mark.parametrize("table", IMMUTABLE)
def test_runtime_role_cannot_rewrite_captured_history(roles, table):
    with roles.connect() as conn:
        seed = Seed(conn)
        seed.observation(symbol="AAA")
        conn.commit()
        roles.as_user(conn, roles.app)
        col = "feature_set_version" if table == "feature_set_registry" else "symbol"
        for sql in (f"UPDATE {table} SET {col} = {col}", f"DELETE FROM {table}", f"TRUNCATE {table}"):
            err = _refused(conn, sql)
            assert isinstance(err, psycopg2.errors.InsufficientPrivilege), (sql, err)


def test_runtime_role_cannot_change_a_runs_identity(roles):
    with roles.connect() as conn:
        run = Seed(conn).run()
        conn.commit()
        roles.as_user(conn, roles.app)
        for col, val in (("session_date", "'2099-02-01'"), ("strategy_id", "strategy_id"),
                         ("session_source", "'explicit'"), ("run_started_at", "NOW()")):
            err = _refused(conn, f"UPDATE candidate_capture_run SET {col} = {val} WHERE id = {run}")
            assert isinstance(err, psycopg2.errors.InsufficientPrivilege), (col, err)


def test_runtime_role_has_no_door_to_the_maintenance_machinery(roles):
    with roles.connect() as conn:
        roles.as_user(conn, roles.app)
        for sql in ("SELECT research_maintenance_open('candidate_observation', 'a long enough reason', 60)",
                    "SELECT research_maintenance_approve(1)", "SELECT research_maintenance_begin(1)",
                    "SELECT research_maintenance_close(1)",
                    "SELECT research_capture_set_state(1, 'enabled', DATE '2099-01-01', 'activation note')",
                    "SELECT * FROM research_maintenance_log", "SELECT * FROM research_maintenance_session",
                    "SELECT * FROM research_maintenance_audit",
                    "INSERT INTO research_maintenance_log (opened_by, reason, target_table) VALUES ('x', 'long enough reason', 'candidate_observation')",
                    "INSERT INTO research_capture_activation (strategy_id, state, effective_from_session, note) VALUES (1, 'enabled', '2099-01-01', 'activation')",
                    "UPDATE research_capture_activation SET state = 'disabled'"):
            err = _refused(conn, sql)
            assert isinstance(err, psycopg2.errors.InsufficientPrivilege), (sql, err)


def test_runtime_role_cannot_disarm_the_triggers_or_replace_the_functions(roles):
    with roles.connect() as conn:
        roles.as_user(conn, roles.app)
        for sql in ("ALTER TABLE candidate_observation DISABLE TRIGGER ALL",
                    "ALTER TABLE candidate_observation DISABLE TRIGGER candidate_observation_immutable_row",
                    "DROP TRIGGER candidate_observation_immutable_row ON candidate_observation",
                    "ALTER TABLE candidate_observation ENABLE REPLICA TRIGGER candidate_observation_immutable_row",
                    "CREATE OR REPLACE FUNCTION research_guard_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                    "DROP FUNCTION research_guard_immutable() CASCADE",
                    "ALTER TABLE candidate_observation DROP CONSTRAINT candidate_observation_identity_key",
                    "DROP TABLE candidate_observation CASCADE",
                    "SET session_replication_role = replica"):
            err = _refused(conn, sql)
            assert isinstance(err, (psycopg2.errors.InsufficientPrivilege, psycopg2.errors.InternalError_,
                                    psycopg2.errors.SyntaxError)) or "must be owner" in str(err) or "permission" in str(err), (sql, err)


def test_replication_role_trick_does_not_bypass_the_triggers_even_for_a_superuser(conn, seed):
    """ENABLE ALWAYS: a superuser who sets session_replication_role = replica still hits the immutability trigger.
    (A superuser can of course still DISABLE the trigger -- the role model, not the trigger, is the boundary.)"""
    cur = conn.cursor()
    cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    if not cur.fetchone()[0]:
        pytest.skip("needs a superuser to set session_replication_role")
    seed.observation(symbol="AAA")
    cur.execute("SET session_replication_role = replica")
    err = _refused(conn, "DELETE FROM candidate_observation")
    assert "immutable" in str(err)


def test_ownership_and_attributes(roles):
    cur = roles.admin.cursor()
    cur.execute(f'SET search_path TO "{roles.schema}"')
    cur.execute("SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.relnamespace = to_regnamespace(%s) "
                "AND c.relname = ANY(%s)", (roles.schema, ["candidate_observation", "feature_snapshot", "feature_set_registry",
                                                           "candidate_capture_run", "research_capture_activation",
                                                           "research_maintenance_log", "research_maintenance_session",
                                                           "research_maintenance_audit"]))
    owners = dict(cur.fetchall())
    assert len(owners) == 8 and set(owners.values()) == {roles.owner}
    cur.execute("SELECT rolsuper, rolcanlogin, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = %s", (roles.app,))
    assert cur.fetchone() == (False, True, False, False, False, False)
    cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname IN (%s, %s)", (roles.owner, roles.group))
    assert [r[0] for r in cur.fetchall()] == [False, False]
    roles.admin.rollback()


@pytest.mark.parametrize("break_sql,expected", [
    ("GRANT UPDATE ON candidate_observation TO {app}", "UPDATE on candidate_observation"),
    ("GRANT DELETE ON feature_snapshot TO {app}", "DELETE on feature_snapshot"),
    ("GRANT TRUNCATE ON feature_set_registry TO {app}", "TRUNCATE on feature_set_registry"),
    ("GRANT INSERT ON research_capture_activation TO {app}", "can write research_capture_activation"),
    ("GRANT SELECT ON research_maintenance_log TO {app}", "can read a maintenance table"),
    ("GRANT EXECUTE ON FUNCTION research_maintenance_begin(bigint) TO {app}", "EXECUTE research_maintenance_begin"),
    ("GRANT UPDATE (session_date) ON candidate_capture_run TO {app}", "identity column"),
    ("ALTER ROLE {app} SUPERUSER", "dangerous attribute"),
    ("GRANT {group} TO {app}", "must not be a member of any role"),
    ("ALTER TABLE candidate_observation OWNER TO {app}", "is not owned by donchian_owner"),
    ("ALTER TABLE candidate_observation DISABLE TRIGGER candidate_observation_immutable_row", "not ENABLE ALWAYS"),
])
def test_verify_script_detects_drift(roles, break_sql, expected):
    cur = roles.admin.cursor()
    cur.execute(f'SET search_path TO "{roles.schema}"')
    cur.execute(break_sql.format(app=roles.app, group=roles.group, owner=roles.owner))
    roles.admin.commit()
    with pytest.raises(psycopg2.Error) as exc:
        roles.run_script(ROLES_VERIFY_SQL)
    roles.admin.rollback()
    msg = str(exc.value)
    for fixed, throwaway in roles.names.items():
        msg = msg.replace(throwaway, fixed)
    assert "FAIL" in msg and expected in msg, msg


def test_rollback_restores_ownership_and_strips_privileges(roles):
    cur = roles.admin.cursor()
    cur.execute("SELECT current_user")
    original = cur.fetchone()[0]
    roles.admin.commit()
    with pytest.raises(psycopg2.Error, match="original_owner"):
        roles.run_script(ROLES_ROLLBACK_SQL)  # refuses without an explicit original owner
    roles.admin.rollback()
    roles.run_script(ROLES_ROLLBACK_SQL, research_roles__original_owner=original)
    cur = roles.admin.cursor()
    cur.execute(f'SET search_path TO "{roles.schema}"')
    cur.execute("SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class WHERE relname IN "
                "('candidate_observation', 'feature_snapshot', 'candidate_capture_run', 'research_maintenance_log')")
    assert [r[0] for r in cur.fetchall()] == [original]
    cur.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = to_regprocedure('research_guard_immutable()')")
    assert cur.fetchone()[0] == original
    for role in (roles.app, roles.group, roles.owner):
        cur.execute("SELECT has_table_privilege(%s, 'candidate_observation', 'SELECT')", (role,))
        assert cur.fetchone()[0] is False, role
    cur.execute("SELECT has_function_privilege('public', 'research_maintenance_begin(bigint)', 'EXECUTE')")
    assert cur.fetchone()[0] is False
    roles.admin.rollback()
    # and the model can be re-applied afterwards
    roles.run_script(ROLES_SQL)
    roles.run_script(ROLES_VERIFY_SQL)
