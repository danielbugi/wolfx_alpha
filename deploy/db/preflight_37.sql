-- deploy/db/preflight_37.sql -- READ-ONLY gate before applying migration 37 (SEC catalyst ingest support). Writes nothing; run it inside a read-only session:
--     PGOPTIONS='-c search_path=public -c default_transaction_read_only=on' psql -v ON_ERROR_STOP=1 -X -f deploy/db/preflight_37.sql
-- Ends with 'PREFLIGHT_37 OK state=ABSENT' (safe to apply) or 'PREFLIGHT_37 OK state=EXACT' (already applied exactly; a re-apply is a no-op).
-- Any other state raises and exits non-zero: STOP. The SEC feed ALSO needs migrations 25 (market_event), 27 (source_observation) and 28 (catalyst_classification): this
-- script reports whether they are present (INFO) but migration 37 itself depends on none of them.
DO $pf$
DECLARE
    marker CONSTANT TEXT := 'catalyst_ingest_migration_37';
    t TEXT;
    f TEXT;
    present INT := 0;
    state TEXT;
    n BIGINT;
    trg BIGINT;
BEGIN
    FOREACH f IN ARRAY ARRAY['research_ingest_guard()', 'research_ingest_stamp()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'PREFLIGHT_37 STOP: function % exists and is not a migration-37 object', f;
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['source_snapshot', 'identifier_link', 'identifier_link_end'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
                RAISE EXCEPTION 'PREFLIGHT_37 STOP: table % exists and is not a migration-37 object (no marker)', t;
            END IF;
            present := present + 1;
            EXECUTE format('SELECT count(*) FROM %I', t) INTO n;
            RAISE NOTICE 'PREFLIGHT_37 info: % exists (% rows)', t, n;
        END IF;
    END LOOP;
    IF present NOT IN (0, 3) THEN
        RAISE EXCEPTION 'PREFLIGHT_37 STOP: % of 3 migration-37 tables exist (partially applied); investigate, do not re-run', present;
    END IF;
    IF present = 3 THEN
        SELECT count(*) INTO trg FROM pg_trigger WHERE tgrelid = ANY(ARRAY['source_snapshot'::regclass, 'identifier_link'::regclass, 'identifier_link_end'::regclass])
            AND NOT tgisinternal AND tgenabled = 'A';
        IF trg <> 9 THEN
            RAISE EXCEPTION 'PREFLIGHT_37 STOP: expected 9 ENABLE ALWAYS triggers on the migration-37 tables, found %', trg;
        END IF;
        state := 'EXACT';
    ELSE
        state := 'ABSENT';
    END IF;
    FOREACH t IN ARRAY ARRAY['market_event_revision', 'source_observation', 'catalyst_classification'] LOOP
        RAISE NOTICE 'PREFLIGHT_37 info: prerequisite-for-the-feed table % is %', t, CASE WHEN to_regclass(t) IS NULL THEN 'ABSENT' ELSE 'present' END;
    END LOOP;
    RAISE NOTICE 'PREFLIGHT_37 OK state=%', state;
END;
$pf$;
