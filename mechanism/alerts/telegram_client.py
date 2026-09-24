# mechanism/alerts/telegram_client.py
"""
Minimal Telegram Bot API client for the alert channel (plain `requests`, no extra dependency).

Safety properties
  * The bot token never appears in logs or exceptions: requests embeds the full URL (which contains the
    token) in its exception messages, so every failure is re-raised with the token redacted.
  * `dry_run=True` (the default everywhere) performs no network call at all.
  * Messages longer than Telegram's 4096-char limit are split on line boundaries.
  * 429 (rate limit) honours `retry_after`; 5xx / network errors retry with backoff; other 4xx fail fast.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path
from typing import List, Optional

import requests

MAX_LEN = 4000  # Telegram hard limit is 4096; keep a margin
TEXT_MAX = 4096  # Telegram's limit for a text message (what an EDIT is checked against; sends keep the MAX_LEN margin)
CAPTION_MAX = 1024  # Telegram's limit for a photo caption
API = "https://api.telegram.org"
PLACEHOLDER_RE = re.compile(r"^\s*$|your[_-]?token|changeme|xxxx", re.I)


PROD_SWITCH = "PROD_SENDING_ENABLED"


class TelegramError(RuntimeError):
    pass


def _load_env() -> None:
    """Load the repo-root .env once (python-dotenv is already a project dependency)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)


def prod_sending_enabled() -> bool:
    """The launch lock: True only when .env (or the environment) sets PROD_SENDING_ENABLED=1."""
    _load_env()
    return os.getenv(PROD_SWITCH, "").strip() == "1"


_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(html_text: str) -> str:
    """The text as Telegram parses it: tags removed, &amp; etc. unescaped."""
    return html.unescape(_TAG_RE.sub("", html_text or ""))


def text_length(html_text: str) -> int:
    """Length Telegram checks against its 4096 limit: the text AFTER entities parsing (tags removed, &amp; etc. unescaped). A link's URL
    lives in the tag, so it does not count; raw `len()` over-counts a row with a ticker link by about 65 characters."""
    return len(plain_text(html_text))


def split_message(text: str, limit: int = MAX_LEN) -> List[str]:
    """Split on line boundaries so an HTML tag is never cut in half (cards are line-oriented). A message whose PARSED length fits is never split:
    a cut inside a <blockquote> would leave an unclosed tag, which Telegram rejects."""
    if text_length(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                      # a single absurdly long line: hard split
            if cur:
                parts.append(cur); cur = ""
            parts.append(line[:limit]); line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


class TelegramClient:
    def __init__(self, token: Optional[str], chat_id: Optional[str], dry_run: bool = True,
                 session: Optional[requests.Session] = None, sleep=time.sleep):
        self.token, self.chat_id, self.dry_run = token, chat_id, dry_run
        self.target: Optional[str] = None      # 'dev' | 'prod' | 'owner', set by from_env
        self.recorder = None                   # message_ledger.LedgerRecorder for channel targets (never raises); None = nothing is recorded
        self._http = session or requests.Session()
        self._sleep = sleep
        if not dry_run:
            if not token or PLACEHOLDER_RE.search(token):
                raise TelegramError("TELEGRAM_BOT_TOKEN is empty/placeholder in .env")
            if not chat_id or PLACEHOLDER_RE.search(str(chat_id)):
                raise TelegramError("target chat id is empty/placeholder in .env")

    @classmethod
    def from_env(cls, target: str = "dev", dry_run: bool = True) -> "TelegramClient":
        """THE ONLY way a script gets a client for a real chat (a test scans that nothing else constructs one). Everything is built and
        tested on the dev channel; posting to the public production channel is locked until PROD_SENDING_ENABLED=1 is set in .env."""
        _load_env()
        if target == "prod" and not dry_run and not prod_sending_enabled():
            raise TelegramError(f"Production sending is switched OFF (the launch lock). Everything is tested on the dev channel; "
                                f"when First Light is ready to go public set {PROD_SWITCH}=1 in .env.")
        # "owner" = the owner's PRIVATE chat with the bot (BOT_OWNER_ID): where every assistant screen belongs. Channels carry only the daily
        # data, promotion, news and information; assistant screens are never posted to a channel.
        key = {"dev": "TELEGRAM_DEV_CHAT_ID", "prod": "TELEGRAM_CHAT_ID", "owner": "BOT_OWNER_ID"}[target]
        client = cls(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv(key), dry_run=dry_run)
        client.target = target
        if target in ("dev", "prod") and not dry_run:
            # Every message sent to a CHANNEL is written to the ledger the Telegram Control Center reads (a bot cannot read a channel's history, so
            # this is the only record). The private 'owner' chat and previews are never recorded. Lazy: no database is touched until the first send.
            from alerts.message_ledger import LedgerRecorder
            client.recorder = LedgerRecorder(target)
        return client

    # ------------------------------------------------------------------
    def _redact(self, text: str) -> str:
        return text.replace(self.token, "<token>") if self.token else text

    def _post(self, method: str, payload: dict, files: Optional[dict] = None) -> dict:
        url = f"{API}/bot{self.token}/{method}"
        last = None
        for attempt in range(4):
            try:
                if files:                                    # multipart upload (photos): fields go in `data`, not JSON
                    r = self._http.post(url, data=payload, files=files, timeout=60)
                else:
                    r = self._http.post(url, json=payload, timeout=20)
            except requests.RequestException as e:
                last = self._redact(f"{type(e).__name__}: {e}")
                self._sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                wait = 5
                try:
                    wait = int(r.json().get("parameters", {}).get("retry_after", 5))
                except Exception:
                    pass
                last = f"429 rate limited (retry_after={wait}s)"
                self._sleep(min(wait, 60))
                continue
            if r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                self._sleep(2 ** attempt)
                continue
            try:
                data = r.json()
            except ValueError:
                raise TelegramError(f"non-JSON response (HTTP {r.status_code})")
            if not data.get("ok"):
                raise TelegramError(self._redact(f"Telegram rejected the request: {data.get('description', r.status_code)}"))
            return data
        raise TelegramError(f"giving up after retries: {last}")

    # ------------------------------------------------------------------
    def get_me(self) -> dict:
        """The bot's own profile (username etc.). Needs a token, so it is refused in dry-run."""
        if self.dry_run:
            raise TelegramError("get_me is not available in dry-run")
        return self._post("getMe", {})["result"]

    def delete_message(self, message_id: int) -> str:
        """'deleted' | 'missing' (no such message) | 'refused' (Telegram will not delete it: too old, or a service message).
        Any other failure (network, rate limit exhausted) raises TelegramError so a sweep never mistakes an outage for 'missing'."""
        if self.dry_run:
            return "dry-run"
        try:
            self._post("deleteMessage", {"chat_id": self.chat_id, "message_id": message_id})
            self._record("deleted", message_id=message_id)
            return "deleted"
        except TelegramError as e:
            text = str(e).lower()
            if "message to delete not found" in text:                  # NOT just "not found": "chat not found" must fail loudly
                self._record("deleted", message_id=message_id)          # it is gone either way
                return "missing"
            if "can't be deleted" in text or "cannot be deleted" in text or "message can't" in text:
                return "refused"
            raise

    def pin_message(self, message_id: int, silent: bool = True) -> bool:
        """Pin a message in the chat (the bot needs the pin right). True when Telegram accepted it."""
        if self.dry_run:
            return False
        self._post("pinChatMessage", {"chat_id": self.chat_id, "message_id": message_id, "disable_notification": silent})
        self._record("pinned", message_id=message_id)
        return True

    def unpin_message(self, message_id: int) -> str:
        """Unpin a message: 'unpinned' | 'missing' (not pinned / no such message - Telegram answers the same "message to unpin not found" for
        both) | 'dry-run'. Any other failure raises TelegramError."""
        if self.dry_run:
            return "dry-run"
        try:
            self._post("unpinChatMessage", {"chat_id": self.chat_id, "message_id": message_id})
        except TelegramError as e:
            if "message to unpin not found" in str(e).lower():
                self._record("unpinned", message_id=message_id)              # already not pinned either way
                return "missing"
            raise
        self._record("unpinned", message_id=message_id)
        return "unpinned"

    def edit_message(self, message_id: int, text: str, *, caption: bool = False, reply_markup: Optional[dict] = None,
                     disable_preview: bool = True) -> str:
        """Replace the text (or, for a photo, the caption) of a message this bot sent: 'edited' | 'unchanged' (Telegram: "message is not
        modified") | 'missing' (no such message) | 'dry-run'. Any other failure raises TelegramError. Telegram REMOVES an inline keyboard that an edit
        does not send again, so pass the message's `reply_markup` to keep its buttons."""
        limit = CAPTION_MAX if caption else TEXT_MAX
        if text_length(text) > limit:
            raise TelegramError(f"the {'caption' if caption else 'text'} is {text_length(text)} chars; Telegram allows {limit}")
        if not text.strip():
            raise TelegramError("an empty message cannot be saved")
        if self.dry_run:
            return "dry-run"
        payload = {"chat_id": self.chat_id, "message_id": message_id, "parse_mode": "HTML"}
        if caption:
            payload["caption"] = text
        else:
            payload["text"] = text
            payload["disable_web_page_preview"] = disable_preview
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            self._post("editMessageCaption" if caption else "editMessageText", payload)
        except TelegramError as e:
            answer = str(e).lower()
            if "message is not modified" in answer:
                return "unchanged"
            if "message to edit not found" in answer:                 # NOT just "not found": "chat not found" must fail loudly
                return "missing"
            raise
        self._record("edited", message_id=message_id, text=text)
        return "edited"

    def replace_photo(self, message_id: int, png: bytes, caption: str, *, reply_markup: Optional[dict] = None) -> str:
        """Swap the image of a message this bot sent, with a new caption in the same call (Telegram's editMessageMedia requires the whole
        media object, not just the file - there is no "keep the old caption" shortcut, so the caller always sends the caption it wants shown).
        'edited' | 'unchanged' (Telegram: not modified - the same image+caption already showed) | 'missing' (no such message) | 'dry-run'. Any
        other failure raises TelegramError. Like edit_message, an inline keyboard the caller does not resend is REMOVED by Telegram."""
        if text_length(caption) > CAPTION_MAX:
            raise TelegramError(f"the caption is {text_length(caption)} chars; Telegram allows {CAPTION_MAX}")
        if self.dry_run:
            return "dry-run"
        media = {"type": "photo", "media": "attach://photo", "caption": caption, "parse_mode": "HTML"}
        payload = {"chat_id": self.chat_id, "message_id": message_id, "media": json.dumps(media)}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            self._post("editMessageMedia", payload, files={"photo": ("first_light.png", png, "image/png")})
        except TelegramError as e:
            answer = str(e).lower()
            if "message is not modified" in answer:
                return "unchanged"
            if "message to edit not found" in answer:
                return "missing"
            raise
        self._record("edited", message_id=message_id, text=caption)
        return "edited"

    def _record(self, event: str, **kw) -> None:
        """Report to the ledger (channels only). The recorder never raises, so a ledger problem cannot break or repeat a send."""
        if self.recorder is not None and not self.dry_run:
            getattr(self.recorder, event)(chat_id=self.chat_id, **kw)

    def chat_info(self) -> dict:
        """getChat for the target chat: {'type', 'title', ...}. Refused in dry-run."""
        if self.dry_run:
            raise TelegramError("chat_info is not available in dry-run")
        return self._post("getChat", {"chat_id": self.chat_id})["result"]

    def send_photo(self, png: bytes, caption: str = "", silent: bool = False, reply_markup: Optional[dict] = None,
                   kind: Optional[str] = None) -> Optional[int]:
        """Send a PNG with an HTML caption (Telegram limit 1024 chars). Returns the message id (None in dry-run). `kind` labels the post in the
        message ledger (e.g. 'digest_card'); unknown = 'other'."""
        if text_length(caption) > CAPTION_MAX:
            raise TelegramError(f"caption is {text_length(caption)} chars; Telegram allows {CAPTION_MAX}")
        if self.dry_run:
            return None
        payload = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML",
                   "disable_notification": "true" if silent else "false"}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)     # multipart fields are strings, so the keyboard is JSON text
        data = self._post("sendPhoto", payload, files={"photo": ("first_light.png", png, "image/png")})
        message_id = data["result"]["message_id"]
        self._record("sent", message_id=message_id, kind=kind, content_type="photo", text=caption, reply_markup=reply_markup, silent=silent)
        self._sleep(1.1)
        return message_id

    def send_message(self, text: str, disable_preview: bool = True, silent: bool = False,
                     reply_markup: Optional[dict] = None, kind: Optional[str] = None) -> List[Optional[int]]:
        """Send HTML text; returns the Telegram message ids (None entries in dry-run). `silent` = no notification sound;
        `reply_markup` (an inline keyboard) is attached to the last part when the text has to be split. `kind` labels the post in the message
        ledger (e.g. 'digest_list'); unknown = 'other'."""
        ids: List[Optional[int]] = []
        parts = split_message(text)
        for k, part in enumerate(parts):
            if self.dry_run:
                ids.append(None)
                continue
            payload = {"chat_id": self.chat_id, "text": part, "parse_mode": "HTML",
                       "disable_web_page_preview": disable_preview, "disable_notification": silent}
            markup = reply_markup if reply_markup and k == len(parts) - 1 else None
            if markup:
                payload["reply_markup"] = markup
            data = self._post("sendMessage", payload)
            ids.append(data["result"]["message_id"])
            self._record("sent", message_id=ids[-1], kind=kind, content_type="text", text=part, reply_markup=markup, silent=silent,
                         disable_preview=disable_preview)
            self._sleep(1.1)   # stay under ~1 msg/sec per chat
        return ids
