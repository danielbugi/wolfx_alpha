-- deploy/db/rollback_24_31.sql -- undo the forward-research migrations 24..31 (market/sector snapshots, market events, forward-return labels,
-- source observations, catalyst classifications, stock relative strength, dataset/experiment registry, sector history).
--
-- !! SAFE ONLY WHILE THOSE TABLES ARE EMPTY.  Every one of them is an append-only evidence table: once forward collection has written a row,
-- !! the row cannot be reconstructed (it records what was observed on that day). The normal way to stop is therefore NOT this script but
-- !! "stop future collection" (disable the timer, unset the flags, set the capture state to disabled) and, for already-collected history,
-- !! quarantine through the provenance architecture, never deletion (docs/operations/FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md, section "Rollback").
-- !! This script REFUSES when any of the tables holds a row unless the operator states in writing that the data loss is approved:
-- !!     PGOPTIONS='-c search_path=public -c research.rollback_data_loss_approved=yes'
-- !! Approve only after exporting what you want to keep (pg_dump -t <table> ...), with a verified backup in hand.
--
-- Run as a superuser or as the owner of the tables (donchian_owner), atomically, with the target schema first on the search_path:
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback_24_31.sql
--
-- What it does, in order (one transaction; any failure leaves everything as it was):
--   1. Refuses when signal_ledger is not in the current schema (wrong search_path), so it can never act on the wrong schema.
--   2. Counts the rows of every table that exists; refuses without the approval setting when anything would be lost.
--   3. Drops, in reverse migration order and with NO CASCADE, whichever of the 16 tables exist (a half-applied set is rolled back too; an unexpected
--      dependent object makes the transaction fail instead of being dropped silently). Triggers go with their tables.
--   4. Drops the 20 trigger/consistency functions of those migrations (again without CASCADE).
--
-- What it does NOT do: it never touches migration 22/23 objects (candidate_observation, feature_snapshot, the capture/maintenance tables), signal_ledger
-- or any other pre-existing table; it does not drop roles or revoke grants (grants disappear with the tables; research_roles.sql is valid
-- before and after these migrations); it does not change the mechanism image, any environment variable or any timer.
-- The table and function lists below are pinned to the compose migration list by test_rollback_24_31_db.py: adding a table to a migration without
-- adding it here fails CI.

DO $rb$
DECLARE
    sch       TEXT := current_schema();
    approved  BOOLEAN := coalesce(nullif(current_setting('research.rollback_data_loss_approved', true), ''), 'no') = 'yes';
    -- reverse migration order (31 -> 24); inside a migration, children before parents
    tbls      TEXT[] := ARRAY[
        'sector_poll', 'sector_observation', 'sector_reconstruction',                              -- 31
        'experiment_result', 'experiment_registration', 'dataset_manifest',                        -- 30
        'stock_relative_strength',                                                                 -- 29
        'catalyst_classification',                                                                 -- 28
        'source_poll', 'source_observation',                                                       -- 27
        'forward_return_label',                                                                    -- 26
        'market_event_revision', 'market_event',                                                   -- 25
        'sector_snapshot', 'market_snapshot', 'universe_snapshot'];                                -- 24
    fns       TEXT[] := ARRAY[
        'research_sector_enc', 'research_sector_guard', 'research_sector_obs_stamp', 'research_sector_poll_stamp',
        'research_sector_recon_stamp', 'research_sector_row_hash',
        'research_registry_consistency', 'research_registry_guard', 'research_registry_stamp',
        'research_rs_guard', 'research_rs_stamp',
        'research_classification_consistency', 'research_classification_guard',
        'research_observation_guard', 'research_observation_stamp', 'research_poll_stamp',
        'research_label_consistency', 'research_label_guard',
        'research_market_event_stamp',
        'research_market_guard'];
    t         TEXT;
    n         BIGINT;
    total     BIGINT := 0;
    present   INTEGER := 0;
    detail    TEXT := '';
    r         RECORD;
    dropped_t INTEGER := 0;
    dropped_f INTEGER := 0;
BEGIN
    IF to_regclass('signal_ledger') IS NULL THEN
        RAISE EXCEPTION 'rollback_24_31 refused: signal_ledger is not in schema % (wrong search_path?)', sch;
    END IF;

    FOREACH t IN ARRAY tbls LOOP
        IF to_regclass(t) IS NULL THEN
            CONTINUE;
        END IF;
        present := present + 1;
        EXECUTE format('SELECT count(*) FROM %I', t) INTO n;
        total := total + n;
        IF n > 0 THEN
            detail := detail || format(' %s=%s', t, n);
        END IF;
    END LOOP;

    IF total > 0 AND NOT approved THEN
        RAISE EXCEPTION 'rollback_24_31 refused: append-only evidence would be destroyed (%). Stop future collection instead, or export the data and state the loss is approved (research.rollback_data_loss_approved=yes)', btrim(detail);
    END IF;

    FOREACH t IN ARRAY tbls LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('DROP TABLE %I', t);                          -- no CASCADE: an unexpected dependent object aborts the whole transaction
            dropped_t := dropped_t + 1;
        END IF;
    END LOOP;

    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = to_regnamespace(sch) AND p.proname = ANY (fns) ORDER BY p.proname LOOP
        EXECUTE format('DROP FUNCTION %s', r.sig);
        dropped_f := dropped_f + 1;
    END LOOP;

    RAISE NOTICE 'rollback_24_31 OK in schema %: % table(s) dropped (of % present), % function(s) dropped, % row(s) discarded%',
        sch, dropped_t, present, dropped_f, total, CASE WHEN total > 0 THEN ' (approved: ' || btrim(detail) || ')' ELSE '' END;
END
$rb$;
