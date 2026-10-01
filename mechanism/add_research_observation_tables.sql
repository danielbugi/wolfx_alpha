-- mechanism/add_research_observation_tables.sql   (migration 22 -- Release B / B1, hardened revision)
--
-- The immutable research observation dataset's foundation: what the strategy SAW (candidate_observation),
-- the T0 market state at that moment (feature_snapshot), the registry that says what a feature set is
-- (feature_set_registry), a per-run observability record (candidate_capture_run), the research-capture
-- activation boundary (research_capture_activation), and the audited maintenance path that is the ONLY way a
-- captured row may ever be corrected (research_maintenance_log / _session / _audit + four SECURITY DEFINER
-- functions). Design: agent_reports/architecture/2026-10-01_quant-lab-release-b-design.md (Revision 2, 1A) and
-- docs/operations/RESEARCH_MIGRATION_22.md (apply procedure) / docs/operations/RESEARCH_DB_ROLES.md (roles).
-- Release A (migrations 19/20/21) supplies `strategies` and `signal_ledger`.
--
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_research_observation_tables.sql
-- (one transaction; any failure leaves nothing behind). NOT applied to production by committing this file.
--
-- Collision safety. Every object this migration creates carries the marker comment
-- 'release_b_migration_22'. Section 0 refuses (RAISE EXCEPTION, nothing is changed) if any object with one of
-- our names already exists WITHOUT that marker, or if only some of the tables exist: `IF NOT EXISTS` never
-- blesses an unknown or half-applied object. On a clean pre-22 database the migration creates everything; on a
-- fully-applied one it creates nothing and replaces nothing (functions are created only when absent). Whether an
-- already-existing set is EXACTLY the intended one is decided by mechanism/check_research_migration_preflight.py
-- (catalog fingerprint vs schema_fingerprint_22.json), never by this file.
--
-- Immutability. Captured rows are protected by two independent layers:
--   (1) PRIVILEGES: the runtime role has INSERT/SELECT only on the immutable tables (deploy/db/research_roles.sql);
--   (2) TRIGGERS: BEFORE UPDATE/DELETE row triggers + BEFORE TRUNCATE statement triggers, created ENABLE ALWAYS
--       (they also fire when session_replication_role = replica), raise unless the transaction holds an approved,
--       unexpired, unclosed, target-matching maintenance ticket that was opened by research_maintenance_begin()
--       IN THIS TRANSACTION. The authorisation is a row in research_maintenance_session keyed by txid_current()
--       -- no GUC is trusted, so it cannot be forged by SET / set_config.
-- What this does NOT stop: a superuser or the table owner can ALTER TABLE ... DISABLE TRIGGER, DROP the trigger,
-- or replace the function; `ENABLE ALWAYS` only defeats session_replication_role. The security boundary is the
-- least-privilege runtime role, not the trigger (docs/operations/RESEARCH_DB_ROLES.md).
--
-- Rules the schema itself enforces (not just the application):
--   * Explicit session: bar_date = session_date (CHECK); session_source = 'explicit'.
--   * First valid write wins: unique keys + INSERT ... ON CONFLICT DO NOTHING in the application.
--   * "Missing is not zero": measured values that were not computed are NULL (or JSON null + a name in
--     missing_features); nothing defaults to 0 / TRUE / 'pass'.

-- ---------------------------------------------------------------------------------------------------
-- 0. Preconditions and collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'release_b_migration_22';
    n_present INTEGER := 0;
    t TEXT;
    f TEXT;
    ix RECORD;
BEGIN
    IF to_regclass('strategies') IS NULL OR to_regclass('signal_ledger') IS NULL THEN
        RAISE EXCEPTION 'migration 22 requires Release A (migrations 19/20/21): strategies / signal_ledger are missing';
    END IF;
    IF (SELECT count(*) FROM pg_attribute WHERE attrelid = to_regclass('signal_ledger') AND NOT attisdropped
        AND attname IN ('observation_id', 'feature_snapshot_id', 'feature_set_version')) <> 3 THEN
        RAISE EXCEPTION 'migration 22 requires signal_ledger lineage columns from migration 21';
    END IF;

    FOREACH t IN ARRAY ARRAY['feature_set_registry', 'candidate_capture_run', 'research_maintenance_log',
                             'research_maintenance_session', 'research_maintenance_audit', 'feature_snapshot',
                             'candidate_observation', 'research_capture_activation'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            n_present := n_present + 1;
            IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
                RAISE EXCEPTION 'migration 22 refused: relation % already exists and is not a migration-22 object (no marker)', t;
            END IF;
        END IF;
    END LOOP;
    IF n_present NOT IN (0, 8) THEN
        RAISE EXCEPTION 'migration 22 refused: % of 8 research tables exist (partially applied); investigate, do not re-run', n_present;
    END IF;

    FOREACH f IN ARRAY ARRAY['research_guard_immutable()', 'research_audit_append_only()',
                             'research_maintenance_log_guard()', 'research_maintenance_open(text,text,integer)',
                             'research_maintenance_approve(bigint)', 'research_maintenance_begin(bigint)',
                             'research_maintenance_close(bigint)', 'research_capture_set_state(bigint,text,date,text)'] LOOP
        IF to_regprocedure(f) IS NOT NULL
           AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 22 refused: function % already exists and is not a migration-22 object (no marker)', f;
        END IF;
    END LOOP;

    FOR ix IN SELECT * FROM (VALUES
            ('idx_candidate_capture_run_session', 'candidate_capture_run'),
            ('idx_feature_snapshot_session', 'feature_snapshot'),
            ('idx_candidate_observation_session', 'candidate_observation'),
            ('idx_candidate_observation_tracked', 'candidate_observation'),
            ('idx_candidate_observation_snapshot', 'candidate_observation'),
            ('idx_research_maintenance_session_txid', 'research_maintenance_session')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx)
                 AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 22 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;

    IF n_present = 0 AND EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('signal_ledger')
           AND conname IN ('signal_ledger_observation_fk', 'signal_ledger_snapshot_fk',
                           'signal_ledger_feature_set_fk', 'signal_ledger_lineage_all_or_none')) THEN
        RAISE EXCEPTION 'migration 22 refused: signal_ledger already carries a lineage constraint but the research tables do not exist';
    END IF;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. Feature-set registry: what `t0_v1` IS. Append-only (immutability trigger, section 9).
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
--
--    Status semantics (the single definition; mechanism/research/observer.py final_status implements it):
--      running  -- started, not finished. run_finished_at IS NULL. A run stuck here is reported as operationally
--                  failed by the read layer WITHOUT rewriting the row.
--      complete -- finished; every candidate is accounted for with nothing lost:
--                  candidates = captured + already_captured + stale_skipped, and snapshot_skipped = 0,
--                  invalid_skipped = 0, guard_not_evaluated = 0, error IS NULL.
--      partial  -- finished without a run-level error, but at least one candidate was not recorded
--                  (snapshot_skipped / invalid_skipped > 0), the guard verdict is missing for a recorded row
--                  (guard_not_evaluated > 0), or the counters do not reconcile. Rows that WERE captured are valid.
--      failed   -- a run-level exception or a refused manifest. error IS NOT NULL.
--    "disabled" is not a stored status: a disabled capture writes no row at all.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candidate_capture_run (
    id                   BIGSERIAL    PRIMARY KEY,
    strategy_id          BIGINT       NOT NULL REFERENCES strategies (id),
    strategy_version     VARCHAR(20)  NOT NULL,
    session_date         DATE         NOT NULL,
    feature_set_version  VARCHAR(30)  NOT NULL REFERENCES feature_set_registry (feature_set_version),
    session_source       VARCHAR(20)  NOT NULL,
    status               VARCHAR(12)  NOT NULL DEFAULT 'running',
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
    code_ref             VARCHAR(160),
    CONSTRAINT candidate_capture_run_session_source_explicit CHECK (session_source = 'explicit'),
    CONSTRAINT candidate_capture_run_status_values
        CHECK (status IN ('running', 'complete', 'partial', 'failed')),
    CONSTRAINT candidate_capture_run_finished_iff_not_running
        CHECK ((status = 'running') = (run_finished_at IS NULL)),
    CONSTRAINT candidate_capture_run_failed_has_error
        CHECK (status <> 'failed' OR error IS NOT NULL),
    CONSTRAINT candidate_capture_run_complete_is_accounted
        CHECK (status <> 'complete' OR (
            error IS NULL
            AND candidates IS NOT NULL AND captured IS NOT NULL AND already_captured IS NOT NULL
            AND stale_skipped IS NOT NULL AND snapshot_skipped IS NOT NULL AND invalid_skipped IS NOT NULL
            AND guard_not_evaluated IS NOT NULL
            AND snapshot_skipped = 0 AND invalid_skipped = 0 AND guard_not_evaluated = 0
            AND candidates = captured + already_captured + stale_skipped)),
    CONSTRAINT candidate_capture_run_partial_is_counted
        CHECK (status <> 'partial' OR (
            error IS NULL AND candidates IS NOT NULL AND captured IS NOT NULL AND already_captured IS NOT NULL
            AND stale_skipped IS NOT NULL AND snapshot_skipped IS NOT NULL AND invalid_skipped IS NOT NULL
            AND guard_not_evaluated IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_candidate_capture_run_session
    ON candidate_capture_run (strategy_id, session_date, run_started_at DESC);

-- ---------------------------------------------------------------------------------------------------
-- 3. Research-capture activation boundary. Append-only (immutable, hatch-correctable), written ONLY by
--    research_capture_set_state() (section 8). Effective state for a session = the latest row with
--    effective_from_session <= session; no row yet / session before the first row = Release B not active.
--    There is no seed row and no date in this file: the eventual production activation sets it.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_capture_activation (
    id                      BIGSERIAL    PRIMARY KEY,
    strategy_id             BIGINT       NOT NULL REFERENCES strategies (id),
    state                   VARCHAR(10)  NOT NULL,
    effective_from_session  DATE         NOT NULL,
    set_by                  VARCHAR(80)  NOT NULL DEFAULT session_user,
    set_at                  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    note                    TEXT         NOT NULL,
    CONSTRAINT research_capture_activation_state_values CHECK (state IN ('enabled', 'disabled')),
    CONSTRAINT research_capture_activation_note_nonempty CHECK (char_length(btrim(note)) >= 5),
    CONSTRAINT research_capture_activation_effective_key UNIQUE (strategy_id, effective_from_session)
);

-- ---------------------------------------------------------------------------------------------------
-- 4. Maintenance ticket, begin-record and audit (the hatch for the immutability triggers, section 9).
--    None of these is writable by the application role; the only writers are the SECURITY DEFINER functions
--    of section 8 and (for the audit) the immutability trigger itself.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_maintenance_log (
    id             BIGSERIAL    PRIMARY KEY,
    opened_by      VARCHAR(80)  NOT NULL,
    reason         TEXT         NOT NULL,
    target_table   VARCHAR(60)  NOT NULL,
    valid_minutes  INTEGER      NOT NULL DEFAULT 60,
    opened_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    approved_by    VARCHAR(80),
    approved_at    TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ,                         -- set at approval: approved_at + valid_minutes
    closed_at      TIMESTAMPTZ,
    CONSTRAINT research_maintenance_log_target_table_values
        CHECK (target_table IN ('candidate_observation', 'feature_snapshot', 'feature_set_registry',
                                'research_capture_activation')),
    CONSTRAINT research_maintenance_log_reason_nonempty CHECK (char_length(btrim(reason)) >= 10),
    CONSTRAINT research_maintenance_log_valid_minutes_range CHECK (valid_minutes BETWEEN 1 AND 240),
    CONSTRAINT research_maintenance_log_approval_complete
        CHECK ((approved_by IS NULL) = (approved_at IS NULL) AND (approved_by IS NULL) = (expires_at IS NULL)),
    CONSTRAINT research_maintenance_log_distinct_approver
        CHECK (approved_by IS NULL OR approved_by <> opened_by),
    CONSTRAINT research_maintenance_log_expiry_after_approval
        CHECK (expires_at IS NULL OR expires_at > approved_at)
);

CREATE TABLE IF NOT EXISTS research_maintenance_session (
    id                 BIGSERIAL    PRIMARY KEY,
    ticket_id          BIGINT       NOT NULL REFERENCES research_maintenance_log (id),
    txid               BIGINT       NOT NULL,            -- txid_current() of the authorised transaction
    backend_pid        INTEGER      NOT NULL,
    session_user_name  VARCHAR(80)  NOT NULL DEFAULT session_user,
    begun_at           TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT research_maintenance_session_once_per_txn UNIQUE (ticket_id, txid, backend_pid)
);

CREATE INDEX IF NOT EXISTS idx_research_maintenance_session_txid
    ON research_maintenance_session (txid, backend_pid);

CREATE TABLE IF NOT EXISTS research_maintenance_audit (
    id             BIGSERIAL    PRIMARY KEY,
    ticket_id      BIGINT       NOT NULL REFERENCES research_maintenance_log (id),
    table_name     VARCHAR(60)  NOT NULL,
    operation      VARCHAR(10)  NOT NULL,
    old_row        JSONB,                               -- the full row as it was (summary for TRUNCATE)
    new_row        JSONB,
    performed_by   VARCHAR(80)  NOT NULL DEFAULT session_user,
    performed_at   TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT research_maintenance_audit_operation_values CHECK (operation IN ('UPDATE', 'DELETE', 'TRUNCATE'))
);

-- ---------------------------------------------------------------------------------------------------
-- 5. T0 feature snapshot: direction-neutral, strategy-neutral market state for (symbol, session, version).
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
-- 6. Candidate observation: one immutable row per strategy decision.
--    Written for EVERY candidate the strategy's own SQL produces (breakouts and near-breakouts) that has a
--    session-correct bar, BEFORE the guards drop anything -- the rejected candidates are the control group.
--    signal_type / direction / triggered take exactly the values the screener's SQL emits
--    (multi_timeframe_screener.py; mirrored by observer.DIRECTION / TRIGGERED) and must agree with each other.
--    atr_source records where the screener's own ATR came from (the ledger refuses a row without a real one):
--      measured -- the screener read a positive atr_14 from technical_indicators;
--      fallback -- the screener's own fallback filled atr_14 in (named in screener_defaults);
--      missing  -- no positive ATR was available.
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
    atr_source                VARCHAR(10)   NOT NULL,   -- measured | fallback | missing (see header of this section)
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
    CONSTRAINT candidate_observation_signal_type_values
        CHECK (signal_type IN ('bullish_breakout', 'near_bullish', 'bearish_breakout', 'near_bearish')),
    CONSTRAINT candidate_observation_direction_matches_signal
        CHECK ((signal_type IN ('bullish_breakout', 'near_bullish') AND direction = 1)
            OR (signal_type IN ('bearish_breakout', 'near_bearish') AND direction = -1)),
    CONSTRAINT candidate_observation_triggered_matches_signal
        CHECK (triggered = (signal_type IN ('bullish_breakout', 'bearish_breakout'))),
    CONSTRAINT candidate_observation_atr_source_values
        CHECK (atr_source IN ('measured', 'fallback', 'missing')),
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
-- 7. Markers (tables). Section 0 guarantees every one of these is ours or was just created by us.
-- ---------------------------------------------------------------------------------------------------
COMMENT ON TABLE feature_set_registry IS 'release_b_migration_22';
COMMENT ON TABLE candidate_capture_run IS 'release_b_migration_22';
COMMENT ON TABLE research_capture_activation IS 'release_b_migration_22';
COMMENT ON TABLE research_maintenance_log IS 'release_b_migration_22';
COMMENT ON TABLE research_maintenance_session IS 'release_b_migration_22';
COMMENT ON TABLE research_maintenance_audit IS 'release_b_migration_22';
COMMENT ON TABLE feature_snapshot IS 'release_b_migration_22';
COMMENT ON TABLE candidate_observation IS 'release_b_migration_22';

-- ---------------------------------------------------------------------------------------------------
-- 8. Functions. Created ONLY when absent (never CREATE OR REPLACE): an existing marked function is left
--    exactly as found and judged by the fingerprint check. All are SECURITY DEFINER where they write a
--    maintenance table, pin search_path (explicit schemas, pg_temp last) and are revoked from PUBLIC.
-- ---------------------------------------------------------------------------------------------------
DO $mk$
DECLARE
    sp TEXT := (SELECT string_agg(quote_ident(s), ', ') FROM unnest(current_schemas(false)) AS s) || ', pg_temp';
BEGIN
    -- 8a. The immutability guard. Authorisation = a research_maintenance_session row for THIS transaction,
    --     whose ticket is approved, unclosed, unexpired, opened by the same session user, and targets THIS table.
    IF to_regprocedure('research_guard_immutable()') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_guard_immutable() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = @SP@ AS $fn$
DECLARE
    sess_ticket BIGINT;
    row_count   BIGINT;
BEGIN
    SELECT s.ticket_id INTO sess_ticket
    FROM research_maintenance_session s
    JOIN research_maintenance_log l ON l.id = s.ticket_id
    WHERE s.txid = txid_current()
      AND s.backend_pid = pg_backend_pid()
      AND s.session_user_name = session_user
      AND l.opened_by = session_user
      AND l.approved_by IS NOT NULL
      AND l.closed_at IS NULL
      AND clock_timestamp() < l.expires_at
      AND l.target_table = TG_TABLE_NAME
    ORDER BY s.id DESC
    LIMIT 1;

    IF sess_ticket IS NOT NULL THEN
        IF TG_OP = 'TRUNCATE' THEN
            EXECUTE 'SELECT count(*) FROM ' || TG_RELID::regclass::text INTO row_count;
            INSERT INTO research_maintenance_audit (ticket_id, table_name, operation, old_row)
            VALUES (sess_ticket, TG_TABLE_NAME, TG_OP, jsonb_build_object('rows_removed', row_count));
            RETURN NULL;
        ELSIF TG_OP = 'DELETE' THEN
            INSERT INTO research_maintenance_audit (ticket_id, table_name, operation, old_row)
            VALUES (sess_ticket, TG_TABLE_NAME, TG_OP, to_jsonb(OLD));
            RETURN OLD;
        ELSE
            INSERT INTO research_maintenance_audit (ticket_id, table_name, operation, old_row, new_row)
            VALUES (sess_ticket, TG_TABLE_NAME, TG_OP, to_jsonb(OLD), to_jsonb(NEW));
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION 'research table % is immutable: % rejected (no approved, unexpired maintenance ticket begun in this transaction for this table)',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_guard_immutable() IS 'release_b_migration_22';
    END IF;

    -- 8b. Append-only, no hatch at all (audit trail and begin-records).
    IF to_regprocedure('research_audit_append_only()') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_audit_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = @SP@ AS $fn$
BEGIN
    RAISE EXCEPTION '% is append-only: % rejected', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_audit_append_only() IS 'release_b_migration_22';
    END IF;

    -- 8c. The ticket log: identity columns are write-once; approval and closure are set once; never deleted.
    IF to_regprocedure('research_maintenance_log_guard()') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_maintenance_log_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = @SP@ AS $fn$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'research_maintenance_log is append-only: % rejected', TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF ROW(NEW.id, NEW.opened_by, NEW.reason, NEW.target_table, NEW.valid_minutes, NEW.opened_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.opened_by, OLD.reason, OLD.target_table, OLD.valid_minutes, OLD.opened_at) THEN
        RAISE EXCEPTION 'research_maintenance_log: ticket identity columns are write-once'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.approved_by IS NOT NULL
       AND ROW(NEW.approved_by, NEW.approved_at, NEW.expires_at)
           IS DISTINCT FROM ROW(OLD.approved_by, OLD.approved_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'research_maintenance_log: an approval cannot be changed'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF OLD.closed_at IS NOT NULL AND NEW.closed_at IS DISTINCT FROM OLD.closed_at THEN
        RAISE EXCEPTION 'research_maintenance_log: a closed ticket cannot be reopened'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_maintenance_log_guard() IS 'release_b_migration_22';
    END IF;

    -- 8d. Ticket flow. open -> approve (a DIFFERENT session user) -> begin (opener, per transaction) -> close.
    IF to_regprocedure('research_maintenance_open(text,text,integer)') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_maintenance_open(p_target_table TEXT, p_reason TEXT, p_valid_minutes INTEGER DEFAULT 60)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER SET search_path = @SP@ AS $fn$
DECLARE
    new_id BIGINT;
BEGIN
    INSERT INTO research_maintenance_log (opened_by, reason, target_table, valid_minutes)
    VALUES (session_user, p_reason, p_target_table, p_valid_minutes)
    RETURNING id INTO new_id;
    RETURN new_id;
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_maintenance_open(text,text,integer) IS 'release_b_migration_22';
    END IF;

    IF to_regprocedure('research_maintenance_approve(bigint)') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_maintenance_approve(p_ticket BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = @SP@ AS $fn$
DECLARE
    opener TEXT;
BEGIN
    SELECT opened_by INTO opener FROM research_maintenance_log WHERE id = p_ticket;
    IF opener IS NULL THEN
        RAISE EXCEPTION 'maintenance ticket % does not exist', p_ticket;
    END IF;
    IF opener = session_user THEN
        RAISE EXCEPTION 'maintenance ticket %: the approver must differ from the opener', p_ticket;
    END IF;
    UPDATE research_maintenance_log
    SET approved_by = session_user,
        approved_at = clock_timestamp(),
        expires_at  = clock_timestamp() + valid_minutes * INTERVAL '1 minute'
    WHERE id = p_ticket AND approved_by IS NULL AND closed_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'maintenance ticket % is not open for approval (already approved or closed)', p_ticket;
    END IF;
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_maintenance_approve(bigint) IS 'release_b_migration_22';
    END IF;

    IF to_regprocedure('research_maintenance_begin(bigint)') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_maintenance_begin(p_ticket BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = @SP@ AS $fn$
DECLARE
    tk RECORD;
BEGIN
    SELECT * INTO tk FROM research_maintenance_log WHERE id = p_ticket;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'maintenance ticket % does not exist', p_ticket;
    END IF;
    IF tk.opened_by <> session_user THEN
        RAISE EXCEPTION 'maintenance ticket %: only its opener may begin a maintenance transaction', p_ticket;
    END IF;
    IF tk.approved_by IS NULL OR tk.closed_at IS NOT NULL OR clock_timestamp() >= tk.expires_at THEN
        RAISE EXCEPTION 'maintenance ticket % is not usable (unapproved, closed or expired)', p_ticket;
    END IF;
    INSERT INTO research_maintenance_session (ticket_id, txid, backend_pid)
    VALUES (p_ticket, txid_current(), pg_backend_pid())
    ON CONFLICT DO NOTHING;
    -- informational only: the immutability guard trusts research_maintenance_session, never this setting
    PERFORM set_config('research.maintenance_ticket', p_ticket::text, true);
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_maintenance_begin(bigint) IS 'release_b_migration_22';
    END IF;

    IF to_regprocedure('research_maintenance_close(bigint)') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_maintenance_close(p_ticket BIGINT)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = @SP@ AS $fn$
BEGIN
    UPDATE research_maintenance_log
    SET closed_at = clock_timestamp()
    WHERE id = p_ticket AND closed_at IS NULL AND session_user IN (opened_by, approved_by);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'maintenance ticket % cannot be closed by this user (missing, already closed, or not a party)', p_ticket;
    END IF;
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_maintenance_close(bigint) IS 'release_b_migration_22';
    END IF;

    -- 8e. The activation boundary writer. effective_from is explicit, strictly increasing per strategy, and may
    --     not pre-date a session that was already captured; the first row must be 'enabled'; no no-op rows.
    IF to_regprocedure('research_capture_set_state(bigint,text,date,text)') IS NULL THEN
        EXECUTE replace($f$
CREATE FUNCTION research_capture_set_state(p_strategy_id BIGINT, p_state TEXT, p_effective_from DATE, p_note TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER SET search_path = @SP@ AS $fn$
DECLARE
    last_eff   DATE;
    last_state TEXT;
    captured   DATE;
    new_id     BIGINT;
BEGIN
    IF p_effective_from IS NULL THEN
        RAISE EXCEPTION 'research_capture_set_state: an explicit effective_from session is required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM strategies WHERE id = p_strategy_id) THEN
        RAISE EXCEPTION 'research_capture_set_state: unknown strategy %', p_strategy_id;
    END IF;
    SELECT effective_from_session, state INTO last_eff, last_state
    FROM research_capture_activation WHERE strategy_id = p_strategy_id
    ORDER BY effective_from_session DESC LIMIT 1;
    IF FOUND THEN
        IF p_effective_from <= last_eff THEN
            RAISE EXCEPTION 'research_capture_set_state: effective_from % must be after the latest boundary %', p_effective_from, last_eff;
        END IF;
        IF p_state = last_state THEN
            RAISE EXCEPTION 'research_capture_set_state: the state is already % (no-op boundary refused)', p_state;
        END IF;
    ELSIF p_state <> 'enabled' THEN
        RAISE EXCEPTION 'research_capture_set_state: the first boundary must be enabled';
    END IF;
    SELECT max(session_date) INTO captured FROM candidate_capture_run WHERE strategy_id = p_strategy_id;
    IF captured IS NOT NULL AND p_effective_from <= captured THEN
        RAISE EXCEPTION 'research_capture_set_state: effective_from % does not postdate the already-captured session %', p_effective_from, captured;
    END IF;
    INSERT INTO research_capture_activation (strategy_id, state, effective_from_session, note)
    VALUES (p_strategy_id, p_state, p_effective_from, p_note)
    RETURNING id INTO new_id;
    RETURN new_id;
END;
$fn$
$f$, '@SP@', sp);
        COMMENT ON FUNCTION research_capture_set_state(bigint,text,date,text) IS 'release_b_migration_22';
    END IF;
END;
$mk$;

-- Nothing is executable by PUBLIC. Grants to concrete roles live in deploy/db/research_roles.sql.
REVOKE ALL ON FUNCTION research_maintenance_open(text,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION research_maintenance_approve(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION research_maintenance_begin(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION research_maintenance_close(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION research_capture_set_state(bigint,text,date,text) FROM PUBLIC;

-- ---------------------------------------------------------------------------------------------------
-- 9. Triggers. Created if absent; an existing one must already be the right function and ENABLE ALWAYS,
--    otherwise the migration refuses (it never silently re-enables or re-points a trigger).
-- ---------------------------------------------------------------------------------------------------
DO $trg$
DECLARE
    r RECORD;
    cur_mode "char";
    cur_fn   TEXT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('candidate_observation',        'candidate_observation_immutable_row',                  'BEFORE UPDATE OR DELETE', 'ROW',       'research_guard_immutable'),
        ('candidate_observation',        'candidate_observation_immutable_truncate',             'BEFORE TRUNCATE',         'STATEMENT', 'research_guard_immutable'),
        ('feature_snapshot',             'feature_snapshot_immutable_row',                       'BEFORE UPDATE OR DELETE', 'ROW',       'research_guard_immutable'),
        ('feature_snapshot',             'feature_snapshot_immutable_truncate',                  'BEFORE TRUNCATE',         'STATEMENT', 'research_guard_immutable'),
        ('feature_set_registry',         'feature_set_registry_immutable_row',                   'BEFORE UPDATE OR DELETE', 'ROW',       'research_guard_immutable'),
        ('feature_set_registry',         'feature_set_registry_immutable_truncate',              'BEFORE TRUNCATE',         'STATEMENT', 'research_guard_immutable'),
        ('research_capture_activation',  'research_capture_activation_immutable_row',            'BEFORE UPDATE OR DELETE', 'ROW',       'research_guard_immutable'),
        ('research_capture_activation',  'research_capture_activation_immutable_truncate',       'BEFORE TRUNCATE',         'STATEMENT', 'research_guard_immutable'),
        ('research_maintenance_audit',   'research_maintenance_audit_append_only_row',           'BEFORE UPDATE OR DELETE', 'ROW',       'research_audit_append_only'),
        ('research_maintenance_audit',   'research_maintenance_audit_append_only_truncate',      'BEFORE TRUNCATE',         'STATEMENT', 'research_audit_append_only'),
        ('research_maintenance_session', 'research_maintenance_session_append_only_row',         'BEFORE UPDATE OR DELETE', 'ROW',       'research_audit_append_only'),
        ('research_maintenance_session', 'research_maintenance_session_append_only_truncate',    'BEFORE TRUNCATE',         'STATEMENT', 'research_audit_append_only'),
        ('research_maintenance_log',     'research_maintenance_log_guard_row',                   'BEFORE UPDATE OR DELETE', 'ROW',       'research_maintenance_log_guard'),
        ('research_maintenance_log',     'research_maintenance_log_guard_truncate',              'BEFORE TRUNCATE',         'STATEMENT', 'research_maintenance_log_guard')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 22 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()',
                           r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;

-- ---------------------------------------------------------------------------------------------------
-- 10. Ledger lineage integrity. signal_ledger.observation_id / feature_snapshot_id / feature_set_version were
--     added (nullable, no FK) by migration 21 for exactly this. Release A rows keep NULL lineage and pass every
--     constraint below; a row that carries lineage must carry all of it and must point at real rows, so a
--     maintenance DELETE of a referenced observation/snapshot is refused by the database.
-- ---------------------------------------------------------------------------------------------------
DO $lin$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('signal_ledger')
                   AND conname = 'signal_ledger_observation_fk') THEN
        ALTER TABLE signal_ledger ADD CONSTRAINT signal_ledger_observation_fk
            FOREIGN KEY (observation_id) REFERENCES candidate_observation (id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('signal_ledger')
                   AND conname = 'signal_ledger_snapshot_fk') THEN
        ALTER TABLE signal_ledger ADD CONSTRAINT signal_ledger_snapshot_fk
            FOREIGN KEY (feature_snapshot_id) REFERENCES feature_snapshot (id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('signal_ledger')
                   AND conname = 'signal_ledger_feature_set_fk') THEN
        ALTER TABLE signal_ledger ADD CONSTRAINT signal_ledger_feature_set_fk
            FOREIGN KEY (feature_set_version) REFERENCES feature_set_registry (feature_set_version);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('signal_ledger')
                   AND conname = 'signal_ledger_lineage_all_or_none') THEN
        ALTER TABLE signal_ledger ADD CONSTRAINT signal_ledger_lineage_all_or_none
            CHECK ((observation_id IS NULL) = (feature_snapshot_id IS NULL)
               AND (observation_id IS NULL) = (feature_set_version IS NULL));
    END IF;
END;
$lin$;
