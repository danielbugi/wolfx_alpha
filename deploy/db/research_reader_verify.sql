-- deploy/db/research_reader_verify.sql -- read-only verification of the research_reader role. Every check RAISEs on failure; success prints 'research_reader verified'.
-- Safe to run in a forced read-only session (it only reads catalogs and runs SELECTs as the role):
--     docker exec -i donchian-screener-postgres-1 sh -c 'PGOPTIONS="-c default_transaction_read_only=on" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/research_reader_verify.sql

DO $$
DECLARE
    allowed text[] := ARRAY['companies','daily_fundamentals','fundamental_inflection_event','inactive_symbols','news_items','sec_quarter','sec_symbol_cik','stock_prices'];
    r record;
    t text;
    n int;
BEGIN
    SELECT * INTO r FROM pg_roles WHERE rolname = 'research_reader';
    IF NOT FOUND THEN RAISE EXCEPTION 'research_reader does not exist'; END IF;
    IF r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls OR r.rolinherit OR NOT r.rolcanlogin THEN
        RAISE EXCEPTION 'unexpected role attributes: %', row_to_json(r);
    END IF;
    IF r.rolconnlimit <> 2 OR r.rolvaliduntil IS NULL THEN RAISE EXCEPTION 'connection limit / expiry not set'; END IF;
    IF NOT (r.rolconfig @> ARRAY['default_transaction_read_only=on','statement_timeout=60s']) THEN
        RAISE EXCEPTION 'session defaults missing: %', r.rolconfig;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = r.oid) THEN RAISE EXCEPTION 'research_reader is a member of another role'; END IF;

    -- exactly the 8 tables are readable, none writable, across every schema
    SELECT count(*) INTO n FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
     WHERE c.relkind IN ('r','p','v','m','f') AND s.nspname NOT IN ('pg_catalog','information_schema') AND s.nspname NOT LIKE 'pg_toast%'
       AND has_table_privilege('research_reader', c.oid, 'SELECT');
    IF n <> 8 THEN RAISE EXCEPTION 'research_reader can read % relations, expected 8', n; END IF;
    FOREACH t IN ARRAY allowed LOOP
        IF NOT has_table_privilege('research_reader', 'public.' || t, 'SELECT') THEN RAISE EXCEPTION 'cannot read %', t; END IF;
    END LOOP;
    SELECT count(*) INTO n FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
     WHERE c.relkind IN ('r','p','v','m','f') AND s.nspname NOT IN ('pg_catalog','information_schema') AND s.nspname NOT LIKE 'pg_toast%'
       AND (has_table_privilege('research_reader', c.oid, 'INSERT') OR has_table_privilege('research_reader', c.oid, 'UPDATE')
            OR has_table_privilege('research_reader', c.oid, 'DELETE') OR has_table_privilege('research_reader', c.oid, 'TRUNCATE')
            OR has_table_privilege('research_reader', c.oid, 'REFERENCES') OR has_table_privilege('research_reader', c.oid, 'TRIGGER'));
    IF n <> 0 THEN RAISE EXCEPTION 'research_reader has write-type privileges on % relations', n; END IF;
    SELECT count(*) INTO n FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
     WHERE c.relkind = 'S' AND s.nspname NOT IN ('pg_catalog','information_schema') AND has_sequence_privilege('research_reader', c.oid, 'USAGE,UPDATE');
    IF n <> 0 THEN RAISE EXCEPTION 'research_reader can use % sequences', n; END IF;
    IF has_schema_privilege('research_reader', 'public', 'CREATE') OR has_database_privilege('research_reader', current_database(), 'CREATE') THEN
        RAISE EXCEPTION 'research_reader can create objects';
    END IF;
END $$;

-- behaviour as the role itself: reads the allowed tables, is refused elsewhere
SET ROLE research_reader;
SELECT 'read ok' AS check, (SELECT count(*) FROM (SELECT 1 FROM companies LIMIT 1) a) + (SELECT count(*) FROM (SELECT 1 FROM stock_prices LIMIT 1) b) AS rows_seen;
DO $$
BEGIN
    PERFORM 1 FROM bot_users LIMIT 1;
    RAISE EXCEPTION 'research_reader could read bot_users';
EXCEPTION WHEN insufficient_privilege THEN NULL;
END $$;
DO $$
BEGIN
    PERFORM 1 FROM signal_ledger LIMIT 1;
    RAISE EXCEPTION 'research_reader could read signal_ledger';
EXCEPTION WHEN insufficient_privilege THEN NULL;
END $$;
RESET ROLE;
SELECT 'research_reader verified' AS result;
