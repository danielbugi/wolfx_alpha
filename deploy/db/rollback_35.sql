-- deploy/db/rollback_35.sql -- undo migration 35 (strategy_variant) while it holds NO row.
-- The table is append-only identity evidence, so this REFUSES (and changes nothing) when any row exists unless the operator states in writing that the loss is approved:
--     PGOPTIONS='-c search_path=public -c research.rollback_data_loss_approved=yes'      (approve only with a verified backup in hand)
-- Run atomically, with the target schema first on the search_path:
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback_35.sql
-- Migration 36 (the paper tracker) references strategy_variant: roll 36 back FIRST (this script refuses while its tables exist).
-- Touches ONLY the migration-35 objects (each is checked for its marker; no CASCADE). It never touches strategies, signal_ledger, roles or migrations 22-34.
DO $rb$
DECLARE
    approved BOOLEAN := coalesce(nullif(current_setting('research.rollback_data_loss_approved', true), ''), 'no') = 'yes';
    n        BIGINT := 0;
    f        TEXT;
BEGIN
    IF to_regclass('strategies') IS NULL THEN
        RAISE EXCEPTION 'rollback_35 refused: strategies is not in the current schema (wrong search_path)';
    END IF;
    IF to_regclass('paper_observation') IS NOT NULL THEN
        RAISE EXCEPTION 'rollback_35 refused: migration 36 (the paper tracker tables) references strategy_variant; roll back 36 first (deploy/db/rollback_36.sql)';
    END IF;
    IF to_regclass('strategy_variant') IS NOT NULL THEN
        IF coalesce(obj_description(to_regclass('strategy_variant'), 'pg_class'), '') <> 'strategy_variant_migration_35' THEN
            RAISE EXCEPTION 'rollback_35 refused: strategy_variant is not a migration-35 object';
        END IF;
        EXECUTE 'SELECT count(*) FROM strategy_variant' INTO n;
        IF n > 0 AND NOT approved THEN
            RAISE EXCEPTION 'rollback_35 refused: strategy_variant holds % row(s) of immutable identity evidence (no data-loss approval given)', n;
        END IF;
        EXECUTE 'DROP TABLE strategy_variant';        -- DDL: the immutability triggers go with the table and do not block it
    END IF;
    FOREACH f IN ARRAY ARRAY['research_strategy_variant_stamp()', 'research_strategy_variant_guard()'] LOOP
        IF to_regprocedure(f) IS NOT NULL THEN
            IF coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> 'strategy_variant_migration_35' THEN
                RAISE EXCEPTION 'rollback_35 refused: function % is not a migration-35 object', f;
            END IF;
            EXECUTE format('DROP FUNCTION %s', f);
        END IF;
    END LOOP;
    RAISE NOTICE 'migration 35 rolled back (% row(s) removed)', n;
END;
$rb$;
