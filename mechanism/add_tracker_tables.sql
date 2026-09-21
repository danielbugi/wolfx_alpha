-- Tracker core of the private assistant (added 2026-09-21, BOT_DESIGN_REPORT.md). New tables only; safe to re-run.
--
-- bot_tracked: a user's Watchlist ('watch') and Portfolio ('hold'). ONE row per (user, symbol). "Performance since added" is measured
--   from ref_price, which is either the price the user typed ('entered') or the last close at that moment ('close'). shares is
--   optional and only meaningful for 'hold'. first_added_at / first_price survive a move from the watchlist to the portfolio.
--   Rows go with the user (ON DELETE CASCADE from bot_users), so the revoked-user purge and /deleteme need no extra code.
-- news_items / news_fetched: headlines fetched ONCE per symbol per few hours and shared by every user (cost grows with unique
--   symbols, not users). Headline + link only, never article text.
-- bot_chart_cache: the Telegram file_id of the chart already uploaded for (symbol, session), so a chart is rendered and uploaded
--   once per symbol per session and every later request re-sends the id.
-- bot_watchlist (older, symbol + added_at only) is superseded by bot_tracked and no longer read.

CREATE TABLE IF NOT EXISTS bot_tracked (
    telegram_user_id BIGINT        NOT NULL REFERENCES bot_users (telegram_user_id) ON DELETE CASCADE,
    symbol           VARCHAR(20)   NOT NULL,
    kind             VARCHAR(5)    NOT NULL CHECK (kind IN ('watch', 'hold')),
    ref_price        NUMERIC(14,4) NOT NULL CHECK (ref_price > 0),
    ref_source       VARCHAR(8)    NOT NULL CHECK (ref_source IN ('entered', 'close')),
    ref_date         DATE          NOT NULL,
    shares           NUMERIC(18,6) CHECK (shares IS NULL OR shares > 0),
    first_added_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    first_price      NUMERIC(14,4) NOT NULL,
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (telegram_user_id, symbol)
);

CREATE TABLE IF NOT EXISTS news_items (
    id           BIGSERIAL    PRIMARY KEY,
    symbol       VARCHAR(20)  NOT NULL,
    published_at TIMESTAMPTZ  NOT NULL,
    headline     VARCHAR(300) NOT NULL,
    source       VARCHAR(60),
    url          VARCHAR(1000) NOT NULL,
    url_hash     CHAR(64)     NOT NULL,
    fetched_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, url_hash)
);
CREATE INDEX IF NOT EXISTS idx_news_items_symbol_pub ON news_items (symbol, published_at DESC);

CREATE TABLE IF NOT EXISTS news_fetched (
    symbol     VARCHAR(20)  PRIMARY KEY,
    fetched_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ok         BOOLEAN      NOT NULL DEFAULT TRUE          -- FALSE = the provider failed; retried after a short back-off
);

CREATE TABLE IF NOT EXISTS bot_chart_cache (
    symbol       VARCHAR(20) NOT NULL,
    session_date DATE        NOT NULL,
    file_id      TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, session_date)
);
