-- mechanism/add_catalyst_ingest_tables.sql   (migration 37 -- SEC catalyst ingestion support: source snapshots + identifier links)
--
-- Supports the SEC EDGAR catalyst feed (Phase 1A). Two things the event model (25), classification (28) and first-seen log (27) do not carry:
--
--   source_snapshot     the raw bytes of what the source returned (a submissions document, the ticker file), kept so every fact and every
--                       link can be re-derived and audited. One row per DISTINCT body (UNIQUE on source/kind/subject/sha256): fetching an
--                       unchanged document again stores nothing (the poll log records that it was seen again). first_fetched_at is DB-stamped.
--   identifier_link     CIK -> trading symbol, held OUTSIDE every fact. A link is OBSERVED (we captured the SEC ticker file on a known date and
--                       the mapping holds from that date: valid_from = that date) or RECONSTRUCTED (today's mapping projected backwards:
--                       valid_from NULL). recorded_at is DB-stamped and is the link's AVAILABILITY: a link recorded after a decision time does not
--                       exist for that decision.
--   identifier_link_end an append-only closure: a later ticker file no longer carried the pair. absent_on = the date of the first file that did
--                       not carry it (the true change lies between the last file that did and that date -- recorded, never guessed). recorded_at
--                       is DB-stamped; a closure recorded after a decision time does not exist for that decision.
--
-- A filing's symbol is NEVER stored on the event (market_event.symbol stays NULL for SEC events: "filer-scoped", the CIK is in the payload).
-- The symbol is resolved at READ time from these tables as of the decision cutoff, so a late mapping, a ticker change or a re-used symbol can
-- never rewrite an earlier decision.
--
-- Immutability: ENABLE ALWAYS BEFORE UPDATE / DELETE row + BEFORE TRUNCATE statement triggers that always raise (no maintenance hatch).
-- Requires nothing but PostgreSQL (does not reference migrations 24-28). APPLY ATOMICALLY:   psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_catalyst_ingest_tables.sql
-- NOT applied to production by committing this file; applying it activates nothing. Re-run deploy/db/research_roles.sql (+ verifier) afterwards:
-- all three tables are append-only and excluded from the full-DML baseline.
--
-- Collision safety. Marker 'catalyst_ingest_migration_37'; section 0 refuses (changes nothing) when an object with one of our names exists without it.

-- ---------------------------------------------------------------------------------------------------
-- 0. Collision guard (read-only; raises, never repairs).
-- ---------------------------------------------------------------------------------------------------
DO $guard$
DECLARE
    marker CONSTANT TEXT := 'catalyst_ingest_migration_37';
    t TEXT;
    f TEXT;
    ix RECORD;
BEGIN
    FOREACH t IN ARRAY ARRAY['source_snapshot', 'identifier_link', 'identifier_link_end'] LOOP
        IF to_regclass(t) IS NOT NULL AND coalesce(obj_description(to_regclass(t), 'pg_class'), '') <> marker THEN
            RAISE EXCEPTION 'migration 37 refused: relation % already exists and is not a migration-37 object (no marker)', t;
        END IF;
    END LOOP;
    FOREACH f IN ARRAY ARRAY['research_ingest_guard()', 'research_ingest_stamp()'] LOOP
        IF to_regprocedure(f) IS NOT NULL AND coalesce(obj_description(to_regprocedure(f), 'pg_proc'), '') <> marker THEN
            RAISE EXCEPTION 'migration 37 refused: function % already exists and is not a migration-37 object (no marker)', f;
        END IF;
    END LOOP;
    FOR ix IN SELECT * FROM (VALUES
            ('idx_source_snapshot_subject',  'source_snapshot'),
            ('idx_identifier_link_cik',      'identifier_link'),
            ('idx_identifier_link_identity', 'identifier_link'),
            ('idx_identifier_link_end_link', 'identifier_link_end')) AS v(idx, tbl) LOOP
        IF to_regclass(ix.idx) IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indexrelid = to_regclass(ix.idx) AND i.indrelid = to_regclass(ix.tbl)) THEN
            RAISE EXCEPTION 'migration 37 refused: index % already exists on a different relation', ix.idx;
        END IF;
    END LOOP;
END;
$guard$;

-- ---------------------------------------------------------------------------------------------------
-- 1. Tables.
-- ---------------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_snapshot (
    id               BIGSERIAL     PRIMARY KEY,
    source           VARCHAR(60)   NOT NULL,            -- adapter name, e.g. sec_edgar
    kind             VARCHAR(40)   NOT NULL,            -- submissions | company_tickers
    subject          VARCHAR(40)   NOT NULL,            -- the CIK (digits) for a submissions document; '-' when the document is not subject-scoped
    url              VARCHAR(300)  NOT NULL,
    sha256           CHAR(64)      NOT NULL,            -- of the body exactly as received (before any parsing)
    byte_length      INTEGER       NOT NULL,
    body_gz          BYTEA         NOT NULL,            -- gzip of the received body; sha256 / byte_length describe the UNcompressed bytes
    first_fetched_at TIMESTAMPTZ   NOT NULL,            -- DB-stamped: when this exact body was first stored
    run_key          VARCHAR(80)   NOT NULL,
    code_ref         VARCHAR(160)  NOT NULL,
    CONSTRAINT source_snapshot_body_key UNIQUE (source, kind, subject, sha256),
    CONSTRAINT source_snapshot_text_chk CHECK (length(source) > 0 AND length(kind) > 0 AND length(subject) > 0 AND length(url) > 0 AND length(run_key) > 0),
    CONSTRAINT source_snapshot_hash_chk CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT source_snapshot_len_chk CHECK (byte_length > 0)
);
CREATE INDEX IF NOT EXISTS idx_source_snapshot_subject ON source_snapshot (source, kind, subject, first_fetched_at);

CREATE TABLE IF NOT EXISTS identifier_link (
    id             BIGSERIAL     PRIMARY KEY,
    cik            BIGINT        NOT NULL,
    symbol         VARCHAR(20)   NOT NULL,
    valid_from     DATE,                                -- observed: the date of the ticker file that carried the pair; reconstructed: NULL (start unknown)
    basis          VARCHAR(13)   NOT NULL,              -- observed | reconstructed
    source         VARCHAR(60)   NOT NULL,              -- e.g. sec_company_tickers
    snapshot_id    BIGINT        NOT NULL REFERENCES source_snapshot (id),
    recorded_at    TIMESTAMPTZ   NOT NULL,              -- DB-stamped: the link's availability
    code_ref       VARCHAR(160)  NOT NULL,
    CONSTRAINT identifier_link_cik_chk CHECK (cik > 0),
    CONSTRAINT identifier_link_symbol_chk CHECK (length(symbol) > 0 AND symbol = upper(symbol)),
    CONSTRAINT identifier_link_basis_chk CHECK (basis IN ('observed', 'reconstructed')),
    CONSTRAINT identifier_link_from_chk CHECK ((basis = 'observed') = (valid_from IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_identifier_link_cik ON identifier_link (cik, recorded_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_identifier_link_identity ON identifier_link (cik, symbol, basis, COALESCE(valid_from, DATE '0001-01-01'));

CREATE TABLE IF NOT EXISTS identifier_link_end (
    id           BIGSERIAL   PRIMARY KEY,
    link_id      BIGINT      NOT NULL REFERENCES identifier_link (id),
    absent_on    DATE        NOT NULL,                  -- the first ticker file that did NOT carry the pair
    snapshot_id  BIGINT      NOT NULL REFERENCES source_snapshot (id),
    recorded_at  TIMESTAMPTZ NOT NULL,                  -- DB-stamped
    code_ref     VARCHAR(160) NOT NULL,
    CONSTRAINT identifier_link_end_once UNIQUE (link_id)
);
CREATE INDEX IF NOT EXISTS idx_identifier_link_end_link ON identifier_link_end (link_id, recorded_at);

COMMENT ON TABLE source_snapshot      IS 'catalyst_ingest_migration_37';
COMMENT ON TABLE identifier_link      IS 'catalyst_ingest_migration_37';
COMMENT ON TABLE identifier_link_end  IS 'catalyst_ingest_migration_37';

-- ---------------------------------------------------------------------------------------------------
-- 2. Functions (created only when absent).
-- ---------------------------------------------------------------------------------------------------
DO $mk$
BEGIN
    IF to_regprocedure('research_ingest_guard()') IS NULL THEN
        EXECUTE $f$
CREATE FUNCTION research_ingest_guard() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not permitted (source snapshots and identifier links are immutable; a correction is a new row)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_ingest_guard() IS 'catalyst_ingest_migration_37';
        REVOKE ALL ON FUNCTION research_ingest_guard() FROM PUBLIC;
    END IF;
    IF to_regprocedure('research_ingest_stamp()') IS NULL THEN
        -- one stamp function for all three tables: the database decides WHEN, the caller cannot back-date or forward-date
        EXECUTE $f$
CREATE FUNCTION research_ingest_stamp() RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_TABLE_NAME = 'source_snapshot' THEN
        NEW.first_fetched_at := clock_timestamp();
    ELSE
        NEW.recorded_at := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$fn$ $f$;
        COMMENT ON FUNCTION research_ingest_stamp() IS 'catalyst_ingest_migration_37';
        REVOKE ALL ON FUNCTION research_ingest_stamp() FROM PUBLIC;
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
        ('source_snapshot',     'source_snapshot_immutable_row',          'BEFORE UPDATE OR DELETE', 'ROW',       'research_ingest_guard'),
        ('source_snapshot',     'source_snapshot_immutable_truncate',     'BEFORE TRUNCATE',         'STATEMENT', 'research_ingest_guard'),
        ('source_snapshot',     'source_snapshot_stamp',                  'BEFORE INSERT',           'ROW',       'research_ingest_stamp'),
        ('identifier_link',     'identifier_link_immutable_row',          'BEFORE UPDATE OR DELETE', 'ROW',       'research_ingest_guard'),
        ('identifier_link',     'identifier_link_immutable_truncate',     'BEFORE TRUNCATE',         'STATEMENT', 'research_ingest_guard'),
        ('identifier_link',     'identifier_link_stamp',                  'BEFORE INSERT',           'ROW',       'research_ingest_stamp'),
        ('identifier_link_end', 'identifier_link_end_immutable_row',      'BEFORE UPDATE OR DELETE', 'ROW',       'research_ingest_guard'),
        ('identifier_link_end', 'identifier_link_end_immutable_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_ingest_guard'),
        ('identifier_link_end', 'identifier_link_end_stamp',              'BEFORE INSERT',           'ROW',       'research_ingest_stamp')
    ) AS v(tbl, trg, timing, lvl, fn) LOOP
        SELECT t.tgenabled, p.proname INTO cur_mode, cur_fn
        FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = to_regclass(r.tbl) AND t.tgname = r.trg AND NOT t.tgisinternal;
        IF FOUND THEN
            IF cur_mode <> 'A' OR cur_fn <> r.fn THEN
                RAISE EXCEPTION 'migration 37 refused: trigger % exists but is not (function %, ENABLE ALWAYS)', r.trg, r.fn;
            END IF;
        ELSE
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', r.trg, r.timing, r.tbl, r.lvl, r.fn);
            EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', r.tbl, r.trg);
        END IF;
    END LOOP;
END;
$trg$;
