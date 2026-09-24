-- Add a batch-fetched earnings-date calendar (past + future report dates per symbol).
-- File Location: mechanism/add_earnings_calendar_table.sql
-- Run this against an existing database.
--
-- Why: no data source in this codebase stored earnings dates anywhere before this --
-- backend/services/earnings_service.py's yfinance lookup was on-demand/per-view only (an
-- in-memory TTL cache, never written to Postgres). Two things need it: (1) an event-driven
-- staleness gate for quarterly_fundamentals_updater.py, replacing a blind 25-day timer with
-- "re-check only once we know a report actually happened since we last looked"; (2) a daily
-- "who reports today" channel post. Added 2026-09-22 per user request -- see
-- DATA_ML_MILESTONES.md M2.
--
-- One row per (symbol, report_date) -- mirrors exactly what yfinance's
-- Ticker.get_earnings_dates() returns (a rolling window of past + future dates), so ingestion
-- never has to decide "which one is next" -- that's a query, not a write.
CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol VARCHAR(20) NOT NULL,
    report_date DATE NOT NULL,
    eps_estimate DECIMAL(12,4),
    eps_actual DECIMAL(12,4),
    surprise_pct DECIMAL(12,4),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, report_date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_calendar_report_date ON earnings_calendar(report_date);
CREATE INDEX IF NOT EXISTS idx_earnings_calendar_symbol ON earnings_calendar(symbol);
