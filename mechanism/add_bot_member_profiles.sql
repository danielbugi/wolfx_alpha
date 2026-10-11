-- mechanism/add_bot_member_profiles.sql
-- Migration 34 (numbers 26-32 are taken by lab/first-light-algo, 33 is widen_news_items_image_url.sql).
--
-- Bot member management from the dashboard (/telegram page):
--   * first_name / username on bot_users (members) and bot_requests (people who tapped "Request access"), mirrored from the
--     person's public Telegram profile and refreshed whenever they use the bot, so the owner sees WHO has access, not only an id.
--     Strangers who never tapped "Request access" are still never stored. Revoked members' rows (and with them these columns)
--     are deleted by the existing 30-day purge; a decided request row goes with its cool-down purge.
--   * bot_settings: the access mode (approve | auto | closed) and the member cap, switchable from the dashboard without a
--     restart. A missing row means the bot's .env value (BOT_ACCESS_MODE / BOT_MAX_MEMBERS) still applies.
-- Additive and idempotent: safe to re-run, nothing existing changes. The code works before this is applied (names are simply
-- not shown, and the mode cannot be switched from the dashboard).

ALTER TABLE bot_users    ADD COLUMN IF NOT EXISTS first_name VARCHAR(64);
ALTER TABLE bot_users    ADD COLUMN IF NOT EXISTS username   VARCHAR(32);
ALTER TABLE bot_requests ADD COLUMN IF NOT EXISTS first_name VARCHAR(64);
ALTER TABLE bot_requests ADD COLUMN IF NOT EXISTS username   VARCHAR(32);

CREATE TABLE IF NOT EXISTS bot_settings (
    key        VARCHAR(40)  PRIMARY KEY,
    value      VARCHAR(200) NOT NULL,
    updated_by BIGINT,
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
