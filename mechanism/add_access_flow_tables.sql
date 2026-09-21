-- Request-access flow + funnel counters (added 2026-09-21, FUNNEL_PLAN.md sections 3-4). New tables only; safe to re-run.
--
-- bot_requests: a person who TAPPED "Request access". Stored only after that explicit tap: their Telegram id and the time (no name, no
--   message). status 'pending' = waiting for the owner; 'declined' = the owner said no, kept only for the cool-down so the same person
--   cannot ask again at once. An approved request is DELETED (the person is then in bot_access). Old rows are purged daily
--   (pending after 14 days, declined after the cool-down). No foreign key: the person is not a bot user yet.
-- funnel_events: AGGREGATE daily counters only (event, day, count) - e.g. how often the channel button was opened. No user id is stored.

CREATE TABLE IF NOT EXISTS bot_requests (
    telegram_user_id BIGINT      PRIMARY KEY,
    status           VARCHAR(10) NOT NULL CHECK (status IN ('pending', 'declined')),
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_bot_requests_status ON bot_requests (status, requested_at);

CREATE TABLE IF NOT EXISTS funnel_events (
    day   DATE        NOT NULL,
    event VARCHAR(24) NOT NULL,                       -- opened_channel | requested | approved_auto | approved | declined
    n     INTEGER     NOT NULL DEFAULT 0,
    PRIMARY KEY (day, event)
);
