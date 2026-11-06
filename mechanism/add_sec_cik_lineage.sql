-- mechanism/add_sec_cik_lineage.sql   (migration 41 -- SEC company identity: predecessor CIKs and other identity breaks behind a listed symbol)
--
-- Additive only (IF NOT EXISTS); touches no existing table. LAB ONLY: not applied to production (owner decision 2026-11-06).
-- Apply to a research / scratch database with:  psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_sec_cik_lineage.sql
-- Applying it activates nothing: only mechanism/data_updaters/sec_lineage.py reads it, and the live inflection engine does not call that yet.
--
-- Why: sec_symbol_cik comes from the SEC's CURRENT ticker file, so a symbol maps to today's registrant only. After a holding-company reorganisation
-- (XOM -> ExxonMobil Holdings Corp, CIK 2115436, while ExxonMobil's history since 2009 is under CIK 34088) the history behind the symbol is lost; after
-- a ticker reuse (GLIBA, MTAL) the price history behind the symbol belongs to a DIFFERENT company. One row per identity break, with its evidence.
--
-- Rules the constraints enforce (see docs/research/SEC_CIK_LINEAGE.md):
--   * entities are never merged on the ticker alone: a row that lets a symbol inherit another CIK's filings must be a CONFIRMED holding-company
--     reorganisation or redomicile, name the predecessor CIK and cite the filing that proves it (8-K12B / 8-K12G3);
--   * company identity is preserved: the predecessor's facts stay under the predecessor's CIK (sec_fact is untouched); the link only says which CIKs
--     a symbol's history is read from, and from which date;
--   * point in time: known_at is when the break became public (the evidence filing); before it, research sees the world as it was then;
--   * append-only: a correction is a new row that names the row it supersedes; nothing is updated or deleted.

CREATE TABLE IF NOT EXISTS sec_cik_lineage (
    id                 BIGSERIAL    PRIMARY KEY,
    symbol             VARCHAR(20)  NOT NULL,             -- the listed symbol this break explains
    successor_cik      BIGINT       NOT NULL,             -- the CIK the SEC ticker file maps the symbol to today
    predecessor_cik    BIGINT,                            -- NULL when nothing in EDGAR precedes the successor for this symbol
    relation           VARCHAR(32)  NOT NULL CHECK (relation IN (
                           'holding_company_reorganization',  -- same business, new parent registrant (8-K12B): inherits history
                           'redomicile',                      -- same business, new jurisdiction / registrant (8-K12B, 8-K12G3): inherits history
                           'merger_successor',                -- new registrant combining two or more businesses: never inherits automatically
                           'foreign_filer_transition',        -- same CIK, 20-F/40-F before 10-K/10-Q: no CIK break, the earlier history is in other forms
                           'newly_registered',                -- traded (OTC, Reg A, bank-regulator filer) before registering: no EDGAR history to inherit
                           'spac_combination',                -- prices before the combination are the blank-check shell's
                           'ticker_reuse',                    -- an unrelated earlier company used the symbol
                           'tracking_stock_split_off',        -- the former parent's filings describe the whole parent, not the tracked group
                           'non_equity_listing')),            -- the listed security is debt or preferred: not common-stock research
    effective_date     DATE         NOT NULL,             -- the day the break took effect (the successor's first trading day as such)
    known_at           DATE         NOT NULL,             -- when the break became public (the evidence filing date)
    evidence_accession VARCHAR(25),                       -- the filing that proves it
    evidence_form      VARCHAR(12),
    inherit_history    BOOLEAN      NOT NULL,             -- read the predecessor's filings for periods ending before effective_date
    research_eligible  BOOLEAN      NOT NULL,             -- FALSE: exclude the symbol from common-stock research
    price_identity_from DATE,                             -- prices before this date belong to another entity (ticker reuse, SPAC shell)
    status             VARCHAR(16)  NOT NULL CHECK (status IN ('confirmed', 'needs_review')),
    supersedes_id      BIGINT       REFERENCES sec_cik_lineage (id),
    note               TEXT         NOT NULL,
    recorded_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT sec_cik_lineage_inherit_needs_proof CHECK (NOT inherit_history OR (
        status = 'confirmed' AND predecessor_cik IS NOT NULL AND predecessor_cik <> successor_cik
        AND relation IN ('holding_company_reorganization', 'redomicile')
        AND evidence_accession IS NOT NULL AND evidence_form IN ('8-K12B', '8-K12G3'))),
    CONSTRAINT sec_cik_lineage_price_identity CHECK (price_identity_from IS NULL OR relation IN ('ticker_reuse', 'spac_combination')),
    CONSTRAINT sec_cik_lineage_non_equity CHECK (relation <> 'non_equity_listing' OR NOT research_eligible)
);
CREATE INDEX IF NOT EXISTS idx_sec_cik_lineage_symbol ON sec_cik_lineage (symbol, id);
CREATE INDEX IF NOT EXISTS idx_sec_cik_lineage_successor ON sec_cik_lineage (successor_cik);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sec_cik_lineage_supersedes ON sec_cik_lineage (supersedes_id) WHERE supersedes_id IS NOT NULL;

CREATE OR REPLACE FUNCTION sec_cik_lineage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'sec_cik_lineage is append-only: record a correction as a new row with supersedes_id';
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'sec_cik_lineage_no_update_delete') THEN
        CREATE TRIGGER sec_cik_lineage_no_update_delete BEFORE UPDATE OR DELETE ON sec_cik_lineage FOR EACH ROW EXECUTE FUNCTION sec_cik_lineage_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'sec_cik_lineage_no_truncate') THEN
        CREATE TRIGGER sec_cik_lineage_no_truncate BEFORE TRUNCATE ON sec_cik_lineage FOR EACH STATEMENT EXECUTE FUNCTION sec_cik_lineage_guard();
    END IF;
END $$;
ALTER TABLE sec_cik_lineage ENABLE ALWAYS TRIGGER sec_cik_lineage_no_update_delete;
ALTER TABLE sec_cik_lineage ENABLE ALWAYS TRIGGER sec_cik_lineage_no_truncate;

-- Read by the pipeline / API role; rows are added by hand (reviewed seed), so the application role gets no write grant.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        GRANT SELECT ON sec_cik_lineage TO donchian_app;
    END IF;
END $$;
