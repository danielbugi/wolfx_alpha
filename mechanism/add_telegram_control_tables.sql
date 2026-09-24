-- Telegram Control Center (added 2026-09-21, TELEGRAM_CONTROL_MILESTONES.md). New tables only; safe to re-run.
--
-- telegram_messages: a ledger of every message sent to a CHANNEL target (dev / prod). A bot cannot read a channel's history, so this is the only
--   way to list, edit or delete what was posted. Written by TelegramClient (the one place every send goes through). The private chat with the
--   owner ('owner' target) and every assistant conversation are NEVER recorded here.
--   text = the current text (or photo caption) exactly as Telegram received it (HTML); original_text = as first sent (kept so an edit can be undone).
--   reply_markup = the inline keyboard sent with it (an edit that omits it would remove the buttons, so it is re-sent).
--   status: 'sent' | 'deleted'. "edited" is derived (edit_count > 0). pinned = pinned BY US (someone unpinning in the app is invisible to a bot).
-- telegram_control_audit: what the control page did (edit / delete), and how Telegram answered. Never message bodies, never tokens.

CREATE TABLE IF NOT EXISTS telegram_messages (
    id              BIGSERIAL    PRIMARY KEY,
    target          VARCHAR(8)   NOT NULL CHECK (target IN ('dev', 'prod')),
    chat_id         TEXT         NOT NULL,
    message_id      BIGINT       NOT NULL,
    kind            VARCHAR(30)  NOT NULL DEFAULT 'other',
    content_type    VARCHAR(5)   NOT NULL CHECK (content_type IN ('text', 'photo')),
    text            TEXT         NOT NULL,
    original_text   TEXT         NOT NULL,
    reply_markup    JSONB,
    silent          BOOLEAN      NOT NULL DEFAULT FALSE,
    disable_preview BOOLEAN      NOT NULL DEFAULT TRUE,
    pinned          BOOLEAN      NOT NULL DEFAULT FALSE,
    status          VARCHAR(8)   NOT NULL DEFAULT 'sent' CHECK (status IN ('sent', 'deleted')),
    edit_count      INTEGER      NOT NULL DEFAULT 0,
    sent_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    edited_at       TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ,
    UNIQUE (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_telegram_messages_target_sent ON telegram_messages (target, sent_at DESC);

CREATE TABLE IF NOT EXISTS telegram_control_audit (
    id         BIGSERIAL   PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action     VARCHAR(10) NOT NULL CHECK (action IN ('edit', 'delete')),
    target     VARCHAR(8)  NOT NULL,
    chat_id    TEXT        NOT NULL,
    message_id BIGINT      NOT NULL,
    outcome    VARCHAR(10) NOT NULL CHECK (outcome IN ('done', 'unchanged', 'gone', 'refused', 'failed')),
    detail     VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_telegram_control_audit_msg ON telegram_control_audit (chat_id, message_id, at DESC);
