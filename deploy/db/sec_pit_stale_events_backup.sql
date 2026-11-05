-- deploy/db/sec_pit_stale_events_backup.sql -- targeted backup of the stale inflection_v2_sec_pit events. READ-ONLY.
--
-- PREPARED, NOT RUN ON PRODUCTION. Procedure: docs/operations/SEC_PIT_STALE_EVENTS_CLEANUP.md.
--
-- "Stale" = an inflection_v2_sec_pit event whose quarter was first public more than 120 days after quarter end (PR #95's guard,
-- inflection.config.SEC_MAX_FIRST_PUBLIC_LAG_DAYS). Writes every such event to a client-side CSV in the table's own column order
-- with a header, then prints the row count, the id list and the full-row fingerprint that sec_pit_stale_events_cleanup.sql demands.
-- No object is created. This is IN ADDITION TO the nightly pg_dump, not instead of it.
--
--     psql -v ON_ERROR_STOP=1 -X -v backup_file=/tmp/sec_pit_stale_events_backup.csv -f deploy/db/sec_pit_stale_events_backup.sql
--
-- (backup_file: no spaces in the path.) Record `sha256sum` of the file and keep it next to the pg_dump.

\if :{?backup_file}
\else
  \echo 'ERROR: pass -v backup_file=/path/to/sec_pit_stale_events_backup.csv'
  \quit 1
\endif

\set stale_pred 'method_version = ''inflection_v2_sec_pit'' AND available_at - quarter_end > 120'

BEGIN TRANSACTION READ ONLY ISOLATION LEVEL REPEATABLE READ;

COPY (SELECT * FROM fundamental_inflection_event WHERE :stale_pred ORDER BY id) TO STDOUT WITH (FORMAT csv, HEADER true) \g :backup_file

SELECT count(*) AS rows_backed_up,
       string_agg(id::text, ',' ORDER BY id) AS ids,
       md5(string_agg(t::text, ',' ORDER BY id)) AS full_row_fingerprint,
       (SELECT count(*) FROM inflection_alert_delivery d WHERE d.event_id IN (SELECT id FROM fundamental_inflection_event WHERE :stale_pred)) AS alert_rows_referencing
FROM fundamental_inflection_event t WHERE :stale_pred;

ROLLBACK;
