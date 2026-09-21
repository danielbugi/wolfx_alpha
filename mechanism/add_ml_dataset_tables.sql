-- ML dataset v2 + price-integrity registry (added 2026-09-20).
-- New tables only; nothing existing is altered. Safe to re-run.

-- Days where the stored price series is not a trustworthy continuous path
-- (unadjusted reverse split, ticker reuse, non-positive/inconsistent bars).
-- Detected by ml_training/features/price_features.find_discontinuities();
-- populated by ml_training/data_preparation/build_dataset.py. Samples whose
-- feature lookback (253 bars) or 20-bar label window cross a row here are
-- excluded from ML training and from ML inference -- never "repaired".
CREATE TABLE IF NOT EXISTS price_discontinuities (
    symbol      VARCHAR(20)  NOT NULL,
    date        DATE         NOT NULL,
    kind        VARCHAR(20)  NOT NULL,      -- jump_up | jump_down | nonpositive | ohlc_inconsistent
    prev_close  NUMERIC,
    close       NUMERIC,
    ratio       NUMERIC,
    detected_at TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, date, kind)
);
CREATE INDEX IF NOT EXISTS idx_price_disc_symbol_date ON price_discontinuities (symbol, date);

-- One row per Donchian breakout bar, computed ONLY from stock_prices by the shared
-- feature module (ml_training/features/price_features.py). Labels:
--   momentum_score / target_binary : legacy composite (>=65), kept for continuity
--   plan_r / r_single / stopped / tp3_hit / mae_r : outcome of the exact trade plan
--        the UI shows (2xATR stop, 2/4/6xATR targets, 20-bar exit), in R units
-- NULL label = window not yet mature (never defaulted).
CREATE TABLE IF NOT EXISTS ml_breakout_dataset_v2 (
    symbol              VARCHAR(20)      NOT NULL,
    date                DATE             NOT NULL,
    direction           SMALLINT         NOT NULL,   -- +1 bullish, -1 bearish
    feature_set_version VARCHAR(20)      NOT NULL,
    features            JSONB            NOT NULL,
    momentum_score      DOUBLE PRECISION,
    target_binary       SMALLINT,
    plan_r              DOUBLE PRECISION,
    r_single            DOUBLE PRECISION,
    stopped             SMALLINT,
    tp3_hit             SMALLINT,
    mae_r               DOUBLE PRECISION,
    created_at          TIMESTAMP        NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_ml_ds_v2_date ON ml_breakout_dataset_v2 (date);
