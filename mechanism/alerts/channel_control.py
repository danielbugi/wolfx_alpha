# mechanism/alerts/channel_control.py
"""
The rules of the Telegram Control Center (TELEGRAM_CONTROL_MILESTONES.md): what the owner may see, edit and delete of what First Light posted to a
channel. Framework-free (the FastAPI router in backend/ is a thin adapter over this), so every rule is unit-tested without a server or a network.

Rules (each is tested in tests/test_channel_control.py)
  * Production is locked: while PROD_SENDING_ENABLED != 1 a prod message can be LISTED but never edited or deleted (view only). The lock also lives
    inside TelegramClient.from_env, so even a bug here could not reach the public channel.
  * A ledger row is acted on only if it belongs to the chat that is CONFIGURED for its target right now. The dev channel has been replaced before
    (a fresh channel is the only clean reset); message ids are per chat, so acting on an old row through the new chat id would hit a DIFFERENT message.
  * Delete: Telegram does not let a bot delete a message older than 48 h (found the hard way, dev_chat_reset.py) - refused with the reason instead of
    pretending. Edit has no such window.
  * Edit: the ledger's stored inline keyboard is sent again (Telegram removes a keyboard an edit does not resend); text <= 4096 and a photo caption
    <= 1024 parsed characters; an unchanged text is refused; a wording-guard word the message did not already contain must be acknowledged.
  * Every edit / delete attempt is written to telegram_control_audit with Telegram's answer (never the message body, never a token).
  * Unknown is null: nothing in an overview is estimated (e.g. `tracking_since` is the first recorded send, not a guess).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from alerts import schedule as sched
from alerts import telegram_client as tc
from alerts.message_ledger import MAX_PAGE as PAGE_MAX
from alerts.message_ledger import STATUSES, TARGETS

DELETE_WINDOW = timedelta(hours=48)
DEFAULT_TZ = "Asia/Jerusalem"
DEFAULT_KIND = "manual"                # a post composed by hand from this page, distinct from every script-originated kind
MAX_UPLOAD_BYTES = 10_000_000          # Telegram's own limit for a photo a bot uploads directly (not by file_id/URL)

TARGET_LABELS = {"dev": "Dev channel", "prod": "Production channel"}
KIND_LABELS = {
    "digest_card": "Daily digest - market card", "digest_header": "Daily digest - header", "digest_list": "Daily digest - list",
    "board": "Momentum board", "health": "Market health", "sector": "Sector rotation", "macro": "Beyond stocks", "gaps": "Gaps and volume",
    "near_highs": "Near highs", "aligned": "Longer-timeframe breakouts", "base_rate": "Base rates", "recap": "Weekly recap",
    "news": "News", "scoreboard": "List scoreboard", "promo": "Assistant promotion", "assistant": "Assistant notice",
    "disclaimer": "Disclaimer notice", "start_here": "Start here (pinned)", "alert_header": "Legacy alerts - header",
    "alert_card": "Legacy alerts - card", "manual": "Manual post", "other": "Other"}

# The project's wording guard (advice-style words are banned in every channel text). Kept identical to the pattern the test-suite enforces on the
# copy itself (test_channel_control.py asserts they cannot drift) so a hand-edit cannot smuggle in what the generated posts are forbidden to say.
ADVICE_WORDS = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                          r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)


class ControlError(Exception):
    """A refused action. `code` is stable (the UI branches on it), `http` is the status the API adapter answers with."""
    HTTP = {"not_found": 404, "locked": 423, "wrong_chat": 409, "deleted": 409, "too_old": 409, "not_configured": 409,
            "bad_filter": 422, "empty": 422, "too_long": 422, "unchanged": 422, "wording": 422, "telegram": 502}

    def __init__(self, code: str, message: str, warnings: Optional[List[str]] = None):
        super().__init__(message)
        self.code, self.message, self.warnings = code, message, warnings or []
        self.http = self.HTTP.get(code, 400)


@dataclass(frozen=True)
class Decision:
    ok: bool
    code: Optional[str] = None
    reason: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "code": self.code, "reason": self.reason}


ALLOWED = Decision(True)


def wording_hits(html_text: str) -> List[str]:
    """The distinct advice-style words in a message, lower-cased and sorted."""
    return sorted({m.group(0).lower() for m in ADVICE_WORDS.finditer(tc.plain_text(html_text))})


def preview_line(html_text: str, width: int = 140) -> str:
    for line in tc.plain_text(html_text).splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= width else line[: width - 1].rstrip() + "…"
    return ""


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _is_set(value: Optional[str]) -> bool:
    return bool(value and value.strip() and not tc.PLACEHOLDER_RE.search(value))


def control_token(env=None) -> Optional[str]:
    """TELEGRAM_CONTROL_TOKEN, or None when it is unset or a placeholder such as "changeme" (edit and delete are then off). The API's token check
    and the overview both use this ONE rule, so a placeholder can never unlock what the page says is locked."""
    if env is None:
        tc._load_env()
        env = os.environ
    raw = env.get("TELEGRAM_CONTROL_TOKEN")
    return raw.strip() if _is_set(raw) else None


class ChannelControl:
    def __init__(self, ledger, client_factory: Optional[Callable[[str], Any]] = None, env=None,
                 now: Optional[Callable[[], datetime]] = None, env_file: Optional[Path] = None,
                 send_client_factory: Optional[Callable[[str], Any]] = None):
        if env is None:
            tc._load_env()
            env = os.environ
            env_file = env_file or Path(__file__).resolve().parents[2] / ".env"      # the real .env, re-read on every lock check (see prod_locked)
        self.ledger, self._env, self._env_file = ledger, env, env_file
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._factory = client_factory or self._real_client
        self._send_factory = send_client_factory or self._real_send_client

    @staticmethod
    def _real_client(target: str):
        """For acting on an EXISTING row (edit/delete/pin/unpin/photo-replace). Only ever through from_env (the production lock lives there).
        This layer writes the ledger itself, so the client's own recorder is off: one writer per action, and a ledger problem surfaces here
        instead of being swallowed."""
        client = tc.TelegramClient.from_env(target, dry_run=False)
        client.recorder = None
        return client

    @staticmethod
    def _real_send_client(target: str):
        """For a brand-new send (compose). Unlike `_real_client`, this KEEPS the client's own LedgerRecorder attached: a compose is exactly what
        every other sender in this codebase already does (build a client, call send_message/send_photo), and its recorder is the one, proven
        path a new message gets into the ledger - reusing it means there is no second, parallel "record a new send" implementation to drift."""
        return tc.TelegramClient.from_env(target, dry_run=False)

    def _send_client(self, target: str):
        try:
            return self._send_factory(target)
        except tc.TelegramError as e:
            raise ControlError("telegram", f"Could not reach Telegram: {e}") from e

    # ------------------------------------------------------------------ configuration
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self._env.get("ALERTS_TIMEZONE") or DEFAULT_TZ)

    def _chat_id(self, target: str) -> Optional[str]:
        raw = self._env.get({"dev": "TELEGRAM_DEV_CHAT_ID", "prod": "TELEGRAM_CHAT_ID"}[target])
        return raw.strip() if _is_set(raw) else None

    def prod_locked(self) -> bool:
        """Open only when the environment says 1 AND the .env file (re-read now) does not say otherwise. This API is long-running and loaded its
        environment once at start, so a later `PROD_SENDING_ENABLED=0` in .env must re-lock production at once, not after a restart. The other
        direction (locked in .env, still open in this process) is never trusted: opening needs a restart, closing does not."""
        if self._env.get(tc.PROD_SWITCH, "").strip() != "1":
            return True
        if self._env_file is not None:
            fresh = dotenv_values(self._env_file).get(tc.PROD_SWITCH)              # a missing / unreadable file is {} -> the environment decides
            if fresh is not None and fresh.strip() != "1":
                return True
        return False

    def locked(self, target: str) -> bool:
        return target == "prod" and self.prod_locked()

    def controls_enabled(self) -> bool:
        """Edit / delete need TELEGRAM_CONTROL_TOKEN (the API refuses every change while it is unset)."""
        return control_token(self._env) is not None

    def config_items(self) -> List[Dict[str, Any]]:
        e = self._env

        def switched_on(key: str) -> bool:
            """Off until exactly 1 - the rule of the news and scoreboard posts in send_channel_posts.py."""
            return (e.get(key) or "").strip() == "1"

        def switched_off(key: str) -> bool:
            """On until exactly 0 - the rule of the momentum board in send_channel_posts.py (`!= "0"`); "false" does NOT switch it off there."""
            return (e.get(key) or "1").strip() == "0"

        def item(group, key, label, value, note=None):
            return {"group": group, "key": key, "label": label, "value": value, "note": note}

        return [
            item("Safety", "PROD_SENDING_ENABLED", "Production channel", "Locked" if self.prod_locked() else "Open",
                 "Locked: nothing is sent to, edited or deleted in the public channel from here or from any script." if self.prod_locked()
                 else "Open: posts, edits and deletes reach the public channel."),
            item("Safety", "TELEGRAM_CONTROL_TOKEN", "Control Center actions", "Enabled" if self.controls_enabled() else "Disabled",
                 None if self.controls_enabled() else "Set TELEGRAM_CONTROL_TOKEN in .env to allow edit, delete, pin and compose."),
            item("Safety", "TELEGRAM_READ_REQUIRES_TOKEN", "Reading needs the token too",
                 "On" if switched_on("TELEGRAM_READ_REQUIRES_TOKEN") else "Off",
                 "Off by default (fine on localhost). Set to 1 before this API is reachable from outside your own machine."),
            item("Connection", "TELEGRAM_BOT_TOKEN", "Bot token", "Set" if _is_set(e.get("TELEGRAM_BOT_TOKEN")) else "Missing", "The token itself is never shown."),
            item("Connection", "TELEGRAM_DEV_CHAT_ID", "Dev channel", "Configured" if self._chat_id("dev") else "Missing"),
            item("Connection", "TELEGRAM_CHAT_ID", "Production channel", "Configured" if self._chat_id("prod") else "Missing"),
            item("Posting", "CHANNEL_BOARD_ENABLED", "Momentum board post", "Off" if switched_off("CHANNEL_BOARD_ENABLED") else "On",
                 "On unless set to exactly 0."),
            item("Posting", "CHANNEL_NEWS_ENABLED", "News post", "On" if switched_on("CHANNEL_NEWS_ENABLED") else "Off", "Off until set to exactly 1 (licence not checked)."),
            item("Posting", "CHANNEL_SCOREBOARD_ENABLED", "List scoreboard post", "On" if switched_on("CHANNEL_SCOREBOARD_ENABLED") else "Off",
                 "Off until set to exactly 1."),
            item("Posting", "ALERTS_TIMEZONE", "Timezone for dates and times", self.tz.key),
            item("Posting", "BOT_ACCESS_MODE", "Assistant access mode", (e.get("BOT_ACCESS_MODE") or "approve").strip().lower(),
                 "approve (default) | auto | closed"),
            item("Telegram limits", "text", "Text message", f"{tc.TEXT_MAX} characters", "Counted after tags are removed."),
            item("Telegram limits", "caption", "Photo caption", f"{tc.CAPTION_MAX} characters"),
            item("Telegram limits", "delete_window", "Delete window", "48 hours",
                 "A bot cannot delete an older message; remove it by hand in the Telegram app."),
        ]

    def target_state(self, target: str) -> Dict[str, Any]:
        chat = self._chat_id(target)
        return {"target": target, "label": TARGET_LABELS[target], "configured": chat is not None,
                "chat_hint": f"…{chat[-4:]}" if chat else None, "locked": self.locked(target)}

    def overview(self) -> Dict[str, Any]:
        now = self._now()
        local_midnight = datetime.combine(now.astimezone(self.tz).date(), dtime.min, tzinfo=self.tz)
        since = local_midnight.astimezone(timezone.utc)
        targets, first_seen, warnings = [], [], []
        for t in TARGETS:
            c = self.ledger.counts(t, since)
            first_seen += [c["first_sent"]] if c["first_sent"] else []
            state = self.target_state(t)
            targets.append({**state, "sent_today": c["since_count"], "recorded": c["total"], "live": c["live"], "edited": c["edited"],
                            "deleted": c["deleted"], "last_sent_at": _iso(c["last_sent"])})
            if not state["configured"]:
                warnings.append(f"{state['label']} is not configured in .env.")
        if not _is_set(self._env.get("TELEGRAM_BOT_TOKEN")):
            warnings.append("TELEGRAM_BOT_TOKEN is missing: nothing can be posted, edited or deleted.")
        if not self.controls_enabled():
            warnings.append("Edit and delete are disabled: TELEGRAM_CONTROL_TOKEN is not set in .env.")
        dev_chat, prod_chat = self._chat_id("dev"), self._chat_id("prod")
        if dev_chat is not None and dev_chat == prod_chat:
            warnings.append("Dev and production point at the same channel: any script run with the default --to dev now posts to your real "
                            "audience too, and the dev channel is no longer a safe place to test.")
        return {"generated_at": _iso(now), "timezone": self.tz.key, "tracking_since": _iso(min(first_seen)) if first_seen else None,
                "controls_enabled": self.controls_enabled(), "targets": targets, "config": self.config_items(),
                "kinds": [{**k, "label": KIND_LABELS.get(k["kind"], k["kind"])} for k in self.ledger.kinds()], "warnings": warnings}

    # ------------------------------------------------------------------ rules
    def can_edit(self, row: Dict[str, Any]) -> Decision:
        if row["status"] == "deleted":
            return Decision(False, "deleted", "This message was deleted.")
        if self.locked(row["target"]):
            return Decision(False, "locked", "The production channel is locked (PROD_SENDING_ENABLED is not 1): view only.")
        configured = self._chat_id(row["target"])
        if configured is None:
            return Decision(False, "not_configured", f"The {row['target']} channel is not configured in .env.")
        if configured != row["chat_id"]:
            return Decision(False, "wrong_chat", f"This message was posted to a {row['target']} channel that is no longer the configured one; "
                            "its message id would point at a different message in the current channel.")
        return ALLOWED

    def can_delete(self, row: Dict[str, Any]) -> Decision:
        base = self.can_edit(row)
        if not base.ok:
            return base
        if self._now() - row["sent_at"] >= DELETE_WINDOW:
            return Decision(False, "too_old", "Telegram does not let a bot delete a message older than 48 hours. Remove it by hand in the Telegram app.")
        return ALLOWED

    def can_pin(self, row: Dict[str, Any]) -> Decision:
        """Same base rule as edit (not deleted, not locked, chat still current) - pin/unpin has no delete-style time window."""
        return self.can_edit(row)

    def _check_target_postable(self, target: str) -> None:
        """The compose-time equivalent of can_edit's checks, before any row exists to check them against."""
        if target not in TARGETS:
            raise ControlError("bad_filter", f"unknown target {target!r}: use dev or prod")
        if self.locked(target):
            raise ControlError("locked", "The production channel is locked (PROD_SENDING_ENABLED is not 1): nothing can be sent there.")
        if self._chat_id(target) is None:
            raise ControlError("not_configured", f"The {target} channel is not configured in .env.")

    # ------------------------------------------------------------------ reads
    def _public(self, row: Dict[str, Any], detail: bool = False) -> Dict[str, Any]:
        out = {
            "id": row["id"], "target": row["target"], "message_id": row["message_id"], "kind": row["kind"],
            "kind_label": KIND_LABELS.get(row["kind"], row["kind"]), "content_type": row["content_type"], "status": row["status"],
            "preview": preview_line(row["text"]), "text_length": tc.text_length(row["text"]), "silent": row["silent"], "pinned": row["pinned"],
            "edit_count": row["edit_count"], "sent_at": _iso(row["sent_at"]), "edited_at": _iso(row["edited_at"]), "deleted_at": _iso(row["deleted_at"]),
            "deletable_until": _iso(row["sent_at"] + DELETE_WINDOW) if row["status"] == "sent" else None,
            "can_edit": self.can_edit(row).as_dict(), "can_delete": self.can_delete(row).as_dict(), "can_pin": self.can_pin(row).as_dict()}
        if detail:
            markup = row.get("reply_markup") or {}
            out.update(text=row["text"], original_text=row["original_text"], disable_preview=row["disable_preview"],
                       buttons=[[{"text": b.get("text"), "url": b.get("url")} for b in line] for line in markup.get("inline_keyboard", [])],
                       limit=tc.CAPTION_MAX if row["content_type"] == "photo" else tc.TEXT_MAX)
        return out

    def list_messages(self, *, target=None, kind=None, status=None, day: Optional[str] = None, q=None, limit=50, offset=0) -> Dict[str, Any]:
        if target and target not in TARGETS:
            raise ControlError("bad_filter", f"unknown target {target!r}: use dev or prod")
        if status and status not in STATUSES:
            raise ControlError("bad_filter", f"unknown status {status!r}: use sent, edited or deleted")
        since = until = None
        if day:
            try:
                local = datetime.combine(datetime.strptime(day, "%Y-%m-%d").date(), dtime.min, tzinfo=self.tz)
            except ValueError:
                raise ControlError("bad_filter", f"the day must be YYYY-MM-DD, got {day!r}") from None
            since, until = local.astimezone(timezone.utc), (local + timedelta(days=1)).astimezone(timezone.utc)
        rows, total = self.ledger.list(target=target, kind=kind or None, status=status or None, since=since, until=until,
                                       q=(q or "").strip() or None, limit=limit, offset=offset)
        return {"total": total, "limit": max(1, min(int(limit), PAGE_MAX)), "offset": max(0, int(offset)), "timezone": self.tz.key,
                "items": [self._public(r) for r in rows]}

    def get_message(self, ledger_id: int) -> Dict[str, Any]:
        row = self._row(ledger_id)
        audit = [{"at": _iso(a["at"]), "action": a["action"], "outcome": a["outcome"], "detail": a["detail"]}
                 for a in self.ledger.audit_for(row["chat_id"], row["message_id"])]
        return {**self._public(row, detail=True), "audit": audit}

    def _row(self, ledger_id: int) -> Dict[str, Any]:
        row = self.ledger.get(ledger_id)
        if row is None:
            raise ControlError("not_found", f"no recorded message with id {ledger_id}")
        return row

    # ------------------------------------------------------------------ actions
    def edit(self, ledger_id: int, text: str, acknowledge_wording: bool = False) -> Dict[str, Any]:
        row = self._row(ledger_id)
        decision = self.can_edit(row)
        if not decision.ok:
            raise ControlError(decision.code, decision.reason)
        caption = row["content_type"] == "photo"
        limit = tc.CAPTION_MAX if caption else tc.TEXT_MAX
        if not (text or "").strip():
            raise ControlError("empty", "An empty message cannot be saved. Use delete to remove it.")
        if tc.text_length(text) > limit:
            raise ControlError("too_long", f"The {'caption' if caption else 'text'} is {tc.text_length(text)} characters; Telegram allows {limit}.")
        if text == row["text"]:
            raise ControlError("unchanged", "Nothing changed.")
        already = set(wording_hits(row["text"]))
        new_words = [w for w in wording_hits(text) if w not in already]
        if new_words and not acknowledge_wording:
            raise ControlError("wording", "The wording guard bans advice-style words in channel posts: " + ", ".join(new_words) + ".", warnings=new_words)
        client = self._client("edit", row)
        try:
            result = client.edit_message(row["message_id"], text, caption=caption, reply_markup=row.get("reply_markup"),
                                         disable_preview=row["disable_preview"])
        except tc.TelegramError as e:
            self._audit("edit", row, "failed", str(e))
            raise ControlError("telegram", f"Telegram did not accept the edit: {e}") from e
        if result == "edited":
            self.ledger.record_edited(chat_id=row["chat_id"], message_id=row["message_id"], text=text)
            self._audit("edit", row, "done", "wording acknowledged: " + ", ".join(new_words) if new_words else None)
        elif result == "missing":
            self.ledger.record_deleted(chat_id=row["chat_id"], message_id=row["message_id"])
            self._audit("edit", row, "gone", "Telegram: the message no longer exists")
        else:
            # "Not modified" means Telegram ALREADY shows exactly this text (e.g. an earlier, retried attempt of this same edit had applied it), so
            # the ledger follows Telegram instead of keeping the old text.
            self.ledger.record_edited(chat_id=row["chat_id"], message_id=row["message_id"], text=text)
            self._audit("edit", row, "unchanged", "Telegram already showed this text; the record was brought in line")
        return {"result": result, "message": self.get_message(ledger_id)}

    def delete(self, ledger_id: int) -> Dict[str, Any]:
        row = self._row(ledger_id)
        decision = self.can_delete(row)
        if not decision.ok:
            raise ControlError(decision.code, decision.reason)
        client = self._client("delete", row)
        try:
            result = client.delete_message(row["message_id"])
        except tc.TelegramError as e:
            self._audit("delete", row, "failed", str(e))
            raise ControlError("telegram", f"Telegram did not delete it: {e}") from e
        if result == "refused":
            self._audit("delete", row, "refused", "Telegram refused (too old or a service message)")
            raise ControlError("too_old", "Telegram refused to delete this message (it is older than 48 hours or cannot be deleted). "
                                          "Remove it by hand in the Telegram app.")
        self.ledger.record_deleted(chat_id=row["chat_id"], message_id=row["message_id"])
        self._audit("delete", row, "done" if result == "deleted" else "gone", None if result == "deleted" else "Telegram: it was already gone")
        return {"result": result, "message": self.get_message(ledger_id)}

    # ------------------------------------------------------------------ pin / unpin
    def pin(self, ledger_id: int) -> Dict[str, Any]:
        row = self._row(ledger_id)
        decision = self.can_pin(row)
        if not decision.ok:
            raise ControlError(decision.code, decision.reason)
        if row["pinned"]:
            raise ControlError("unchanged", "This message is already pinned.")
        client = self._client("pin", row)
        try:
            client.pin_message(row["message_id"])
        except tc.TelegramError as e:
            if "message to pin not found" in str(e).lower():
                self.ledger.record_deleted(chat_id=row["chat_id"], message_id=row["message_id"])
                self._audit("pin", row, "gone", "Telegram: the message no longer exists")
                return {"result": "gone", "message": self.get_message(ledger_id)}
            self._audit("pin", row, "failed", str(e))
            raise ControlError("telegram", f"Telegram did not pin it: {e}") from e
        self.ledger.record_pinned(chat_id=row["chat_id"], message_id=row["message_id"])
        self._audit("pin", row, "done", None)
        return {"result": "pinned", "message": self.get_message(ledger_id)}

    def unpin(self, ledger_id: int) -> Dict[str, Any]:
        row = self._row(ledger_id)
        decision = self.can_pin(row)
        if not decision.ok:
            raise ControlError(decision.code, decision.reason)
        if not row["pinned"]:
            raise ControlError("unchanged", "This message is not pinned.")
        client = self._client("unpin", row)
        try:
            result = client.unpin_message(row["message_id"])
        except tc.TelegramError as e:
            self._audit("unpin", row, "failed", str(e))
            raise ControlError("telegram", f"Telegram did not unpin it: {e}") from e
        self.ledger.record_unpinned(chat_id=row["chat_id"], message_id=row["message_id"])
        self._audit("unpin", row, "done" if result == "unpinned" else "gone",
                   None if result == "unpinned" else "Telegram: it was already not pinned")
        return {"result": result, "message": self.get_message(ledger_id)}

    # ------------------------------------------------------------------ photo replace (an edit that also swaps the image)
    def replace_photo(self, ledger_id: int, png: bytes, caption: str, acknowledge_wording: bool = False) -> Dict[str, Any]:
        row = self._row(ledger_id)
        decision = self.can_edit(row)
        if not decision.ok:
            raise ControlError(decision.code, decision.reason)
        if row["content_type"] != "photo":
            raise ControlError("not_photo", "Only a photo message's image can be replaced; this is a text message.")
        if tc.text_length(caption) > tc.CAPTION_MAX:
            raise ControlError("too_long", f"The caption is {tc.text_length(caption)} characters; Telegram allows {tc.CAPTION_MAX}.")
        if len(png) > MAX_UPLOAD_BYTES:
            raise ControlError("too_long", f"The image is {len(png) / 1_000_000:.1f} MB; Telegram allows up to {MAX_UPLOAD_BYTES // 1_000_000} MB.")
        already = set(wording_hits(row["text"]))
        new_words = [w for w in wording_hits(caption) if w not in already]
        if new_words and not acknowledge_wording:
            raise ControlError("wording", "The wording guard bans advice-style words in channel posts: " + ", ".join(new_words) + ".", warnings=new_words)
        client = self._client("edit", row)
        try:
            result = client.replace_photo(row["message_id"], png, caption, reply_markup=row.get("reply_markup"))
        except tc.TelegramError as e:
            self._audit("edit", row, "failed", str(e))
            raise ControlError("telegram", f"Telegram did not accept the new photo: {e}") from e
        if result == "edited":
            self.ledger.record_edited(chat_id=row["chat_id"], message_id=row["message_id"], text=caption)
            detail = "photo replaced" + (" - wording acknowledged: " + ", ".join(new_words) if new_words else "")
            self._audit("edit", row, "done", detail)
        elif result == "missing":
            self.ledger.record_deleted(chat_id=row["chat_id"], message_id=row["message_id"])
            self._audit("edit", row, "gone", "Telegram: the message no longer exists")
        else:
            self.ledger.record_edited(chat_id=row["chat_id"], message_id=row["message_id"], text=caption)
            self._audit("edit", row, "unchanged", "Telegram already showed this image and caption; the record was brought in line")
        return {"result": result, "message": self.get_message(ledger_id)}

    # ------------------------------------------------------------------ compose (a brand-new post)
    def compose(self, target: str, text: str, kind: str = DEFAULT_KIND, silent: bool = True, disable_preview: bool = True,
               acknowledge_wording: bool = False) -> Dict[str, Any]:
        self._check_target_postable(target)
        if not (text or "").strip():
            raise ControlError("empty", "An empty message cannot be sent.")
        if tc.text_length(text) > tc.MAX_LEN:
            raise ControlError("too_long", f"The text is {tc.text_length(text)} characters; a composed post is capped at {tc.MAX_LEN} so it is "
                                          "never silently split into more than one message.")
        new_words = wording_hits(text)
        if new_words and not acknowledge_wording:
            raise ControlError("wording", "The wording guard bans advice-style words in channel posts: " + ", ".join(new_words) + ".", warnings=new_words)
        client = self._send_client(target)
        try:
            ids = client.send_message(text, disable_preview=disable_preview, silent=silent, kind=kind or DEFAULT_KIND)
        except tc.TelegramError as e:
            raise ControlError("telegram", f"Telegram did not accept the post: {e}") from e
        return self._composed_result(target, client.chat_id, ids[0], kind=kind or DEFAULT_KIND, content_type="text", text=text,
                                     silent=silent, disable_preview=disable_preview)

    def compose_photo(self, target: str, png: bytes, caption: str = "", kind: str = DEFAULT_KIND, silent: bool = True,
                      acknowledge_wording: bool = False) -> Dict[str, Any]:
        self._check_target_postable(target)
        if tc.text_length(caption) > tc.CAPTION_MAX:
            raise ControlError("too_long", f"The caption is {tc.text_length(caption)} characters; Telegram allows {tc.CAPTION_MAX}.")
        if len(png) > MAX_UPLOAD_BYTES:
            raise ControlError("too_long", f"The image is {len(png) / 1_000_000:.1f} MB; Telegram allows up to {MAX_UPLOAD_BYTES // 1_000_000} MB.")
        new_words = wording_hits(caption)
        if new_words and not acknowledge_wording:
            raise ControlError("wording", "The wording guard bans advice-style words in channel posts: " + ", ".join(new_words) + ".", warnings=new_words)
        client = self._send_client(target)
        try:
            message_id = client.send_photo(png, caption, silent=silent, kind=kind or DEFAULT_KIND)
        except tc.TelegramError as e:
            raise ControlError("telegram", f"Telegram did not accept the post: {e}") from e
        return self._composed_result(target, client.chat_id, message_id, kind=kind or DEFAULT_KIND, content_type="photo", text=caption,
                                     silent=silent, disable_preview=True)

    def _composed_result(self, target: str, chat_id: str, message_id: int, *, kind: str, content_type: str, text: str,
                         silent: bool, disable_preview: bool) -> Dict[str, Any]:
        row = self.ledger.get_by_message(chat_id, message_id)
        if row is None:
            # The client's own recorder is the normal, one path a new send gets into the ledger (see _real_send_client's docstring); this is a
            # fallback for the rare case where that write silently failed (LedgerRecorder never raises, by design) while the send to Telegram
            # itself succeeded. record_sent's own ON CONFLICT DO NOTHING makes a second attempt here safe even if the recorder's write actually
            # lands a moment later. If even this fails (e.g. the database is still unreachable), the send still succeeded - that is reported
            # honestly (never as a failure), just without the detail a ledger row would carry.
            try:
                self.ledger.record_sent(target=target, chat_id=chat_id, message_id=message_id, kind=kind, content_type=content_type, text=text,
                                        reply_markup=None, silent=silent, disable_preview=disable_preview)
            except Exception:                                       # noqa: BLE001 - the send already succeeded; this best-effort repair must never raise
                pass
            row = self.ledger.get_by_message(chat_id, message_id)
        if row is None:
            return {"result": "sent", "target": target, "message_id": message_id, "message": None}
        return {"result": "sent", "target": target, "message_id": message_id, "message": self._public(row, detail=True)}

    # ------------------------------------------------------------------ today's schedule (read-only; see schedule.py)
    def schedule(self, target: str) -> Dict[str, Any]:
        if target not in TARGETS:
            raise ControlError("bad_filter", f"unknown target {target!r}: use dev or prod")
        return {"target": target, "timezone": self.tz.key, "jobs": sched.today_schedule(target, self.tz, self._now())}

    def _client(self, action: str, row: Dict[str, Any]):
        """The Telegram client for a row's channel. Building it can fail (missing / placeholder token or chat id, the production lock): that is a
        refused action like any other, audited and reported as 'telegram', never an unhandled error."""
        try:
            return self._factory(row["target"])
        except tc.TelegramError as e:
            self._audit(action, row, "failed", str(e))
            raise ControlError("telegram", f"Could not reach Telegram: {e}") from e

    def _audit(self, action: str, row: Dict[str, Any], outcome: str, detail: Optional[str]) -> None:
        self.ledger.audit_add(action=action, target=row["target"], chat_id=row["chat_id"], message_id=row["message_id"], outcome=outcome, detail=detail)
