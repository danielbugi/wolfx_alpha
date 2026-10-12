-- deploy/db/preflight_35.sql -- READ-ONLY gate before applying migration 35 (strategy_variant). Writes nothing; run it inside a read-only session:
--     PGOPTIONS='-c search_path=public -c default_transaction_read_only=on' psql -v ON_ERROR_STOP=1 -X -f deploy/db/preflight_35.sql
-- Prints one NOTICE per check and ends with 'PREFLIGHT_35 OK state=ABSENT' (safe to apply) or 'PREFLIGHT_35 OK state=EXACT' (already applied exactly; a re-apply is a no-op).
-- Any other state raises and exits non-zero: STOP. Optional: -c research.preflight_require_roles=yes also demands the research roles (production must have them).
DO $pf$
DECLARE
    marker CONSTANT TEXT := 'strategy_variant_migration_35';
    state  TEXT;
    n      BIGINT;
    f      TEXT;
    trg    BIGINT;
    need_roles BOOLEAN := coalesce(nullif(current_setting('research.preflight_require_roles', true), ''), 'no') = 'yes';
BEGIN
    IF to_regclass('strategies') IS NULL THEN RAISE EXCEPTION 'PREFLIGHT_35 STOP: strategies is missing from the current schema (wrong search_path, or the strategy identity migration is not applied)'; END IF;
    IF to_regclass('signal_ledger') IS NULL THEN RAISE EXCEPTION 'PREFLIGHT_35 STOP: signal_ledger is missing'; END IF;
    SELECT count(*) INTO n FROM strategies WHERE strategy_key = 'donchian_breakout' AND strategy_version = 'v1';
    IF n <> 1 THEN RAISE EXCEPTION 'PREFLIGHT_35 STOP: the parent strategy donchian_breakout v1 is not present exactly once (found %)', n; END IF;
    RAISE NOTICE 'PREFLIGHT_35 check ok: strategies and the parent strategy exist; strategies rows=%', (SELECT count(*) FROM strategies);
    FOREACH f IN ARRAY ARRAY['research_strategy_variant_guard()', 'research_strategy_variant_stamp()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'PREFLIGHT_35 STOP: function % exists and is not a migration-35 object', f;
        END IF;
    END LOOP;
    IF to_regclass('strategy_variant') IS NULL THEN
        state := 'ABSENT';
        RAISE NOTICE 'PREFLIGHT_35 check ok: strategy_variant is absent (first apply)';
    ELSE
        IF coalesce(obj_description(to_regclass('strategy_variant'), 'pg_class'), '') <> marker THEN
            RAISE EXCEPTION 'PREFLIGHT_35 STOP: strategy_variant exists and is not a migration-35 object (no marker)';
        END IF;
        SELECT count(*) INTO trg FROM pg_trigger t WHERE t.tgrelid = to_regclass('strategy_variant') AND NOT t.tgisinternal AND t.tgenabled = 'A'
            AND t.tgname IN ('strategy_variant_immutable_row', 'strategy_variant_immutable_truncate', 'strategy_variant_stamp');
        IF trg <> 3 THEN RAISE EXCEPTION 'PREFLIGHT_35 STOP: strategy_variant exists but % of its 3 triggers are enabled ALWAYS (expected 3)', trg; END IF;
        EXECUTE 'SELECT count(*) FROM strategy_variant' INTO n;
        state := 'EXACT';
        RAISE NOTICE 'PREFLIGHT_35 check ok: strategy_variant is already applied exactly; rows=%', n;
    END IF;
    IF to_regrole('donchian_owner') IS NULL OR to_regrole('donchian_app') IS NULL THEN
        IF need_roles THEN RAISE EXCEPTION 'PREFLIGHT_35 STOP: research roles are missing (donchian_owner / donchian_app)'; END IF;
        RAISE NOTICE 'PREFLIGHT_35 note: research roles not present in this database (fine for a scratch/dev database; required in production)';
    ELSE
        RAISE NOTICE 'PREFLIGHT_35 check ok: research roles present';
    END IF;
    RAISE NOTICE 'PREFLIGHT_35 info: signal_ledger rows=% (untouched by migration 35)', (SELECT count(*) FROM signal_ledger);
    RAISE NOTICE 'PREFLIGHT_35 OK state=%', state;
END;
$pf$;
