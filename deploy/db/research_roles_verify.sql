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
    obj RECORD;
    nrel INT := 0;
    nfn INT := 0;
    sch TEXT := current_schema();
    mi TEXT[];
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
    -- Market Intelligence tables (migrations 24 / 25) are included when present: append-only, runtime INSERT/SELECT, admin SELECT only.
    mi := ARRAY(SELECT x FROM unnest(ARRAY['universe_snapshot', 'market_snapshot', 'sector_snapshot', 'market_event',
                                           'market_event_revision']) x WHERE to_regclass(x) IS NOT NULL);
    allt := allt || mi;
    imm := imm || mi;
    fns := fns || ARRAY(SELECT x FROM unnest(ARRAY['research_market_guard()', 'research_market_event_stamp()']) x
                        WHERE to_regprocedure(x) IS NOT NULL);
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
    RAISE NOTICE 'OK % tables and % functions are owned by donchian_owner', array_length(allt, 1), array_length(fns, 1);

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
    FOREACH t IN ARRAY mi LOOP
        IF has_table_privilege('donchian_research_admin', t, 'INSERT') OR has_table_privilege('donchian_research_admin', t, 'UPDATE')
           OR has_table_privilege('donchian_research_admin', t, 'DELETE') OR has_table_privilege('donchian_research_admin', t, 'TRUNCATE') THEN
            RAISE EXCEPTION 'FAIL donchian_research_admin may write % (append-only, no maintenance hatch)', t;
        END IF;
        IF has_table_privilege('public', t, 'SELECT') THEN RAISE EXCEPTION 'FAIL PUBLIC may SELECT %', t; END IF;
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

    -- RUNTIME COVERAGE: every object the services use that is NOT part of the research layer must be reachable. This is
    -- what catches a view/sequence/function the role script forgot (and any object a later migration added).
    FOR obj IN SELECT c.oid, c.relname, c.relkind FROM pg_class c
             WHERE c.relnamespace = to_regnamespace(sch) AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
               AND c.relname <> ALL (allt)
               AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')
                                                    AND d.refobjid = ANY (SELECT to_regclass(x)::oid FROM unnest(allt) x)))
    LOOP
        nrel := nrel + 1;
        IF obj.relkind = 'S' THEN
            IF NOT (has_sequence_privilege('donchian_app', obj.oid, 'USAGE') AND has_sequence_privilege('donchian_app', obj.oid, 'SELECT')
                    AND has_sequence_privilege('donchian_app', obj.oid, 'UPDATE')) THEN
                RAISE EXCEPTION 'FAIL donchian_app lacks USAGE/SELECT/UPDATE on sequence %', obj.relname;
            END IF;
        ELSIF obj.relkind IN ('v', 'm') THEN
            IF NOT has_table_privilege('donchian_app', obj.oid, 'SELECT') THEN
                RAISE EXCEPTION 'FAIL donchian_app cannot SELECT from view %', obj.relname;
            END IF;
            FOREACH priv IN ARRAY ARRAY['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'] LOOP
                IF has_table_privilege('donchian_app', obj.oid, priv) THEN
                    RAISE EXCEPTION 'FAIL donchian_app has % on view %', priv, obj.relname;
                END IF;
            END LOOP;
        ELSE
            FOREACH priv IN ARRAY ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE'] LOOP
                IF NOT has_table_privilege('donchian_app', obj.oid, priv) THEN
                    RAISE EXCEPTION 'FAIL donchian_app lacks % on table %', priv, obj.relname;
                END IF;
            END LOOP;
            FOREACH priv IN ARRAY ARRAY['TRUNCATE', 'TRIGGER', 'REFERENCES'] LOOP
                IF has_table_privilege('donchian_app', obj.oid, priv) THEN
                    RAISE EXCEPTION 'FAIL donchian_app has % on table % (baseline is DML only)', priv, obj.relname;
                END IF;
            END LOOP;
        END IF;
    END LOOP;
    FOR obj IN SELECT p.oid, p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = to_regnamespace(sch) AND p.prokind IN ('f', 'p') AND p.proname NOT LIKE 'research\_%' LOOP
        nfn := nfn + 1;
        IF NOT has_function_privilege('donchian_app', obj.oid, 'EXECUTE') THEN
            RAISE EXCEPTION 'FAIL donchian_app cannot EXECUTE %', obj.sig;
        END IF;
    END LOOP;
    IF NOT has_schema_privilege('donchian_app', sch, 'USAGE') THEN
        RAISE EXCEPTION 'FAIL donchian_app has no USAGE on schema %', sch;
    END IF;
    IF has_schema_privilege('donchian_app', sch, 'CREATE') THEN
        RAISE EXCEPTION 'FAIL donchian_app (or PUBLIC) may CREATE in schema % -- the runtime role must not run DDL', sch;
    END IF;
    IF has_database_privilege('donchian_app', current_database(), 'CREATE') THEN
        RAISE EXCEPTION 'FAIL donchian_app may CREATE schemas in database %', current_database();
    END IF;
    IF NOT has_database_privilege('donchian_app', current_database(), 'CONNECT') THEN
        RAISE EXCEPTION 'FAIL donchian_app cannot CONNECT to database %', current_database();
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = 'donchian_app')) THEN
        RAISE EXCEPTION 'FAIL donchian_app owns a relation (an owner can ALTER/DROP it)';
    END IF;
    RAISE NOTICE 'OK runtime coverage: % relations (tables DML / views SELECT / sequences USAGE+SELECT+UPDATE) and % functions reachable; no CREATE on schema or database; owns nothing', nrel, nfn;

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
    expected INT := 10;
    mc TEXT;
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
    FOREACH t IN ARRAY ARRAY['universe_snapshot', 'market_snapshot', 'sector_snapshot', 'market_event', 'market_event_revision'] LOOP
        IF to_regclass(t) IS NULL THEN CONTINUE; END IF;
        expected := expected + 3;
        SELECT attname INTO mc FROM pg_attribute WHERE attrelid = to_regclass(t) AND attnum > 0 AND NOT attisdropped ORDER BY attnum LIMIT 1;
        BEGIN EXECUTE format('UPDATE %I SET %I = %I', t, mc, mc); RAISE EXCEPTION 'FAIL UPDATE on % was allowed', t;
        EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
        BEGIN EXECUTE format('DELETE FROM %I', t); RAISE EXCEPTION 'FAIL DELETE on % was allowed', t;
        EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
        BEGIN EXECUTE format('TRUNCATE %I', t); RAISE EXCEPTION 'FAIL TRUNCATE on % was allowed', t;
        EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
    END LOOP;
    BEGIN PERFORM research_maintenance_begin(1); RAISE EXCEPTION 'FAIL donchian_app executed the maintenance hatch';
    EXCEPTION WHEN insufficient_privilege THEN refused := refused + 1; END;
    RESET ROLE;
    IF refused <> expected THEN RAISE EXCEPTION 'FAIL expected % refusals, saw %', expected, refused; END IF;
    RAISE NOTICE 'OK behavioural: donchian_app was refused %/% mutation attempts (UPDATE/DELETE/TRUNCATE on every immutable table + the maintenance hatch)', refused, expected;
END;
$b$;

-- Behavioural proof, part 2: the runtime role cannot run schema/maintenance/privilege operations. Each statement must
-- fail with SQLSTATE 42501 (insufficient_privilege) specifically -- any other outcome (success, or an unrelated
-- error such as a typo) fails the check, so a broken probe can never read as a pass.
DO $d$
DECLARE
    tbl TEXT;
    col TEXT;
    trg RECORD;
    stmt TEXT;
    refused INT := 0;
    expected INT := 0;
    probes TEXT[];
BEGIN
    SELECT c.relname INTO tbl FROM pg_class c
    WHERE c.relnamespace = to_regnamespace(current_schema()) AND c.relkind = 'r'
      AND c.relname NOT LIKE 'research\_%' AND c.relname NOT IN ('candidate_observation', 'feature_snapshot',
          'feature_set_registry', 'candidate_capture_run')
      AND has_table_privilege('donchian_app', c.oid, 'DELETE')
    ORDER BY (c.relname = 'ml_models') DESC, c.relname LIMIT 1;
    IF tbl IS NULL THEN
        RAISE NOTICE 'SKIP part 2: the schema has no baseline table to probe';
        RETURN;
    END IF;
    SELECT attname INTO col FROM pg_attribute WHERE attrelid = to_regclass(tbl) AND attnum > 0 AND NOT attisdropped ORDER BY attnum LIMIT 1;
    probes := ARRAY[
        'CREATE TABLE zz_rb_probe (x int)',
        'CREATE VIEW zz_rb_probe AS SELECT 1',
        'CREATE FUNCTION zz_rb_probe() RETURNS int LANGUAGE sql AS ''SELECT 1''',
        format('ALTER TABLE %I ADD COLUMN zz_rb_probe int', tbl),
        format('ALTER TABLE %I DROP COLUMN %I', tbl, col),
        format('ALTER TABLE %I RENAME TO zz_rb_probe', tbl),
        format('DROP TABLE %I', tbl),
        format('TRUNCATE %I', tbl),
        format('CREATE INDEX zz_rb_probe ON %I (%I)', tbl, col),
        format('COMMENT ON TABLE %I IS ''x''', tbl),
        'ALTER TABLE candidate_observation DISABLE TRIGGER ALL',
        'DROP FUNCTION research_guard_immutable() CASCADE',
        'CREATE OR REPLACE FUNCTION research_guard_immutable() RETURNS trigger LANGUAGE plpgsql AS ''BEGIN RETURN NEW; END''',
        'CREATE SCHEMA zz_rb_probe',
        'CREATE ROLE zz_rb_probe',
        'ALTER ROLE donchian_app SUPERUSER',
        'ALTER ROLE donchian_app BYPASSRLS',
        'SET session_replication_role = replica',
        'COPY (SELECT 1) TO PROGRAM ''true''',
        'ALTER SEQUENCE candidate_observation_id_seq RESTART'
    ];
    FOR trg IN SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
               WHERE c.oid = to_regclass('candidate_observation') AND NOT t.tgisinternal LIMIT 1 LOOP
        probes := probes || format('DROP TRIGGER %I ON %I', trg.tgname, trg.relname);
    END LOOP;
    SET LOCAL ROLE donchian_app;
    FOREACH stmt IN ARRAY probes LOOP
        expected := expected + 1;
        BEGIN
            EXECUTE stmt;
            RAISE EXCEPTION 'FAIL donchian_app was allowed to run: %', stmt;
        EXCEPTION WHEN insufficient_privilege THEN
            refused := refused + 1;
        END;
    END LOOP;
    RESET ROLE;
    IF refused <> expected THEN RAISE EXCEPTION 'FAIL expected % refusals, saw %', expected, refused; END IF;
    RAISE NOTICE 'OK behavioural: donchian_app was refused %/% schema/maintenance/privilege operations (DDL, TRUNCATE, ALTER/DROP, trigger and function tampering, role escalation, replication-role bypass, COPY PROGRAM)', refused, expected;
END;
$d$;

ROLLBACK;
\echo RESEARCH ROLE VERIFICATION PASSED
