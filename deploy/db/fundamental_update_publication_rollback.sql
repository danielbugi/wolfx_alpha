-- deploy/db/fundamental_update_publication_rollback.sql -- remove migration 41 (mechanism/add_fundamental_update_publication.sql). Touches nothing else.
--
-- The publication record is the public history of what was published. If it holds ANY row this script REFUSES, unless the operator has first
-- taken a backup and confirms in the same session:
--     docker exec donchian-screener-postgres-1 sh -c 'pg_dump -U "$POSTGRES_USER" -d trading_production -t fundamental_update_publication --data-only' > fup_backup_$(date -u +%Y%m%dT%H%M%SZ).sql
--     docker exec -i donchian-screener-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production -c "SET fup.confirm_drop_with_rows = '"'"'yes'"'"'" -f -' < deploy/db/fundamental_update_publication_rollback.sql
-- With no rows (the expected case before anything is published), no confirmation is needed:
--     docker exec -i donchian-screener-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/fundamental_update_publication_rollback.sql
-- Run with -1 (one transaction): any failure leaves everything as it was. Before rolling back the database, roll back (or keep at 503) the API:
-- /api/public/signals answers 503 by itself once the view is gone.

DO $$
BEGIN
    IF to_regclass('public.fundamental_update_publication') IS NOT NULL
       AND EXISTS (SELECT 1 FROM fundamental_update_publication)
       AND coalesce(current_setting('fup.confirm_drop_with_rows', true), '') <> 'yes' THEN
        RAISE EXCEPTION 'fundamental_update_publication has rows: back it up (pg_dump -t) and confirm with SET fup.confirm_drop_with_rows = ''yes''';
    END IF;
END $$;

DROP VIEW IF EXISTS fundamental_update_current;
DROP TABLE IF EXISTS fundamental_update_publication;            -- its triggers, constraints, indexes and sequence go with it
DROP FUNCTION IF EXISTS fundamental_update_publication_append_only();

SELECT CASE WHEN to_regclass('public.fundamental_update_publication') IS NULL AND to_regclass('public.fundamental_update_current') IS NULL
            AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'fundamental_update_publication_append_only')
            THEN 'migration 41 removed' ELSE 'STILL PRESENT' END AS result;
