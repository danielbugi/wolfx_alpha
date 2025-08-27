-- Multi-Timeframe Extensions
-- File Location: mechanism/add_multi_timeframe_tables.sql
-- Run this AFTER your existing create_trading_schema.sql

-- Weekly Technical Indicators Table  
CREATE TABLE weekly_technical_indicators (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    week_ending_date DATE NOT NULL,
    
    -- Weekly Donchian (20-week period)
    donchian_high_20w DECIMAL(12,4),
    donchian_low_20w DECIMAL(12,4),
    donchian_mid_20w DECIMAL(12,4),
    
    -- Weekly OHLCV
    weekly_open DECIMAL(12,4),
    weekly_high DECIMAL(12,4), 
    weekly_low DECIMAL(12,4),
    weekly_close DECIMAL(12,4),
    weekly_volume BIGINT,
    
    -- Weekly Moving Averages
    sma_10w DECIMAL(12,4),
    sma_20w DECIMAL(12,4),
    
    -- Weekly Indicators
    rsi_14w DECIMAL(6,2),
    volume_ratio_weekly DECIMAL(8,2),
    price_position_weekly DECIMAL(6,2),
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(symbol, week_ending_date)
);

-- Monthly Technical Indicators Table
CREATE TABLE monthly_technical_indicators (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    month_ending_date DATE NOT NULL,
    
    -- Monthly Donchian (12-month period)
    donchian_high_12m DECIMAL(12,4),
    donchian_low_12m DECIMAL(12,4),
    donchian_mid_12m DECIMAL(12,4),
    
    -- Monthly OHLCV
    monthly_open DECIMAL(12,4),
    monthly_high DECIMAL(12,4),
    monthly_low DECIMAL(12,4),
    monthly_close DECIMAL(12,4),
    monthly_volume BIGINT,
    
    -- Monthly Trend Analysis
    trend_direction VARCHAR(20), -- 'bullish', 'bearish', 'sideways'
    trend_strength_6m DECIMAL(6,2),
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    UNIQUE(symbol, month_ending_date)
);

-- Indexes for performance
CREATE INDEX idx_weekly_tech_symbol_date ON weekly_technical_indicators(symbol, week_ending_date DESC);
CREATE INDEX idx_monthly_tech_symbol_date ON monthly_technical_indicators(symbol, month_ending_date DESC);
