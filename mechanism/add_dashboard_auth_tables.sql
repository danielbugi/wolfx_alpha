-- Dashboard authentication + RBAC (added 2026-09-22, PLATFORM_ARCHITECTURE.md). New tables only; safe to re-run.
--
-- dashboard_users: the two people who may use the FastAPI backend / Next.js dashboard at all (today: one Owner, one Collaborator).
--   Passwords are bcrypt hashes, never plaintext. role gates what the API allows (see backend/auth/dependencies.py):
--   'owner' = everything, incl. Telegram channel mutations; 'collaborator' = read-only across the whole app.
-- dashboard_login_challenges: one row per in-progress login (password already checked, 2FA code emailed, not yet verified).
--   Both challenge_token and code are only ever stored as their sha256 hash - a stolen row cannot be replayed.
-- dashboard_sessions: refresh tokens, hashed, revocable - the Owner can force a Collaborator's session to end immediately from /users
--   (see backend/routers/auth.py's DELETE /sessions/{id}) without waiting for the access token to expire.
-- dashboard_auth_audit: who logged in, who was locked out, who created/deactivated a user, who revoked a session. Never a password, code or token.

CREATE TABLE IF NOT EXISTS dashboard_users (
    id             BIGSERIAL    PRIMARY KEY,
    email          TEXT         NOT NULL UNIQUE,
    password_hash  TEXT         NOT NULL,
    role           VARCHAR(12)  NOT NULL CHECK (role IN ('owner', 'collaborator')),
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_login_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS dashboard_login_challenges (
    id                   BIGSERIAL    PRIMARY KEY,
    user_id              BIGINT       NOT NULL REFERENCES dashboard_users(id) ON DELETE CASCADE,
    challenge_token_hash CHAR(64)     NOT NULL UNIQUE,
    code_hash            CHAR(64)     NOT NULL,
    expires_at           TIMESTAMPTZ  NOT NULL,
    attempts             INTEGER      NOT NULL DEFAULT 0,
    consumed_at          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dashboard_login_challenges_user ON dashboard_login_challenges (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS dashboard_sessions (
    id                  BIGSERIAL    PRIMARY KEY,
    user_id             BIGINT       NOT NULL REFERENCES dashboard_users(id) ON DELETE CASCADE,
    refresh_token_hash  CHAR(64)     NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ  NOT NULL,
    revoked_at          TIMESTAMPTZ,
    user_agent          TEXT,
    last_used_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dashboard_sessions_user ON dashboard_sessions (user_id, revoked_at);

CREATE TABLE IF NOT EXISTS dashboard_auth_audit (
    id             BIGSERIAL   PRIMARY KEY,
    at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action         VARCHAR(30) NOT NULL CHECK (action IN (
                       'login_password_ok', 'login_password_fail', 'login_code_ok', 'login_code_fail',
                       'login_locked', 'refresh', 'logout', 'session_revoked', 'user_created', 'user_deactivated',
                       'user_reactivated'
                   )),
    user_id        BIGINT REFERENCES dashboard_users(id) ON DELETE SET NULL,
    actor_user_id  BIGINT REFERENCES dashboard_users(id) ON DELETE SET NULL,
    detail         VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_dashboard_auth_audit_user ON dashboard_auth_audit (user_id, at DESC);
