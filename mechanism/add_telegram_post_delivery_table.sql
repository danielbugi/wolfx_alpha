-- Post-market package idempotency (added 2026-09-25). New table only; safe to re-run.
--
-- telegram_post_delivery: one row per (market_session, post_kind, target) -- the explicit US trading-session date, NOT
--   the Israel-local date the send happened on (a retry after midnight Israel time must still target the same session).
--   This is the durable claim/delivery record the post-market publisher (mechanism/alerts/publish_post_market.py) uses
--   instead of the old coarse per-script session_state.json key, so a partial failure (e.g. digest + board sent, top
--   gainers failed) can be retried without re-sending what already went out, and two racing processes cannot both
--   send the same post.
--
-- status: 'reserved' (claimed, send in progress or crashed mid-send) | 'sent' (delivered, message_id known) |
--   'failed' (attempted, Telegram/transport error -- safe to reclaim on the next retry). A row is NEVER moved to
--   'sent' except by the process that actually received a successful Telegram API response.
--
-- The UNIQUE constraint is the concurrency guard: claim() is a single atomic
--   INSERT ... ON CONFLICT (market_session, post_kind, target) DO UPDATE ... WHERE <reclaimable> RETURNING id
-- which only returns a row (i.e. only succeeds) when no row exists yet, or the existing row is 'failed', or a
-- 'reserved' row has gone stale (its claimant crashed without ever marking sent/failed) -- never when another
-- process's claim is fresh, and never when the post was already delivered. See mechanism/alerts/post_delivery.py.
CREATE TABLE IF NOT EXISTS telegram_post_delivery (
    id             BIGSERIAL    PRIMARY KEY,
    market_session DATE         NOT NULL,
    post_kind      VARCHAR(30)  NOT NULL,
    target         VARCHAR(8)   NOT NULL CHECK (target IN ('dev', 'prod')),
    status         VARCHAR(8)   NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'sent', 'failed')),
    message_id     BIGINT,
    claimed_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at        TIMESTAMPTZ,
    attempts       INTEGER      NOT NULL DEFAULT 1,
    last_error     VARCHAR(200),
    UNIQUE (market_session, post_kind, target)
);
CREATE INDEX IF NOT EXISTS idx_telegram_post_delivery_session ON telegram_post_delivery (market_session DESC, target);
