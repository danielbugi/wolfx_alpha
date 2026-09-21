-- Telegram alert ledger (added 2026-09-20). New table only; safe to re-run.
-- One row per alert actually SENT (dry runs are never recorded). Together with the outcome tracker
-- (next slice) this is the evidence base for which alert types work: every alert stores the plan and
-- the descriptive flags it carried, so forward results (+100% / +300% reached, R multiples) can be
-- measured per alert type instead of guessed.
CREATE TABLE IF NOT EXISTS alerts (
    id                  BIGSERIAL PRIMARY KEY,
    alert_date          DATE         NOT NULL,      -- the US session the alert describes
    symbol              VARCHAR(20)  NOT NULL,
    source              VARCHAR(30)  NOT NULL,      -- 'top_gainers'
    channel             VARCHAR(10)  NOT NULL,      -- 'dev' | 'prod'
    entry_ref           NUMERIC,                    -- last close, the reference entry
    atr                 NUMERIC,
    stop                NUMERIC,
    tp1                 NUMERIC,
    tp2                 NUMERIC,
    tp3                 NUMERIC,
    trail_pct           NUMERIC,
    plan                JSONB,                      -- full plan template used
    flags               JSONB,                      -- descriptive flags/features at alert time
    telegram_message_id BIGINT,
    sent_at             TIMESTAMP    NOT NULL DEFAULT NOW(),
    UNIQUE (alert_date, symbol, channel, source)
);
CREATE INDEX IF NOT EXISTS idx_alerts_date ON alerts (alert_date);
CREATE INDEX IF NOT EXISTS idx_alerts_symbol ON alerts (symbol, alert_date);
