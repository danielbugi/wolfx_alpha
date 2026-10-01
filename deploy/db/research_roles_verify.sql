-- deploy/db/research_roles_verify.sql -- read-only verification of the research role model.
-- Run as a superuser with the target schema first on the search_path:
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -f deploy/db/research_roles_verify.sql
-- Raises (and psql exits non-zero) on the first failed check; prints OK lines otherwise. Never writes.
--
-- The privilege checks use has_table_privilege / has_column_privilege / has_function_privilege on the REAL ACLs, and
-- the final block proves the behaviour by SET LOCAL ROLE donchian_app inside a rolled-back transaction.

BEGIN READ WRITE;

DO $v$
DECLARE
    t TEXT;
    f TEXT;
    bad TEXT;
    imm TEXT[] := ARRAY['candidate_observation', 'feature_snapshot', 'feature_set_registry'];
    maint TEXT[] := ARRAY['research_maintenance_log', 'research_maintenance_session', 'research_maintenance_audit',
                          'research_capture_activation'];
    allt TEXT[] := ARRAY['feature_set_registry', 'candidate_capture_run', 'research_capture_activation',
                         'research_maintenance_log', 'research_maintenance_session', 'research_maintenance_audit',
                         'feature_snapshot', 'candidate_observation'];
    fns TEXT[] := ARRAY['research_guard_immutable()', 'research_audit_append_only()', 'research_maintenance_log_guard()',
                        'research_maintenance_open(text,text,integer)', 'research_maintenance_approve(bigint)',
                        'research_maintenance_begin(bigint)', 'research_maintenance_close(bigint)',
                        'research_capture_set_state(bigint,text,date,text)'];
    priv TEXT;
BEGIN
    -- roles and their attributes
    FOR t IN SELECT unnest(ARRAY['donchian_owner', 'donchian_app', 'donchian_research_admin']) LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = t) THEN
            RAISE EXCEPTION 'FAIL role % does not exist', t;
        END IF;
        SELECT string_agg(a, ',') INTO bad FROM (
            SELECT 'superuser' a FROM pg_roles WHERE rolname = t AND rolsuper
            UNION ALL SELECT 'createdb' FROM pg_roles WHERE rolname = t AND rolcreatedb
            UNION ALL SELECT 'createrole' FROM pg_roles WHERE rolname = t AND rolcreaterole
            UNION ALL SELECT 'replication' FROM pg_roles WHERE rolname = t AND rolreplication
            UNION ALL SELECT 'bypassrls' FROM pg_roles WHERE rolname = t AND rolbypassrls) x;
        IF bad IS NOT NULL THEN RAISE EXCEPTION 'FAIL role % has dangerous attribute(s): %', t, bad; END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_owner' AND rolcanlogin) THEN
        RAISE EXCEPTION 'FAIL donchian_owner must be NOLOGIN';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_research_admin' AND rolcanlogin) THEN
        RAISE EXCEPTION 'FAIL donchian_research_admin must be NOLOGIN (people hold their own login roles)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app' AND rolcanlogin) THEN
        RAISE EXCEPTION 'FAIL donchian_app must be able to log in';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
               WHERE r.rolname = 'donchian_app') THEN
        RAISE EXCEPTION 'FAIL donchian_app must not be a member of any role';
    END IF;
    RAISE NOTICE 'OK roles exist with safe attributes; donchian_app is a member of nothing';

    -- ownership
    FOREACH t IN ARRAY allt LOOP
        IF (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = to_regclass(t)) <> 'donchian_owner' THEN
            RAISE EXCEPTION 'FAIL table % is not owned by donchian_owner', t;
        END IF;
    END LOOP;
    FOREACH f IN ARRAY fns LOOP
        IF (SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = to_regprocedure(f)) <> 'donchian_owner' THEN
            RAISE EXCEPTION 'FAIL function % is not owned by donchian_owner', f;
        END IF;
    END LOOP;
    RAISE NOTICE 'OK 8 tables and 8 functions are owned by donchian_owner';

    -- runtime role: forbidden privileges
    FOREACH t IN ARRAY allt LOOP
        FOREACH priv IN ARRAY ARRAY['DELETE', 'TRUNCATE', 'TRIGGER', 'REFERENCES'] LOOP
            IF has_table_privilege('donchian_app', t, priv) THEN
                RAISE EXCEPTION 'FAIL donchian_app has % on %', priv, t;
            END IF;
        END LOOP;
    END LOOP;
    FOREACH t IN ARRAY imm LOOP
        IF has_table_privilege('donchian_app', t, 'UPDATE') THEN RAISE EXCEPTION 'FAIL donchian_app has UPDATE on %', t; END IF;
        IF NOT has_table_privilege('donchian_app', t, 'INSERT') OR NOT has_table_privilege('donchian_app', t, 'SELECT') THEN
            RAISE EXCEPTION 'FAIL donchian_app lacks INSERT/SELECT on %', t;
        END IF;
    END LOOP;
    FOREACH t IN ARRAY maint LOOP
        IF has_table_privilege('donchian_app', t, 'INSERT') OR has_table_privilege('donchian_app', t, 'UPDATE') THEN
            RAISE EXCEPTION 'FAIL donchian_app can write %', t;
        END IF;
    END LOOP;
    IF has_table_privilege('donchian_app', 'research_maintenance_log', 'SELECT')
       OR has_table_privilege('donchian_app', 'research_maintenance_session', 'SELECT')
       OR has_table_privilege('donchian_app', 'research_maintenance_audit', 'SELECT') THEN
        RAISE EXCEPTION 'FAIL donchian_app can read a maintenance table';
    END IF;
    IF has_table_privilege('donchian_app', 'candidate_capture_run', 'UPDATE') THEN
        NULL; -- table-level UPDATE is reported separately below via has_any_column_privilege
    END IF;
    FOREACH t IN ARRAY ARRAY['id', 'strategy_id', 'session_date', 'session_source', 'run_started_at', 'rule_set_version'] LOOP
        IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass('candidate_capture_run') AND attname = t
                   AND NOT attisdropped) AND has_column_privilege('donchian_app', 'candidate_capture_run', t, 'UPDATE') THEN
            RAISE EXCEPTION 'FAIL donchian_app may UPDATE candidate_capture_run.% (identity column)', t;
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_maintenance_open(text,text,integer)', 'research_maintenance_approve(bigint)',
                             'research_maintenance_begin(bigint)', 'research_maintenance_close(bigint)',
                             'research_capture_set_state(bigint,text,date,text)'] LOOP
        IF has_function_privilege('donchian_app', f, 'EXECUTE') THEN
            RAISE EXCEPTION 'FAIL donchian_app may EXECUTE %', f;
        END IF;
        IF has_function_privilege('public', f, 'EXECUTE') THEN
            RAISE EXCEPTION 'FAIL PUBLIC may EXECUTE %', f;
        END IF;
        IF NOT has_function_privilege('donchian_research_admin', f, 'EXECUTE') THEN
            RAISE EXCEPTION 'FAIL donchian_research_admin cannot EXECUTE %', f;
        END IF;
    END LOOP;
    IF NOT has_column_privilege('donchian_app', 'candidate_capture_run', 'status', 'UPDATE') THEN
        RAISE EXCEPTION 'FAIL donchian_app cannot finalise a capture run (status UPDATE)';
    END IF;
    RAISE NOTICE 'OK donchian_app: INSERT/SELECT only on immutable tables; no write on maintenance/activation tables; no EXECUTE on maintenance functions';

    -- triggers are ALWAYS-enabled
    IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = ANY (SELECT to_regclass(x) FROM unnest(allt) x)
               AND NOT tgisinternal AND tgenabled <> 'A') THEN
        RAISE EXCEPTION 'FAIL a research trigger is not ENABLE ALWAYS';
    END IF;
    RAISE NOTICE 'OK every research trigger is ENABLE ALWAYS (this only defeats session_replication_role; it does not bind a superuser or the owner)';
END;
$v$;

-- Behavioural proof: as donchian_app, every mutation of an immutable table must be refused.
DO $b$
DECLARE
    t TEXT;
    refused INT := 0;
BEGIN
    SET LOCAL ROLE donchian_app;
    FOREACH t IN ARRAY ARRAY['candidate_observation', 'feature_snapshot', 'feature_set_registry'] LOOP
        BEGIN EXECUTE format('UPDATE %I SET %I = %I', t, CASE t WHEN 'feature_set_registry' THEN 'feature_set_version' ELSE 'symbol' END, CASE t WHEN 'feature_set_registry' THEN 'feature_set_version' ELSE 'symbol' END); RAISE EXCEPTION 'FAIL UPDATE on % was allowed', t;
        EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
        BEGIN EXECUTE format('DELETE FROM %I', t); RAISE EXCEPTION 'FAIL DELETE on % was allowed', t;
        EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
        BEGIN EXECUTE format('TRUNCATE %I', t); RAISE EXCEPTION 'FAIL TRUNCATE on % was allowed', t;
        EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
    END LOOP;
    BEGIN PERFORM research_maintenance_begin(1); RAISE EXCEPTION 'FAIL donchian_app executed the maintenance hatch';
    EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
    RESET ROLE;
    IF refused <> 10 THEN RAISE EXCEPTION 'FAIL expected 10 refusals, saw %', refused; END IF;
    RAISE NOTICE 'OK behavioural: donchian_app was refused 10/10 mutation attempts (UPDATE/DELETE/TRUNCATE x3 tables + hatch)';
END;
$b$;

ROLLBACK;
\echo RESEARCH ROLE VERIFICATION PASSED
