-- mechanism/add_fundamental_update_publication.sql   (migration 41 -- Fundamental Updates publication record)
--
-- Additive only (IF NOT EXISTS); touches no existing table. NOT applied anywhere: production needs a separate owner approval. Apply manually:
--     psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_fundamental_update_publication.sql
-- Applying it activates nothing: rows are written only by mechanism/inflection/update_ledger.py, which no scheduled job calls.
-- Numbering: 41 on this branch. PR #98 (research missions) and PR #100 (CIK lineage) also use 41/42; whichever merges later renumbers.
--
--   fundamental_update_publication   one row per VERSION of a public Fundamental Update, APPEND-ONLY (a trigger refuses UPDATE and DELETE):
--       version 1      kind 'initial'    the update as first published;
--       version n > 1  kind 'restated'   a figure, the label or the explanation changed  (changed = what changed);
--                      kind 'redated'    the public date or its source changed;
--                      kind 'withdrawn'  the event no longer exists or is no longer publishable (public_json keeps the last published facts).
--     UNIQUE (update_key, version) is the idempotency guard: two writers racing for the same version cannot both win, and re-publishing the same
--     update is a no-op. public_json is exactly PublicUpdate.to_public_dict() (the allowlisted fields) as published, frozen: news and links included.
--     event_id and method_version are INTERNAL provenance: the public API never returns them.
--   fundamental_update_current       view: the latest version of every update, with its first publication time (the feed's order) and version count.

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
    CONSTRAINT fundamental_update_publication_hash_chk CHECK (content_hash ~ '^[0-9a-f]{64}$')
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

-- The pipeline/API login: it may ADD versions and read; nothing may change or delete one (the trigger also refuses the owner).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        GRANT SELECT, INSERT ON fundamental_update_publication TO donchian_app;
        GRANT SELECT ON fundamental_update_current TO donchian_app;
        GRANT USAGE, SELECT ON SEQUENCE fundamental_update_publication_id_seq TO donchian_app;
    END IF;
END;
$$;
