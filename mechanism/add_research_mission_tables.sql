-- Migration 42: Research Missions (docs/product/RESEARCH_MISSIONS_MVP_DESIGN.md). Additive and idempotent; nothing existing is touched.
-- A mission researches one written question (a company deep-dive or a theme), stores every source document and evidence item with its
-- provenance (append-only), the analyst's claims with their citations and the verifier's verdicts, the companies and supply-chain
-- relations found, a versioned report, and a ledger of every paid model call (the budget guard reads it before each call).

CREATE TABLE IF NOT EXISTS research_mission (
    id          BIGSERIAL PRIMARY KEY,
    kind        VARCHAR(20)   NOT NULL CHECK (kind IN ('company', 'thematic')),
    title       VARCHAR(200)  NOT NULL,
    question    TEXT          NOT NULL,
    domain      VARCHAR(60)   NOT NULL,
    params      JSONB         NOT NULL DEFAULT '{}'::jsonb,
    status      VARCHAR(20)   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'queued', 'running', 'completed', 'failed', 'stopped_budget')),
    budget_usd  NUMERIC(10,4) NOT NULL DEFAULT 5 CHECK (budget_usd >= 0),
    spent_usd   NUMERIC(12,6) NOT NULL DEFAULT 0,
    error       TEXT,
    created_by  VARCHAR(120),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_mission_status ON research_mission (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS research_step (
    id          BIGSERIAL PRIMARY KEY,
    mission_id  BIGINT       NOT NULL REFERENCES research_mission(id),
    step        VARCHAR(20)  NOT NULL CHECK (step IN ('plan', 'collect', 'analyze', 'verify', 'report')),
    status      VARCHAR(20)  NOT NULL CHECK (status IN ('running', 'done', 'failed', 'skipped')),
    detail      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    started_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_research_step_mission ON research_step (mission_id, id);

-- Source documents, shared across missions. APPEND-ONLY: what we saw, where, when, under which licence.
CREATE TABLE IF NOT EXISTS research_document (
    id             BIGSERIAL PRIMARY KEY,
    source         VARCHAR(40)  NOT NULL,                       -- first_light_db | news_items | gdelt | tiingo_news | sec_edgar
    url            TEXT         NOT NULL,                       -- a real URL, or firstlight://<table>/<keys> for our own data
    title          TEXT,
    publisher      VARCHAR(200),
    published_at   TIMESTAMPTZ,                                 -- when the world could know it (NULL when the source does not say)
    retrieved_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    license        VARCHAR(20)  NOT NULL CHECK (license IN ('public', 'internal_only')),
    content_sha256 CHAR(64)     NOT NULL,
    excerpt        TEXT,                                        -- short; never a full article
    meta           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source, url, content_sha256)
);

CREATE TABLE IF NOT EXISTS research_evidence (
    id          BIGSERIAL PRIMARY KEY,
    mission_id  BIGINT       NOT NULL REFERENCES research_mission(id),
    document_id BIGINT       NOT NULL REFERENCES research_document(id),
    label       VARCHAR(12)  NOT NULL,                          -- E1, E2, ... (what the agents cite)
    quote       TEXT         NOT NULL,
    locator     TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (mission_id, label)
);

CREATE TABLE IF NOT EXISTS research_claim (
    id            BIGSERIAL PRIMARY KEY,
    mission_id    BIGINT       NOT NULL REFERENCES research_mission(id),
    label         VARCHAR(12)  NOT NULL,                        -- C1, C2, ...
    kind          VARCHAR(12)  NOT NULL CHECK (kind IN ('fact', 'inference', 'forecast')),
    text          TEXT         NOT NULL,
    confidence    VARCHAR(8)   NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    status        VARCHAR(12)  NOT NULL DEFAULT 'unverified' CHECK (status IN ('unverified', 'verified', 'partial', 'unsupported')),
    verifier_note TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (mission_id, label)
);

CREATE TABLE IF NOT EXISTS research_claim_evidence (
    claim_id    BIGINT NOT NULL REFERENCES research_claim(id),
    evidence_id BIGINT NOT NULL REFERENCES research_evidence(id),
    PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS research_company (
    id           BIGSERIAL PRIMARY KEY,
    mission_id   BIGINT       NOT NULL REFERENCES research_mission(id),
    symbol       VARCHAR(20)  NOT NULL,
    name         VARCHAR(200),
    in_universe  BOOLEAN      NOT NULL,
    role         VARCHAR(40)  NOT NULL,
    exposure     VARCHAR(8)   NOT NULL CHECK (exposure IN ('high', 'medium', 'low', 'unclear')),
    claim_labels JSONB        NOT NULL DEFAULT '[]'::jsonb,
    risk_labels  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (mission_id, symbol)
);

CREATE TABLE IF NOT EXISTS research_relation (
    id           BIGSERIAL PRIMARY KEY,
    mission_id   BIGINT       NOT NULL REFERENCES research_mission(id),
    from_entity  VARCHAR(120) NOT NULL,
    to_entity    VARCHAR(120) NOT NULL,
    relation     VARCHAR(20)  NOT NULL CHECK (relation IN ('supplier_of', 'customer_of', 'competitor_of', 'produces', 'uses')),
    claim_labels JSONB        NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS research_report (
    id          BIGSERIAL PRIMARY KEY,
    mission_id  BIGINT       NOT NULL REFERENCES research_mission(id),
    version     INTEGER      NOT NULL,
    markdown    TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (mission_id, version)
);

-- Ledger of every paid model call. The budget guard reserves a worst-case cost here BEFORE a call (under an advisory lock) and settles the
-- actual cost after it; a refused reservation is recorded too and no request is sent.
CREATE TABLE IF NOT EXISTS research_llm_usage (
    id                 BIGSERIAL PRIMARY KEY,
    mission_id         BIGINT        REFERENCES research_mission(id),
    role               VARCHAR(20)   NOT NULL,
    model              VARCHAR(60)   NOT NULL,
    status             VARCHAR(10)   NOT NULL CHECK (status IN ('reserved', 'settled', 'refused', 'failed')),
    reserved_usd       NUMERIC(12,6) NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(12,6) NOT NULL DEFAULT 0,
    input_tokens       INTEGER       NOT NULL DEFAULT 0,
    output_tokens      INTEGER       NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER       NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER       NOT NULL DEFAULT 0,
    web_searches       INTEGER       NOT NULL DEFAULT 0,
    served_model       VARCHAR(60),
    note               TEXT,
    created_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    settled_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_research_llm_usage_month ON research_llm_usage (created_at);

-- Append-only guards: documents and evidence never change; a usage row may only move reserved -> settled|failed, once.
CREATE OR REPLACE FUNCTION research_mission_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE OR REPLACE FUNCTION research_llm_usage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'research_llm_usage is append-only' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status <> 'reserved' OR NEW.status NOT IN ('settled', 'failed') OR NEW.reserved_usd <> OLD.reserved_usd OR NEW.created_at <> OLD.created_at
       OR NEW.mission_id IS DISTINCT FROM OLD.mission_id OR NEW.model <> OLD.model THEN
        RAISE EXCEPTION 'research_llm_usage: only reserved -> settled|failed is allowed' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

-- Row triggers refuse UPDATE/DELETE, statement triggers refuse TRUNCATE; all are ENABLE ALWAYS, so session_replication_role = replica cannot
-- switch them off (the repository-wide rule checked by ops/checkpoint/assert_forward_research.sql).
DO $$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT * FROM (VALUES
            ('research_document',  'research_document_append_only',  'BEFORE UPDATE OR DELETE', 'ROW',       'research_mission_append_only'),
            ('research_document',  'research_document_no_truncate',  'BEFORE TRUNCATE',         'STATEMENT', 'research_mission_append_only'),
            ('research_evidence',  'research_evidence_append_only',  'BEFORE UPDATE OR DELETE', 'ROW',       'research_mission_append_only'),
            ('research_evidence',  'research_evidence_no_truncate',  'BEFORE TRUNCATE',         'STATEMENT', 'research_mission_append_only'),
            ('research_llm_usage', 'research_llm_usage_guard',       'BEFORE UPDATE OR DELETE', 'ROW',       'research_llm_usage_guard'),
            ('research_llm_usage', 'research_llm_usage_no_truncate', 'BEFORE TRUNCATE',         'STATEMENT', 'research_mission_append_only')
        ) AS v(tbl, trg, timing, lvl, fn)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = t.trg AND tgrelid = t.tbl::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I %s ON %I FOR EACH %s EXECUTE FUNCTION %I()', t.trg, t.timing, t.tbl, t.lvl, t.fn);
        END IF;
        EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', t.tbl, t.trg);
    END LOOP;
END $$;

-- Runtime role (pipeline writes, API reads), as migration 40: explicit grants, no DELETE anywhere.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        GRANT SELECT, INSERT, UPDATE ON research_mission, research_step, research_document, research_evidence, research_claim, research_claim_evidence,
              research_company, research_relation, research_report, research_llm_usage TO donchian_app;
        GRANT USAGE, SELECT ON SEQUENCE research_mission_id_seq, research_step_id_seq, research_document_id_seq, research_evidence_id_seq, research_claim_id_seq,
              research_company_id_seq, research_relation_id_seq, research_report_id_seq, research_llm_usage_id_seq TO donchian_app;
    END IF;
END $$;
