-- mechanism/add_signal_ledger_tables.sql
-- Live outcome ledger: one row per daily breakout signal, written once at entry
-- and mutated only by mechanism/screeners/evaluate_signal_ledger.py as it walks
-- forward. This is the "verified track record" data source (see
-- docs/architecture/DATABASE.md and the 2026-09-27/28 monetization strategy
-- deck) -- distinct from the dead `breakouts` table and from
-- `ml_breakout_dataset_v2` (a batch-rebuilt ML training artifact with no
-- "still open" concept). Additive only, per CLAUDE.md's migration invariant --
-- every statement CREATE ... IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS signal_ledger (
    id                   BIGSERIAL PRIMARY KEY,
    symbol               VARCHAR(10) NOT NULL,
    signal_date          DATE NOT NULL,              -- the breakout session (explicit trading-session date)
    direction            SMALLINT NOT NULL CHECK (direction IN (1, -1)),   -- +1 bullish, -1 bearish

    entry_price          NUMERIC(12,4) NOT NULL,
    atr                  NUMERIC(12,4) NOT NULL CHECK (atr > 0),

    -- Precomputed at write time from mechanism/shared/trade_plan.py's constants, so a later
    -- constant change never silently reinterprets an already-open row.
    stop_price           NUMERIC(12,4) NOT NULL,
    target1_price        NUMERIC(12,4) NOT NULL,     -- 1R  (2 x ATR)
    target2_price        NUMERIC(12,4) NOT NULL,     -- 2R  (4 x ATR)
    target3_price        NUMERIC(12,4) NOT NULL,     -- 3R  (6 x ATR)

    sector               VARCHAR(60),                -- denormalized at signal time: the signal's context
    quality_grade        VARCHAR(4),                 -- then, not fundamentals as they drift later

    status               VARCHAR(10) NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'stopped', 'target1', 'target2', 'target3', 'expired')),
    outcome_r            NUMERIC(6,3),                -- mean of 3 tranches once resolved; NULL while open
    mae_r                NUMERIC(6,3),                -- max adverse excursion in R, updated every evaluation pass
    resolved_date        DATE,
    bars_held            SMALLINT,

    last_evaluated_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (symbol, signal_date, direction)
);

-- The evaluator's entire daily working set is "still open" rows -- this partial index keeps
-- that scan cheap regardless of how large the ledger grows.
CREATE INDEX IF NOT EXISTS idx_signal_ledger_open
    ON signal_ledger (last_evaluated_date)
    WHERE status = 'open';

-- For track-record summary/cohort queries (backend/services/track_record_service.py).
CREATE INDEX IF NOT EXISTS idx_signal_ledger_session
    ON signal_ledger (signal_date DESC);
