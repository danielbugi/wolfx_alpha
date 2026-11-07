-- deploy/db/fundamental_update_publication_verify.sql -- read-only verification of migration 41 (mechanism/add_fundamental_update_publication.sql).
-- Every check RAISEs on failure; success prints 'migration 41 verified'. Safe in a forced read-only session:
--     docker exec -i donchian-screener-postgres-1 sh -c 'PGOPTIONS="-c default_transaction_read_only=on" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/fundamental_update_publication_verify.sql

DO $$
DECLARE
    n int;
BEGIN
    IF to_regclass('public.fundamental_update_publication') IS NULL THEN RAISE EXCEPTION 'fundamental_update_publication is missing'; END IF;
    IF to_regclass('public.fundamental_update_current') IS NULL THEN RAISE EXCEPTION 'fundamental_update_current is missing'; END IF;

    SELECT count(*) INTO n FROM pg_constraint WHERE conrelid = 'public.fundamental_update_publication'::regclass
       AND conname IN ('fundamental_update_publication_version', 'fundamental_update_publication_version_chk', 'fundamental_update_publication_kind_chk',
                       'fundamental_update_publication_first_chk', 'fundamental_update_publication_key_chk', 'fundamental_update_publication_hash_chk');
    IF n <> 6 THEN RAISE EXCEPTION 'expected 6 constraints, found %', n; END IF;

    -- both append-only triggers exist and fire ALWAYS ('A'), so even a replica-role session cannot bypass them
    SELECT count(*) INTO n FROM pg_trigger WHERE tgrelid = 'public.fundamental_update_publication'::regclass AND NOT tgisinternal AND tgenabled = 'A'
       AND tgname IN ('fundamental_update_publication_no_update', 'fundamental_update_publication_no_truncate');
    IF n <> 2 THEN RAISE EXCEPTION 'expected 2 ALWAYS triggers, found %', n; END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        IF NOT has_table_privilege('donchian_app', 'public.fundamental_update_publication', 'SELECT,INSERT') THEN RAISE EXCEPTION 'donchian_app lacks SELECT/INSERT'; END IF;
        IF has_table_privilege('donchian_app', 'public.fundamental_update_publication', 'UPDATE') OR has_table_privilege('donchian_app', 'public.fundamental_update_publication', 'DELETE')
           OR has_table_privilege('donchian_app', 'public.fundamental_update_publication', 'TRUNCATE') THEN
            RAISE EXCEPTION 'donchian_app must not have UPDATE/DELETE/TRUNCATE';
        END IF;
        IF NOT has_table_privilege('donchian_app', 'public.fundamental_update_current', 'SELECT') THEN RAISE EXCEPTION 'donchian_app cannot read the view'; END IF;
    END IF;
END $$;

SELECT count(*) AS publication_rows, count(DISTINCT update_key) AS updates FROM fundamental_update_publication;
SELECT count(*) AS current_rows FROM fundamental_update_current;
SELECT 'migration 41 verified' AS result;
