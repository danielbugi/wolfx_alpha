-- deploy/db/preflight_36.sql -- READ-ONLY gate before applying migration 36 (paper tracker tables). Writes nothing; run it inside a read-only session:
--     PGOPTIONS='-c search_path=public -c default_transaction_read_only=on' psql -v ON_ERROR_STOP=1 -X -f deploy/db/preflight_36.sql
-- Ends with 'PREFLIGHT_36 OK state=ABSENT' (safe to apply) or 'PREFLIGHT_36 OK state=EXACT' (already applied exactly; a re-apply is a no-op). Any other state raises and exits non-zero: STOP.
-- Optional: -c research.preflight_require_roles=yes also demands the research roles (production must have them).
DO $pf$
DECLARE
    marker CONSTANT TEXT := 'paper_tracker_migration_36';
    t TEXT;
    f TEXT;
    present INT := 0;
    state TEXT;
    n BIGINT;
    trg BIGINT;
    need_roles BOOLEAN := coalesce(nullif(current_setting('research.preflight_require_roles', true), ''), 'no') = 'yes';
BEGIN
    IF to_regclass('strategy_variant') IS NULL THEN RAISE EXCEPTION 'PREFLIGHT_36 STOP: strategy_variant is missing (migration 35 not applied, or wrong search_path)'; END IF;
    IF to_regclass('candidate_observation') IS NULL THEN RAISE EXCEPTION 'PREFLIGHT_36 STOP: candidate_observation is missing (migration 22 not applied)'; END IF;
    SELECT count(*) INTO n FROM strategy_variant;
    IF n < 1 THEN RAISE EXCEPTION 'PREFLIGHT_36 STOP: strategy_variant holds no registered variant (the tracker has nothing to follow)'; END IF;
    RAISE NOTICE 'PREFLIGHT_36 check ok: strategy_variant (% rows) and candidate_observation exist', n;
    FOREACH f IN ARRAY ARRAY['research_paper_guard()', 'research_paper_session_stamp()', 'research_paper_observation_stamp()', 'research_paper_event_stamp()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'PREFLIGHT_36 STOP: function % exists and is not a migration-36 object', f;
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['paper_session', 'paper_observation', 'paper_event'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
                RAISE EXCEPTION 'PREFLIGHT_36 STOP: table % exists and is not a migration-36 object (no marker)', t;
            END IF;
            present := present + 1;
        END IF;
    END LOOP;
    IF present = 0 THEN
        state := 'ABSENT';
        RAISE NOTICE 'PREFLIGHT_36 check ok: none of the three paper tables exists (first apply)';
    ELSIF present = 3 THEN
        SELECT count(*) INTO trg FROM pg_trigger tg WHERE tg.tgrelid = ANY (ARRAY[to_regclass('paper_session'), to_regclass('paper_observation'), to_regclass('paper_event')]) AND NOT tg.tgisinternal AND tg.tgenabled = 'A';
        IF trg <> 9 THEN RAISE EXCEPTION 'PREFLIGHT_36 STOP: the tables exist but % of their 9 triggers are enabled ALWAYS (expected 9)', trg; END IF;
        state := 'EXACT';
        RAISE NOTICE 'PREFLIGHT_36 check ok: all three tables are already applied exactly';
    ELSE
        RAISE EXCEPTION 'PREFLIGHT_36 STOP: only % of the 3 paper tables exist (a partial state)', present;
    END IF;
    IF to_regrole('donchian_owner') IS NULL OR to_regrole('donchian_app') IS NULL THEN
        IF need_roles THEN RAISE EXCEPTION 'PREFLIGHT_36 STOP: research roles are missing (donchian_owner / donchian_app)'; END IF;
        RAISE NOTICE 'PREFLIGHT_36 note: research roles not present in this database (fine for a scratch/dev database; required in production)';
    ELSE
        RAISE NOTICE 'PREFLIGHT_36 check ok: research roles present';
    END IF;
    RAISE NOTICE 'PREFLIGHT_36 OK state=%', state;
END;
$pf$;
