-- "First Light" digest snapshot + interactive-bot tables (added 2026-09-21). New tables only; safe to re-run.
--
-- digest_runs / digest_stocks: ONE precomputed snapshot per US session, written by send_daily_digest.py. Every bot
-- reply is a read from here -- the bot never calls a price provider on a user's behalf (cost must not grow with the
-- audience). digest_stocks holds every liquid stock priced on the session (not only the ones in a group), so a
-- watchlist can report on any name. It is also the evidence base for the future scoreboard (which lists earn their place).
--
-- bot_users / bot_watchlist: the only per-person data the bot stores (Telegram user id, when they acknowledged that the
-- content is educational, and the symbols they follow). No names, no messages.

CREATE TABLE IF NOT EXISTS digest_runs (
    session_date  DATE PRIMARY KEY,                    -- the US session the snapshot describes
    created_at    TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    universe_n    INTEGER          NOT NULL,           -- liquid stocks priced on the session date
    up_n          INTEGER          NOT NULL,
    down_n        INTEGER          NOT NULL,
    counts        JSONB            NOT NULL,           -- {"breakout": 51, "near_breakout": 282}
    index_lines   JSONB,                               -- ["S&P 500 +0.2%", "VIX (volatility index) 14.8"]
    min_dv        DOUBLE PRECISION NOT NULL,           -- $ volume floor used for the ranked lists
    top_n         INTEGER          NOT NULL
);

CREATE TABLE IF NOT EXISTS digest_stocks (
    session_date    DATE             NOT NULL REFERENCES digest_runs (session_date) ON DELETE CASCADE,
    symbol          VARCHAR(20)      NOT NULL,
    category        VARCHAR(16),                       -- breakout | near_breakout | breakdown | near_breakdown | NULL = no group
    prev_category   VARCHAR(16),
    close           DOUBLE PRECISION NOT NULL,
    ret1_pct        DOUBLE PRECISION NOT NULL,
    rvol            DOUBLE PRECISION,                  -- today's volume / median of the prior 50 sessions
    range_atr       DOUBLE PRECISION,                  -- today's true range / prior-day ATR14
    below_high_pct  DOUBLE PRECISION,                  -- % the close is below the 20-day high
    dv20            DOUBLE PRECISION NOT NULL,         -- prior-20-session average dollar volume
    list_ranks      JSONB,                             -- {"gainers": 1, "volume": 4} = rank in each reported list, else NULL
    PRIMARY KEY (session_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_digest_stocks_symbol ON digest_stocks (symbol, session_date);

CREATE TABLE IF NOT EXISTS bot_users (
    telegram_user_id BIGINT      PRIMARY KEY,
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at  TIMESTAMPTZ,                      -- tapped "I understand this is educational information, not advice"
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_watchlist (
    telegram_user_id BIGINT      NOT NULL REFERENCES bot_users (telegram_user_id) ON DELETE CASCADE,
    symbol           VARCHAR(20) NOT NULL,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (telegram_user_id, symbol)
);

-- 2026-09-21: the bot's ATR risk framework (/levels) needs the ATR value itself (range_atr above is a RATIO). Additive, re-runnable.
ALTER TABLE digest_stocks ADD COLUMN IF NOT EXISTS atr DOUBLE PRECISION;   -- Wilder ATR(14) of the session bar, in price units
