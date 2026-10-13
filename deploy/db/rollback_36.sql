-- deploy/db/rollback_36.sql -- undo migration 36 (paper_session, paper_observation, paper_event) while it holds NO row.
-- The tables are append-only evidence, so this REFUSES (and changes nothing) when any row exists unless the operator states in writing that the loss is approved:
--     PGOPTIONS='-c search_path=public -c research.rollback_data_loss_approved=yes'      (approve only with a verified backup in hand)
-- Run atomically, with the target schema first on the search_path:
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback_36.sql
-- Touches ONLY the migration-36 objects (each is checked for its marker; no CASCADE). It never touches strategies, strategy_variant, signal_ledger, candidate_observation, roles or migrations 22-35.
DO $rb$
DECLARE
    approved BOOLEAN := coalesce(nullif(current_setting('research.rollback_data_loss_approved', true), ''), 'no') = 'yes';
    n        BIGINT := 0;
    total    BIGINT := 0;
    t        TEXT;
    f        TEXT;
BEGIN
    IF to_regclass('strategy_variant') IS NULL THEN
        RAISE EXCEPTION 'rollback_36 refused: strategy_variant is not in the current schema (wrong search_path)';
    END IF;
    FOREACH t IN ARRAY ARRAY['paper_event', 'paper_observation', 'paper_session'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> 'paper_tracker_migration_36' THEN
                RAISE EXCEPTION 'rollback_36 refused: % is not a migration-36 object', t;
            END IF;
            EXECUTE format('SELECT count(*) FROM %I', t) INTO n;
            total := total + n;
        END IF;
    END LOOP;
    IF total > 0 AND NOT approved THEN
        RAISE EXCEPTION 'rollback_36 refused: the paper-tracker tables hold % row(s) of immutable evidence (no data-loss approval given)', total;
    END IF;
    FOREACH t IN ARRAY ARRAY['paper_event', 'paper_observation', 'paper_session'] LOOP      -- children first; DDL: the immutability triggers go with the table and do not block it
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('DROP TABLE %I', t);
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_paper_event_stamp()', 'research_paper_observation_stamp()', 'research_paper_session_stamp()', 'research_paper_guard()'] LOOP
        IF to_regprocedure(f) IS NOT NULL THEN
            IF coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> 'paper_tracker_migration_36' THEN
                RAISE EXCEPTION 'rollback_36 refused: function % is not a migration-36 object', f;
            END IF;
            EXECUTE format('DROP FUNCTION %s', f);
        END IF;
    END LOOP;
    RAISE NOTICE 'migration 36 rolled back (% row(s) removed)', total;
END;
$rb$;
