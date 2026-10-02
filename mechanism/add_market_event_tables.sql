-- mechanism/add_market_event_tables.sql   (migration 25 -- append-only earnings / catalyst EVENT MODEL)
--
-- A foundation only: the schema and the rules a future collector must obey. No collector exists, no vendor is chosen, and nothing
-- reads this table in production. The existing `earnings_calendar` is untouched and is NOT backfilled into here (its first-seen
-- time was never recorded, so it cannot prove when anything became known).
--
--   market_event           the immutable identity of one real-world event (what it is, which symbol; NULL symbol = market-wide)
--   market_event_revision  append-only: every state the event was ever seen in, with WHEN it became known
--
-- Point-in-time (PIT) rules the schema itself enforces:
--   * known_at is when the information was available TO US OR PUBLICLY, with an explicit basis:
--       ingested          we recorded it ourselves; known_at = ingested_at (set by the database, not the caller)
--       vendor_published  the source supplied a publication time, kept in published_at; known_at = published_at
--       unknown           no provable time. known_at is NULL -- it is never guessed, back-dated or defaulted.
--   * ingested_at is stamped by a BEFORE INSERT trigger with the database clock; a caller cannot back-date it.
--   * known_at can never be later than ingested_at (information cannot become known after we stored it).
--   * pit_grade X  <=>  known_at_basis = 'unknown'. An event with grade X is NOT PIT-eligible and must not feed any model
--     (the read helper mechanism/market_intelligence/events.py refuses it; there is no override).
--   * Estimates and actuals are separate columns; an actual may only exist on a reported/revised status. Surprise is DERIVED
--     at read time from (actual, estimate) and is never stored.
--   * Revisions are append-only: an event is revised by inserting revision n+1, never by editing revision n.
--   * provenance observed|reconstructed (D3); reconstructed rows must name their basis and are excluded from default reads.
--
-- Requires migration 24 (research_market_guard()). APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_market_event_tables.sql
-- NOT applied to production by committing this file.

-- ---------------------------------------------------------------------------------------------------
-- 0. Collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'market_intelligence_migration_25';
    n_present INTEGER := 0;
    t TEXT;
BEGIN
    IF to_regprocedure('research_market_guard()') IS NULL THEN
        RAISE EXCEPTION 'migration 25 requires migration 24 (research_market_guard() is missing)';
    END IF;
    FOREACH t IN ARRAY ARRAY['market_event', 'market_event_revision'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            n_present := n_present + 1;
            IF coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
                RAISE EXCEPTION 'migration 25 refused: relation % already exists and is not a migration-25 object (no marker)', t;
            END IF;
        END IF;
    END LOOP;
    IF n_present NOT IN (0, 2) THEN
        RAISE EXCEPTION 'migration 25 refused: % of 2 market event tables exist (partially applied); investigate, do not re-run', n_present;
    END IF;
    IF to_regprocedure('research_market_event_stamp()') IS NOT NULL
       AND coalesce(obj_description(to_regprocedure('research_market_event_stamp()'), 'pg_proc'), '') <> marker THEN
        RAISE EXCEPTION 'migration 25 refused: function research_market_event_stamp() already exists and is not a migration-25 object (no marker)';
    END IF;
    IF to_regclass('idx_market_event_revision_key') IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass('idx_market_event_revision_key')
             AND i.indrelid = to_regclass('market_event_revision')) THEN
        RAISE EXCEPTION 'migration 25 refused: index idx_market_event_revision_key already exists on a different relation';
    END IF;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. Identity. event_key is chosen by the normaliser and is stable across revisions of the same real-world event.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_event (
    event_id    BIGSERIAL    PRIMARY KEY,
    event_key   VARCHAR(200) NOT NULL,
    symbol      VARCHAR(20),
    event_type  VARCHAR(24)  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT market_event_key UNIQUE (event_key),
    CONSTRAINT market_event_key_nonempty_chk CHECK (length(event_key) > 0),
    CONSTRAINT market_event_symbol_chk CHECK (symbol IS NULL OR length(symbol) > 0),
    CONSTRAINT market_event_type_chk CHECK (event_type IN (
        'earnings_scheduled', 'earnings_reported', 'estimate_eps', 'estimate_revenue', 'guidance', 'analyst_action',
        'price_target', 'corporate_event', 'regulatory', 'mna', 'dividend', 'split', 'news'))
);

-- ---------------------------------------------------------------------------------------------------
-- 2. Revisions (append-only).
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_event_revision (
    id                    BIGSERIAL    PRIMARY KEY,
    event_key             VARCHAR(200) NOT NULL REFERENCES market_event (event_key),
    revision              INTEGER      NOT NULL,
    event_time            DATE         NOT NULL,                 -- the date the event happens / happened
    session_timing        VARCHAR(10)  NOT NULL DEFAULT 'UNKNOWN',  -- BMO / AMC / INTRADAY / UNKNOWN
    published_at          TIMESTAMPTZ,                           -- the source's own publication time, when it supplies one
    known_at              TIMESTAMPTZ,                           -- NULL when unprovable -- never fabricated
    known_at_basis        VARCHAR(16)  NOT NULL,
    ingested_at           TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),   -- stamped by trigger; not caller-controlled
    source                VARCHAR(40)  NOT NULL,
    source_ref            VARCHAR(160) NOT NULL,                 -- the source's own event / record id
    pit_grade             CHAR(1)      NOT NULL,                 -- A proven vendor time + revisions kept | B our ingestion time | C vendor time, may revise in place | X unverified
    status                VARCHAR(10)  NOT NULL,
    fiscal_period         VARCHAR(16),
    eps_estimate          NUMERIC(14,4),
    eps_actual            NUMERIC(14,4),
    revenue_estimate      NUMERIC(20,2),
    revenue_actual        NUMERIC(20,2),
    payload               JSONB        NOT NULL DEFAULT '{}'::jsonb,   -- guidance text, metadata, anything else the source carried
    payload_hash          CHAR(64)     NOT NULL,
    provenance            VARCHAR(13)  NOT NULL,
    reconstruction_basis  TEXT,
    CONSTRAINT market_event_revision_key UNIQUE (event_key, revision),
    CONSTRAINT market_event_revision_n_chk CHECK (revision >= 1),
    CONSTRAINT market_event_revision_timing_chk CHECK (session_timing IN ('BMO', 'AMC', 'INTRADAY', 'UNKNOWN')),
    CONSTRAINT market_event_revision_basis_chk CHECK (known_at_basis IN ('ingested', 'vendor_published', 'unknown')),
    CONSTRAINT market_event_revision_known_chk CHECK ((known_at_basis = 'unknown') = (known_at IS NULL)),
    CONSTRAINT market_event_revision_vendor_chk CHECK (known_at_basis <> 'vendor_published' OR (published_at IS NOT NULL AND known_at = published_at)),
    CONSTRAINT market_event_revision_order_chk CHECK (known_at IS NULL OR known_at <= ingested_at),
    CONSTRAINT market_event_revision_grade_chk CHECK (pit_grade IN ('A', 'B', 'C', 'X')),
    CONSTRAINT market_event_revision_grade_known_chk CHECK ((pit_grade = 'X') = (known_at_basis = 'unknown')),
    CONSTRAINT market_event_revision_status_chk CHECK (status IN ('scheduled', 'confirmed', 'reported', 'revised', 'cancelled')),
    CONSTRAINT market_event_revision_actuals_chk CHECK (
        (eps_actual IS NULL AND revenue_actual IS NULL) OR status IN ('reported', 'revised')),
    CONSTRAINT market_event_revision_provenance_chk CHECK (provenance IN ('observed', 'reconstructed')),
    CONSTRAINT market_event_revision_reconstruction_chk CHECK ((provenance = 'reconstructed') = (reconstruction_basis IS NOT NULL)),
    CONSTRAINT market_event_revision_source_chk CHECK (length(source) > 0 AND length(source_ref) > 0)
);
CREATE INDEX IF NOT EXISTS idx_market_event_revision_key ON market_event_revision (event_key, revision DESC);

COMMENT ON TABLE market_event          IS 'market_intelligence_migration_25';
COMMENT ON TABLE market_event_revision IS 'market_intelligence_migration_25';

-- ---------------------------------------------------------------------------------------------------
-- 3. The ingestion stamp: the database, not the caller, decides ingested_at, and for basis 'ingested' known_at equals it.
-- ---------------------------------------------------------------------------------------------------
DO $mk$
BEGIN
    IF to_regprocedure('research_market_event_stamp()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_market_event_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.ingested_at := clock_timestamp();
    IF NEW.known_at_basis = 'ingested' THEN
        NEW.known_at := NEW.ingested_at;
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_market_event_stamp() IS 'market_intelligence_migration_25';
        REVOKE ALL ON FUNCTION research_market_event_stamp() FROM PUBLIC;
    END IF;
END;
$mk$;

-- ---------------------------------------------------------------------------------------------------
-- 4. Triggers: the insert stamp, plus the always-raising append-only guard from migration 24. An existing trigger must already be
--    right (function, ENABLE ALWAYS) or the migration refuses.
-- ---------------------------------------------------------------------------------------------------
DO $trg$
DECLARE
    r RECORD;
    cur_mode "char";
    cur_fn   TEXT;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('market_event',          'market_event_immutable_row',             'BEFORE UPDATE OR DELETE', 'ROW',       'research_market_guard'),
        ('market_event',          'market_event_immutable_truncate',        'BEFORE TRUNCATE',         'STATEMENT', 'research_market_guard'),
        ('market_event_revision', 'market_event_revision_immutable_row',    'BEFORE UPDATE OR DELETE', 'ROW',       'research_market_guard'),
        ('market_event_revision', 'market_event_revision_immutable_truncate', 'BEFORE TRUNCATE',       'STATEMENT', 'research_market_guard'),
        ('market_event_revision', 'market_event_revision_stamp',            'BEFORE INSERT',           'ROW',       'research_market_event_stamp')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 25 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
