-- deploy/db/sec_pit_stale_events_cleanup.sql -- delete the stale inflection_v2_sec_pit events (first public > 120 days after quarter end).
--
-- PREPARED, NOT RUN ON PRODUCTION. Procedure: docs/operations/SEC_PIT_STALE_EVENTS_CLEANUP.md. Run it only AFTER the derived image with
-- PR #95's guard is live: the old step 8e re-emits these events every night (they are inside its 200-day window).
--
-- DRY RUN BY DEFAULT (ROLLBACK unless -v commit=on). Refuses unless the CURRENT stale set is exactly the backed-up set:
--   -v expected_ids=14826,14875,...   the id list printed by sec_pit_stale_events_backup.sql
--   -v backup_fp=<md5>                its full_row_fingerprint
-- and refuses if any alert delivery row references one of them (none did on 2026-11-05).
--
--     psql -v ON_ERROR_STOP=1 -X -v expected_ids=... -v backup_fp=... -f deploy/db/sec_pit_stale_events_cleanup.sql
--     psql -v ON_ERROR_STOP=1 -X -v expected_ids=... -v backup_fp=... -v commit=on -f deploy/db/sec_pit_stale_events_cleanup.sql

\if :{?expected_ids}
\else
  \echo 'ERROR: pass -v expected_ids=<comma-separated ids from the backup>'
  \quit 1
\endif
\if :{?backup_fp}
\else
  \echo 'ERROR: pass -v backup_fp=<full_row_fingerprint from the backup>'
  \quit 1
\endif
\if :{?commit}
\else
  \set commit off
\endif

\set stale_pred 'method_version = ''inflection_v2_sec_pit'' AND available_at - quarter_end > 120'

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SET LOCAL application_name = 'sec_pit_stale_events_cleanup';
LOCK TABLE fundamental_inflection_event IN SHARE ROW EXCLUSIVE MODE;

-- preflight: the stale set now must be byte-for-byte the backed-up set, and nothing may reference it
SELECT (string_agg(id::text, ',' ORDER BY id) = :'expected_ids') AS ids_match,
       (md5(string_agg(t::text, ',' ORDER BY id)) = :'backup_fp') AS fingerprint_match,
       (SELECT count(*) FROM inflection_alert_delivery d WHERE d.event_id IN (SELECT id FROM fundamental_inflection_event WHERE :stale_pred)) = 0 AS unreferenced
FROM fundamental_inflection_event t WHERE :stale_pred \gset pre_
\if :pre_ids_match
\else
  \echo 'REFUSED: the current stale set differs from expected_ids (re-run the backup)'
  ROLLBACK;
  \quit 1
\endif
\if :pre_fingerprint_match
\else
  \echo 'REFUSED: the stale rows changed since the backup (re-run the backup)'
  ROLLBACK;
  \quit 1
\endif
\if :pre_unreferenced
\else
  \echo 'REFUSED: an alert delivery references a stale event'
  ROLLBACK;
  \quit 1
\endif

DELETE FROM fundamental_inflection_event WHERE :stale_pred AND id = ANY (string_to_array(:'expected_ids', ',')::bigint[]);
SELECT count(*) AS stale_remaining FROM fundamental_inflection_event WHERE :stale_pred;

\if :commit
  COMMIT;
  \echo 'COMMITTED'
\else
  ROLLBACK;
  \echo 'DRY RUN: rolled back (pass -v commit=on to apply)'
\endif
