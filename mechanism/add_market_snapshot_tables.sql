-- mechanism/add_market_snapshot_tables.sql   (migration 24 -- Market Intelligence session-level snapshots)
--
-- Strategy-neutral, immutable, session-level market context:
--   universe_snapshot  what the universe and its sector map were for a session (one row per session x provenance)
--   market_snapshot    risk_regime_v1 (every raw + normalised component) and the broad-market / universe-median returns
--   sector_snapshot    rs_v1 per sector: constituent counts, equal-weight median returns, comparisons, rank
-- These are NOT copied into candidate_observation / feature_snapshot (D1): a candidate observation references the session by date.
-- Nothing here is a Donchian eligibility rule or a screener / ML input. Design: docs/architecture/MARKET_INTELLIGENCE.md.
--
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_market_snapshot_tables.sql
-- (one transaction; any failure leaves nothing behind). NOT applied to production by committing this file. Independent of 22/23
-- except for requiring nothing from them: this migration carries its own immutability guard.
--
-- Provenance (D3). Every row says whether it was OBSERVED (written by the live pipeline for that session, from data held then) or
-- RECONSTRUCTED (computed later from stored history). `provenance` is NOT NULL with a CHECK; it is part of every UNIQUE key, so a
-- reconstructed row can never collide with, overwrite or be mistaken for an observed one. A reconstructed row must say how it was
-- reconstructed (CHECK). Readers default to observed-only (mechanism/market_intelligence/provenance.py).
--
-- Immutability. Append-only, two layers: (1) privileges (deploy/db/research_roles.sql: runtime role INSERT/SELECT only);
-- (2) ENABLE ALWAYS BEFORE UPDATE/DELETE row triggers and BEFORE TRUNCATE statement triggers that ALWAYS raise. There is no maintenance
-- hatch: a correction is a new feature_set_version (a new row), never an edit. As in migration 22, a superuser or the owner can still
-- disable a trigger; the security boundary is the least-privilege runtime role.
--
-- Collision safety. Every object carries the marker comment 'market_intelligence_migration_24'. Section 0 refuses (changes nothing) if
-- an object with one of our names exists WITHOUT the marker or only some of the tables exist. "Missing is not zero": a measurement
-- that was not computed is NULL, never 0.

-- ---------------------------------------------------------------------------------------------------
-- 0. Collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'market_intelligence_migration_24';
    n_present INTEGER := 0;
    t TEXT;
    ix RECORD;
BEGIN
    FOREACH t IN ARRAY ARRAY['universe_snapshot', 'market_snapshot', 'sector_snapshot'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            n_present := n_present + 1;
            IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
                RAISE EXCEPTION 'migration 24 refused: relation % already exists and is not a migration-24 object (no marker)', t;
            END IF;
        END IF;
    END LOOP;
    IF n_present NOT IN (0, 3) THEN
        RAISE EXCEPTION 'migration 24 refused: % of 3 market snapshot tables exist (partially applied); investigate, do not re-run', n_present;
    END IF;
    IF to_regprocedure('research_market_guard()') IS NOT NULL
       AND coalesce(obj_description(to_regprocedure('research_market_guard()'), 'pg_proc'), '') <> marker THEN
        RAISE EXCEPTION 'migration 24 refused: function research_market_guard() already exists and is not a migration-24 object (no marker)';
    END IF;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_market_snapshot_session', 'market_snapshot'),
            ('idx_sector_snapshot_session', 'sector_snapshot'),
            ('idx_sector_snapshot_market', 'sector_snapshot')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 24 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. universe_snapshot: the universe and the sector map used for a session.
--    sector_map is {symbol: sector or null}. `sector_pit_safe` is TRUE only for an observed row: the source (daily_fundamentals.sector)
--    is rewritten in place, so a reconstructed map is today's metadata projected backwards and must never claim to be as-of.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS universe_snapshot (
    id                   BIGSERIAL    PRIMARY KEY,
    session_date         DATE         NOT NULL,
    provenance           VARCHAR(13)  NOT NULL,
    n_symbols            INTEGER      NOT NULL,
    n_classified         INTEGER      NOT NULL,
    sector_map           JSONB        NOT NULL,
    sector_source        VARCHAR(60)  NOT NULL,
    sector_asof_rule     TEXT         NOT NULL,
    sector_pit_safe      BOOLEAN      NOT NULL,
    content_hash         CHAR(64)     NOT NULL,
    code_ref             VARCHAR(160) NOT NULL,
    reconstruction_basis TEXT,
    captured_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT universe_snapshot_provenance_chk CHECK (provenance IN ('observed', 'reconstructed')),
    CONSTRAINT universe_snapshot_counts_chk CHECK (n_symbols >= 0 AND n_classified >= 0 AND n_classified <= n_symbols),
    CONSTRAINT universe_snapshot_reconstruction_chk CHECK ((provenance = 'reconstructed') = (reconstruction_basis IS NOT NULL)),
    CONSTRAINT universe_snapshot_pit_chk CHECK (NOT sector_pit_safe OR provenance = 'observed'),
    CONSTRAINT universe_snapshot_key UNIQUE (session_date, provenance),
    CONSTRAINT universe_snapshot_ref_key UNIQUE (id, session_date, provenance)
);

-- ---------------------------------------------------------------------------------------------------
-- 2. market_snapshot: risk_regime_v1 + broad-market and universe-median returns. One row per
--    (session, feature set, provenance). feature_set_version names the combined definition ('mi_v1' =
--    risk_regime_v1 + rs_v1); the model versions are stored on the row as well.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_snapshot (
    id                      BIGSERIAL    PRIMARY KEY,
    session_date            DATE         NOT NULL,
    provenance              VARCHAR(13)  NOT NULL,
    feature_set_version     VARCHAR(30)  NOT NULL,
    regime_model_version    VARCHAR(30)  NOT NULL,
    rs_model_version        VARCHAR(30)  NOT NULL,
    regime_state            VARCHAR(12)  NOT NULL,
    regime_score            NUMERIC(7,4),
    regime_strength         NUMERIC(7,4),
    regime_strength_label   VARCHAR(10),
    regime_agreement        NUMERIC(7,4),
    regime_present_weight   NUMERIC(7,4) NOT NULL,
    regime_components       JSONB        NOT NULL,          -- raw + normalised + availability per component
    regime_reasons          JSONB        NOT NULL DEFAULT '[]'::jsonb,
    spx_ret_5               DOUBLE PRECISION,               -- ^GSPC percentage return over h sessions
    spx_ret_20              DOUBLE PRECISION,
    spx_ret_60              DOUBLE PRECISION,
    univ_ret_5              DOUBLE PRECISION,               -- equal-weight MEDIAN of all valid stock returns
    univ_ret_20             DOUBLE PRECISION,
    univ_ret_60             DOUBLE PRECISION,
    univ_n_valid_5          INTEGER,
    univ_n_valid_20         INTEGER,
    univ_n_valid_60         INTEGER,
    coverage                JSONB        NOT NULL,
    source                  VARCHAR(120) NOT NULL,
    universe_snapshot_id    BIGINT       NOT NULL,
    content_hash            CHAR(64)     NOT NULL,
    code_ref                VARCHAR(160) NOT NULL,
    reconstruction_basis    TEXT,
    captured_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT market_snapshot_provenance_chk CHECK (provenance IN ('observed', 'reconstructed')),
    CONSTRAINT market_snapshot_reconstruction_chk CHECK ((provenance = 'reconstructed') = (reconstruction_basis IS NOT NULL)),
    CONSTRAINT market_snapshot_state_chk CHECK (regime_state IN ('RISK_ON', 'RISK_OFF', 'NEUTRAL', 'UNAVAILABLE')),
    CONSTRAINT market_snapshot_unavailable_chk CHECK ((regime_state = 'UNAVAILABLE') = (regime_score IS NULL)),
    CONSTRAINT market_snapshot_score_range_chk CHECK (regime_score IS NULL OR regime_score BETWEEN -1 AND 1),
    CONSTRAINT market_snapshot_strength_chk CHECK (regime_state <> 'UNAVAILABLE' OR (regime_strength IS NULL AND regime_strength_label IS NULL
                                                                                     AND regime_agreement IS NULL)),
    CONSTRAINT market_snapshot_weight_chk CHECK (regime_present_weight BETWEEN 0 AND 1),
    CONSTRAINT market_snapshot_key UNIQUE (session_date, feature_set_version, provenance),
    CONSTRAINT market_snapshot_ref_key UNIQUE (id, provenance),
    CONSTRAINT market_snapshot_universe_fk FOREIGN KEY (universe_snapshot_id, session_date, provenance)
        REFERENCES universe_snapshot (id, session_date, provenance)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_session ON market_snapshot (session_date DESC, provenance);

-- ---------------------------------------------------------------------------------------------------
-- 3. sector_snapshot: rs_v1 per sector. A sector with fewer than 5 valid member returns for a horizon keeps its counts and has NULL
--    return / comparisons (and no rank for horizon 20): "not enough members" is stored as NULL, never as 0.
--    sec_vs_spx compares an equal-weight median with a cap-weighted index; sec_vs_univ is the like-for-like benchmark. Kept separate.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sector_snapshot (
    id                    BIGSERIAL    PRIMARY KEY,
    session_date          DATE         NOT NULL,
    provenance            VARCHAR(13)  NOT NULL,
    feature_set_version   VARCHAR(30)  NOT NULL,
    sector                VARCHAR(80)  NOT NULL,
    n_members             INTEGER      NOT NULL,
    n_valid_5             INTEGER      NOT NULL,
    n_valid_20            INTEGER      NOT NULL,
    n_valid_60            INTEGER      NOT NULL,
    n_excluded_5          INTEGER      NOT NULL,
    n_excluded_20         INTEGER      NOT NULL,
    n_excluded_60         INTEGER      NOT NULL,
    sec_ret_5             DOUBLE PRECISION,
    sec_ret_20            DOUBLE PRECISION,
    sec_ret_60            DOUBLE PRECISION,
    sec_vs_spx_5          DOUBLE PRECISION,
    sec_vs_spx_20         DOUBLE PRECISION,
    sec_vs_spx_60         DOUBLE PRECISION,
    sec_vs_univ_5         DOUBLE PRECISION,
    sec_vs_univ_20        DOUBLE PRECISION,
    sec_vs_univ_60        DOUBLE PRECISION,
    rank_20               INTEGER,
    market_snapshot_id    BIGINT       NOT NULL,
    reconstruction_basis  TEXT,
    captured_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT sector_snapshot_provenance_chk CHECK (provenance IN ('observed', 'reconstructed')),
    CONSTRAINT sector_snapshot_reconstruction_chk CHECK ((provenance = 'reconstructed') = (reconstruction_basis IS NOT NULL)),
    CONSTRAINT sector_snapshot_members_chk CHECK (n_members >= 0 AND n_valid_5 BETWEEN 0 AND n_members
        AND n_valid_20 BETWEEN 0 AND n_members AND n_valid_60 BETWEEN 0 AND n_members),
    CONSTRAINT sector_snapshot_excluded_chk CHECK (n_excluded_5 = n_members - n_valid_5
        AND n_excluded_20 = n_members - n_valid_20 AND n_excluded_60 = n_members - n_valid_60),
    CONSTRAINT sector_snapshot_min_members_chk CHECK (
        (sec_ret_5  IS NULL OR n_valid_5  >= 5) AND (sec_ret_20 IS NULL OR n_valid_20 >= 5) AND (sec_ret_60 IS NULL OR n_valid_60 >= 5)),
    CONSTRAINT sector_snapshot_null_propagation_chk CHECK (
        (sec_ret_5  IS NOT NULL OR (sec_vs_spx_5  IS NULL AND sec_vs_univ_5  IS NULL))
        AND (sec_ret_20 IS NOT NULL OR (sec_vs_spx_20 IS NULL AND sec_vs_univ_20 IS NULL))
        AND (sec_ret_60 IS NOT NULL OR (sec_vs_spx_60 IS NULL AND sec_vs_univ_60 IS NULL))),
    CONSTRAINT sector_snapshot_rank_chk CHECK (rank_20 IS NULL OR (rank_20 >= 1 AND sec_ret_20 IS NOT NULL)),
    CONSTRAINT sector_snapshot_key UNIQUE (session_date, feature_set_version, provenance, sector),
    CONSTRAINT sector_snapshot_market_fk FOREIGN KEY (market_snapshot_id, provenance)
        REFERENCES market_snapshot (id, provenance)
);
CREATE INDEX IF NOT EXISTS idx_sector_snapshot_session ON sector_snapshot (session_date DESC, provenance);
CREATE INDEX IF NOT EXISTS idx_sector_snapshot_market ON sector_snapshot (market_snapshot_id);

-- ---------------------------------------------------------------------------------------------------
-- 4. Markers (collision guard on a re-run) and the always-raising guard function (created only when absent).
-- ---------------------------------------------------------------------------------------------------
COMMENT ON TABLE universe_snapshot IS 'market_intelligence_migration_24';
COMMENT ON TABLE market_snapshot   IS 'market_intelligence_migration_24';
COMMENT ON TABLE sector_snapshot   IS 'market_intelligence_migration_24';

DO $mk$
BEGIN
    IF to_regprocedure('research_market_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_market_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (Market Intelligence snapshots are immutable; a correction is a new feature_set_version)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_market_guard() IS 'market_intelligence_migration_24';
        REVOKE ALL ON FUNCTION research_market_guard() FROM PUBLIC;
    END IF;
END;
$mk$;

-- ---------------------------------------------------------------------------------------------------
-- 5. Triggers. Created if absent; an existing one must already be the right function and ENABLE ALWAYS, otherwise refuse.
-- ---------------------------------------------------------------------------------------------------
DO $trg$
DECLARE
    r RECORD;
    cur_mode "char";
    cur_fn   TEXT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('universe_snapshot', 'universe_snapshot_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW'),
        ('universe_snapshot', 'universe_snapshot_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT'),
        ('market_snapshot',   'market_snapshot_immutable_row',        'BEFORE UPDATE OR DELETE', 'ROW'),
        ('market_snapshot',   'market_snapshot_immutable_truncate',   'BEFORE TRUNCATE',         'STATEMENT'),
        ('sector_snapshot',   'sector_snapshot_immutable_row',        'BEFORE UPDATE OR DELETE', 'ROW'),
        ('sector_snapshot',   'sector_snapshot_immutable_truncate',   'BEFORE TRUNCATE',         'STATEMENT')
    ) AS v(tbl, trg, timing, lvl) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> 'research_market_guard' THEN
                RAISE EXCEPTION 'migration 24 refused: trigger % exists but is not (function research_market_guard, ENABLE ALWAYS)', r.trg;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION research_market_guard()',
                           r.trg, r.timing, r.tbl, r.lvl);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
