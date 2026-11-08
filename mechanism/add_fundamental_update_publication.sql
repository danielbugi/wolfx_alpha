-- mechanism/add_fundamental_update_publication.sql   (migration 41 -- Fundamental Updates publication record)
--
-- Additive only; touches no existing table. NOT applied to production: that needs a separate owner approval. Apply manually, as ONE transaction:
--     psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_fundamental_update_publication.sql
-- Applying it activates nothing: rows are written only by mechanism/inflection/update_ledger.py, which no scheduled job calls.
-- Numbering: 41 (merged first, PR #101); PR #98 becomes 42 and PR #100 becomes 43 when rebased (owner, 2026-11-07).
--
-- FAIL CLOSED: if fundamental_update_publication already exists with any column different from this file (name, type, nullability, default,
-- order), the migration raises before touching anything. After creating, it checks the complete result (columns, constraints, indexes,
-- triggers, view columns, grants) and raises on any difference, so under -1 a partial or unexpected schema is never committed. Re-applying it to
-- a correct schema changes nothing and keeps every row. deploy/db/fundamental_update_publication_verify.sql re-checks the same and more, read-only.
--
--   fundamental_update_publication   one row per VERSION of a public Fundamental Update, APPEND-ONLY (triggers refuse UPDATE, DELETE, TRUNCATE):
--       version 1      kind 'initial'    the update as first published;
--       version n > 1  kind 'restated'   a figure, the label or the explanation changed  (changed = what changed);
--                      kind 'redated'    the public date or its source changed;
--                      kind 'withdrawn'  the event no longer exists or is no longer publishable (public_json keeps the last published facts).
--     UNIQUE (update_key, version) is the idempotency guard: two writers racing for the same version cannot both win, and re-publishing the same
--     update is a no-op. public_json is exactly PublicUpdate.to_public_dict() as published, frozen: news and links included.
--     The database itself enforces the allowlist: public_json must be an object with EXACTLY the 18 keys of update_model.PUBLIC_FIELDS (a test pins
--     the two lists equal), and its key / symbol / content_hash / render_version must equal the row's own columns.
--     event_id and method_version are INTERNAL provenance: the public API never selects them.
--   fundamental_update_current       view: the latest version of every update, with its first publication time (the feed's order).
--   Privileges: donchian_app (the runtime login) gets SELECT + INSERT on the table, SELECT on the view and USAGE on the id sequence; nothing else.
--     Everything is revoked first, from donchian_app and PUBLIC, so the result never depends on earlier or default grants.

-- ------------------------------------------------------------------------------------------------------------------------ 1. pre-check
DO $$
DECLARE
    expected CONSTANT text := 'id bigint not null default nextval(''fundamental_update_publication_id_seq''::regclass), update_key character varying(80) not null, '
        'version integer not null, kind character varying(10) not null, changed jsonb not null default ''[]''::jsonb, public_json jsonb not null, '
        'content_hash character(64) not null, render_version character varying(10) not null, symbol character varying(20) not null, quarter_end date not null, '
        'event_type character varying(40) not null, sector character varying(50), public_date date not null, event_id bigint, '
        'method_version character varying(30) not null, published_at timestamp with time zone not null default now()';
    actual text;
BEGIN
    IF to_regclass('public.fundamental_update_publication') IS NULL THEN
        IF to_regclass('public.fundamental_update_current') IS NOT NULL THEN
            RAISE EXCEPTION 'migration 41: fundamental_update_current exists without fundamental_update_publication; refusing to continue';
        END IF;
        RETURN;
    END IF;
    IF (SELECT relkind FROM pg_class WHERE oid = 'public.fundamental_update_publication'::regclass) <> 'r' THEN
        RAISE EXCEPTION 'migration 41: fundamental_update_publication exists but is not a plain table; refusing to continue';
    END IF;
    SELECT string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod) || CASE WHEN a.attnotnull THEN ' not null' ELSE '' END
                      || coalesce(' default ' || pg_get_expr(d.adbin, d.adrelid), ''), ', ' ORDER BY a.attnum)
      INTO actual
      FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
     WHERE a.attrelid = 'public.fundamental_update_publication'::regclass AND a.attnum > 0 AND NOT a.attisdropped;
    IF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'migration 41: fundamental_update_publication exists with an unexpected schema; refusing to continue. Found: %', actual;
    END IF;
END $$;

-- ------------------------------------------------------------------------------------------------------------------------ 2. objects
CREATE TABLE IF NOT EXISTS fundamental_update_publication (
    id               BIGSERIAL    PRIMARY KEY,
    update_key       VARCHAR(80)  NOT NULL,                  -- natural key SYMBOL/quarter_end/slug, also the public URL path
    version          INTEGER      NOT NULL,
    kind             VARCHAR(10)  NOT NULL,
    changed          JSONB        NOT NULL DEFAULT '[]'::jsonb,
    public_json      JSONB        NOT NULL,
    content_hash     CHAR(64)     NOT NULL,
    render_version   VARCHAR(10)  NOT NULL,
    symbol           VARCHAR(20)  NOT NULL,                  -- denormalised from public_json for filtering and indexing
    quarter_end      DATE         NOT NULL,
    event_type       VARCHAR(40)  NOT NULL,
    sector           VARCHAR(50),
    public_date      DATE         NOT NULL,
    event_id         BIGINT,                                 -- internal provenance (no FK: the event may later be removed; the record must survive)
    method_version   VARCHAR(30)  NOT NULL,                  -- internal provenance
    published_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT fundamental_update_publication_version UNIQUE (update_key, version),
    CONSTRAINT fundamental_update_publication_version_chk CHECK (version >= 1),
    CONSTRAINT fundamental_update_publication_kind_chk CHECK (kind IN ('initial', 'restated', 'redated', 'withdrawn')),
    CONSTRAINT fundamental_update_publication_first_chk CHECK ((version = 1) = (kind = 'initial')),
    CONSTRAINT fundamental_update_publication_key_chk CHECK (update_key ~ '^[A-Z][A-Z0-9.\-]{0,9}/\d{4}-\d{2}-\d{2}/[a-z\-]+$'),
    CONSTRAINT fundamental_update_publication_hash_chk CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fundamental_update_publication_changed_chk CHECK (jsonb_typeof(changed) = 'array'),
    -- the public allowlist, enforced by the database: exactly these 18 top-level keys, nothing more, nothing less (= update_model.PUBLIC_FIELDS)
    CONSTRAINT fundamental_update_publication_public_keys_chk CHECK (
        jsonb_typeof(public_json) = 'object'
        AND public_json ?& ARRAY['key', 'symbol', 'company_name', 'event_type', 'label', 'quarter_end', 'fiscal_label', 'public_date', 'date_source', 'sector',
                                 'industry', 'market_cap', 'figures', 'why', 'links', 'news', 'content_hash', 'render_version']
        AND (public_json - ARRAY['key', 'symbol', 'company_name', 'event_type', 'label', 'quarter_end', 'fiscal_label', 'public_date', 'date_source', 'sector',
                                 'industry', 'market_cap', 'figures', 'why', 'links', 'news', 'content_hash', 'render_version']) = '{}'::jsonb
        AND jsonb_typeof(public_json -> 'figures') = 'array' AND jsonb_typeof(public_json -> 'news') = 'array' AND jsonb_typeof(public_json -> 'links') = 'object'),
    -- the row's own columns are the published values, never different ones
    CONSTRAINT fundamental_update_publication_public_match_chk CHECK (
        public_json ->> 'key' = update_key AND public_json ->> 'symbol' = symbol AND public_json ->> 'content_hash' = content_hash
        AND public_json ->> 'render_version' = render_version AND public_json ->> 'event_type' = event_type)
);
CREATE INDEX IF NOT EXISTS idx_fup_key_version ON fundamental_update_publication (update_key, version DESC);
CREATE INDEX IF NOT EXISTS idx_fup_published ON fundamental_update_publication (published_at DESC) WHERE version = 1;

CREATE OR REPLACE FUNCTION fundamental_update_publication_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'fundamental_update_publication is append-only (% refused): record a new version instead', TG_OP;
END;
$$;
DROP TRIGGER IF EXISTS fundamental_update_publication_no_update ON fundamental_update_publication;
CREATE TRIGGER fundamental_update_publication_no_update BEFORE UPDATE OR DELETE ON fundamental_update_publication
    FOR EACH ROW EXECUTE FUNCTION fundamental_update_publication_append_only();
ALTER TABLE fundamental_update_publication ENABLE ALWAYS TRIGGER fundamental_update_publication_no_update;
DROP TRIGGER IF EXISTS fundamental_update_publication_no_truncate ON fundamental_update_publication;
CREATE TRIGGER fundamental_update_publication_no_truncate BEFORE TRUNCATE ON fundamental_update_publication
    FOR EACH STATEMENT EXECUTE FUNCTION fundamental_update_publication_append_only();
ALTER TABLE fundamental_update_publication ENABLE ALWAYS TRIGGER fundamental_update_publication_no_truncate;

CREATE OR REPLACE VIEW fundamental_update_current AS
SELECT DISTINCT ON (p.update_key)
       p.update_key, p.version, p.kind, p.public_json, p.content_hash, p.symbol, p.quarter_end, p.event_type, p.sector, p.public_date, p.published_at AS updated_at,
       first.published_at AS first_published_at, first.public_date AS first_public_date
  FROM fundamental_update_publication p
  JOIN fundamental_update_publication first ON first.update_key = p.update_key AND first.version = 1
 ORDER BY p.update_key, p.version DESC;

-- ------------------------------------------------------------------------------------------------------------------------ 3. privileges
REVOKE ALL ON fundamental_update_publication FROM PUBLIC;
REVOKE ALL ON fundamental_update_current FROM PUBLIC;
REVOKE ALL ON SEQUENCE fundamental_update_publication_id_seq FROM PUBLIC;
REVOKE ALL ON FUNCTION fundamental_update_publication_append_only() FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        REVOKE ALL ON fundamental_update_publication FROM donchian_app;
        REVOKE ALL ON fundamental_update_current FROM donchian_app;
        REVOKE ALL ON SEQUENCE fundamental_update_publication_id_seq FROM donchian_app;
        GRANT SELECT, INSERT ON fundamental_update_publication TO donchian_app;
        GRANT SELECT ON fundamental_update_current TO donchian_app;
        GRANT USAGE ON SEQUENCE fundamental_update_publication_id_seq TO donchian_app;     -- nextval only: no setval (UPDATE), no SELECT needed
    END IF;
END $$;

-- ------------------------------------------------------------------------------------------------------------------------ 4. post-check (fail closed)
DO $$
DECLARE
    t CONSTANT regclass := 'public.fundamental_update_publication'::regclass;
    got text;
BEGIN
    SELECT string_agg(conname || ': ' || pg_get_constraintdef(oid), E'\n' ORDER BY conname) INTO got FROM pg_constraint WHERE conrelid = t;
    IF got IS DISTINCT FROM concat_ws(E'\n',
        'fundamental_update_publication_changed_chk: CHECK ((jsonb_typeof(changed) = ''array''::text))',
        'fundamental_update_publication_first_chk: CHECK (((version = 1) = ((kind)::text = ''initial''::text)))',
        'fundamental_update_publication_hash_chk: CHECK ((content_hash ~ ''^[0-9a-f]{64}$''::text))',
        'fundamental_update_publication_key_chk: CHECK (((update_key)::text ~ ''^[A-Z][A-Z0-9.\-]{0,9}/\d{4}-\d{2}-\d{2}/[a-z\-]+$''::text))',
        'fundamental_update_publication_kind_chk: CHECK (((kind)::text = ANY ((ARRAY[''initial''::character varying, ''restated''::character varying, ''redated''::character varying, ''withdrawn''::character varying])::text[])))',
        'fundamental_update_publication_pkey: PRIMARY KEY (id)',
        'fundamental_update_publication_public_keys_chk: CHECK (((jsonb_typeof(public_json) = ''object''::text) AND (public_json ?& ARRAY[''key''::text, ''symbol''::text, ''company_name''::text, ''event_type''::text, ''label''::text, ''quarter_end''::text, ''fiscal_label''::text, ''public_date''::text, ''date_source''::text, ''sector''::text, ''industry''::text, ''market_cap''::text, ''figures''::text, ''why''::text, ''links''::text, ''news''::text, ''content_hash''::text, ''render_version''::text]) AND ((public_json - ARRAY[''key''::text, ''symbol''::text, ''company_name''::text, ''event_type''::text, ''label''::text, ''quarter_end''::text, ''fiscal_label''::text, ''public_date''::text, ''date_source''::text, ''sector''::text, ''industry''::text, ''market_cap''::text, ''figures''::text, ''why''::text, ''links''::text, ''news''::text, ''content_hash''::text, ''render_version''::text]) = ''{}''::jsonb) AND (jsonb_typeof((public_json -> ''figures''::text)) = ''array''::text) AND (jsonb_typeof((public_json -> ''news''::text)) = ''array''::text) AND (jsonb_typeof((public_json -> ''links''::text)) = ''object''::text)))',
        'fundamental_update_publication_public_match_chk: CHECK ((((public_json ->> ''key''::text) = (update_key)::text) AND ((public_json ->> ''symbol''::text) = (symbol)::text) AND ((public_json ->> ''content_hash''::text) = (content_hash)::text) AND ((public_json ->> ''render_version''::text) = (render_version)::text) AND ((public_json ->> ''event_type''::text) = (event_type)::text)))',
        'fundamental_update_publication_version: UNIQUE (update_key, version)',
        'fundamental_update_publication_version_chk: CHECK ((version >= 1))') THEN
        RAISE EXCEPTION 'migration 41: unexpected constraints on fundamental_update_publication:%', E'\n' || coalesce(got, '(none)');
    END IF;

    SELECT string_agg(pg_get_indexdef(indexrelid), E'\n' ORDER BY pg_get_indexdef(indexrelid)) INTO got FROM pg_index WHERE indrelid = t;
    IF got IS DISTINCT FROM concat_ws(E'\n',
        'CREATE INDEX idx_fup_key_version ON public.fundamental_update_publication USING btree (update_key, version DESC)',
        'CREATE INDEX idx_fup_published ON public.fundamental_update_publication USING btree (published_at DESC) WHERE (version = 1)',
        'CREATE UNIQUE INDEX fundamental_update_publication_pkey ON public.fundamental_update_publication USING btree (id)',
        'CREATE UNIQUE INDEX fundamental_update_publication_version ON public.fundamental_update_publication USING btree (update_key, version)') THEN
        RAISE EXCEPTION 'migration 41: unexpected indexes on fundamental_update_publication:%', E'\n' || coalesce(got, '(none)');
    END IF;

    SELECT string_agg(tgname || ' ' || tgenabled::text || ' ' || pg_get_triggerdef(oid), E'\n' ORDER BY tgname) INTO got FROM pg_trigger WHERE tgrelid = t AND NOT tgisinternal;
    IF got IS DISTINCT FROM concat_ws(E'\n',
        'fundamental_update_publication_no_truncate A CREATE TRIGGER fundamental_update_publication_no_truncate BEFORE TRUNCATE ON public.fundamental_update_publication FOR EACH STATEMENT EXECUTE FUNCTION fundamental_update_publication_append_only()',
        'fundamental_update_publication_no_update A CREATE TRIGGER fundamental_update_publication_no_update BEFORE DELETE OR UPDATE ON public.fundamental_update_publication FOR EACH ROW EXECUTE FUNCTION fundamental_update_publication_append_only()') THEN
        RAISE EXCEPTION 'migration 41: unexpected triggers on fundamental_update_publication:%', E'\n' || coalesce(got, '(none)');
    END IF;

    SELECT string_agg(attname || ' ' || format_type(atttypid, atttypmod), ', ' ORDER BY attnum) INTO got FROM pg_attribute
     WHERE attrelid = 'public.fundamental_update_current'::regclass AND attnum > 0 AND NOT attisdropped;
    IF got IS DISTINCT FROM 'update_key character varying(80), version integer, kind character varying(10), public_json jsonb, content_hash character(64), '
                            'symbol character varying(20), quarter_end date, event_type character varying(40), sector character varying(50), public_date date, '
                            'updated_at timestamp with time zone, first_published_at timestamp with time zone, first_public_date date' THEN
        RAISE EXCEPTION 'migration 41: unexpected columns on fundamental_update_current: %', got;
    END IF;

    IF has_table_privilege('public', t, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') THEN
        RAISE EXCEPTION 'migration 41: PUBLIC has a privilege on fundamental_update_publication';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') AND (
           NOT has_table_privilege('donchian_app', t, 'SELECT') OR NOT has_table_privilege('donchian_app', t, 'INSERT')
           OR has_table_privilege('donchian_app', t, 'UPDATE') OR has_table_privilege('donchian_app', t, 'DELETE') OR has_table_privilege('donchian_app', t, 'TRUNCATE')
           OR has_table_privilege('donchian_app', t, 'REFERENCES') OR has_table_privilege('donchian_app', t, 'TRIGGER')
           OR NOT has_sequence_privilege('donchian_app', 'public.fundamental_update_publication_id_seq', 'USAGE')
           OR has_sequence_privilege('donchian_app', 'public.fundamental_update_publication_id_seq', 'UPDATE')
           OR NOT has_table_privilege('donchian_app', 'public.fundamental_update_current', 'SELECT')
           OR has_table_privilege('donchian_app', 'public.fundamental_update_current', 'INSERT,UPDATE,DELETE,TRUNCATE')) THEN
        RAISE EXCEPTION 'migration 41: donchian_app privileges are not exactly SELECT+INSERT (table), SELECT (view), USAGE (sequence)';
    END IF;
END $$;
