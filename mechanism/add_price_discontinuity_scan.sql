-- mechanism/add_price_discontinuity_scan.sql   (migration 32 -- append-only heartbeat that proves a price-discontinuity scan completed)
--
-- WHY. `price_discontinuities` is a CURRENT-STATE table that the nightly dataset builder maintains. Its rows say what was found; they cannot say that the
-- detector RAN, on WHICH session, over WHICH price data, or WHEN it finished. If the nightly step is skipped or fails, new discontinuities are simply absent
-- (not "late"), and an `observed` market/sector/relative-strength snapshot would be written without them. This table is the missing evidence:
--
--   price_discontinuity_scan   one immutable row per scan OUTCOME.
--     status = 'complete'  the scan finished for `session_date`. The row is only accepted if, AT INSERT TIME, the database itself recomputes
--                          (a) the fingerprint of every stock_prices row dated <= session_date and finds it equal to the claimed `input_fingerprint`
--                              (so the prices did not change while the scan ran, and the claim is not a guess), with the newest bar of the WHOLE price table =
--                              session_date (the detector reads the whole table, so the session's own bar must be loaded and nothing newer mixed in), and
--                          (b) the fingerprint of the price_discontinuities key set and finds it equal to `result_fingerprint` (the table the readers
--                              will see is exactly what this scan produced).
--                          `finished_at` is the database clock (clock_timestamp()), never the caller's.
--     status = 'failed'    the scan was attempted and did not complete (failure_reason is coded). It claims no fingerprint, so it can never satisfy a
--                          reader. A scan that was skipped leaves no row at all; that also cannot satisfy a reader.
--   Retries are idempotent: a second COMPLETE scan with the same (session_date, input_fingerprint, result_fingerprint) is a no-op (unique partial index
--   + ON CONFLICT DO NOTHING in the writer), so the FIRST completion time is kept. Changed prices (a vendor correction) have a different fingerprint and
--   need a new scan; the old row stays as history and no longer matches.
--
-- Nothing is backfilled and nothing is inferred: no row exists for any session until the builder writes one at the end of a complete run.
-- Immutability, two layers like migrations 24-31: privileges (runtime role SELECT + INSERT only; deploy/db/research_roles.sql) and ENABLE ALWAYS
-- BEFORE UPDATE / DELETE row + TRUNCATE triggers. Additive and forward-safe (IF NOT EXISTS; refuses to adopt a same-named object it did not create).
-- Independent of migrations 24-31. Rollback while empty: deploy/db/rollback_32.sql.

DO $guard$
BEGIN
    IF to_regclass('price_discontinuity_scan') IS NOT NULL
       AND coalesce(obj_description(to_regclass('price_discontinuity_scan'), 'pg_class'), '') <> 'discontinuity_scan_migration_32' THEN
        RAISE EXCEPTION 'migration 32 refused: table price_discontinuity_scan already exists and is not a migration-32 object (no marker)';
    END IF;
END;
$guard$;

CREATE TABLE IF NOT EXISTS price_discontinuity_scan (
    id                  BIGSERIAL    PRIMARY KEY,
    session_date        DATE         NOT NULL,            -- the intended US market session (explicit, passed by the pipeline; never inferred from the clock)
    status              VARCHAR(10)  NOT NULL,            -- complete | failed
    failure_reason      VARCHAR(40),                      -- coded; set exactly when status = failed
    newest_bar          DATE,                             -- max(stock_prices.date) the scan saw; = session_date for a complete scan
    n_symbols           INTEGER,                          -- distinct symbols in the price input (dated <= session_date)
    n_price_rows        BIGINT,                           -- price rows in the input
    input_fingerprint   CHAR(64),                         -- sha256 over the price input, RECOMPUTED BY THE DATABASE at insert (research_price_input_fingerprint)
    n_discontinuities   INTEGER,                          -- rows of price_discontinuities when the scan completed
    result_fingerprint  CHAR(64),                         -- sha256 over the price_discontinuities key set + first-detected stamps, RECOMPUTED BY THE DATABASE
    started_at          TIMESTAMPTZ  NOT NULL,            -- the builder's start (database clock, read before the scan); validated <= finished_at
    finished_at         TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),   -- OVERWRITTEN by the stamp trigger with the database clock: the completion time
    run_id              VARCHAR(80)  NOT NULL,
    writer              VARCHAR(80)  NOT NULL,
    code_ref            VARCHAR(160) NOT NULL,
    CONSTRAINT price_discontinuity_scan_status_chk CHECK (status IN ('complete', 'failed')),
    CONSTRAINT price_discontinuity_scan_complete_chk CHECK (status <> 'complete' OR (
        failure_reason IS NULL AND newest_bar = session_date AND n_symbols IS NOT NULL AND n_price_rows IS NOT NULL AND input_fingerprint IS NOT NULL
        AND n_discontinuities IS NOT NULL AND result_fingerprint IS NOT NULL)),
    CONSTRAINT price_discontinuity_scan_failed_chk CHECK (status <> 'failed' OR (
        failure_reason IS NOT NULL AND input_fingerprint IS NULL AND result_fingerprint IS NULL)),
    CONSTRAINT price_discontinuity_scan_reason_fmt_chk CHECK (failure_reason IS NULL OR failure_reason ~ '^[a-z0-9_]{1,40}$'),
    CONSTRAINT price_discontinuity_scan_hash_chk CHECK ((input_fingerprint IS NULL OR input_fingerprint ~ '^[0-9a-f]{64}$')
                                                        AND (result_fingerprint IS NULL OR result_fingerprint ~ '^[0-9a-f]{64}$')),
    CONSTRAINT price_discontinuity_scan_counts_chk CHECK ((n_symbols IS NULL OR n_symbols >= 0) AND (n_price_rows IS NULL OR n_price_rows >= 0)
                                                          AND (n_discontinuities IS NULL OR n_discontinuities >= 0)),
    CONSTRAINT price_discontinuity_scan_text_chk CHECK (length(run_id) > 0 AND length(writer) > 0 AND length(code_ref) > 0)
);
-- A retry that finds the same prices and the same result is the SAME evidence: one complete row per (session, input, result).
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_discontinuity_scan_complete
    ON price_discontinuity_scan (session_date, input_fingerprint, result_fingerprint) WHERE status = 'complete';
CREATE INDEX IF NOT EXISTS idx_price_discontinuity_scan_session ON price_discontinuity_scan (session_date, finished_at);
COMMENT ON TABLE price_discontinuity_scan IS 'discontinuity_scan_migration_32';

DO $mk$
BEGIN
    IF to_regprocedure('research_discontinuity_scan_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_discontinuity_scan_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (scan evidence is immutable; a retry or a correction is a new scan)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_discontinuity_scan_guard() IS 'discontinuity_scan_migration_32';
        REVOKE ALL ON FUNCTION research_discontinuity_scan_guard() FROM PUBLIC;
    END IF;

    -- The fingerprint of the price INPUT of a scan for `p_session`: every stock_prices row dated <= p_session. Order-independent (a sum of 64-bit row hashes) so
    -- it needs no sort of the whole table; detection-relevant columns only (symbol, date, open, high, low, close). It detects an accidental change, a vendor
    -- correction, a partial load or a missing bar; it is not a defence against an adversary who can craft collisions (the writer is the runtime role).
    IF to_regprocedure('research_price_input_fingerprint(date)') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_price_input_fingerprint(p_session DATE)
RETURNS TABLE (newest_bar DATE, n_symbols INTEGER, n_price_rows BIGINT, fingerprint CHAR(64)) LANGUAGE sql STABLE AS $fn$
    WITH s AS (
        SELECT symbol, date,
               hashtextextended(symbol || '|' || date::text || '|' || coalesce(open::text, '~') || '|' || coalesce(high::text, '~') || '|' ||
                                coalesce(low::text, '~') || '|' || coalesce(close::text, '~'), 0) AS h
        FROM stock_prices WHERE date <= p_session)
    SELECT max(date), count(DISTINCT symbol)::integer, count(*)::bigint,
           encode(sha256(convert_to('price_input_v1;' || count(*)::text || ';' || count(DISTINCT symbol)::text || ';' ||
                                    coalesce(max(date)::text, '-') || ';' || coalesce(sum(h::numeric), 0)::text, 'UTF8')), 'hex')::char(64)
    FROM s
$fn$ $f$;
        COMMENT ON FUNCTION research_price_input_fingerprint(date) IS 'discontinuity_scan_migration_32';
        REVOKE ALL ON FUNCTION research_price_input_fingerprint(date) FROM PUBLIC;
    END IF;

    -- The fingerprint of what the scan PRODUCED: the key set of price_discontinuities with each row's first-detected stamp.
    IF to_regprocedure('research_discontinuity_result_fingerprint()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_discontinuity_result_fingerprint()
RETURNS TABLE (n_rows INTEGER, fingerprint CHAR(64)) LANGUAGE sql STABLE AS $fn$
    SELECT count(*)::integer,
           encode(sha256(convert_to('disc_result_v1;' || count(*)::text || ';' ||
                                    coalesce(string_agg(symbol || '|' || date::text || '|' || kind || '|' || detected_at::text, ',' ORDER BY symbol, date, kind), ''),
                                    'UTF8')), 'hex')::char(64)
    FROM price_discontinuities
$fn$ $f$;
        COMMENT ON FUNCTION research_discontinuity_result_fingerprint() IS 'discontinuity_scan_migration_32';
        REVOKE ALL ON FUNCTION research_discontinuity_result_fingerprint() FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_discontinuity_scan_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_discontinuity_scan_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    fp         RECORD;
    res        RECORD;
    newest_all DATE;
BEGIN
    -- serialise scans of the same session; held until the writer's transaction ends
    PERFORM pg_advisory_xact_lock(hashtextextended('discontinuity_scan|' || coalesce(NEW.session_date::text, ''), 0));
    -- the completion time is the database's, never the caller's
    NEW.finished_at := clock_timestamp();
    IF NEW.started_at > NEW.finished_at THEN
        RAISE EXCEPTION 'price_discontinuity_scan refused: started_at % is after the database clock', NEW.started_at USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF NEW.status = 'complete' THEN
        SELECT * INTO fp FROM research_price_input_fingerprint(NEW.session_date);
        SELECT max(date) INTO newest_all FROM stock_prices;
        IF fp.newest_bar IS DISTINCT FROM NEW.session_date OR newest_all IS DISTINCT FROM NEW.session_date THEN
            RAISE EXCEPTION 'price_discontinuity_scan refused: the newest stored price bar is %, not the scan session % (the session was not loaded, or a later bar is mixed in: the detector reads the whole table)',
                newest_all, NEW.session_date USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.newest_bar IS DISTINCT FROM fp.newest_bar OR NEW.n_symbols IS DISTINCT FROM fp.n_symbols
           OR NEW.n_price_rows IS DISTINCT FROM fp.n_price_rows OR NEW.input_fingerprint IS DISTINCT FROM fp.fingerprint THEN
            RAISE EXCEPTION 'price_discontinuity_scan refused: the claimed price input does not match stored prices (they changed while the scan ran, or the claim is wrong)'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        SELECT * INTO res FROM research_discontinuity_result_fingerprint();
        IF NEW.n_discontinuities IS DISTINCT FROM res.n_rows OR NEW.result_fingerprint IS DISTINCT FROM res.fingerprint THEN
            RAISE EXCEPTION 'price_discontinuity_scan refused: the claimed result does not match price_discontinuities as stored'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_discontinuity_scan_stamp() IS 'discontinuity_scan_migration_32';
        REVOKE ALL ON FUNCTION research_discontinuity_scan_stamp() FROM PUBLIC;
    END IF;
END;
$mk$;

DO $trg$
DECLARE
    r        RECORD;
    cur_mode "char";
    cur_fn   NAME;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('price_discontinuity_scan', 'price_discontinuity_scan_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_discontinuity_scan_guard'),
        ('price_discontinuity_scan', 'price_discontinuity_scan_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_discontinuity_scan_guard'),
        ('price_discontinuity_scan', 'price_discontinuity_scan_stamp',              'BEFORE INSERT',           'ROW',       'research_discontinuity_scan_stamp')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 32 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
