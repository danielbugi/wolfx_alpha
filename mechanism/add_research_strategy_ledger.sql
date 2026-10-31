-- mechanism/add_research_strategy_ledger.sql   (migration 39 -- research ledger for the strategy lab: registered strategy specs and immutable run records)
--
-- Additive only (IF NOT EXISTS); touches no existing table, and in particular never the live `strategies` / `signal_ledger` (Strategy Intelligence reads
-- those: a strategy that has not earned forward evidence must not appear there). Apply manually and only to a RESEARCH database unless the owner decides
-- otherwise:  psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_research_strategy_ledger.sql
--
--   research_strategy_spec  one row per (strategy key, version): the canonical definition and its sha256. Never edited; a changed definition is a new version.
--   research_strategy_run   one row per backtest run: which spec, which data (hash of the extract), which code, the split, the pre-declared decision rule, the
--                           metrics, the verdict and the hash of the generated report. Append-only: rerunning with identical inputs reproduces identical hashes, and a
--                           repeat of the same (spec, data, code, split, seed) is refused as a duplicate rather than stored twice.
-- Both tables refuse UPDATE, DELETE and TRUNCATE (triggers), like the other research tables.

CREATE TABLE IF NOT EXISTS research_strategy_spec (
    id            BIGSERIAL PRIMARY KEY,
    strategy_key  VARCHAR(60)  NOT NULL,
    version       VARCHAR(20)  NOT NULL,
    spec_hash     CHAR(64)     NOT NULL,
    definition    JSONB        NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT research_strategy_spec_ident UNIQUE (strategy_key, version),
    CONSTRAINT research_strategy_spec_hash UNIQUE (spec_hash),
    CONSTRAINT research_strategy_spec_hash_chk CHECK (spec_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT research_strategy_spec_def_chk CHECK (jsonb_typeof(definition) = 'object')
);

CREATE TABLE IF NOT EXISTS research_strategy_run (
    id             BIGSERIAL PRIMARY KEY,
    spec_hash      CHAR(64)    NOT NULL REFERENCES research_strategy_spec (spec_hash),
    data_hash      CHAR(64)    NOT NULL,       -- sha256 over the extract's file hashes (the exact prices and earnings the run read)
    code_ref       VARCHAR(64) NOT NULL,       -- the commit that ran it
    run_hash       CHAR(64)    NOT NULL,       -- sha256 of (spec_hash, data_hash, code_ref, split, seed, decision rule): the identity of the run
    split          JSONB       NOT NULL,
    decision_rule  JSONB       NOT NULL,       -- written before the results are looked at
    seed           INTEGER     NOT NULL,
    metrics        JSONB       NOT NULL,
    verdict        VARCHAR(40) NOT NULL,       -- NO_EDGE_DEMONSTRATED | PASS_FOR_FORWARD_SHADOW | INSUFFICIENT_DATA
    report_sha256  CHAR(64)    NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT research_strategy_run_unique UNIQUE (run_hash),
    CONSTRAINT research_strategy_run_verdict_chk CHECK (verdict IN ('NO_EDGE_DEMONSTRATED', 'PASS_FOR_FORWARD_SHADOW', 'INSUFFICIENT_DATA')),
    CONSTRAINT research_strategy_run_json_chk CHECK (jsonb_typeof(metrics) = 'object' AND jsonb_typeof(split) = 'object' AND jsonb_typeof(decision_rule) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_research_strategy_run_spec ON research_strategy_run (spec_hash, created_at DESC);

CREATE OR REPLACE FUNCTION research_strategy_ledger_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is refused: the strategy research ledger is append-only', TG_OP, TG_TABLE_NAME;
END;
$$;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['research_strategy_spec', 'research_strategy_run'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = t || '_row_guard' AND tgrelid = t::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION research_strategy_ledger_guard()', t || '_row_guard', t);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = t || '_truncate_guard' AND tgrelid = t::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION research_strategy_ledger_guard()', t || '_truncate_guard', t);
        END IF;
        -- ENABLE ALWAYS: the guards also hold under session_replication_role = replica (the repo's research-trigger rule).
        EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', t, t || '_row_guard');
        EXECUTE format('ALTER TABLE %I ENABLE ALWAYS TRIGGER %I', t, t || '_truncate_guard');
    END LOOP;
END;
$$;
