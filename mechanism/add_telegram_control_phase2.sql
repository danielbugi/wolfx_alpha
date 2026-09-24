-- Telegram Control Center phase 2 (added 2026-09-22, TELEGRAM_CONTROL_MILESTONES.md): compose, pin/unpin, photo replace, schedule view.
-- Only the audit table's allowed actions grow; telegram_messages needs no new columns (compose reuses the existing send/ledger path,
-- pin/unpin update the existing `pinned` column, photo replace reuses `text`/`edit_count` exactly like a text edit). Safe to re-run.

ALTER TABLE telegram_control_audit DROP CONSTRAINT IF EXISTS telegram_control_audit_action_check;
ALTER TABLE telegram_control_audit ADD CONSTRAINT telegram_control_audit_action_check
    CHECK (action IN ('edit', 'delete', 'pin', 'unpin'));
