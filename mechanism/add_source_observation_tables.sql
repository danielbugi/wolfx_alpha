-- mechanism/add_source_observation_tables.sql   (migration 27 -- immutable first-seen observation log)
--
-- WHAT FIRST LIGHT KNEW, WHEN IT FIRST KNEW IT, AND EVERY LATER REVISION -- for mutable external inputs (earnings calendar dates,
-- EPS / revenue estimates, estimate revisions, guidance observations ...). Provider-neutral: `source` and `dataset` are free text chosen
-- by an adapter; no vendor, field name or enum is encoded here. Independent of migrations 24 / 25 / 26 (requires none of them).
--
--   source_observation  one row per CHANGE of a series. A series is (source, dataset, subject, period). Re-seeing an unchanged value
--                       writes NO row (that is `source_poll`'s job). Rows form a hash chain: seq 1, 2, 3 ... where each row names the
--                       value_hash of its predecessor, so a gap, a fork or a reorder cannot be inserted. A -> B -> A is three rows.
--   source_poll         one row per collection run per (source, dataset): what was asked for, how it ended, which subjects were covered.
--                       The only way to say "this series was still current at T" without mutating anything.
--
-- Time. `observed_at` / `polled_at` are stamped by a BEFORE INSERT trigger with clock_timestamp(); a caller cannot choose, back-date or
-- forward-date them. That is the whole PIT guarantee of this table: AVAILABILITY = observed_at (pit grade B, "our ingestion time"). The
-- provider's own claimed time, when it supplies one, is stored as `source_asof` for audit only and is NEVER used as availability.
-- There is no backfill path: nothing can be inserted with an observed_at in the past, so reconstructed history cannot masquerade as
-- observed history. History that predates this table is simply absent (and absence is not zero).
--
-- Immutability, two layers (as migrations 24 / 26): privileges (runtime role SELECT + INSERT only) and ENABLE ALWAYS BEFORE UPDATE /
-- DELETE row + BEFORE TRUNCATE statement triggers that ALWAYS raise. No maintenance hatch: a correction is a new observation.
--
-- APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_source_observation_tables.sql
-- NOT applied to production by committing this file; applying it activates nothing (no collector is scheduled). Re-run
-- deploy/db/research_roles.sql (+ the verifier) afterwards: both tables are append-only and are excluded from the full-DML baseline.
--
-- Collision safety. Every object carries the marker comment 'source_observation_migration_27'; section 0 refuses (changes nothing) when
-- an object with one of our names exists without it.

-- ---------------------------------------------------------------------------------------------------
-- 0. Collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'source_observation_migration_27';
    t TEXT;
    f TEXT;
    ix RECORD;
BEGIN
    FOREACH t IN ARRAY ARRAY['source_observation', 'source_poll'] LOOP
        IF to_regclass(t) IS NOT NULL AND coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
            RAISE EXCEPTION 'migration 27 refused: relation % already exists and is not a migration-27 object (no marker)', t;
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_observation_guard()', 'research_observation_stamp()', 'research_poll_stamp()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 27 refused: function % already exists and is not a migration-27 object (no marker)', f;
        END IF;
    END LOOP;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_source_observation_asof',  'source_observation'),
            ('idx_source_observation_subject', 'source_observation'),
            ('idx_source_poll_asof',         'source_poll')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 27 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. Tables.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_observation (
    id               BIGSERIAL     PRIMARY KEY,
    series_key       VARCHAR(300)  NOT NULL,            -- canonical text built by market_intelligence.first_seen.series_key(); components below
    source           VARCHAR(60)   NOT NULL,            -- adapter name (free text, provider-neutral)
    dataset          VARCHAR(60)   NOT NULL,            -- what the value is: earnings_date, eps_estimate, revenue_estimate, guidance, ...
    subject_type     VARCHAR(12)   NOT NULL,            -- symbol | cik
    subject_id       VARCHAR(40)   NOT NULL,
    period_key       VARCHAR(40),                       -- fiscal period end / quarter label as the adapter normalises it; NULL = not period-scoped
    seq              INTEGER       NOT NULL,            -- 1-based position in the series' chain
    prev_value_hash  CHAR(64),                          -- value_hash of seq-1; NULL exactly when seq = 1 (checked by trigger against the real predecessor)
    value            JSONB         NOT NULL,            -- the source's value, verbatim (no derivation: surprise etc. are computed at read time)
    value_hash       CHAR(64)      NOT NULL,            -- sha256 of the canonical JSON of `value` (computed by the writer; recomputable by verify_chain)
    source_asof      TIMESTAMPTZ,                       -- the provider's OWN claimed publication / as-of time, audit only, never availability
    observed_at      TIMESTAMPTZ   NOT NULL,            -- DB-stamped first-seen time; THE availability timestamp (pit grade B)
    run_key          VARCHAR(80)   NOT NULL,            -- the collection run that saw it
    code_ref         VARCHAR(160)  NOT NULL,
    CONSTRAINT source_observation_chain UNIQUE (series_key, seq),
    CONSTRAINT source_observation_seq_chk CHECK (seq >= 1),
    CONSTRAINT source_observation_prev_chk CHECK ((seq = 1) = (prev_value_hash IS NULL)),
    CONSTRAINT source_observation_change_chk CHECK (prev_value_hash IS NULL OR prev_value_hash <> value_hash),
    CONSTRAINT source_observation_subject_chk CHECK (subject_type IN ('symbol', 'cik') AND length(subject_id) > 0),
    CONSTRAINT source_observation_text_chk CHECK (length(source) > 0 AND length(dataset) > 0 AND length(series_key) > 0
                                                  AND (period_key IS NULL OR length(period_key) > 0)),
    CONSTRAINT source_observation_hash_chk CHECK (value_hash ~ '^[0-9a-f]{64}$' AND (prev_value_hash IS NULL OR prev_value_hash ~ '^[0-9a-f]{64}$')),
    CONSTRAINT source_observation_value_chk CHECK (jsonb_typeof(value) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_source_observation_asof    ON source_observation (dataset, observed_at);
CREATE INDEX IF NOT EXISTS idx_source_observation_subject ON source_observation (subject_type, subject_id, dataset);

CREATE TABLE IF NOT EXISTS source_poll (
    id                BIGSERIAL     PRIMARY KEY,
    run_key           VARCHAR(80)   NOT NULL,
    source            VARCHAR(60)   NOT NULL,
    dataset           VARCHAR(60)   NOT NULL,
    status            VARCHAR(8)    NOT NULL,            -- complete | partial | failed
    subjects_polled   TEXT[]        NOT NULL,            -- 'symbol:AAPL' ... every subject the run asked the source about AND got an answer for
    n_new_observations INTEGER      NOT NULL,
    detail            JSONB         NOT NULL,            -- errors / warnings the run chose to keep; {} when none
    polled_at         TIMESTAMPTZ   NOT NULL,            -- DB-stamped
    code_ref          VARCHAR(160)  NOT NULL,
    CONSTRAINT source_poll_key UNIQUE (run_key, source, dataset),
    CONSTRAINT source_poll_status_chk CHECK (status IN ('complete', 'partial', 'failed')),
    CONSTRAINT source_poll_count_chk CHECK (n_new_observations >= 0),
    CONSTRAINT source_poll_failed_chk CHECK (status <> 'failed' OR cardinality(subjects_polled) = 0),
    CONSTRAINT source_poll_detail_chk CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_source_poll_asof ON source_poll (source, dataset, polled_at);

-- ---------------------------------------------------------------------------------------------------
-- 2. Markers and functions (created only when absent).
-- ---------------------------------------------------------------------------------------------------
COMMENT ON TABLE source_observation IS 'source_observation_migration_27';
COMMENT ON TABLE source_poll        IS 'source_observation_migration_27';

DO $mk$
BEGIN
    IF to_regprocedure('research_observation_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_observation_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (first-seen observations are immutable; a correction is a new observation)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_observation_guard() IS 'source_observation_migration_27';
        REVOKE ALL ON FUNCTION research_observation_guard() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_observation_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_observation_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    last_seq  INTEGER;
    last_hash CHAR(64);
BEGIN
    -- the first-seen time is the database's, never the caller's
    NEW.observed_at := clock_timestamp();
    -- the chain: seq must be exactly predecessor + 1 and prev_value_hash must be the predecessor's value_hash
    SELECT seq, value_hash INTO last_seq, last_hash FROM source_observation
    WHERE series_key = NEW.series_key ORDER BY seq DESC LIMIT 1;
    IF NOT FOUND THEN
        IF NEW.seq <> 1 THEN
            RAISE EXCEPTION 'source_observation refused: series % has no observations, so seq must be 1 (got %)', NEW.series_key, NEW.seq
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSE
        IF NEW.seq <> last_seq + 1 THEN
            RAISE EXCEPTION 'source_observation refused: series % is at seq %, so the next must be % (got %)',
                NEW.series_key, last_seq, last_seq + 1, NEW.seq USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF NEW.prev_value_hash IS DISTINCT FROM last_hash THEN
            RAISE EXCEPTION 'source_observation refused: prev_value_hash does not match the stored predecessor of series %', NEW.series_key
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_observation_stamp() IS 'source_observation_migration_27';
        REVOKE ALL ON FUNCTION research_observation_stamp() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_poll_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_poll_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.polled_at := clock_timestamp();
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_poll_stamp() IS 'source_observation_migration_27';
        REVOKE ALL ON FUNCTION research_poll_stamp() FROM PUBLIC;
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
        ('source_observation', 'source_observation_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_observation_guard'),
        ('source_observation', 'source_observation_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_observation_guard'),
        ('source_observation', 'source_observation_stamp',              'BEFORE INSERT',           'ROW',       'research_observation_stamp'),
        ('source_poll',        'source_poll_immutable_row',             'BEFORE UPDATE OR DELETE', 'ROW',       'research_observation_guard'),
        ('source_poll',        'source_poll_immutable_truncate',        'BEFORE TRUNCATE',         'STATEMENT', 'research_observation_guard'),
        ('source_poll',        'source_poll_stamp',                     'BEFORE INSERT',           'ROW',       'research_poll_stamp')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 27 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
