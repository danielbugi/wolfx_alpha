-- mechanism/add_paper_tracker_tables.sql   (migration 36 -- isolated, append-only storage for the forward PAPER tracker of the registered strategy variants)
--
-- WHY. The paper tracker (mechanism/research/paper_tracker.py) follows every registered variant independently after each parent-strategy signal and records what each exit policy WOULD have done. It opens no
-- order and changes no live signal. Its evidence must be durable, immutable and impossible to confuse with the live ledger, so it lives in its own tables and references the rest of the schema READ-ONLY:
--
--   paper_observation   one immutable row per (registered variant, symbol, direction, signal session): the registration, i.e. everything known at the DECISION (ATR, the channel window, the decision-time
--                       context with its own availability timestamps), plus observation_hash, the root of that observation's hash chain.
--   paper_event         the append-only event log of an observation: entered | not_enterable | closed | hindsight. Each row carries prev_hash / event_hash so the chain is verifiable; the database enforces
--                       the order (entered -> closed -> hindsight), contiguity (seq) and chain linkage. 'hindsight' rows hold measurements that need LATER bars (payload.decision_time_information = false).
--   paper_session       one immutable row per processed session, written LAST in the same transaction as that session's observations and events: the cursor (a session is done iff its row exists),
--                       the counts, and open_state (+ its hash): the snapshot of every in-flight observation that the next run restores from after re-verifying the hash.
--
-- Nothing here writes to, or alters, strategies, strategy_variant, signal_ledger, candidate_observation or any other existing table (foreign keys only READ them). The in-flight state is stored once per session
-- (open_state); the event log stays the immutable record of what happened, and an audit can replay it against prices (tolerating vendor revisions, which are reported, not hidden).
-- Immutability, two layers like migrations 24-35: privileges (runtime role SELECT + INSERT only; deploy/db/research_roles.sql) and ENABLE ALWAYS BEFORE UPDATE / DELETE row + TRUNCATE triggers that always raise.
-- Additive and forward-safe (IF NOT EXISTS; refuses to adopt same-named objects it did not create). Needs strategy_variant (migration 35) and candidate_observation (migration 22).
-- NOT applied to production by committing this file. Activation is a separate, owner-approved step (docs/research/FORWARD_PAPER_TRACKER.md). Rollback while empty: deploy/db/rollback_36.sql.

DO $guard$
DECLARE
    t TEXT;
BEGIN
    IF to_regclass('strategy_variant') IS NULL THEN
        RAISE EXCEPTION 'migration 36 refused: strategy_variant does not exist in the current schema (apply migration 35 first / wrong search_path)';
    END IF;
    IF to_regclass('candidate_observation') IS NULL THEN
        RAISE EXCEPTION 'migration 36 refused: candidate_observation does not exist in the current schema (apply migration 22 first / wrong search_path)';
    END IF;
    FOREACH t IN ARRAY ARRAY['paper_session', 'paper_observation', 'paper_event'] LOOP
        IF to_regclass(t) IS NOT NULL AND coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> 'paper_tracker_migration_36' THEN
            RAISE EXCEPTION 'migration 36 refused: table % already exists and is not a migration-36 object (no marker)', t;
        END IF;
    END LOOP;
END;
$guard$;

CREATE TABLE IF NOT EXISTS paper_session (
    id                BIGSERIAL    PRIMARY KEY,
    session_date      DATE         NOT NULL,                  -- the explicit trading session that was processed (never inferred from the clock)
    scope             VARCHAR(12)  NOT NULL,                  -- triggered (every breakout) | tracked (only what the live ledger would take)
    source            VARCHAR(24)  NOT NULL,                  -- candidate_observation (production) | dataset_replay (a shadow run on a scratch database)
    n_variants        SMALLINT     NOT NULL,
    n_candidates      INTEGER      NOT NULL,
    n_registered      INTEGER      NOT NULL,
    n_skipped_open    INTEGER      NOT NULL,                  -- (variant, symbol, direction) already had an open / pending observation
    n_entered         INTEGER      NOT NULL,
    n_closed          INTEGER      NOT NULL,
    n_not_enterable   INTEGER      NOT NULL,
    n_open_after      INTEGER      NOT NULL,
    open_state        JSONB        NOT NULL,                  -- snapshot of every open + pending observation after this session: the next run continues from it (paper_open_state_v1)
    open_state_hash   CHAR(64)     NOT NULL,                  -- sha256 of that snapshot's canonical JSON, re-verified by the next run before it continues
    run_id            VARCHAR(80)  NOT NULL,
    writer            VARCHAR(80)  NOT NULL,
    code_ref          VARCHAR(160) NOT NULL,
    started_at        TIMESTAMPTZ  NOT NULL,
    finished_at       TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),     -- OVERWRITTEN by the stamp trigger with the database clock
    CONSTRAINT paper_session_date_key UNIQUE (session_date),
    CONSTRAINT paper_session_scope_chk CHECK (scope IN ('triggered', 'tracked')),
    CONSTRAINT paper_session_source_chk CHECK (source IN ('candidate_observation', 'dataset_replay')),
    CONSTRAINT paper_session_counts_chk CHECK (n_variants > 0 AND n_candidates >= 0 AND n_registered >= 0 AND n_skipped_open >= 0 AND n_entered >= 0 AND n_closed >= 0 AND n_not_enterable >= 0 AND n_open_after >= 0),
    CONSTRAINT paper_session_hash_chk CHECK (open_state_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT paper_session_state_chk CHECK (jsonb_typeof(open_state) = 'object' AND open_state ->> 'schema' = 'paper_open_state_v1' AND open_state ->> 'last_session' = session_date::text),
    CONSTRAINT paper_session_text_chk CHECK (length(run_id) > 0 AND length(writer) > 0 AND length(code_ref) > 0)
);
COMMENT ON TABLE paper_session IS 'paper_tracker_migration_36';

CREATE TABLE IF NOT EXISTS paper_observation (
    id                        BIGSERIAL    PRIMARY KEY,
    variant_id                BIGINT       NOT NULL REFERENCES strategy_variant (id),
    symbol                    VARCHAR(20)  NOT NULL,
    direction                 SMALLINT     NOT NULL,
    signal_session            DATE         NOT NULL,           -- the session whose close produced the parent signal
    candidate_observation_id  BIGINT       REFERENCES candidate_observation (id),     -- NULL only for a dataset_replay shadow run
    atr                       DOUBLE PRECISION NOT NULL,        -- ATR14 at the signal bar (1R, standardised = 2 x ATR)
    hold_cap                  SMALLINT     NOT NULL,
    registered_cap            BOOLEAN      NOT NULL,           -- TRUE = the cap in the registered definition (60); a longer cap is a research condition
    hist_h                    DOUBLE PRECISION[] NOT NULL,      -- highs / lows of the bars up to the signal bar that the channel policies look at
    hist_l                    DOUBLE PRECISION[] NOT NULL,
    context                   JSONB        NOT NULL,           -- decision-time context, each item with the time it became available
    observation_hash          CHAR(64)     NOT NULL,           -- sha256 of the canonical registration: the root of this observation's event chain
    run_id                    VARCHAR(80)  NOT NULL,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT paper_observation_identity_key UNIQUE (variant_id, symbol, direction, signal_session),
    CONSTRAINT paper_observation_direction_chk CHECK (direction IN (1, -1)),
    CONSTRAINT paper_observation_atr_chk CHECK (atr > 0 AND atr < 'Infinity'::float8),
    CONSTRAINT paper_observation_cap_chk CHECK (hold_cap BETWEEN 1 AND 1000),
    CONSTRAINT paper_observation_hist_chk CHECK (cardinality(hist_h) = cardinality(hist_l) AND cardinality(hist_h) BETWEEN 1 AND 250),
    CONSTRAINT paper_observation_context_chk CHECK (jsonb_typeof(context) = 'object'),
    CONSTRAINT paper_observation_hash_chk CHECK (observation_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT paper_observation_text_chk CHECK (length(symbol) > 0 AND length(run_id) > 0)
);
CREATE INDEX IF NOT EXISTS idx_paper_observation_session ON paper_observation (signal_session);
COMMENT ON TABLE paper_observation IS 'paper_tracker_migration_36';

CREATE TABLE IF NOT EXISTS paper_event (
    id              BIGSERIAL    PRIMARY KEY,
    observation_id  BIGINT       NOT NULL REFERENCES paper_observation (id),
    seq             INTEGER      NOT NULL,
    event_type      VARCHAR(16)  NOT NULL,
    session_date    DATE         NOT NULL,
    payload         JSONB        NOT NULL,
    prev_hash       CHAR(64)     NOT NULL,                     -- the observation's observation_hash for seq 1, otherwise the previous event's event_hash
    event_hash      CHAR(64)     NOT NULL,                     -- computed by the writer over (identity, seq, type, session, payload, prev_hash); re-verified by research.paper_store.verify
    run_id          VARCHAR(80)  NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT paper_event_seq_key UNIQUE (observation_id, seq),
    CONSTRAINT paper_event_type_once_key UNIQUE (observation_id, event_type),
    CONSTRAINT paper_event_type_chk CHECK (event_type IN ('entered', 'not_enterable', 'closed', 'hindsight')),
    CONSTRAINT paper_event_seq_chk CHECK (seq >= 1),
    CONSTRAINT paper_event_payload_chk CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT paper_event_hash_chk CHECK (prev_hash ~ '^[0-9a-f]{64}$' AND event_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT paper_event_hindsight_flag_chk CHECK (event_type <> 'hindsight' OR coalesce(payload -> 'decision_time_information' = 'false'::jsonb, FALSE))     -- NULL (key missing) must FAIL, not pass
);
CREATE INDEX IF NOT EXISTS idx_paper_event_session ON paper_event (session_date, event_type);
COMMENT ON TABLE paper_event IS 'paper_tracker_migration_36';

DO $mk$
BEGIN
    IF to_regprocedure('research_paper_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_paper_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (paper-tracker evidence is immutable; a correction is a new event)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_paper_guard() IS 'paper_tracker_migration_36';
        REVOKE ALL ON FUNCTION research_paper_guard() FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_paper_session_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_paper_session_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    newest DATE;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('paper_session', 0));
    NEW.finished_at := clock_timestamp();
    IF NEW.started_at > NEW.finished_at THEN
        RAISE EXCEPTION 'paper_session refused: started_at % is after the database clock', NEW.started_at USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT max(session_date) INTO newest FROM paper_session;
    IF newest IS NOT NULL AND NEW.session_date <= newest THEN
        RAISE EXCEPTION 'paper_session refused: session % is not after the last processed session % (sessions are processed once, in order)', NEW.session_date, newest
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_paper_session_stamp() IS 'paper_tracker_migration_36';
        REVOKE ALL ON FUNCTION research_paper_session_stamp() FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_paper_observation_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_paper_observation_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    v RECORD;
    defined_cap INTEGER;
BEGIN
    NEW.created_at := clock_timestamp();
    SELECT variant_key, definition INTO v FROM strategy_variant WHERE id = NEW.variant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'paper_observation refused: strategy_variant % does not exist', NEW.variant_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    defined_cap := (v.definition -> 'exit' ->> 'max_hold_bars')::integer;
    IF NEW.registered_cap IS DISTINCT FROM (NEW.hold_cap = defined_cap) THEN
        RAISE EXCEPTION 'paper_observation refused: registered_cap=% does not match hold_cap % against the registered definition cap %', NEW.registered_cap, NEW.hold_cap, defined_cap
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_paper_observation_stamp() IS 'paper_tracker_migration_36';
        REVOKE ALL ON FUNCTION research_paper_observation_stamp() FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_paper_event_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_paper_event_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    obs  RECORD;
    last RECORD;
    expect_prev CHAR(64);
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('paper_event|' || NEW.observation_id::text, 0));
    NEW.created_at := clock_timestamp();
    SELECT observation_hash, signal_session INTO obs FROM paper_observation WHERE id = NEW.observation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'paper_event refused: observation % does not exist', NEW.observation_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    SELECT seq, event_type, event_hash, session_date INTO last FROM paper_event WHERE observation_id = NEW.observation_id ORDER BY seq DESC LIMIT 1;
    IF NOT FOUND THEN
        IF NEW.seq <> 1 THEN
            RAISE EXCEPTION 'paper_event refused: the first event of an observation must have seq 1, got %', NEW.seq USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        expect_prev := obs.observation_hash;
        IF NEW.event_type NOT IN ('entered', 'not_enterable') THEN
            RAISE EXCEPTION 'paper_event refused: an observation starts with entered or not_enterable, not %', NEW.event_type USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.session_date <= obs.signal_session THEN
            RAISE EXCEPTION 'paper_event refused: the entry session % is not after the signal session %', NEW.session_date, obs.signal_session USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        IF NEW.seq <> last.seq + 1 THEN
            RAISE EXCEPTION 'paper_event refused: seq must be % (contiguous), got %', last.seq + 1, NEW.seq USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        expect_prev := last.event_hash;
        IF NOT ((last.event_type = 'entered' AND NEW.event_type = 'closed')
             OR (last.event_type IN ('closed', 'not_enterable') AND NEW.event_type = 'hindsight')) THEN
            RAISE EXCEPTION 'paper_event refused: % cannot follow %', NEW.event_type, last.event_type USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.session_date < last.session_date THEN
            RAISE EXCEPTION 'paper_event refused: session % is before the previous event session %', NEW.session_date, last.session_date USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    IF NEW.prev_hash IS DISTINCT FROM expect_prev THEN
        RAISE EXCEPTION 'paper_event refused: prev_hash does not link to the chain (expected %)', left(expect_prev, 12) USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_paper_event_stamp() IS 'paper_tracker_migration_36';
        REVOKE ALL ON FUNCTION research_paper_event_stamp() FROM PUBLIC;
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
        ('paper_session',     'paper_session_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_paper_guard'),
        ('paper_session',     'paper_session_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_paper_guard'),
        ('paper_session',     'paper_session_stamp',              'BEFORE INSERT',           'ROW',       'research_paper_session_stamp'),
        ('paper_observation', 'paper_observation_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_paper_guard'),
        ('paper_observation', 'paper_observation_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_paper_guard'),
        ('paper_observation', 'paper_observation_stamp',              'BEFORE INSERT',           'ROW',       'research_paper_observation_stamp'),
        ('paper_event',       'paper_event_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_paper_guard'),
        ('paper_event',       'paper_event_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_paper_guard'),
        ('paper_event',       'paper_event_stamp',              'BEFORE INSERT',           'ROW',       'research_paper_event_stamp')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 36 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
