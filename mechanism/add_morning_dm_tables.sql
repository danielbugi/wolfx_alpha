-- Morning message settings (CHANNEL_CONTENT_MILESTONES.md M5.2). One row per member who asked for it; opt-in, default off.
-- Only a switch and the last session that was messaged: no message text, no stock symbols. Removed with the user (ON DELETE CASCADE),
-- with /deleteme, and with /morning off.
CREATE TABLE IF NOT EXISTS bot_user_settings (
    telegram_user_id BIGINT PRIMARY KEY REFERENCES bot_users(telegram_user_id) ON DELETE CASCADE,
    morning_dm       BOOLEAN NOT NULL DEFAULT FALSE,
    last_dm_session  DATE,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bot_user_settings_dm ON bot_user_settings (morning_dm) WHERE morning_dm;
