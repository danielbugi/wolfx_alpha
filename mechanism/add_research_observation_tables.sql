-- mechanism/add_research_observation_tables.sql   (migration 22 -- Release B / B1)
--
-- The immutable research observation dataset's foundation: what the strategy SAW (candidate_observation),
-- the T0 market state at that moment (feature_snapshot), the registry that says what a feature set is
-- (feature_set_registry), a per-run observability record (candidate_capture_run), and the audited
-- maintenance path that is the ONLY way a captured row may ever be corrected (research_maintenance_log /
-- research_maintenance_audit). Design: agent_reports/architecture/2026-10-01_quant-lab-release-b-design.md
-- (Revision 2, section 1A). Release A (migrations 19/20/21) supplies `strategies` and `signal_ledger`.
--
-- Additive / forward-safe only: every statement is IF NOT EXISTS or an existence-checked DO block, so
-- applying this twice is a no-op. No existing table is altered and no row is written: there are no seed
-- rows (the registry row for t0_v1 is written by the application at first use, after it proves the code's
-- manifest hash).
--
-- NOT applied to production by committing this file. It is applied manually on the VPS and verified with
-- a real query afterwards (docs/architecture/DATABASE.md, docs/operations/DEPLOYMENT.md).
--
-- Rules the schema itself enforces (not just the application):
--   * A candidate/snapshot is keyed by an EXPLICIT session: bar_date = session_date (CHECK).
--   * First valid write wins: the unique keys below + INSERT ... ON CONFLICT DO NOTHING in the application.
--   * Captured rows are immutable: BEFORE UPDATE/DELETE row triggers and a BEFORE TRUNCATE statement
--     trigger raise unless an approved, open, recent research_maintenance_log ticket is named by the
--     transaction-local setting research.maintenance_ticket; a permitted change is audited with the full
--     old row. A Postgres superuser / table owner can still ALTER TABLE ... DISABLE TRIGGER: this prevents
--     accidents and leaves an audit trail, it is not a defence against a compromised database admin.
--   * "Missing is not zero": measured values that were not computed are NULL (or JSON null + a name in
--     missing_features); nothing defaults to 0 / TRUE / 'pass'.

-- ---------------------------------------------------------------------------------------------------
-- 1. Feature-set registry: what `t0_v1` IS. Append-only (immutability trigger below).
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feature_set_registry (
    feature_set_version  VARCHAR(30)  PRIMARY KEY,
    manifest             JSONB        NOT NULL,        -- ordered: name, type, unit, source, missing semantics
    manifest_hash        CHAR(64)     NOT NULL,        -- sha256 of the canonical manifest JSON
    impl_ref             VARCHAR(160) NOT NULL,        -- module + implementation fingerprint at registration
    extends              VARCHAR(30)  REFERENCES feature_set_registry (feature_set_version),
    description          TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------------------------------
-- 2. Per-run observability: the denominator for "was this session fully captured?".
--    An operational log, not research data -- deliberately NOT immutable (counters are finalised at the
--    end of the run). One row per capture attempt; a retried session produces another row.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidate_capture_run (
    id                   BIGSERIAL    PRIMARY KEY,
    strategy_id          BIGINT       NOT NULL REFERENCES strategies (id),
    strategy_version     VARCHAR(20)  NOT NULL,
    session_date         DATE         NOT NULL,
    feature_set_version  VARCHAR(30)  NOT NULL REFERENCES feature_set_registry (feature_set_version),
    session_source       VARCHAR(20)  NOT NULL CHECK (session_source = 'explicit'),
    status               VARCHAR(12)  NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'complete', 'failed')),
    run_started_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    run_finished_at      TIMESTAMPTZ,
    universe_size        INTEGER,                      -- NULL when the caller did not provide it
    candidates           INTEGER,                      -- what the screener produced (before any skip)
    captured             INTEGER,                      -- observations newly written by this run
    already_captured     INTEGER,                      -- first-valid-write-wins no-ops (same identity existed)
    hash_drift           INTEGER,                      -- ... of which the re-run payload differed (NOT applied)
    guard_rejected       INTEGER,                      -- subset of captured+already_captured: guard said no
    guard_not_evaluated  INTEGER,                      -- subset: guards unavailable (passed_guard NULL)
    stale_skipped        INTEGER,                      -- candidate whose own bar is not the session
    snapshot_skipped     INTEGER,                      -- no usable T0 snapshot could be built
    snapshot_drift       INTEGER,                      -- existing snapshot whose recomputed content differed (NOT applied)
    invalid_skipped      INTEGER,                      -- unreadable / out-of-contract candidate payload
    defaulted_flagged    INTEGER,                      -- subset of captured: screener filled a missing input
    skipped_symbols      JSONB,                        -- {"SYM": "reason", ...}
    error                TEXT,
    code_ref             VARCHAR(160)
);

CREATE INDEX IF NOT EXISTS idx_candidate_capture_run_session
    ON candidate_capture_run (strategy_id, session_date, run_started_at DESC);

-- ---------------------------------------------------------------------------------------------------
-- 3. Maintenance ticket + audit (the hatch for the immutability triggers, section 6 below).
--    The application never writes these tables and never sets research.maintenance_ticket.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_maintenance_log (
    id            BIGSERIAL    PRIMARY KEY,
    opened_by     VARCHAR(80)  NOT NULL,
    reason        TEXT         NOT NULL,
    target_table  VARCHAR(60)  NOT NULL,
    opened_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    approved_by   VARCHAR(80),
    approved_at   TIMESTAMPTZ,
    closed_at     TIMESTAMPTZ,
    CHECK (approved_by IS NULL OR approved_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS research_maintenance_audit (
    id             BIGSERIAL    PRIMARY KEY,
    ticket_id      BIGINT       NOT NULL REFERENCES research_maintenance_log (id),
    table_name     VARCHAR(60)  NOT NULL,
    operation      VARCHAR(10)  NOT NULL,
    old_row        JSONB,                               -- the full row as it was (summary for TRUNCATE)
    new_row        JSONB,
    performed_by   VARCHAR(80)  NOT NULL DEFAULT session_user,
    performed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------------------------------
-- 4. T0 feature snapshot: direction-neutral, strategy-neutral market state for (symbol, session, version).
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feature_snapshot (
    id                   BIGSERIAL     PRIMARY KEY,
    symbol               VARCHAR(10)   NOT NULL,
    session_date         DATE          NOT NULL,
    feature_set_version  VARCHAR(30)   NOT NULL REFERENCES feature_set_registry (feature_set_version),
    bar_date             DATE          NOT NULL,       -- the symbol's own T0 bar, read from stock_prices
    open                 NUMERIC(14,4) NOT NULL,       -- raw T0 bar, as known at capture; never re-adjusted
    high                 NUMERIC(14,4) NOT NULL,
    low                  NUMERIC(14,4) NOT NULL,
    close                NUMERIC(14,4) NOT NULL,
    volume               BIGINT,                       -- NULL when the vendor bar had none (also in missing_features)
    prev_close           NUMERIC(14,4),                -- NULL when there is only one bar
    bars_available       INTEGER       NOT NULL,
    snapshot_status      VARCHAR(10)   NOT NULL CHECK (snapshot_status IN ('complete', 'partial')),
    features             JSONB         NOT NULL,       -- manifest name -> number | boolean | null (NaN/inf -> null)
    missing_features     TEXT[]        NOT NULL DEFAULT '{}',
    sector               VARCHAR(60),                  -- NULL when unknown -- never 'Unknown'
    sector_source        VARCHAR(40),
    sector_asof          DATE,
    manifest_hash        CHAR(64)      NOT NULL,       -- the registry hash this row was built under
    content_hash         CHAR(64)      NOT NULL,       -- sha256 of the captured payload: same-key re-capture drift detector
    code_ref             VARCHAR(160),
    captured_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT feature_snapshot_identity_key UNIQUE (symbol, session_date, feature_set_version),
    CONSTRAINT feature_snapshot_bar_is_session CHECK (bar_date = session_date),
    CONSTRAINT feature_snapshot_partial_names_missing
        CHECK ((snapshot_status = 'complete') = (cardinality(missing_features) = 0))
);

CREATE INDEX IF NOT EXISTS idx_feature_snapshot_session
    ON feature_snapshot (session_date, feature_set_version);

-- ---------------------------------------------------------------------------------------------------
-- 5. Candidate observation: one immutable row per strategy decision.
--    Written for EVERY candidate the strategy's own SQL produces (breakouts and near-breakouts) that has a
--    session-correct bar, BEFORE the guards drop anything -- the rejected candidates are the control group.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidate_observation (
    id                        BIGSERIAL     PRIMARY KEY,
    strategy_id               BIGINT        NOT NULL REFERENCES strategies (id),
    strategy_version          VARCHAR(20)   NOT NULL,
    symbol                    VARCHAR(10)   NOT NULL,
    session_date              DATE          NOT NULL,
    direction                 SMALLINT      NOT NULL CHECK (direction IN (1, -1)),
    bar_date                  DATE          NOT NULL,
    session_source            VARCHAR(20)   NOT NULL CHECK (session_source = 'explicit'),
    signal_type               VARCHAR(30)   NOT NULL,   -- the strategy's raw class: bullish_breakout, near_bearish, ...
    triggered                 BOOLEAN       NOT NULL,   -- breakout (TRUE) vs near-breakout (FALSE)
    entry_close               NUMERIC(14,4) NOT NULL,   -- raw T0 close
    channel_high_prev         NUMERIC(14,4) NOT NULL,   -- 20-bar channel as it stood at T0-1
    channel_low_prev          NUMERIC(14,4) NOT NULL,
    breakout_dist_atr         NUMERIC(14,6),            -- signed, direction-adjusted; NULL when ATR is unavailable
    distance_to_channel_pct   NUMERIC(10,4) NOT NULL,   -- the distance the screener's <=3% rule tested
    -- funnel
    passed_guard              BOOLEAN,                  -- NULL = guards unavailable / not evaluated (never TRUE by default)
    guard_reasons             TEXT[],                   -- NULL unless passed_guard IS FALSE
    alignment_score           NUMERIC(8,3),
    quality_grade             VARCHAR(4),               -- the screener's alignment grade (A..F)
    combined_score            NUMERIC(8,3),
    session_rank              INTEGER,
    ml_status                 VARCHAR(20)   NOT NULL CHECK (ml_status IN ('scored', 'not_processed')),
    ml_score                  NUMERIC(10,6),            -- NULL unless ml_status = 'scored' (never 0)
    ml_confidence             VARCHAR(20),
    ml_model_version          VARCHAR(80),
    tracked_intent            BOOLEAN       NOT NULL,   -- would the ledger writer take this row (shared predicate)
    screener_defaults         TEXT[]        NOT NULL DEFAULT '{}',   -- inputs the screener had to fill in
    strategy_context          JSONB,                    -- curated raw strategy outputs (see mechanism/research/observer.py)
    snapshot_id               BIGINT        NOT NULL REFERENCES feature_snapshot (id),
    capture_run_id            BIGINT        NOT NULL REFERENCES candidate_capture_run (id),
    capture_hash              CHAR(64)      NOT NULL,   -- sha256 of the captured payload: same-session re-run drift detector
    code_ref                  VARCHAR(160),
    captured_at               TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT candidate_observation_identity_key UNIQUE (strategy_id, symbol, session_date, direction),
    CONSTRAINT candidate_observation_bar_is_session CHECK (bar_date = session_date),
    CONSTRAINT candidate_observation_guard_reasons_only_on_reject
        CHECK ((passed_guard IS FALSE) = (guard_reasons IS NOT NULL AND cardinality(guard_reasons) > 0)),
    CONSTRAINT candidate_observation_ml_score_only_when_scored
        CHECK (ml_status = 'scored' OR ml_score IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_candidate_observation_session
    ON candidate_observation (session_date, strategy_id);
CREATE INDEX IF NOT EXISTS idx_candidate_observation_tracked
    ON candidate_observation (session_date) WHERE tracked_intent;
CREATE INDEX IF NOT EXISTS idx_candidate_observation_snapshot
    ON candidate_observation (snapshot_id);

-- ---------------------------------------------------------------------------------------------------
-- 6. Immutability triggers.
-- ---------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION research_guard_immutable() RETURNS trigger AS $fn$
DECLARE
    ticket_text TEXT;
    ticket_id   BIGINT;
    row_count   BIGINT;
BEGIN
    ticket_text := current_setting('research.maintenance_ticket', true);
    IF ticket_text IS NOT NULL AND ticket_text <> '' THEN
        BEGIN
            ticket_id := ticket_text::BIGINT;
        EXCEPTION WHEN others THEN
            ticket_id := NULL;
        END;
    END IF;

    IF ticket_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM research_maintenance_log
        WHERE id = ticket_id
          AND approved_by IS NOT NULL
          AND closed_at IS NULL
          AND opened_at > NOW() - INTERVAL '60 minutes'
    ) THEN
        IF TG_OP = 'TRUNCATE' THEN
            EXECUTE 'SELECT count(*) FROM ' || TG_RELID::regclass::text INTO row_count;
            INSERT INTO research_maintenance_audit (ticket_id, table_name, operation, old_row)
            VALUES (ticket_id, TG_TABLE_NAME, TG_OP, jsonb_build_object('rows_removed', row_count));
            RETURN NULL;
        ELSIF TG_OP = 'DELETE' THEN
            INSERT INTO research_maintenance_audit (ticket_id, table_name, operation, old_row)
            VALUES (ticket_id, TG_TABLE_NAME, TG_OP, to_jsonb(OLD));
            RETURN OLD;
        ELSE
            INSERT INTO research_maintenance_audit (ticket_id, table_name, operation, old_row, new_row)
            VALUES (ticket_id, TG_TABLE_NAME, TG_OP, to_jsonb(OLD), to_jsonb(NEW));
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION 'research table % is immutable: % rejected (no approved, open, recent research_maintenance_log ticket named by research.maintenance_ticket)',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ LANGUAGE plpgsql;

-- The audit trail itself is append-only, with no hatch at all.
CREATE OR REPLACE FUNCTION research_audit_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'research_maintenance_audit is append-only: % rejected', TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ LANGUAGE plpgsql;

DO $do$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['candidate_observation', 'feature_snapshot', 'feature_set_registry'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = t || '_immutable_row'
                       AND tgrelid = to_regclass(t)) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW '
                           'EXECUTE FUNCTION research_guard_immutable()', t || '_immutable_row', t);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = t || '_immutable_truncate'
                       AND tgrelid = to_regclass(t)) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT '
                           'EXECUTE FUNCTION research_guard_immutable()', t || '_immutable_truncate', t);
        END IF;
    END LOOP;

    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'research_maintenance_audit_append_only_row'
                   AND tgrelid = to_regclass('research_maintenance_audit')) THEN
        CREATE TRIGGER research_maintenance_audit_append_only_row
            BEFORE UPDATE OR DELETE ON research_maintenance_audit
            FOR EACH ROW EXECUTE FUNCTION research_audit_append_only();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'research_maintenance_audit_append_only_truncate'
                   AND tgrelid = to_regclass('research_maintenance_audit')) THEN
        CREATE TRIGGER research_maintenance_audit_append_only_truncate
            BEFORE TRUNCATE ON research_maintenance_audit
            FOR EACH STATEMENT EXECUTE FUNCTION research_audit_append_only();
    END IF;
END;
$do$;
