-- mechanism/add_forward_return_label_table.sql   (migration 26 -- fwd_v1 forward-outcome labels)
--
-- WHAT HAPPENED AFTER T0, for every candidate_observation, independent of whether the candidate ever became a signal.
-- One row per (observation, horizon in trading sessions, label_version). Immutable. Written ONLY when the label is
-- determinable; there is no "partial" row:
--   label_status = 'final'  the horizon session (the Nth completed US session AFTER T0) is complete and every input was usable.
--   label_status = 'void'   terminal: the horizon passed, a bounded grace period passed, and the inputs were still unusable
--                           (missing bar, split-like discontinuity, invalid bar). All outcome columns are NULL, void_reason says why.
-- A horizon that has not matured yet, or is still inside its grace period, has NO ROW. So a consumer that filters
-- label_status = 'final' (mechanism/research/labels/repository.final_labels) can never see a partial label.
--
-- Independent of signal_ledger: the only parent is candidate_observation (migration 22, which this migration requires).
-- Semantics, price-basis policy and the leakage rules: docs/research/FWD_V1_FORWARD_OUTCOME_ENGINE.md.
-- Nothing here is a Donchian rule: any strategy's observations are labelled the same way.
--
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_forward_return_label_table.sql
-- (one transaction; any failure leaves nothing behind). NOT applied to production by committing this file; applying it
-- activates nothing -- no scheduled job writes to it. Re-run deploy/db/research_roles.sql (+ the verifier) afterwards:
-- the table is append-only for the runtime role (SELECT + INSERT) and is excluded from the full-DML baseline there.
--
-- Immutability, two layers (as migration 24): privileges (runtime role INSERT/SELECT only) and ENABLE ALWAYS BEFORE
-- UPDATE/DELETE row + BEFORE TRUNCATE statement triggers that ALWAYS raise. There is no maintenance hatch: a correction
-- is a new label_version (a new row), never an edit. A superuser or the owner can still disable a trigger; the security
-- boundary is the least-privilege runtime role.
--
-- Collision safety. Every object carries the marker comment 'forward_return_label_migration_26'. Section 0 refuses
-- (changes nothing) when migration 22 is absent, when an object with one of our names exists without the marker, or on a
-- partial prior state. "Missing is not zero": an outcome that was not computed is NULL, never 0.

-- ---------------------------------------------------------------------------------------------------
-- 0. Collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'forward_return_label_migration_26';
    f TEXT;
    ix RECORD;
BEGIN
    IF to_regclass('candidate_observation') IS NULL
       OR coalesce(obj_description(to_regclass('candidate_observation'), 'pg_class'), '') <> 'release_b_migration_22' THEN
        RAISE EXCEPTION 'migration 26 refused: migration 22 (candidate_observation) is required and is not present';
    END IF;
    IF to_regclass('forward_return_label') IS NOT NULL
       AND coalesce(obj_description(to_regclass('forward_return_label'), 'pg_class'), '') <> marker THEN
        RAISE EXCEPTION 'migration 26 refused: relation forward_return_label already exists and is not a migration-26 object (no marker)';
    END IF;
    FOREACH f IN ARRAY ARRAY['research_label_guard()', 'research_label_consistency()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 26 refused: function % already exists and is not a migration-26 object (no marker)', f;
        END IF;
    END LOOP;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_forward_return_label_horizon', 'forward_return_label'),
            ('idx_forward_return_label_t0',      'forward_return_label')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 26 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. The table.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forward_return_label (
    id                          BIGSERIAL     PRIMARY KEY,
    observation_id              BIGINT        NOT NULL REFERENCES candidate_observation (id),
    horizon_sessions            SMALLINT      NOT NULL,
    label_version               VARCHAR(30)   NOT NULL,
    methodology_version         VARCHAR(30)   NOT NULL,
    label_status                VARCHAR(5)    NOT NULL,
    void_reason                 VARCHAR(40),

    -- identity copies, checked against the observation row by research_label_consistency() on INSERT
    symbol                      VARCHAR(10)   NOT NULL,
    direction                   SMALLINT      NOT NULL,
    t0_session                  DATE          NOT NULL,
    horizon_session             DATE          NOT NULL,   -- the Nth completed session AFTER t0_session, from the explicit calendar
    computed_as_of_session      DATE          NOT NULL,   -- the explicit "latest completed session" the run was given (never wall-clock)

    -- reference price and provenance
    reference_close             NUMERIC(14,4),            -- T0 close AS STORED when the label was computed (the basis every return uses)
    entry_close_captured        NUMERIC(14,4),            -- candidate_observation.entry_close: the raw T0 close captured at T0
    basis_ratio                 DOUBLE PRECISION,         -- entry_close_captured / reference_close; <> 1 means the stored history was restated since T0
    horizon_close               NUMERIC(14,4),

    -- outcomes (simple returns; fractions, not percent)
    raw_return                  DOUBLE PRECISION,         -- horizon_close / reference_close - 1
    directional_return          DOUBLE PRECISION,         -- direction * raw_return (a short profits when price falls)
    benchmark_symbol            VARCHAR(20)   NOT NULL,
    benchmark_state             VARCHAR(13)   NOT NULL,   -- ok | unavailable | not_evaluated
    benchmark_return            DOUBLE PRECISION,         -- benchmark over the same t0 -> horizon window
    excess_return               DOUBLE PRECISION,         -- raw_return - benchmark_return
    directional_excess_return   DOUBLE PRECISION,         -- direction * excess_return
    path_state                  VARCHAR(13)   NOT NULL,   -- complete | incomplete | not_evaluated
    mfe                         DOUBLE PRECISION,         -- >= 0, direction-adjusted favourable excursion over sessions t0+1 .. horizon
    mae                         DOUBLE PRECISION,         -- <= 0, direction-adjusted adverse excursion over the same sessions
    bars_expected               SMALLINT      NOT NULL,   -- = horizon_sessions
    bars_observed               SMALLINT,                 -- bars actually present in sessions t0+1 .. horizon

    -- data quality and reproducibility
    data_quality                VARCHAR(14)   NOT NULL,   -- ok | basis_adjusted | not_evaluated
    dq_details                  JSONB         NOT NULL DEFAULT '{}'::jsonb,
    price_source                VARCHAR(40)   NOT NULL,
    price_basis                 VARCHAR(40)   NOT NULL,
    calendar_source             VARCHAR(40)   NOT NULL,
    input_hash                  CHAR(64)      NOT NULL,   -- sha256 of every input value used: a later restatement is detectable by recomputing it
    code_ref                    VARCHAR(160)  NOT NULL,
    computed_at                 TIMESTAMPTZ   NOT NULL DEFAULT NOW(),   -- audit only; never an input to any computation

    CONSTRAINT forward_return_label_key UNIQUE (observation_id, horizon_sessions, label_version),
    CONSTRAINT forward_return_label_horizon_chk CHECK (horizon_sessions IN (1, 3, 5, 10, 20, 60)),
    CONSTRAINT forward_return_label_status_chk CHECK (label_status IN ('final', 'void')),
    CONSTRAINT forward_return_label_direction_chk CHECK (direction IN (1, -1)),
    CONSTRAINT forward_return_label_order_chk CHECK (horizon_session > t0_session AND computed_as_of_session >= horizon_session),
    CONSTRAINT forward_return_label_bars_chk CHECK (bars_expected = horizon_sessions
        AND (bars_observed IS NULL OR bars_observed BETWEEN 0 AND bars_expected)),
    CONSTRAINT forward_return_label_benchmark_state_chk CHECK (benchmark_state IN ('ok', 'unavailable', 'not_evaluated')),
    CONSTRAINT forward_return_label_path_state_chk CHECK (path_state IN ('complete', 'incomplete', 'not_evaluated')),
    CONSTRAINT forward_return_label_dq_chk CHECK (data_quality IN ('ok', 'basis_adjusted', 'not_evaluated')),
    -- a final label carries its outcome and a reason-free status; a void label carries a reason and NO outcome
    CONSTRAINT forward_return_label_final_chk CHECK (label_status <> 'final' OR (
            void_reason IS NULL AND reference_close IS NOT NULL AND horizon_close IS NOT NULL AND entry_close_captured IS NOT NULL
            AND basis_ratio IS NOT NULL AND raw_return IS NOT NULL AND directional_return IS NOT NULL AND bars_observed IS NOT NULL
            AND benchmark_state <> 'not_evaluated' AND path_state <> 'not_evaluated' AND data_quality <> 'not_evaluated')),
    CONSTRAINT forward_return_label_void_chk CHECK (label_status <> 'void' OR (
            void_reason IS NOT NULL AND reference_close IS NULL AND horizon_close IS NULL AND basis_ratio IS NULL
            AND raw_return IS NULL AND directional_return IS NULL AND benchmark_return IS NULL AND excess_return IS NULL
            AND directional_excess_return IS NULL AND mfe IS NULL AND mae IS NULL
            AND benchmark_state = 'not_evaluated' AND path_state = 'not_evaluated' AND data_quality = 'not_evaluated')),
    CONSTRAINT forward_return_label_benchmark_chk CHECK (
            (benchmark_state = 'ok') = (benchmark_return IS NOT NULL AND excess_return IS NOT NULL AND directional_excess_return IS NOT NULL)
            AND (benchmark_state = 'ok' OR (benchmark_return IS NULL AND excess_return IS NULL AND directional_excess_return IS NULL))),
    CONSTRAINT forward_return_label_path_chk CHECK (
            (path_state = 'complete') = (mfe IS NOT NULL AND mae IS NOT NULL)
            AND (path_state = 'complete' OR (mfe IS NULL AND mae IS NULL))),
    CONSTRAINT forward_return_label_excursion_sign_chk CHECK ((mfe IS NULL OR mfe >= 0) AND (mae IS NULL OR mae <= 0)),
    CONSTRAINT forward_return_label_directional_chk CHECK (raw_return IS NULL OR directional_return = direction * raw_return),
    CONSTRAINT forward_return_label_complete_path_bars_chk CHECK (path_state <> 'complete' OR bars_observed = bars_expected)
);

-- as-of reads by horizon session (embargo / maturity filters) and by T0 session
CREATE INDEX IF NOT EXISTS idx_forward_return_label_horizon
    ON forward_return_label (label_version, horizon_session) WHERE label_status = 'final';
CREATE INDEX IF NOT EXISTS idx_forward_return_label_t0
    ON forward_return_label (t0_session, label_version);

-- ---------------------------------------------------------------------------------------------------
-- 2. Markers, guard function and consistency function (created only when absent).
-- ---------------------------------------------------------------------------------------------------
COMMENT ON TABLE forward_return_label IS 'forward_return_label_migration_26';

DO $mk$
BEGIN
    IF to_regprocedure('research_label_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_label_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (forward-return labels are immutable; a correction is a new label_version)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_label_guard() IS 'forward_return_label_migration_26';
        REVOKE ALL ON FUNCTION research_label_guard() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_label_consistency()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_label_consistency() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    o RECORD;
BEGIN
    -- the label's identity copies must be the observation's own values (the parent is immutable, so this holds for good)
    SELECT symbol, direction, session_date INTO o FROM candidate_observation WHERE id = NEW.observation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'forward_return_label refused: observation % does not exist', NEW.observation_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF o.symbol <> NEW.symbol OR o.direction <> NEW.direction OR o.session_date <> NEW.t0_session THEN
        RAISE EXCEPTION 'forward_return_label refused: symbol/direction/t0_session do not match observation %', NEW.observation_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_label_consistency() IS 'forward_return_label_migration_26';
        REVOKE ALL ON FUNCTION research_label_consistency() FROM PUBLIC;
    END IF;
END;
$mk$;

-- ---------------------------------------------------------------------------------------------------
-- 3. Triggers. Created if absent; an existing one must already be the right function and ENABLE ALWAYS, otherwise refuse.
-- ---------------------------------------------------------------------------------------------------
DO $trg$
DECLARE
    r RECORD;
    cur_mode "char";
    cur_fn   TEXT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('forward_return_label_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_label_guard'),
        ('forward_return_label_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_label_guard'),
        ('forward_return_label_consistency',        'BEFORE INSERT',           'ROW',       'research_label_consistency')
    ) AS v(trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass('forward_return_label') AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 26 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON forward_return_label FOR EACH %s EXECUTE FUNCTION %I()',
                           r.trg, r.timing, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE forward_return_label ENABLE ALWAYS TRIGGER %I', r.trg);
        END IF;
    END LOOP;
END;
$trg$;
