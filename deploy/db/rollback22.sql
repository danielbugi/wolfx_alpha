-- deploy/db/rollback22.sql -- undo mechanism/add_research_observation_tables.sql (migration 22, Release B research layer).
--
-- !! SAFE ONLY BEFORE MEANINGFUL RELEASE B DATA EXISTS.  This script DROPS the research tables.  Once capture has
-- !! run in production, the candidate observations, T0 snapshots, capture-run history, activation boundaries and
-- !! maintenance audit trail it deletes cannot be reconstructed (the screener inputs of past sessions are gone).
-- !! It therefore REFUSES to run when any of those tables holds a row, or when signal_ledger carries lineage,
-- !! unless the operator states in writing that the data loss is approved:
-- !!     -c research.rollback_data_loss_approved=yes
-- !! Approve only after exporting what you want to keep (pg_dump -t candidate_observation -t feature_snapshot ...).
--
-- Run as a superuser or as the owner of the research tables AND of signal_ledger, atomically, with the target
-- schema first on the search_path (it operates on current_schema()):
--     PGOPTIONS='-c search_path=public' \
--         psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback22.sql          # empty tables: no approval needed
--     PGOPTIONS='-c search_path=public -c research.rollback_data_loss_approved=yes' \
--         psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback22.sql          # tables hold data: explicit approval
--
-- What it does, in order (one transaction; any failure leaves everything as it was):
--   1. Refuses unless every object it is about to drop carries the migration-22 marker comment, so it can never
--      drop a look-alike table that someone else created.
--   2. Counts rows; refuses without the approval GUC when anything would be lost.
--   3. Drops the four ledger lineage constraints from signal_ledger (observation FK, snapshot FK, feature-set FK,
--      all-or-none CHECK). With approval, NULLs the ledger's lineage columns that point at rows about to vanish;
--      the Release A rows (NULL lineage) are untouched, and no other signal_ledger column is ever written.
--   4. Drops the 8 tables and the 8 functions (their triggers go with the tables). No CASCADE: an unexpected
--      dependent object makes the transaction fail instead of being silently dropped.
--
-- What it does NOT do: it does not drop roles (see research_roles_rollback.sql) and does not touch migrations
-- 19/20/21 or any Release A column. The migration-21 lineage columns on signal_ledger remain (nullable).
-- Order when also undoing the roles: services back to their old DB_USER, then research_roles_rollback.sql,
-- then this script.

DO $rb$
DECLARE
    sch      TEXT := current_schema();
    marker   CONSTANT TEXT := 'release_b_migration_22';
    approved BOOLEAN := coalesce(nullif(current_setting('research.rollback_data_loss_approved', true), ''), 'no') = 'yes';
    t        TEXT;
    n        BIGINT;
    total    BIGINT := 0;
    present  INTEGER := 0;
    detail   TEXT := '';
    lineage  BIGINT := 0;
BEGIN
    FOREACH t IN ARRAY ARRAY['feature_set_registry', 'candidate_capture_run', 'research_capture_activation',
                             'research_maintenance_log', 'research_maintenance_session',
                             'research_maintenance_audit', 'feature_snapshot', 'candidate_observation'] LOOP
        IF to_regclass(t) IS NULL THEN
            CONTINUE;
        END IF;
        present := present + 1;
        IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
            RAISE EXCEPTION 'rollback22 refused: table % in schema % is not a migration-22 object (no marker); will not drop it', t, sch;
        END IF;
        EXECUTE format('SELECT count(*) FROM %I', t) INTO n;
        total := total + n;
        IF n > 0 THEN
            detail := detail || format(' %s=%s', t, n);
        END IF;
    END LOOP;

    IF present = 0 THEN
        RAISE EXCEPTION 'rollback22 refused: no migration-22 tables in schema %; nothing to roll back (wrong search_path?)', sch;
    END IF;
    IF present <> 8 THEN
        RAISE EXCEPTION 'rollback22 refused: % of 8 research tables exist in schema % (partial state); investigate by hand', present, sch;
    END IF;

    IF to_regclass('signal_ledger') IS NOT NULL
       AND EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = sch AND table_name = 'signal_ledger' AND column_name = 'observation_id') THEN
        SELECT count(*) INTO lineage FROM signal_ledger
        WHERE observation_id IS NOT NULL OR feature_snapshot_id IS NOT NULL OR feature_set_version IS NOT NULL;
    END IF;

    IF (total > 0 OR lineage > 0) AND NOT approved THEN
        RAISE EXCEPTION 'rollback22 refused: it would destroy Release B data (rows:%; signal_ledger rows with lineage: %). '
                        'Export what you need, then re-run with -c research.rollback_data_loss_approved=yes', detail, lineage;
    END IF;
    IF total > 0 OR lineage > 0 THEN
        RAISE NOTICE 'rollback22: data loss APPROVED -- dropping rows:% and clearing lineage on % ledger row(s)', detail, lineage;
    END IF;

    -- 3. ledger lineage constraints, then (approved only) the now-dangling lineage values
    IF to_regclass('signal_ledger') IS NOT NULL THEN
        ALTER TABLE signal_ledger DROP CONSTRAINT IF EXISTS signal_ledger_lineage_all_or_none;
        ALTER TABLE signal_ledger DROP CONSTRAINT IF EXISTS signal_ledger_observation_fk;
        ALTER TABLE signal_ledger DROP CONSTRAINT IF EXISTS signal_ledger_snapshot_fk;
        ALTER TABLE signal_ledger DROP CONSTRAINT IF EXISTS signal_ledger_feature_set_fk;
        IF lineage > 0 THEN
            UPDATE signal_ledger SET observation_id = NULL, feature_snapshot_id = NULL, feature_set_version = NULL
            WHERE observation_id IS NOT NULL OR feature_snapshot_id IS NOT NULL OR feature_set_version IS NOT NULL;
        END IF;
    END IF;

    -- 4. tables (children first), then functions. No CASCADE.
    DROP TABLE candidate_observation;
    DROP TABLE feature_snapshot;
    DROP TABLE candidate_capture_run;
    DROP TABLE research_maintenance_audit;
    DROP TABLE research_maintenance_session;
    DROP TABLE research_maintenance_log;
    DROP TABLE research_capture_activation;
    DROP TABLE feature_set_registry;

    DROP FUNCTION research_guard_immutable();
    DROP FUNCTION research_audit_append_only();
    DROP FUNCTION research_maintenance_log_guard();
    DROP FUNCTION research_maintenance_open(text, text, integer);
    DROP FUNCTION research_maintenance_approve(bigint);
    DROP FUNCTION research_maintenance_begin(bigint);
    DROP FUNCTION research_maintenance_close(bigint);
    DROP FUNCTION research_capture_set_state(bigint, text, date, text);
END;
$rb$;

-- Verification: nothing of migration 22 may remain; the Release A tables and columns must.
DO $v$
DECLARE
    sch TEXT := current_schema();
    left_over TEXT;
BEGIN
    SELECT string_agg(c.relname, ', ') INTO left_over FROM pg_class c
    WHERE c.relnamespace = to_regnamespace(sch)
      AND c.relname IN ('feature_set_registry', 'candidate_capture_run', 'research_capture_activation',
                        'research_maintenance_log', 'research_maintenance_session', 'research_maintenance_audit',
                        'feature_snapshot', 'candidate_observation');
    IF left_over IS NOT NULL THEN
        RAISE EXCEPTION 'rollback22 verify failed: tables remain: %', left_over;
    END IF;
    SELECT string_agg(p.proname, ', ') INTO left_over FROM pg_proc p
    WHERE p.pronamespace = to_regnamespace(sch) AND p.proname LIKE 'research\_%';
    IF left_over IS NOT NULL THEN
        RAISE EXCEPTION 'rollback22 verify failed: functions remain: %', left_over;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('signal_ledger')
               AND conname LIKE 'signal\_ledger\_%' AND conname IN ('signal_ledger_observation_fk', 'signal_ledger_snapshot_fk',
                   'signal_ledger_feature_set_fk', 'signal_ledger_lineage_all_or_none')) THEN
        RAISE EXCEPTION 'rollback22 verify failed: a ledger lineage constraint remains';
    END IF;
    IF to_regclass('strategies') IS NULL OR to_regclass('signal_ledger') IS NULL THEN
        RAISE EXCEPTION 'rollback22 verify failed: a Release A table is missing';
    END IF;
END;
$v$;
