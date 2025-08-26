-- create_trading_schema.sql
-- Run this script to create your trading database schema in PostgreSQL

-- Connect to your trading database first:
-- psql -U trading_user -d trading_production -h localhost

-- =============================================================================
-- 1. CREATE CORE TABLES
-- =============================================================================

-- Drop existing tables if they exist (for clean setup)
DROP TABLE IF EXISTS api_requests CASCADE;
DROP TABLE IF EXISTS data_quality_checks CASCADE;
DROP TABLE IF EXISTS data_updates CASCADE;
DROP TABLE IF EXISTS ml_predictions CASCADE;
DROP TABLE IF EXISTS ml_models CASCADE;
DROP TABLE IF EXISTS breakouts CASCADE;
DROP TABLE IF EXISTS quarterly_fundamentals CASCADE;
DROP TABLE IF EXISTS daily_fundamentals CASCADE;
DROP TABLE IF EXISTS technical_indicators CASCADE;
DROP TABLE IF EXISTS stock_prices CASCADE;
DROP TABLE IF EXISTS companies CASCADE;

-- Companies table (master data)
CREATE TABLE companies (
    symbol VARCHAR(10) PRIMARY KEY,
    company_name VARCHAR(200),
    sector VARCHAR(50),
    industry VARCHAR(100),
    exchange VARCHAR(10),
    market_cap_category VARCHAR(20),
    is_active BOOLEAN DEFAULT true,
    first_trading_date DATE,
    last_trading_date DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Stock prices table (main data)
CREATE TABLE stock_prices (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    open DECIMAL(12,4),
    high DECIMAL(12,4),
    low DECIMAL(12,4),
    close DECIMAL(12,4),
    adj_close DECIMAL(12,4),
    volume BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(symbol, date)
);

-- Technical indicators table
CREATE TABLE technical_indicators (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,

    -- Moving averages
    sma_10 DECIMAL(12,4),
    sma_20 DECIMAL(12,4),
    sma_50 DECIMAL(12,4),

    -- Momentum indicators
    rsi_14 DECIMAL(6,2),
    macd DECIMAL(12,4),
    macd_signal DECIMAL(12,4),
    macd_histogram DECIMAL(12,4),

    -- Volatility indicators
    bollinger_upper DECIMAL(12,4),
    bollinger_lower DECIMAL(12,4),
    atr_14 DECIMAL(12,4),

    -- Breakout indicators
    donchian_high_20 DECIMAL(12,4),
    donchian_low_20 DECIMAL(12,4),
    donchian_mid_20 DECIMAL(12,4),

    -- Volume indicators
    volume_sma_10 BIGINT,
    volume_ratio DECIMAL(8,2),

    -- Position indicators
    price_position DECIMAL(6,2),
    channel_width_pct DECIMAL(6,2),

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(symbol, date)
);

-- Daily fundamentals table
CREATE TABLE daily_fundamentals (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,

    -- Market data
    market_cap BIGINT,
    shares_outstanding BIGINT,
    float_shares BIGINT,

    -- Valuation ratios
    pe_ratio DECIMAL(8,2),
    pb_ratio DECIMAL(8,2),
    ps_ratio DECIMAL(8,2),
    peg_ratio DECIMAL(8,2),

    -- Risk metrics
    beta DECIMAL(6,3),
    dividend_yield DECIMAL(6,3),

    -- Company info
    sector VARCHAR(50),
    industry VARCHAR(100),

    -- ML-derived scores
    growth_score DECIMAL(4,2),
    profitability_score DECIMAL(4,2),
    financial_health_score DECIMAL(4,2),
    valuation_score DECIMAL(4,2),
    overall_quality_score DECIMAL(4,2),
    quality_grade CHAR(1),

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(symbol, date)
);

-- Quarterly fundamentals table
CREATE TABLE quarterly_fundamentals (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    quarter DATE NOT NULL,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,

    -- Income statement
    revenue BIGINT,
    gross_profit BIGINT,
    operating_income BIGINT,
    net_income BIGINT,
    ebitda BIGINT,
    eps DECIMAL(8,4),

    -- Balance sheet
    total_assets BIGINT,
    total_debt BIGINT,
    total_equity BIGINT,
    current_assets BIGINT,
    current_liabilities BIGINT,
    cash BIGINT,

    -- Cash flow
    operating_cash_flow BIGINT,
    free_cash_flow BIGINT,
    capital_expenditures BIGINT,

    -- Calculated ratios
    gross_margin DECIMAL(6,2),
    operating_margin DECIMAL(6,2),
    net_margin DECIMAL(6,2),
    roe DECIMAL(6,2),
    roa DECIMAL(6,2),
    debt_to_equity DECIMAL(6,2),
    current_ratio DECIMAL(6,2),

    -- Growth rates
    revenue_growth_yoy DECIMAL(6,2),
    eps_growth_yoy DECIMAL(6,2),

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(symbol, quarter)
);

-- Breakouts table (ML training data)
CREATE TABLE breakouts (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    date DATE NOT NULL,
    breakout_type VARCHAR(10) NOT NULL,

    -- Entry conditions
    entry_price DECIMAL(12,4),
    volume_ratio DECIMAL(8,2),
    atr_pct DECIMAL(6,2),
    rsi_value DECIMAL(6,2),
    price_change_pct DECIMAL(6,2),

    -- Labels (for ML)
    success BOOLEAN,
    max_gain_10d DECIMAL(6,2),
    max_loss_10d DECIMAL(6,2),
    days_to_peak INTEGER,

    created_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(symbol, date, breakout_type)
);

-- ML models table
CREATE TABLE ml_models (
    id BIGSERIAL PRIMARY KEY,
    model_name VARCHAR(50) NOT NULL,
    version VARCHAR(20) NOT NULL,
    model_type VARCHAR(30),

    -- Training data info
    training_start_date DATE,
    training_end_date DATE,
    training_samples INTEGER,

    -- Performance metrics
    accuracy DECIMAL(6,4),
    precision_score DECIMAL(6,4),
    recall_score DECIMAL(6,4),
    f1_score DECIMAL(6,4),
    auc_score DECIMAL(6,4),

    -- Model files
    model_file_path VARCHAR(500),
    scaler_file_path VARCHAR(500),
    feature_names TEXT,

    -- Status
    is_active BOOLEAN DEFAULT false,
    deployment_date TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(model_name, version)
);

-- Data updates log
CREATE TABLE data_updates (
    id BIGSERIAL PRIMARY KEY,
    update_type VARCHAR(30) NOT NULL,
    update_date DATE NOT NULL,

    -- Results
    symbols_targeted INTEGER,
    symbols_successful INTEGER,
    symbols_failed INTEGER,

    -- Timing
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_seconds INTEGER,

    -- Status
    status VARCHAR(20),
    error_message TEXT,

    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================================
-- 2. CREATE INDEXES FOR PERFORMANCE
-- =============================================================================

-- Stock prices indexes
CREATE INDEX idx_stock_prices_symbol ON stock_prices(symbol);
CREATE INDEX idx_stock_prices_date ON stock_prices(date DESC);
CREATE INDEX idx_stock_prices_symbol_date ON stock_prices(symbol, date DESC);

-- Technical indicators indexes
CREATE INDEX idx_technical_symbol_date ON technical_indicators(symbol, date DESC);
CREATE INDEX idx_technical_donchian ON technical_indicators(donchian_high_20, donchian_low_20) WHERE donchian_high_20 IS NOT NULL;

-- Daily fundamentals indexes
CREATE INDEX idx_daily_fundamentals_symbol_date ON daily_fundamentals(symbol, date DESC);
CREATE INDEX idx_daily_fundamentals_quality ON daily_fundamentals(quality_grade) WHERE quality_grade IS NOT NULL;
CREATE INDEX idx_daily_fundamentals_score ON daily_fundamentals(overall_quality_score DESC) WHERE overall_quality_score IS NOT NULL;

-- Quarterly fundamentals indexes
CREATE INDEX idx_quarterly_fundamentals_symbol_quarter ON quarterly_fundamentals(symbol, quarter DESC);

-- Breakouts indexes
CREATE INDEX idx_breakouts_symbol_date ON breakouts(symbol, date DESC);
CREATE INDEX idx_breakouts_type_success ON breakouts(breakout_type, success);
CREATE INDEX idx_breakouts_date ON breakouts(date DESC);

-- =============================================================================
-- 3. CREATE USEFUL FUNCTIONS
-- =============================================================================

-- Function to get last update date for a symbol
CREATE OR REPLACE FUNCTION get_last_update_date(symbol_param VARCHAR(10))
RETURNS DATE AS $$
BEGIN
    RETURN (
        SELECT MAX(date)
        FROM stock_prices
        WHERE symbol = symbol_param
    );
END;
$$ LANGUAGE plpgsql;

-- Function to check data freshness (days behind)
CREATE OR REPLACE FUNCTION check_data_freshness(symbol_param VARCHAR(10))
RETURNS INTEGER AS $$
BEGIN
    RETURN (
        SELECT EXTRACT(DAY FROM NOW() - MAX(date))::INTEGER
        FROM stock_prices
        WHERE symbol = symbol_param
    );
END;
$$ LANGUAGE plpgsql;

-- Function to get latest fundamentals for a symbol
CREATE OR REPLACE FUNCTION get_latest_fundamentals(symbol_param VARCHAR(10))
RETURNS TABLE (
    symbol VARCHAR(10),
    date DATE,
    overall_quality_score DECIMAL(4,2),
    quality_grade CHAR(1),
    pe_ratio DECIMAL(8,2),
    sector VARCHAR(50)
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        df.symbol,
        df.date,
        df.overall_quality_score,
        df.quality_grade,
        df.pe_ratio,
        df.sector
    FROM daily_fundamentals df
    WHERE df.symbol = symbol_param
    AND df.quality_grade IS NOT NULL
    ORDER BY df.date DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 4. CREATE USEFUL VIEWS
-- =============================================================================

-- Latest stock data view
CREATE VIEW latest_stock_data AS
SELECT DISTINCT ON (symbol)
    symbol, date, open, high, low, close, adj_close, volume
FROM stock_prices
ORDER BY symbol, date DESC;

-- Latest fundamentals view
CREATE VIEW latest_fundamentals AS
SELECT DISTINCT ON (symbol)
    symbol, date, market_cap, pe_ratio, pb_ratio,
    overall_quality_score, quality_grade, sector
FROM daily_fundamentals
WHERE quality_grade IS NOT NULL
ORDER BY symbol, date DESC;

-- ML training ready data view
CREATE VIEW ml_training_data AS
SELECT
    b.*,
    df.overall_quality_score,
    df.quality_grade,
    df.sector,
    ti.rsi_14,
    ti.volume_ratio as tech_volume_ratio
FROM breakouts b
LEFT JOIN daily_fundamentals df ON b.symbol = df.symbol AND b.date = df.date
LEFT JOIN technical_indicators ti ON b.symbol = ti.symbol AND b.date = ti.date
WHERE b.created_at >= NOW() - INTERVAL '2 years';

-- =============================================================================
-- 5. GRANT PERMISSIONS
-- =============================================================================

-- Grant all necessary permissions to trading_user
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO trading_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO trading_user;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO trading_user;
GRANT ALL PRIVILEGES ON ALL VIEWS IN SCHEMA public TO trading_user;

-- Set default privileges for future objects
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO trading_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO trading_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO trading_user;

-- =============================================================================
-- 6. VERIFICATION QUERIES
-- =============================================================================

-- Verify tables were created
SELECT
    schemaname,
    tablename,
    tableowner
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

-- Verify indexes were created
SELECT
    indexname,
    tablename,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;

-- Verify functions were created
SELECT
    proname as function_name,
    pg_get_function_result(oid) as return_type
FROM pg_proc
WHERE pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
AND proname LIKE '%update%' OR proname LIKE '%fresh%' OR proname LIKE '%fundamental%';

-- Test functions
SELECT get_last_update_date('AAPL'); -- Should return NULL initially
SELECT check_data_freshness('AAPL'); -- Should return NULL initially

PRINT 'PostgreSQL schema created successfully!';
PRINT 'Ready for data migration from SQLite.';