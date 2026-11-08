-- deploy/db/fundamental_update_publication_rollback.sql -- remove migration 41 (mechanism/add_fundamental_update_publication.sql). Touches nothing else:
-- it drops only the view fundamental_update_current, the table fundamental_update_publication (with its triggers, constraints, indexes, grants
-- and owned id sequence) and the function fundamental_update_publication_append_only().
--
-- The publication record is the public history of what was published. If it holds ANY row this script REFUSES, unless the operator has first
-- taken a backup and confirms in the same session. Run with -1 (one transaction): any failure leaves everything as it was. /api/public/signals
-- answers 503 ("not available yet") by itself once the view is gone; no API change is needed for the rollback.
--
-- PROCEDURE (drilled end to end, with these exact client commands, by mechanism/inflection/tests/test_publication_schema.py::
-- test_backup_rollback_reapply_restore_drill; C = donchian-screener-postgres-1, TS = $(date -u +%Y%m%dT%H%M%SZ)):
--   0. Record the state:   ... psql ... -At -c "SELECT count(*), max(id) FROM fundamental_update_publication"
--   1. Back up (data only, this table only; no trigger disabling is involved):
--        docker exec C sh -c 'pg_dump -U "$POSTGRES_USER" -d trading_production --data-only --table=public.fundamental_update_publication' > fup_backup_TS.sql
--        sha256sum fup_backup_TS.sql            # keep the file and its hash off the VPS before step 2; the nightly backup also holds the table
--      Always dump with the pg_dump INSIDE the postgres container (the same major version as the server, 16). A newer pg_dump (17+) writes
--      settings such as `SET transaction_timeout` that PostgreSQL 16 rejects, so its dump would fail to restore (found by CI, 2026-11-08).
--   2. Roll back. With rows:
--        docker exec -i C sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production -c "SET fup.confirm_drop_with_rows = '"'"'yes'"'"'" -f -' < deploy/db/fundamental_update_publication_rollback.sql
--      With no rows (the expected case before anything is published), no confirmation is needed:
--        docker exec -i C sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/fundamental_update_publication_rollback.sql
--      Either prints 'migration 41 removed'.
-- RESTORE (to undo the rollback):
--   3. Re-create the schema:  docker exec -i C sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < mechanism/add_fundamental_update_publication.sql
--   4. Load the backup:       docker exec -i C sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < fup_backup_TS.sql
--      (COPY re-inserts every row with its original id and published_at; INSERT is allowed by the append-only triggers; the dump's setval puts the
--      id sequence past the restored ids. Every row passes the table's constraints again, so a damaged backup fails the whole load and changes nothing.)
--   5. Verify:                deploy/db/fundamental_update_publication_verify.sql must print 'migration 41 verified', and the count / max(id) must
--      equal step 0.

DO $$
DECLARE
    has_rows boolean;
BEGIN
    IF to_regclass('public.fundamental_update_publication') IS NOT NULL THEN
        -- dynamic SQL: a static reference to the table fails to plan once the table is gone, so a re-run after a completed rollback must not name it
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM public.fundamental_update_publication)' INTO has_rows;
        IF has_rows AND coalesce(current_setting('fup.confirm_drop_with_rows', true), '') <> 'yes' THEN
            RAISE EXCEPTION 'fundamental_update_publication has rows: back it up (pg_dump -t) and confirm with SET fup.confirm_drop_with_rows = ''yes''';
        END IF;
    END IF;
END $$;

DROP VIEW IF EXISTS fundamental_update_current;
DROP TABLE IF EXISTS fundamental_update_publication;            -- its triggers, constraints, indexes and sequence go with it
DROP FUNCTION IF EXISTS fundamental_update_publication_append_only();

SELECT CASE WHEN to_regclass('public.fundamental_update_publication') IS NULL AND to_regclass('public.fundamental_update_current') IS NULL
            AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'fundamental_update_publication_append_only')
            THEN 'migration 41 removed' ELSE 'STILL PRESENT' END AS result;
