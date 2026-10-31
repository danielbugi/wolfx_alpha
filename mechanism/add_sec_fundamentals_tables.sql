-- mechanism/add_sec_fundamentals_tables.sql   (migration 38 -- SEC EDGAR fundamentals: raw facts, normalized quarters, ingestion state)
--
-- Additive only (IF NOT EXISTS); touches no existing table. Apply manually:  psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_sec_fundamentals_tables.sql
-- Applying it activates nothing: nothing reads these tables until the updater (mechanism/data_updaters/sec_fundamentals_updater.py) runs.
--
-- Why a separate set of tables instead of writing into quarterly_fundamentals: the existing table is filled from yfinance and upserts in place,
-- so it cannot say which filing a number came from, when it was filed, or what it was before a restatement. These tables keep that, and the
-- two sources are reconciled side by side (updater --reconcile) before anything is displayed from SEC data.
--
--   sec_symbol_cik          ticker -> CIK from the SEC's own ticker file (refreshed weekly).
--   sec_filing              every periodic report (10-Q, 10-K and their amendments) with its filing date and acceptance timestamp.
--   sec_fact                the raw XBRL values, verbatim, one row per (filing, metric, period): append-only (a restatement is a NEW row from the
--                           amending/later filing; the earlier row stays). first_seen_at is DB-stamped: when WE learned the value.
--   sec_quarter             the normalized quarterly figure per (company, period, metric): reported directly, differenced from year-to-date cash-flow
--                           values, or Q4 = annual - (Q1+Q2+Q3), with basis, source filing(s), filing date and a revision count. The earlier value of a
--                           restated quarter is recoverable from sec_fact; point-in-time views are rebuilt from sec_fact with an as-of date.
--   sec_fundamentals_state  per-company ingestion state, so the updater is incremental (checks only who may have a new filing) and observable.

CREATE TABLE IF NOT EXISTS sec_symbol_cik (
    symbol       VARCHAR(20) PRIMARY KEY,
    cik          BIGINT      NOT NULL,
    company_name TEXT,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sec_symbol_cik_cik ON sec_symbol_cik (cik);

CREATE TABLE IF NOT EXISTS sec_filing (
    accession     VARCHAR(25) PRIMARY KEY,
    cik           BIGINT      NOT NULL,
    form          VARCHAR(12) NOT NULL,
    filed_date    DATE        NOT NULL,
    accepted_at   TIMESTAMPTZ,
    period_end    DATE,
    is_amendment  BOOLEAN     NOT NULL DEFAULT FALSE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sec_filing_cik_filed ON sec_filing (cik, filed_date DESC);

CREATE TABLE IF NOT EXISTS sec_fact (
    cik           BIGINT       NOT NULL,
    accession     VARCHAR(25)  NOT NULL,
    metric        VARCHAR(40)  NOT NULL,
    tag           VARCHAR(120) NOT NULL,
    period_start  DATE         NOT NULL,   -- instants (balance sheet): period_start = period_end
    period_end    DATE         NOT NULL,
    value         NUMERIC(28,6) NOT NULL,
    unit          VARCHAR(16)  NOT NULL,
    fy            INTEGER,                 -- the FILING's fiscal year / period, as XBRL labels it (a comparative carries the filing's label)
    fp            VARCHAR(4),
    form          VARCHAR(12)  NOT NULL,
    filed_date    DATE         NOT NULL,
    first_seen_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cik, accession, metric, period_start, period_end)
);
CREATE INDEX IF NOT EXISTS idx_sec_fact_cik_metric_end ON sec_fact (cik, metric, period_end);

CREATE TABLE IF NOT EXISTS sec_quarter (
    cik            BIGINT       NOT NULL,
    period_end     DATE         NOT NULL,
    metric         VARCHAR(40)  NOT NULL,
    value          NUMERIC(28,6) NOT NULL,
    unit           VARCHAR(16)  NOT NULL,
    basis          VARCHAR(24)  NOT NULL,  -- reported | ytd_difference | annual_less_quarters | instant | computed
    fiscal_year    INTEGER,
    fiscal_quarter INTEGER,                -- NULL when the filing that reported the period does not state it
    accession      VARCHAR(25)  NOT NULL,  -- the filing the value comes from (derived: the newest filing among its components)
    filed_date     DATE         NOT NULL,
    form           VARCHAR(12)  NOT NULL,
    first_seen_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    revision_count INTEGER      NOT NULL DEFAULT 0,
    PRIMARY KEY (cik, period_end, metric)
);
CREATE INDEX IF NOT EXISTS idx_sec_quarter_end ON sec_quarter (period_end);

CREATE TABLE IF NOT EXISTS sec_fundamentals_state (
    cik                       BIGINT PRIMARY KEY,
    symbol                    VARCHAR(20),
    last_checked_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_status               VARCHAR(24) NOT NULL,   -- ok | no_filings | no_xbrl | not_found | error
    newest_periodic_accession VARCHAR(25),
    newest_periodic_filed     DATE,
    facts_fetched_at          TIMESTAMPTZ,
    last_error                TEXT
);

-- donchian_app is the role the pipeline (the SEC updater WRITES these tables) and the API (reads) run as. Tables created by a manual apply do not inherit the default privileges the older
-- tables have (found with migration 40 in production, 2026-10-31), so the grant is explicit. No DELETE: the updater upserts, nothing deletes. No sequences in these tables.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'donchian_app') THEN
        GRANT SELECT, INSERT, UPDATE ON sec_symbol_cik, sec_filing, sec_fact, sec_quarter, sec_fundamentals_state TO donchian_app;
    END IF;
END $$;
