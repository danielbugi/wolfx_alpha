-- deploy/db/rollback_32.sql -- undo migration 32 (price_discontinuity_scan) while it holds NO row.
-- The table is append-only evidence, so this REFUSES (and changes nothing) when any row exists unless the operator states in writing that the loss is approved:
--     PGOPTIONS='-c search_path=public -c research.rollback_data_loss_approved=yes'      (approve only with a verified backup in hand)
-- Run atomically, with the target schema first on the search_path:
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback_32.sql
-- Touches ONLY the migration-32 objects (each is checked for its marker; no CASCADE). It never touches price_discontinuities, roles, grants or migrations 22-31.
DO $rb$
DECLARE
    approved BOOLEAN := coalesce(nullif(current_setting('research.rollback_data_loss_approved', true), ''), 'no') = 'yes';
    n        BIGINT := 0;
    f        TEXT;
BEGIN
    IF to_regclass('signal_ledger') IS NULL THEN
        RAISE EXCEPTION 'rollback_32 refused: signal_ledger is not in the current schema (wrong search_path)';
    END IF;
    IF to_regclass('price_discontinuity_scan') IS NOT NULL THEN
        IF coalesce(obj_description(to_regclass('price_discontinuity_scan'), 'pg_class'), '') <> 'discontinuity_scan_migration_32' THEN
            RAISE EXCEPTION 'rollback_32 refused: price_discontinuity_scan is not a migration-32 object';
        END IF;
        EXECUTE 'SELECT count(*) FROM price_discontinuity_scan' INTO n;
        IF n > 0 AND NOT approved THEN
            RAISE EXCEPTION 'rollback_32 refused: price_discontinuity_scan holds % row(s) of immutable evidence (no data-loss approval given)', n;
        END IF;
        EXECUTE 'DROP TABLE price_discontinuity_scan';        -- DDL: the immutability triggers go with the table and do not block it
    END IF;
    FOREACH f IN ARRAY ARRAY['research_discontinuity_scan_stamp()', 'research_discontinuity_scan_guard()',
                             'research_price_input_fingerprint(date)', 'research_discontinuity_result_fingerprint()'] LOOP
        IF to_regprocedure(f) IS NOT NULL THEN
            IF coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> 'discontinuity_scan_migration_32' THEN
                RAISE EXCEPTION 'rollback_32 refused: function % is not a migration-32 object', f;
            END IF;
            EXECUTE format('DROP FUNCTION %s', f);
        END IF;
    END LOOP;
    RAISE NOTICE 'migration 32 rolled back (% row(s) removed)', n;
END;
$rb$;
