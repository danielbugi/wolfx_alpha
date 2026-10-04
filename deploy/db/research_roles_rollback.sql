-- deploy/db/research_roles_rollback.sql -- undo deploy/db/research_roles.sql (privileges and ownership only).
--
-- Run as a superuser, atomically, with the target schema first on the search_path:
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/research_roles_rollback.sql
--
-- What it restores: the 8 research tables and 8 functions go back to the owner recorded BEFORE the role script ran
-- (set it with a custom GUC; there is deliberately no default so a rollback cannot silently hand objects to the
-- wrong role):
--     PGOPTIONS='-c search_path=public -c research_roles.original_owner=trading_user' psql -v ON_ERROR_STOP=1 -1 -f ...
-- Record the original owner BEFORE running research_roles.sql:
--     SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class WHERE relname IN ('candidate_observation','feature_snapshot');
--
-- What it does NOT do: it does not drop the three roles (DROP ROLE fails while they hold privileges or own objects,
-- and dropping a role that services still use is an outage); drop them by hand afterwards with
--     DROP OWNED BY donchian_app; DROP ROLE donchian_app;   -- etc., once nothing connects as them.
-- Services must be pointed back at their previous DB_USER FIRST; otherwise they lose access to every table.
-- It does not touch migration 22 (see deploy/db/rollback22.sql), and it does not re-grant CREATE on the schema to PUBLIC
-- (research_roles.sql section 1b removed it; every supported Postgres version defaults to that anyway).

DO $rb$
DECLARE
    sch TEXT := current_schema();
    orig TEXT := nullif(current_setting('research_roles.original_owner', true), '');
    t TEXT;
    f TEXT;
    r RECORD;
BEGIN
    IF orig IS NULL THEN
        RAISE EXCEPTION 'set -c research_roles.original_owner=<role> (the owner the research objects had before research_roles.sql)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = orig) THEN
        RAISE EXCEPTION 'original owner role % does not exist', orig;
    END IF;
    FOREACH t IN ARRAY ARRAY['feature_set_registry', 'candidate_capture_run', 'research_capture_activation',
                             'research_maintenance_log', 'research_maintenance_session',
                             'research_maintenance_audit', 'feature_snapshot', 'candidate_observation'] LOOP
        EXECUTE format('ALTER TABLE %I OWNER TO %I', t, orig);
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_guard_immutable()', 'research_audit_append_only()',
                             'research_maintenance_log_guard()', 'research_maintenance_open(text,text,integer)',
                             'research_maintenance_approve(bigint)', 'research_maintenance_begin(bigint)',
                             'research_maintenance_close(bigint)', 'research_capture_set_state(bigint,text,date,text)'] LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO %I', f, orig);
    END LOOP;
    -- Market Intelligence objects (migrations 24 / 25), when present: tables, their sequences and the two trigger functions
    FOREACH t IN ARRAY ARRAY['universe_snapshot', 'market_snapshot', 'sector_snapshot', 'market_event', 'market_event_revision',
                             'forward_return_label',
                             'source_observation', 'source_poll', 'catalyst_classification', 'stock_relative_strength', 'dataset_manifest', 'experiment_registration', 'experiment_result'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %I OWNER TO %I', t, orig);
            EXECUTE format('ALTER SEQUENCE %s OWNER TO %I',
                           pg_get_serial_sequence(t, CASE WHEN t = 'market_event' THEN 'event_id' ELSE 'id' END), orig);
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_market_guard()', 'research_market_event_stamp()', 'research_label_guard()',
                             'research_label_consistency()',
                             'research_observation_guard()', 'research_observation_stamp()', 'research_poll_stamp()', 'research_classification_guard()', 'research_classification_consistency()', 'research_rs_guard()', 'research_rs_stamp()', 'research_registry_guard()', 'research_registry_stamp()', 'research_registry_consistency()'] LOOP
        IF to_regprocedure(f) IS NOT NULL THEN
            EXECUTE format('ALTER FUNCTION %s OWNER TO %I', f, orig);
        END IF;
    END LOOP;

    -- strip every privilege the three roles hold on objects in this schema
    FOR r IN SELECT c.relname, c.relkind FROM pg_class c
             WHERE c.relnamespace = to_regnamespace(sch) AND c.relkind IN ('r', 'p', 'v', 'm', 'S') LOOP
        IF r.relkind = 'S' THEN
            EXECUTE format('REVOKE ALL ON SEQUENCE %I FROM donchian_app, donchian_research_admin, donchian_owner', r.relname);
        ELSE
            EXECUTE format('REVOKE ALL ON %I FROM donchian_app, donchian_research_admin, donchian_owner', r.relname);
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_maintenance_open(text,text,integer)', 'research_maintenance_approve(bigint)',
                             'research_maintenance_begin(bigint)', 'research_maintenance_close(bigint)',
                             'research_capture_set_state(bigint,text,date,text)'] LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM donchian_app, donchian_research_admin, donchian_owner', f);
    END LOOP;
    -- the explicit EXECUTE grants research_roles.sql section 6 gave the runtime role on the non-research functions
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = to_regnamespace(sch) AND p.prokind IN ('f', 'p') AND p.proname NOT LIKE 'research\_%' LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM donchian_app, donchian_research_admin, donchian_owner', r.sig);
    END LOOP;
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM donchian_app, donchian_research_admin, donchian_owner', sch);

    -- the five definer functions stay un-executable by PUBLIC (that is part of migration 22), but the original
    -- owner must still be able to run them
    FOREACH f IN ARRAY ARRAY['research_maintenance_open(text,text,integer)', 'research_maintenance_approve(bigint)',
                             'research_maintenance_begin(bigint)', 'research_maintenance_close(bigint)',
                             'research_capture_set_state(bigint,text,date,text)'] LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I', f, orig);
    END LOOP;
    RAISE NOTICE 'research roles rolled back: objects owned by %, privileges of the three roles revoked', orig;
END;
$rb$;
