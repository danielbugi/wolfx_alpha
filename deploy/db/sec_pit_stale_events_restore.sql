-- deploy/db/sec_pit_stale_events_restore.sql -- put back stale inflection_v2_sec_pit events removed by sec_pit_stale_events_cleanup.sql.
--
-- PREPARED, NOT RUN ON PRODUCTION. Procedure: docs/operations/SEC_PIT_STALE_EVENTS_CLEANUP.md.
-- Input: the CSV written by sec_pit_stale_events_backup.sql, on STANDARD INPUT. Re-inserts only rows whose id is currently missing, with
-- their original ids and timestamps (idempotent). DRY RUN BY DEFAULT (ROLLBACK unless -v commit=on).
--
--     psql -v ON_ERROR_STOP=1 -X -f deploy/db/sec_pit_stale_events_restore.sql < /tmp/sec_pit_stale_events_backup.csv
--     psql -v ON_ERROR_STOP=1 -X -v commit=on -f deploy/db/sec_pit_stale_events_restore.sql < /tmp/sec_pit_stale_events_backup.csv

\if :{?commit}
\else
  \set commit off
\endif

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL application_name = 'sec_pit_stale_events_restore';
CREATE TEMP TABLE restore_rows (LIKE fundamental_inflection_event) ON COMMIT DROP;
\copy restore_rows FROM pstdin WITH (FORMAT csv, HEADER true)
SELECT count(*) AS rows_in_file, count(*) FILTER (WHERE id NOT IN (SELECT id FROM fundamental_inflection_event)) AS rows_to_restore FROM restore_rows;
INSERT INTO fundamental_inflection_event SELECT * FROM restore_rows WHERE id NOT IN (SELECT id FROM fundamental_inflection_event);
-- a deployed guard does not re-create them, so the identity (symbol, quarter_end, event_type, method_version) is free; a conflict aborts the restore
\if :commit
  COMMIT;
  \echo 'COMMITTED'
\else
  ROLLBACK;
  \echo 'DRY RUN: rolled back (pass -v commit=on to apply)'
\endif
