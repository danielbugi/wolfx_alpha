-- mechanism/add_sector_history_tables.sql   (migration 31 -- append-only point-in-time sector observation history)
--
-- WHAT FIRST LIGHT KNEW ABOUT EACH SYMBOL'S SECTOR, AND WHEN IT FIRST KNEW IT. A sector tag is a vendor classification attribute, not a
-- dated observation of the world: `daily_fundamentals.sector` is rewritten in place on every re-fetch, so it cannot say what was known
-- yesterday. These tables are the forward record that can. Designed in docs/research/LAB_SLICE9_SECTOR_COVERAGE_AND_HISTORY_DESIGN.md,
-- implemented and reviewed in docs/research/LAB_SLICE10_SECTOR_HISTORY.md. Independent of migrations 24 / 25 / 26 (requires none of them).
--
--   sector_observation    one row per CHANGE of a symbol's effective sector, per vendor (the chain key is (symbol, source)). A -> B -> A is
--                         three rows; re-seeing an unchanged sector writes NO row (that is `sector_poll`'s job). An explicit vendor "no
--                         sector" answer is a row too (sector IS NULL, no_sector_reason set), so "the vendor said none" is distinguishable from
--                         "we never asked" and from "the request failed". Rows form a hash chain: seq 1, 2, 3 ... each naming the value_hash of
--                         its predecessor. The hash is COMPUTED BY THE DATABASE from the row's own content, never chosen by the caller.
--   sector_poll           one row per refresh attempt (run_id, symbol, source): the vendor's answer or the failure, and what it did to the chain
--                         (created an observation, confirmed the current head, or nothing). The only way to say "this classification was still
--                         current at T" without mutating anything. Failed attempts are recorded and never extend freshness.
--   sector_reconstruction a SEPARATE store for any classification projected backwards from a later map (e.g. a daily_fundamentals import).
--                         It shares no key, no FK and no timestamp semantics with sector_observation and is never read by the forward path:
--                         reconstructed history is structurally unable to satisfy forward-observed coverage.
--
-- Time, kept apart:
--   1. source_asof     the vendor's OWN claimed validity time. NULL unless the vendor really supplied one (yfinance / Tiingo current-
--                      classification paths supply none and must not have one invented). Audit only; never selected on.
--   2. captured_at     when OUR system learned it: stamped by a BEFORE INSERT trigger with clock_timestamp() AFTER the chain lock is held, so
--                      captured_at is monotone along a chain and a caller can neither back-date nor forward-date it. THE availability time.
--   3. effective_session  the UTC calendar date of captured_at (stamped, CHECK-pinned). An index / audit convenience that names the earliest
--                      session on which the observation may legally be consumed. It is NOT the PIT grace rule: the readers apply
--                      is_known() AND effective_session <= decision session (see sector_history.py).
-- There is no backfill path: nothing can be inserted with a captured_at in the past.
--
-- Immutability, two layers: privileges (runtime role SELECT + INSERT only; research_roles.sql) and ENABLE ALWAYS BEFORE UPDATE / DELETE row +
-- BEFORE TRUNCATE statement triggers that ALWAYS raise. The chain is integrity EVIDENCE, not a substitute for permissions. No maintenance
-- hatch: a correction is a new observation; a wrong row is superseded, never edited.
--
-- Concurrency. The insert triggers serialise per (symbol, source) with pg_advisory_xact_lock, so two writers refreshing the same symbol are
-- ordered, the loser re-reads the head and (for an unchanged value) records only a confirm poll. A fork cannot be inserted: seq is UNIQUE and the
-- trigger demands seq = head + 1 and prev_value_hash = head.value_hash under the lock.
--
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_sector_history_tables.sql
-- NOT applied to production by committing this file; applying it activates nothing (the recorder is behind a flag that is OFF by default and no
-- collector is scheduled). Re-run deploy/db/research_roles.sql (+ the verifier) afterwards: all three tables are append-only and are excluded from
-- the full-DML baseline.
--
-- Collision safety. Every object carries the marker comment 'sector_history_migration_31'; section 0 refuses (changes nothing) when an object
-- with one of our names exists without it.

-- ---------------------------------------------------------------------------------------------------
-- 0. Collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'sector_history_migration_31';
    t TEXT;
    f TEXT;
    ix RECORD;
BEGIN
    FOREACH t IN ARRAY ARRAY['sector_observation', 'sector_poll', 'sector_reconstruction'] LOOP
        IF to_regclass(t) IS NOT NULL AND coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
            RAISE EXCEPTION 'migration 31 refused: relation % already exists and is not a migration-31 object (no marker)', t;
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_sector_guard()', 'research_sector_obs_stamp()', 'research_sector_poll_stamp()',
                             'research_sector_recon_stamp()', 'research_sector_enc(text)',
                             'research_sector_row_hash(text,text,integer,text,text,text,timestamptz,text,text,text)'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 31 refused: function % already exists and is not a migration-31 object (no marker)', f;
        END IF;
    END LOOP;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_sector_observation_asof',    'sector_observation'),
            ('idx_sector_observation_session', 'sector_observation'),
            ('idx_sector_poll_asof',           'sector_poll'),
            ('idx_sector_poll_obs',            'sector_poll'),
            ('idx_sector_reconstruction_sym',  'sector_reconstruction')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 31 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. Tables.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sector_observation (
    id                BIGSERIAL     PRIMARY KEY,
    symbol            VARCHAR(20)   NOT NULL,
    source            VARCHAR(40)   NOT NULL,            -- the vendor that answered (yfinance | tiingo | ...): a chain is per (symbol, source)
    seq               INTEGER       NOT NULL,            -- 1-based position in the (symbol, source) chain; validated by the trigger
    sector            VARCHAR(100),                      -- the CLEANED canonical sector; NULL exactly when the vendor explicitly said "no sector"
    sector_raw        VARCHAR(200),                      -- the vendor's string verbatim (audit); may be NULL when the vendor sent nothing
    no_sector_reason  VARCHAR(30),                       -- set exactly when sector IS NULL: vendor_null | vendor_blank | vendor_unknown_label
    change_kind       VARCHAR(12)   NOT NULL,            -- first | changed | became_none | became_set  (validated by the trigger against the head)
    captured_at       TIMESTAMPTZ   NOT NULL,            -- DB-stamped, AFTER the chain lock: the moment OUR system learned it (availability)
    effective_session DATE          NOT NULL,            -- DB-stamped: the UTC date of captured_at. NOT the PIT grace rule (see header)
    source_asof       TIMESTAMPTZ,                       -- the vendor's own claimed validity time; NULL unless truly supplied; never selected on
    provenance        VARCHAR(20)   NOT NULL,            -- always observed_forward; reconstruction lives in sector_reconstruction
    raw_payload_hash  CHAR(64)      NOT NULL,            -- sha256 of the canonical projection of the vendor response the sector was read from
    raw_payload       JSONB,                             -- optional minimal projection (never a full vendor payload)
    run_id            VARCHAR(80)   NOT NULL,            -- the refresh run that created it
    writer            VARCHAR(80)   NOT NULL,            -- the authoritative writer's identity
    code_ref          VARCHAR(160)  NOT NULL,
    prev_value_hash   CHAR(64),                          -- value_hash of seq-1; NULL exactly when seq = 1
    value_hash        CHAR(64)      NOT NULL,            -- COMPUTED BY THE TRIGGER (research_sector_row_hash); the caller cannot choose it
    CONSTRAINT sector_observation_chain UNIQUE (symbol, source, seq),
    CONSTRAINT sector_observation_value_unique UNIQUE (symbol, source, value_hash),
    CONSTRAINT sector_observation_seq_chk CHECK (seq >= 1),
    CONSTRAINT sector_observation_prev_chk CHECK ((seq = 1) = (prev_value_hash IS NULL)),
    CONSTRAINT sector_observation_kind_chk CHECK (change_kind IN ('first', 'changed', 'became_none', 'became_set')
                                                  AND ((change_kind = 'first') = (seq = 1))),
    CONSTRAINT sector_observation_none_chk CHECK ((sector IS NOT NULL) <> (no_sector_reason IS NOT NULL)),
    CONSTRAINT sector_observation_reason_chk CHECK (no_sector_reason IS NULL
                                                    OR no_sector_reason IN ('vendor_null', 'vendor_blank', 'vendor_unknown_label')),
    CONSTRAINT sector_observation_clean_chk CHECK (sector IS NULL
                                                   OR (sector = btrim(sector) AND length(sector) > 0 AND lower(sector) <> 'unknown')),
    CONSTRAINT sector_observation_kind_value_chk CHECK ((change_kind NOT IN ('changed', 'became_set') OR sector IS NOT NULL)
                                                        AND (change_kind <> 'became_none' OR sector IS NULL)),
    CONSTRAINT sector_observation_provenance_chk CHECK (provenance = 'observed_forward'),
    CONSTRAINT sector_observation_session_chk CHECK (effective_session = (captured_at AT TIME ZONE 'UTC')::date),
    CONSTRAINT sector_observation_text_chk CHECK (length(symbol) > 0 AND length(source) > 0 AND length(run_id) > 0
                                                  AND length(writer) > 0 AND length(code_ref) > 0),
    CONSTRAINT sector_observation_hash_chk CHECK (value_hash ~ '^[0-9a-f]{64}$' AND raw_payload_hash ~ '^[0-9a-f]{64}$'
                                                  AND (prev_value_hash IS NULL OR prev_value_hash ~ '^[0-9a-f]{64}$')),
    CONSTRAINT sector_observation_payload_chk CHECK (raw_payload IS NULL OR jsonb_typeof(raw_payload) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_sector_observation_asof    ON sector_observation (symbol, source, captured_at);
CREATE INDEX IF NOT EXISTS idx_sector_observation_session ON sector_observation (effective_session);

CREATE TABLE IF NOT EXISTS sector_poll (
    id                BIGSERIAL     PRIMARY KEY,
    run_id            VARCHAR(80)   NOT NULL,
    symbol            VARCHAR(20)   NOT NULL,
    source            VARCHAR(40)   NOT NULL,
    response_state    VARCHAR(16)   NOT NULL,            -- sector | no_sector | request_failed | invalid_response   (what the vendor interaction was)
    chain_effect      VARCHAR(20)   NOT NULL,            -- created_observation | confirmed_head | none               (what it did to the chain)
    response_sector   VARCHAR(100),                      -- the cleaned sector answered (NULL for no_sector and for the failed states)
    no_sector_reason  VARCHAR(30),                       -- set exactly for response_state = no_sector
    failure_reason    VARCHAR(40),                       -- coded; set exactly for the failed states (timeout, http_error, empty_response, ...)
    raw_payload_hash  CHAR(64),                          -- sha256 of the canonical response projection; NULL when there was no response
    observation_id    BIGINT        REFERENCES sector_observation (id),   -- the row created / confirmed; NULL exactly when chain_effect = none
    attempted_at      TIMESTAMPTZ   NOT NULL,            -- DB-stamped
    writer            VARCHAR(80)   NOT NULL,
    code_ref          VARCHAR(160)  NOT NULL,
    CONSTRAINT sector_poll_key UNIQUE (run_id, symbol, source),
    CONSTRAINT sector_poll_state_chk CHECK (response_state IN ('sector', 'no_sector', 'request_failed', 'invalid_response')),
    CONSTRAINT sector_poll_effect_chk CHECK (chain_effect IN ('created_observation', 'confirmed_head', 'none')),
    CONSTRAINT sector_poll_obs_chk CHECK ((chain_effect <> 'none') = (observation_id IS NOT NULL)),
    CONSTRAINT sector_poll_sector_chk CHECK (response_state <> 'sector'
                                             OR (response_sector IS NOT NULL AND no_sector_reason IS NULL AND failure_reason IS NULL
                                                 AND chain_effect <> 'none' AND raw_payload_hash IS NOT NULL)),
    CONSTRAINT sector_poll_nosector_chk CHECK (response_state <> 'no_sector'
                                               OR (response_sector IS NULL AND no_sector_reason IS NOT NULL AND failure_reason IS NULL
                                                   AND chain_effect <> 'none' AND raw_payload_hash IS NOT NULL)),
    CONSTRAINT sector_poll_failed_chk CHECK (response_state NOT IN ('request_failed', 'invalid_response')
                                             OR (response_sector IS NULL AND no_sector_reason IS NULL AND failure_reason IS NOT NULL
                                                 AND chain_effect = 'none')),
    CONSTRAINT sector_poll_reason_chk CHECK (no_sector_reason IS NULL
                                             OR no_sector_reason IN ('vendor_null', 'vendor_blank', 'vendor_unknown_label')),
    CONSTRAINT sector_poll_clean_chk CHECK (response_sector IS NULL
                                            OR (response_sector = btrim(response_sector) AND length(response_sector) > 0
                                                AND lower(response_sector) <> 'unknown')),
    CONSTRAINT sector_poll_failure_fmt_chk CHECK (failure_reason IS NULL OR failure_reason ~ '^[a-z0-9_]{1,40}$'),
    CONSTRAINT sector_poll_hash_chk CHECK (raw_payload_hash IS NULL OR raw_payload_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT sector_poll_text_chk CHECK (length(run_id) > 0 AND length(symbol) > 0 AND length(source) > 0
                                           AND length(writer) > 0 AND length(code_ref) > 0)
);
CREATE INDEX IF NOT EXISTS idx_sector_poll_asof ON sector_poll (symbol, source, attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_sector_poll_obs  ON sector_poll (observation_id) WHERE observation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sector_reconstruction (
    id                 BIGSERIAL    PRIMARY KEY,
    symbol             VARCHAR(20)  NOT NULL,
    sector             VARCHAR(100),                     -- the projected classification; NULL = the source row carried none
    reconstructed_from VARCHAR(80)  NOT NULL,            -- e.g. daily_fundamentals.sector (a later vendor map projected backwards)
    row_date           DATE         NOT NULL,            -- the historical date the projection claims to describe
    method             VARCHAR(80)  NOT NULL,
    import_batch       VARCHAR(80)  NOT NULL,
    provenance         VARCHAR(20)  NOT NULL,            -- always 'reconstructed'
    imported_at        TIMESTAMPTZ  NOT NULL,            -- DB-stamped import time (when we wrote the projection, not when the sector was known)
    code_ref           VARCHAR(160) NOT NULL,
    CONSTRAINT sector_reconstruction_key UNIQUE (symbol, row_date, reconstructed_from, import_batch),
    CONSTRAINT sector_reconstruction_provenance_chk CHECK (provenance = 'reconstructed'),
    CONSTRAINT sector_reconstruction_text_chk CHECK (length(symbol) > 0 AND length(reconstructed_from) > 0 AND length(method) > 0
                                                     AND length(import_batch) > 0 AND length(code_ref) > 0)
);
CREATE INDEX IF NOT EXISTS idx_sector_reconstruction_sym ON sector_reconstruction (symbol, row_date);

-- ---------------------------------------------------------------------------------------------------
-- 2. Markers and functions (created only when absent).
-- ---------------------------------------------------------------------------------------------------
COMMENT ON TABLE sector_observation    IS 'sector_history_migration_31';
COMMENT ON TABLE sector_poll           IS 'sector_history_migration_31';
COMMENT ON TABLE sector_reconstruction IS 'sector_history_migration_31';

DO $mk$
BEGIN
    IF to_regprocedure('research_sector_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_sector_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (sector history is immutable; a correction is a new observation)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_sector_guard() IS 'sector_history_migration_31';
        REVOKE ALL ON FUNCTION research_sector_guard() FROM PUBLIC;
    END IF;

    -- canonical, unambiguous field encoding: NULL -> '-;', otherwise <char length>:<text>;  (no delimiter can be forged by a field's content)
    IF to_regprocedure('research_sector_enc(text)') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_sector_enc(p TEXT) RETURNS TEXT LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE WHEN p IS NULL THEN '-;' ELSE length(p)::text || ':' || p || ';' END
$fn$ $f$;
        COMMENT ON FUNCTION research_sector_enc(text) IS 'sector_history_migration_31';
        REVOKE ALL ON FUNCTION research_sector_enc(text) FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_sector_row_hash(text,text,integer,text,text,text,timestamptz,text,text,text)') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_sector_row_hash(p_symbol TEXT, p_source TEXT, p_seq INTEGER, p_sector TEXT, p_reason TEXT, p_raw TEXT,
                                         p_captured TIMESTAMPTZ, p_provenance TEXT, p_payload_hash TEXT, p_prev TEXT)
RETURNS CHAR(64) LANGUAGE sql IMMUTABLE AS $fn$
    SELECT encode(sha256(convert_to(
        'sector_obs_v1;' ||
        research_sector_enc(p_symbol) || research_sector_enc(p_source) || research_sector_enc(p_seq::text) ||
        research_sector_enc(p_sector) || research_sector_enc(p_reason) || research_sector_enc(p_raw) ||
        research_sector_enc(to_char(p_captured AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')) ||
        research_sector_enc(p_provenance) || research_sector_enc(p_payload_hash) || research_sector_enc(p_prev),
        'UTF8')), 'hex')
$fn$ $f$;
        COMMENT ON FUNCTION research_sector_row_hash(text,text,integer,text,text,text,timestamptz,text,text,text) IS 'sector_history_migration_31';
        REVOKE ALL ON FUNCTION research_sector_row_hash(text,text,integer,text,text,text,timestamptz,text,text,text) FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_sector_obs_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_sector_obs_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    head_seq   INTEGER;
    head_hash  CHAR(64);
    head_sec   VARCHAR(100);
    head_at    TIMESTAMPTZ;
    found_head BOOLEAN;
    kind       TEXT;
BEGIN
    -- serialise every writer of this (symbol, source) chain; held until the writer's transaction ends
    PERFORM pg_advisory_xact_lock(hashtextextended('sector_history|' || coalesce(NEW.symbol, '') || '|' || coalesce(NEW.source, ''), 0));
    -- the learned-at time is the database's, taken AFTER the lock so it is monotone along the chain; never the caller's
    NEW.captured_at := clock_timestamp();
    NEW.effective_session := (NEW.captured_at AT TIME ZONE 'UTC')::date;
    SELECT seq, value_hash, sector, captured_at INTO head_seq, head_hash, head_sec, head_at
    FROM sector_observation WHERE symbol = NEW.symbol AND source = NEW.source ORDER BY seq DESC LIMIT 1;
    found_head := FOUND;
    IF NOT found_head THEN
        IF NEW.seq IS DISTINCT FROM 1 THEN
            RAISE EXCEPTION 'sector_observation refused: (%, %) has no observations, so seq must be 1 (got %)', NEW.symbol, NEW.source, NEW.seq
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        kind := 'first';
    ELSE
        IF NEW.seq IS DISTINCT FROM head_seq + 1 THEN
            RAISE EXCEPTION 'sector_observation refused: (%, %) is at seq %, so the next must be % (got %)',
                NEW.symbol, NEW.source, head_seq, head_seq + 1, NEW.seq USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.prev_value_hash IS DISTINCT FROM head_hash THEN
            RAISE EXCEPTION 'sector_observation refused: prev_value_hash does not match the stored predecessor of (%, %)', NEW.symbol, NEW.source
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.captured_at < head_at THEN
            RAISE EXCEPTION 'sector_observation refused: captured_at would move backwards on (%, %)', NEW.symbol, NEW.source
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.sector IS NOT DISTINCT FROM head_sec THEN
            RAISE EXCEPTION 'sector_observation refused: (%, %) already has this effective sector as its head; record a confirm poll instead of a duplicate observation',
                NEW.symbol, NEW.source USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        kind := CASE WHEN head_sec IS NULL THEN 'became_set' WHEN NEW.sector IS NULL THEN 'became_none' ELSE 'changed' END;
    END IF;
    IF NEW.change_kind IS DISTINCT FROM kind THEN
        RAISE EXCEPTION 'sector_observation refused: change_kind % does not match the chain (expected %)', NEW.change_kind, kind
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    NEW.value_hash := research_sector_row_hash(NEW.symbol, NEW.source, NEW.seq, NEW.sector, NEW.no_sector_reason, NEW.sector_raw,
                                               NEW.captured_at, NEW.provenance, NEW.raw_payload_hash, NEW.prev_value_hash);
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_sector_obs_stamp() IS 'sector_history_migration_31';
        REVOKE ALL ON FUNCTION research_sector_obs_stamp() FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_sector_poll_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_sector_poll_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    o_symbol VARCHAR(20);
    o_source VARCHAR(40);
    o_seq    INTEGER;
    o_sector VARCHAR(100);
    o_run    VARCHAR(80);
    head_seq INTEGER;
BEGIN
    NEW.attempted_at := clock_timestamp();
    IF NEW.observation_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('sector_history|' || coalesce(NEW.symbol, '') || '|' || coalesce(NEW.source, ''), 0));
        SELECT symbol, source, seq, sector, run_id INTO o_symbol, o_source, o_seq, o_sector, o_run
        FROM sector_observation WHERE id = NEW.observation_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'sector_poll refused: observation % does not exist', NEW.observation_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF o_symbol IS DISTINCT FROM NEW.symbol OR o_source IS DISTINCT FROM NEW.source THEN
            RAISE EXCEPTION 'sector_poll refused: observation % belongs to a different (symbol, source)', NEW.observation_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        SELECT max(seq) INTO head_seq FROM sector_observation WHERE symbol = NEW.symbol AND source = NEW.source;
        IF o_seq IS DISTINCT FROM head_seq THEN
            RAISE EXCEPTION 'sector_poll refused: observation % (seq %) is not the current head (seq %) of (%, %)',
                NEW.observation_id, o_seq, head_seq, NEW.symbol, NEW.source USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF o_sector IS DISTINCT FROM NEW.response_sector THEN
            RAISE EXCEPTION 'sector_poll refused: the poll answered % but observation % holds %', NEW.response_sector, NEW.observation_id, o_sector
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.chain_effect = 'created_observation' AND o_run IS DISTINCT FROM NEW.run_id THEN
            RAISE EXCEPTION 'sector_poll refused: observation % was created by run %, not %', NEW.observation_id, o_run, NEW.run_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.chain_effect = 'confirmed_head' AND o_run IS NOT DISTINCT FROM NEW.run_id THEN
            RAISE EXCEPTION 'sector_poll refused: a run cannot confirm the observation it created itself' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_sector_poll_stamp() IS 'sector_history_migration_31';
        REVOKE ALL ON FUNCTION research_sector_poll_stamp() FROM PUBLIC;
    END IF;

    IF to_regprocedure('research_sector_recon_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_sector_recon_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.imported_at := clock_timestamp();
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_sector_recon_stamp() IS 'sector_history_migration_31';
        REVOKE ALL ON FUNCTION research_sector_recon_stamp() FROM PUBLIC;
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
        ('sector_observation',    'sector_observation_immutable_row',        'BEFORE UPDATE OR DELETE', 'ROW',       'research_sector_guard'),
        ('sector_observation',    'sector_observation_immutable_truncate',   'BEFORE TRUNCATE',         'STATEMENT', 'research_sector_guard'),
        ('sector_observation',    'sector_observation_stamp',                'BEFORE INSERT',           'ROW',       'research_sector_obs_stamp'),
        ('sector_poll',           'sector_poll_immutable_row',               'BEFORE UPDATE OR DELETE', 'ROW',       'research_sector_guard'),
        ('sector_poll',           'sector_poll_immutable_truncate',          'BEFORE TRUNCATE',         'STATEMENT', 'research_sector_guard'),
        ('sector_poll',           'sector_poll_stamp',                       'BEFORE INSERT',           'ROW',       'research_sector_poll_stamp'),
        ('sector_reconstruction', 'sector_reconstruction_immutable_row',     'BEFORE UPDATE OR DELETE', 'ROW',       'research_sector_guard'),
        ('sector_reconstruction', 'sector_reconstruction_immutable_truncate','BEFORE TRUNCATE',         'STATEMENT', 'research_sector_guard'),
        ('sector_reconstruction', 'sector_reconstruction_stamp',             'BEFORE INSERT',           'ROW',       'research_sector_recon_stamp')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 31 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
