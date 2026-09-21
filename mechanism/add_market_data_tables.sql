-- Add market-index/macro price history and a persisted daily sector-
-- performance rollup.
-- File Location: mechanism/add_market_data_tables.sql
-- Run this against an existing database.
--
-- Why: the dashboard's "Market Overview" card and sector heatmap were
-- entirely derived from the internal stock universe (stock_prices /
-- daily_fundamentals) -- there was no ingestion anywhere of actual market
-- indices (S&P 500, Nasdaq, Russell 2000, Dow, VIX, Treasury yields,
-- commodities), and no history was stored for sector performance, only a
-- live per-request snapshot (backend/services/market_service.py). Added
-- per the dashboard data-representation review, 2026-09-19.

-- Daily OHLCV for a small fixed set of index/commodity/macro tickers,
-- fetched by mechanism/data_updaters/market_index_updater.py. Same shape
-- as stock_prices, but symbols here are never equities (^GSPC, ^VIX, GC=F,
-- etc.) so this is deliberately a separate table, not more rows in
-- stock_prices.
CREATE TABLE IF NOT EXISTS market_index_prices (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(14,4),
    high DECIMAL(14,4),
    low DECIMAL(14,4),
    close DECIMAL(14,4),
    volume BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_market_index_prices_symbol_date
    ON market_index_prices(symbol, date DESC);

-- One row per (date, sector) -- a persisted version of the aggregate
-- market_service.py already computes live on every request, so the
-- dashboard's planned sector trend chart has history to draw from instead
-- of only "today". Populated once/day by
-- mechanism/data_updaters/sector_performance_snapshot.py.
CREATE TABLE IF NOT EXISTS sector_performance_daily (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,
    sector VARCHAR(100) NOT NULL,
    stock_count INTEGER NOT NULL,
    avg_performance DECIMAL(8,4),
    created_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(date, sector)
);

CREATE INDEX IF NOT EXISTS idx_sector_performance_daily_date
    ON sector_performance_daily(date DESC);
CREATE INDEX IF NOT EXISTS idx_sector_performance_daily_sector_date
    ON sector_performance_daily(sector, date DESC);
