-- mechanism/add_stock_relative_strength_table.sql   (migration 29 -- per-stock relative strength, rs_v1 and successors)
--
-- One row per (session, symbol, horizon) of what the Market Intelligence relative-strength model measured for that stock:
-- the stock's return, its excess over the broad market and over its own sector median, and its universe percentile.
-- `rs_v1` computed these on every run (market_intelligence.relative_strength.compute -> .stocks) but never stored them; migration 24
-- stores only sector / market aggregates.
--
-- Typed, versioned, immutable, point-in-time:
--   * model_version names the definition (rs_v1 ...), feature_set_version the runner composition (mi_v2 ...). A different definition is a
--     NEW model_version and therefore NEW rows -- there is no UPDATE and no overwrite. A correction is a new version.
--   * provenance is part of the UNIQUE key, exactly as in migration 24: `observed` (computed by the live run for that session and
--     stamped by the database at that time) and `reconstructed` (recomputed later from stored history) coexist and never overwrite
--     each other. A reconstructed row must say how (reconstruction_basis). Research reads are observed-only by default.
--   * `sector_pit_safe` is TRUE only for an observed row whose sector map was point-in-time evidenced; it can never be TRUE on a
--     reconstructed row. A vs-sector measurement therefore always travels with the truth about its sector tag.
--   * AVAILABILITY of an observed row is created_at, stamped by the database (not the caller). Session end is NOT availability.
--   * "Missing is not zero": an unavailable measurement is NULL and state = 'unavailable'; the CHECKs make the two agree, and a
--     number that is NaN or infinite cannot be stored at all.
--
-- Independent of migrations 24 / 25 / 26 (requires none; the session-level snapshot is linked by session_date + feature_set_version +
-- provenance, not by a foreign key, so this table can be applied and read without 24).
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_stock_relative_strength_table.sql
-- NOT applied to production by committing this file; nothing writes to it unless the runner is invoked with --with-stock-rs.
-- Re-run deploy/db/research_roles.sql (+ verifier) afterwards: append-only for the runtime role.
--
-- Collision safety. Marker 'stock_relative_strength_migration_29'.

DO $guard$
DECLARE
    marker CONSTANT TEXT := 'stock_relative_strength_migration_29';
    f TEXT;
    ix RECORD;
BEGIN
    IF to_regclass('stock_relative_strength') IS NOT NULL
       AND coalesce(obj_description(to_regclass('stock_relative_strength'), 'pg_class'), '') <> marker THEN
        RAISE EXCEPTION 'migration 29 refused: relation stock_relative_strength already exists and is not a migration-29 object (no marker)';
    END IF;
    FOREACH f IN ARRAY ARRAY['research_rs_guard()', 'research_rs_stamp()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 29 refused: function % already exists and is not a migration-29 object (no marker)', f;
        END IF;
    END LOOP;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_stock_rs_symbol', 'stock_relative_strength'),
            ('idx_stock_rs_rank',   'stock_relative_strength')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 29 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

CREATE TABLE IF NOT EXISTS stock_relative_strength (
    id                      BIGSERIAL        PRIMARY KEY,
    session_date            DATE             NOT NULL,
    symbol                  VARCHAR(20)      NOT NULL,
    horizon_sessions        SMALLINT         NOT NULL,
    model_version           VARCHAR(20)      NOT NULL,          -- rs_v1 ...
    feature_set_version     VARCHAR(30)      NOT NULL,          -- mi_v2 ...
    provenance              VARCHAR(13)      NOT NULL,          -- observed | reconstructed
    reconstruction_basis    TEXT,
    sector                  VARCHAR(80),                        -- NULL = no sector (never an 'Unknown' pseudo-sector)
    sector_pit_safe         BOOLEAN          NOT NULL,
    state                   VARCHAR(11)      NOT NULL,          -- ok | unavailable
    ret_pct                 DOUBLE PRECISION,                   -- (C_t / C_{t-h} - 1) * 100
    vs_spx_pp               DOUBLE PRECISION,                   -- ret_pct - ^GSPC return over the same window (percentage points)
    vs_sector_pp            DOUBLE PRECISION,                   -- ret_pct - sector median return (pp); needs a sector with enough valid members
    rs_percentile           DOUBLE PRECISION,                   -- 0..100 within the valid universe; NULL when the universe is too small
    n_universe_valid        INTEGER          NOT NULL,          -- N the percentile was ranked against (valid stocks for this horizon)
    benchmark_symbol        VARCHAR(20)      NOT NULL,
    run_content_hash        CHAR(64)         NOT NULL,          -- hash of the whole session batch these rows belong to (reproducibility)
    code_ref                VARCHAR(160)     NOT NULL,
    created_at              TIMESTAMPTZ      NOT NULL,          -- DB-stamped; the availability time of an observed row
    CONSTRAINT stock_relative_strength_key UNIQUE (session_date, symbol, horizon_sessions, model_version, feature_set_version, provenance),
    CONSTRAINT stock_relative_strength_horizon_chk CHECK (horizon_sessions > 0),
    CONSTRAINT stock_relative_strength_prov_chk CHECK (provenance IN ('observed', 'reconstructed')),
    CONSTRAINT stock_relative_strength_recon_chk CHECK ((provenance = 'reconstructed') = (reconstruction_basis IS NOT NULL)),
    CONSTRAINT stock_relative_strength_pit_chk CHECK (NOT sector_pit_safe OR (provenance = 'observed' AND sector IS NOT NULL)),
    CONSTRAINT stock_relative_strength_state_chk CHECK (state IN ('ok', 'unavailable')),
    CONSTRAINT stock_relative_strength_state_value_chk CHECK ((state = 'ok') = (ret_pct IS NOT NULL)),
    CONSTRAINT stock_relative_strength_dependents_chk CHECK (
        ret_pct IS NOT NULL OR (vs_spx_pp IS NULL AND vs_sector_pp IS NULL AND rs_percentile IS NULL)),
    CONSTRAINT stock_relative_strength_sector_chk CHECK (vs_sector_pp IS NULL OR sector IS NOT NULL),
    CONSTRAINT stock_relative_strength_percentile_chk CHECK (rs_percentile IS NULL OR rs_percentile BETWEEN 0 AND 100),
    -- range checks double as NaN / infinity refusals (in PostgreSQL NaN sorts above every number)
    CONSTRAINT stock_relative_strength_finite_chk CHECK (
        (ret_pct IS NULL OR (ret_pct > -1e9 AND ret_pct < 1e9))
        AND (vs_spx_pp IS NULL OR (vs_spx_pp > -1e9 AND vs_spx_pp < 1e9))
        AND (vs_sector_pp IS NULL OR (vs_sector_pp > -1e9 AND vs_sector_pp < 1e9))),
    CONSTRAINT stock_relative_strength_n_chk CHECK (n_universe_valid >= 0),
    CONSTRAINT stock_relative_strength_hash_chk CHECK (run_content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT stock_relative_strength_text_chk CHECK (length(symbol) > 0 AND length(model_version) > 0 AND length(feature_set_version) > 0)
);
CREATE INDEX IF NOT EXISTS idx_stock_rs_symbol ON stock_relative_strength (symbol, horizon_sessions, session_date);
CREATE INDEX IF NOT EXISTS idx_stock_rs_rank   ON stock_relative_strength (session_date, horizon_sessions, model_version, provenance, rs_percentile);

COMMENT ON TABLE stock_relative_strength IS 'stock_relative_strength_migration_29';

DO $mk$
BEGIN
    IF to_regprocedure('research_rs_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_rs_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (relative-strength rows are immutable; a correction is a new model_version)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_rs_guard() IS 'stock_relative_strength_migration_29';
        REVOKE ALL ON FUNCTION research_rs_guard() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_rs_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_rs_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_rs_stamp() IS 'stock_relative_strength_migration_29';
        REVOKE ALL ON FUNCTION research_rs_stamp() FROM PUBLIC;
    END IF;
END;
$mk$;

DO $trg$
DECLARE
    r RECORD;
    cur_mode "char";
    cur_fn   TEXT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('stock_relative_strength_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_rs_guard'),
        ('stock_relative_strength_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_rs_guard'),
        ('stock_relative_strength_stamp',              'BEFORE INSERT',           'ROW',       'research_rs_stamp')
    ) AS v(trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass('stock_relative_strength') AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 29 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON stock_relative_strength FOR EACH %s EXECUTE FUNCTION %I()',
                           r.trg, r.timing, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE stock_relative_strength ENABLE ALWAYS TRIGGER %I', r.trg);
        END IF;
    END LOOP;
END;
$trg$;
