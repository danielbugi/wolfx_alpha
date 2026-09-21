-- Private-assistant access layer (added 2026-09-21, PRIVATE_ASSISTANT_PLAN.md phase 7.0). New tables only; safe to re-run.
--
-- bot_access: who may use the bot at all. The OWNER is not stored here (BOT_OWNER_ID in .env), so a database problem can never lock
--   the owner out. status: 'active' = may use the bot, 'revoked' = blocked (kept so the block survives), 'pending' is reserved for a
--   later request/paid flow and is not written by the current code.
-- bot_invites: one-time invitation links (t.me/<bot>?start=inv_<code>). Only a SHA-256 hash of the code is stored, so a database
--   read cannot yield a usable link.
-- bot_audit: administrative actions only (who / when / what / which user id). Never holdings, never message text.
-- bot_user_settings (plan section 5) is NOT created yet: nothing reads it before phase 7.2.

CREATE TABLE IF NOT EXISTS bot_access (
    telegram_user_id BIGINT      PRIMARY KEY,
    status           VARCHAR(10) NOT NULL CHECK (status IN ('pending', 'active', 'revoked')),
    tier             VARCHAR(10) NOT NULL DEFAULT 'member' CHECK (tier IN ('member', 'admin')),
    invited_by       BIGINT,                                   -- the owner's id for now
    approved_at      TIMESTAMPTZ,
    revoked_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_invites (
    code_hash  CHAR(64)    PRIMARY KEY,                        -- sha256 hex of the code inside the link
    created_by BIGINT      NOT NULL,
    tier       VARCHAR(10) NOT NULL DEFAULT 'member' CHECK (tier IN ('member', 'admin')),
    max_uses   INTEGER     NOT NULL DEFAULT 1 CHECK (max_uses > 0),
    uses       INTEGER     NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    note       VARCHAR(60),                                    -- the owner's own label, e.g. "Dana"
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_audit (
    id     BIGSERIAL   PRIMARY KEY,
    at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor  BIGINT      NOT NULL,
    action VARCHAR(20) NOT NULL,                               -- invite | approve | revoke | redeem | purge
    target BIGINT,
    detail VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS idx_bot_audit_at ON bot_audit (at);
