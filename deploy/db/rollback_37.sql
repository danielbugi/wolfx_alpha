-- deploy/db/rollback_37.sql -- undo migration 37 (source_snapshot, identifier_link, identifier_link_end) while it holds NO row.
-- The tables are append-only evidence, so this REFUSES (and changes nothing) when any row exists unless the operator states in writing that the loss is approved:
--     PGOPTIONS='-c search_path=public -c research.rollback_data_loss_approved=yes'      (approve only with a verified backup in hand)
-- Run atomically, with the target schema first on the search_path:
--     PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback_37.sql
-- Touches ONLY the migration-37 objects (each is checked for its marker; no CASCADE). It never touches market_event, market_event_revision, source_observation,
-- source_poll, catalyst_classification, candidate_observation, paper_*, roles or migrations 22-36. Facts and first-seen rows written by the SEC feed live in the
-- migration 25 / 27 / 28 tables and are NOT removed by this script (they are immutable; removing them needs its own approved procedure).
DO $rb$
DECLARE
    approved BOOLEAN := coalesce(nullif(current_setting('research.rollback_data_loss_approved', true), ''), 'no') = 'yes';
    n        BIGINT := 0;
    total    BIGINT := 0;
    t        TEXT;
    f        TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['identifier_link_end', 'identifier_link', 'source_snapshot'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> 'catalyst_ingest_migration_37' THEN
                RAISE EXCEPTION 'rollback_37 refused: % is not a migration-37 object', t;
            END IF;
            EXECUTE format('SELECT count(*) FROM %I', t) INTO n;
            total := total + n;
        END IF;
    END LOOP;
    IF total > 0 AND NOT approved THEN
        RAISE EXCEPTION 'rollback_37 refused: the catalyst-ingest tables hold % row(s) of immutable evidence (no data-loss approval given)', total;
    END IF;
    FOREACH t IN ARRAY ARRAY['identifier_link_end', 'identifier_link', 'source_snapshot'] LOOP      -- children first; the immutability triggers go with the table and do not block a DROP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('DROP TABLE %I', t);
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_ingest_stamp()', 'research_ingest_guard()'] LOOP
        IF to_regprocedure(f) IS NOT NULL THEN
            IF coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> 'catalyst_ingest_migration_37' THEN
                RAISE EXCEPTION 'rollback_37 refused: function % is not a migration-37 object', f;
            END IF;
            EXECUTE format('DROP FUNCTION %s', f);
        END IF;
    END LOOP;
    RAISE NOTICE 'migration 37 rolled back (% row(s) removed)', total;
END;
$rb$;
