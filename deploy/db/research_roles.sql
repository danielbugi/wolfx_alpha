-- deploy/db/research_roles.sql  -- least-privilege role model for the Release B research tables.
--
-- PREPARED, NOT APPLIED. Nothing here has been run against production (production still connects as the
-- docker POSTGRES_USER, which is a superuser). Procedure, verification and rollback:
-- docs/operations/RESEARCH_DB_ROLES.md, deploy/db/research_roles_verify.sql, deploy/db/research_roles_rollback.sql.
--
-- Run AFTER migration 22 is applied and verified, as a superuser, atomically, with the target schema first on the
-- search_path (production: public):
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/research_roles.sql
--
-- Idempotent: safe to re-run, and it MUST be re-run (then deploy/db/research_roles_verify.sql) after every later
-- migration that adds a table, sequence, view or function, otherwise the runtime role cannot see the new object (the
-- verify script's coverage check fails until you do). Deliberately NO `ALTER DEFAULT PRIVILEGES`: it would hand the
-- runtime role UPDATE/DELETE on every future table automatically, including any future immutable research table.
--
-- Roles (names are fixed; passwords are NEVER in this repo -- set them out of band with \password):
--   donchian_owner           NOLOGIN. Owns the 8 research tables and the 8 research functions. The SECURITY DEFINER
--                            functions therefore execute with this role's (small) privileges, not a superuser's.
--                            Schema migrations are applied by a person who `SET ROLE donchian_owner`.
--   donchian_app             LOGIN. The ONE role every runtime service (pipeline, screener, backend, bot) connects
--                            as. Not a superuser, not an owner, not a member of any other role here. INSERT/SELECT
--                            only on the immutable research tables; column-limited UPDATE on candidate_capture_run;
--                            no access to the maintenance tables; no EXECUTE on any maintenance function.
--   donchian_research_admin  NOLOGIN group. Maintenance / activation. Individual people get their OWN login roles and
--                            are GRANTed this group (so session_user differs per person: a ticket's approver must
--                            differ from its opener). Reaches the immutable tables for UPDATE/DELETE/TRUNCATE, but
--                            the immutability triggers still require an approved, begun ticket.
--
-- WHAT THIS DOES AND DOES NOT PREVENT (read before trusting it):
--   * Prevents: the runtime role (donchian_app) updating, deleting or truncating captured research rows, disabling
--     or dropping a trigger (it is not the owner), replacing a function, writing a maintenance ticket/audit row,
--     or writing an activation boundary. Prevents a maintenance user acting without a two-person approved ticket.
--   * Does NOT prevent: a SUPERUSER (bypasses every privilege check; may DISABLE/DROP triggers, replace functions,
--     set session_replication_role = replica), the TABLE OWNER donchian_owner (may ALTER TABLE ... DISABLE TRIGGER /
--     DROP TRIGGER / replace the functions -- keep it NOLOGIN), or any role holding pg_write_all_data /
--     pg_execute_server_program / ownership via membership. `ENABLE ALWAYS TRIGGER` only defeats
--     session_replication_role; it is not a defence against a superuser. The security boundary is the runtime
--     role's lack of privileges.

-- ---------------------------------------------------------------------------------------------------
-- 1. Roles (create if absent; never alters an existing role's password; the verify script checks attributes)
-- ---------------------------------------------------------------------------------------------------
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_owner') THEN
        CREATE ROLE donchian_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        CREATE ROLE donchian_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_research_admin') THEN
        CREATE ROLE donchian_research_admin NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END;
$roles$;

-- ---------------------------------------------------------------------------------------------------
-- 1b. No role may create objects in the schema through PUBLIC (nor the runtime role directly, nor schemas in the database). PG15+ already ships it that way, but a schema restored
--     from an older dump can still carry `GRANT CREATE ... TO PUBLIC`, which would let donchian_app run DDL.
-- ---------------------------------------------------------------------------------------------------
DO $sch$
BEGIN
    EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM PUBLIC', current_schema());
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM donchian_app', current_database());
    EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM donchian_app', current_schema());
END;
$sch$;

-- ---------------------------------------------------------------------------------------------------
-- 2. Ownership: the research objects move to donchian_owner (functions then run as it, not as a superuser).
--    PG15+ requires the new owner to hold CREATE on the schema; SECURITY DEFINER bodies read `strategies`.
-- ---------------------------------------------------------------------------------------------------
DO $own$
DECLARE
    sch TEXT := current_schema();
    t TEXT;
    f TEXT;
BEGIN
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO donchian_owner', sch);
    EXECUTE 'GRANT SELECT ON strategies TO donchian_owner';
    FOREACH t IN ARRAY ARRAY['feature_set_registry', 'candidate_capture_run', 'research_capture_activation',
                             'research_maintenance_log', 'research_maintenance_session',
                             'research_maintenance_audit', 'feature_snapshot', 'candidate_observation'] LOOP
        EXECUTE format('ALTER TABLE %I OWNER TO donchian_owner', t);
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_guard_immutable()', 'research_audit_append_only()',
                             'research_maintenance_log_guard()', 'research_maintenance_open(text,text,integer)',
                             'research_maintenance_approve(bigint)', 'research_maintenance_begin(bigint)',
                             'research_maintenance_close(bigint)', 'research_capture_set_state(bigint,text,date,text)'] LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO donchian_owner', f);
    END LOOP;
END;
$own$;

-- ---------------------------------------------------------------------------------------------------
-- 3. Nothing on the research tables for PUBLIC; nothing dangerous for the runtime role.
-- ---------------------------------------------------------------------------------------------------
REVOKE ALL ON candidate_observation, feature_snapshot, feature_set_registry, candidate_capture_run,
              research_capture_activation, research_maintenance_log, research_maintenance_session,
              research_maintenance_audit FROM PUBLIC;
REVOKE ALL ON candidate_observation, feature_snapshot, feature_set_registry, candidate_capture_run,
              research_capture_activation, research_maintenance_log, research_maintenance_session,
              research_maintenance_audit FROM donchian_app;
-- the five callable (SECURITY DEFINER) entry points are for the admin group only; revoke convergently so a stray
-- EXECUTE granted to the runtime role (or restored to PUBLIC) is undone by re-running this script
REVOKE ALL ON FUNCTION research_maintenance_open(text,text,integer), research_maintenance_approve(bigint),
              research_maintenance_begin(bigint), research_maintenance_close(bigint),
              research_capture_set_state(bigint,text,date,text) FROM PUBLIC, donchian_app;

-- ---------------------------------------------------------------------------------------------------
-- 4. Runtime role: what the services legitimately do to the research tables, and nothing more.
-- ---------------------------------------------------------------------------------------------------
DO $app$
DECLARE
    sch TEXT := current_schema();
    seq TEXT;
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO donchian_app', sch);
    GRANT SELECT, INSERT ON candidate_observation, feature_snapshot, feature_set_registry TO donchian_app;
    GRANT SELECT, INSERT ON candidate_capture_run TO donchian_app;
    -- the observer finalises a run: only these columns may change; a run's identity never can
    GRANT UPDATE (status, run_finished_at, candidates, captured, already_captured, hash_drift, guard_rejected,
                  guard_not_evaluated, stale_skipped, snapshot_skipped, snapshot_drift, invalid_skipped,
                  defaulted_flagged, skipped_symbols, error) ON candidate_capture_run TO donchian_app;
    GRANT SELECT ON research_capture_activation TO donchian_app;   -- read-only: the observer/API read the boundary
    FOREACH seq IN ARRAY ARRAY[pg_get_serial_sequence('candidate_observation', 'id'),
                               pg_get_serial_sequence('feature_snapshot', 'id'),
                               pg_get_serial_sequence('candidate_capture_run', 'id')] LOOP
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO donchian_app', seq);
    END LOOP;
END;
$app$;

-- ---------------------------------------------------------------------------------------------------
-- 5. Maintenance group. Table rights are necessary but not sufficient: every UPDATE/DELETE/TRUNCATE on an
--    immutable table is still rejected by the trigger unless a two-person ticket was begun in the transaction.
-- ---------------------------------------------------------------------------------------------------
DO $adm$
DECLARE
    sch TEXT := current_schema();
BEGIN
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO donchian_research_admin', sch);
    GRANT SELECT, UPDATE, DELETE, TRUNCATE ON candidate_observation, feature_snapshot, feature_set_registry,
          research_capture_activation TO donchian_research_admin;
    GRANT SELECT ON candidate_capture_run, research_maintenance_log, research_maintenance_session,
          research_maintenance_audit, strategies TO donchian_research_admin;
    GRANT EXECUTE ON FUNCTION research_maintenance_open(text,text,integer), research_maintenance_approve(bigint),
          research_maintenance_begin(bigint), research_maintenance_close(bigint),
          research_capture_set_state(bigint,text,date,text) TO donchian_research_admin;
END;
$adm$;

-- ---------------------------------------------------------------------------------------------------
-- 6. Runtime baseline for the runtime role on every PRE-EXISTING (non-research) object -- REQUIRED before any service
--    is switched to donchian_app, because those services currently run as a superuser and never needed it:
--      tables/partitioned tables  SELECT, INSERT, UPDATE, DELETE   (no TRUNCATE / TRIGGER / REFERENCES)
--      views / materialized views SELECT                           (production has latest_stock_data,
--                                                                   latest_fundamentals, ml_training_data)
--      sequences                  USAGE, SELECT, UPDATE            (nextval/currval/setval behind SERIAL inserts)
--      functions                  EXECUTE                          (get_last_update_date & co.; explicit so a revoked
--                                                                   PUBLIC default cannot silently lock the role out)
--    No DDL of any kind: the schema is owned by someone else and donchian_app has no CREATE on it (1b above).
--    Verified against the full bootstrapped schema by mechanism/research/tests/test_roles_full_schema.py.
-- ---------------------------------------------------------------------------------------------------
DO $base$
DECLARE
    r RECORD;
BEGIN
    FOR r IN SELECT c.oid, c.relname, c.relkind FROM pg_class c
             WHERE c.relnamespace = to_regnamespace(current_schema()) AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
               AND c.relname NOT IN ('feature_set_registry', 'candidate_capture_run', 'research_capture_activation',
                                     'research_maintenance_log', 'research_maintenance_session',
                                     'research_maintenance_audit', 'feature_snapshot', 'candidate_observation',
                                     'candidate_observation_id_seq', 'feature_snapshot_id_seq',
                                     'candidate_capture_run_id_seq', 'research_capture_activation_id_seq',
                                     'research_maintenance_log_id_seq', 'research_maintenance_session_id_seq',
                                     'research_maintenance_audit_id_seq') LOOP
        -- REVOKE first so a re-run CONVERGES to the baseline (drops any drifted extra privilege such as TRUNCATE).
        IF r.relkind = 'S' THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE %I FROM donchian_app', r.relname);
            EXECUTE format('GRANT USAGE, SELECT, UPDATE ON SEQUENCE %I TO donchian_app', r.relname);
        ELSIF r.relkind IN ('v', 'm') THEN
            EXECUTE format('REVOKE ALL ON %I FROM donchian_app', r.relname);
            EXECUTE format('GRANT SELECT ON %I TO donchian_app', r.relname);
        ELSE
            EXECUTE format('REVOKE ALL ON %I FROM donchian_app', r.relname);
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO donchian_app', r.relname);
        END IF;
    END LOOP;
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = to_regnamespace(current_schema()) AND p.prokind IN ('f', 'p')
               AND p.proname NOT LIKE 'research\_%' LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO donchian_app', r.sig);
    END LOOP;
END;
$base$;
