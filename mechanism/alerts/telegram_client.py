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

import json
import os
import re
import time
from pathlib import Path
from typing import List, Optional

import requests

MAX_LEN = 4000  # Telegram hard limit is 4096; keep a margin
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


def split_message(text: str, limit: int = MAX_LEN) -> List[str]:
    """Split on line boundaries so an HTML tag is never cut in half (cards are line-oriented)."""
    if len(text) <= limit:
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
        return cls(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv(key), dry_run=dry_run)

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
            return "deleted"
        except TelegramError as e:
            text = str(e).lower()
            if "message to delete not found" in text:                  # NOT just "not found": "chat not found" must fail loudly
                return "missing"
            if "can't be deleted" in text or "cannot be deleted" in text or "message can't" in text:
                return "refused"
            raise

    def pin_message(self, message_id: int, silent: bool = True) -> bool:
        """Pin a message in the chat (the bot needs the pin right). True when Telegram accepted it."""
        if self.dry_run:
            return False
        self._post("pinChatMessage", {"chat_id": self.chat_id, "message_id": message_id, "disable_notification": silent})
        return True

    def chat_info(self) -> dict:
        """getChat for the target chat: {'type', 'title', ...}. Refused in dry-run."""
        if self.dry_run:
            raise TelegramError("chat_info is not available in dry-run")
        return self._post("getChat", {"chat_id": self.chat_id})["result"]

    def send_photo(self, png: bytes, caption: str = "", silent: bool = False, reply_markup: Optional[dict] = None) -> Optional[int]:
        """Send a PNG with an HTML caption (Telegram limit 1024 chars). Returns the message id (None in dry-run)."""
        if len(caption) > CAPTION_MAX:
            raise TelegramError(f"caption is {len(caption)} chars; Telegram allows {CAPTION_MAX}")
        if self.dry_run:
            return None
        payload = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML",
                   "disable_notification": "true" if silent else "false"}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)     # multipart fields are strings, so the keyboard is JSON text
        data = self._post("sendPhoto", payload, files={"photo": ("first_light.png", png, "image/png")})
        self._sleep(1.1)
        return data["result"]["message_id"]

    def send_message(self, text: str, disable_preview: bool = True, silent: bool = False,
                     reply_markup: Optional[dict] = None) -> List[Optional[int]]:
        """Send HTML text; returns the Telegram message ids (None entries in dry-run). `silent` = no notification sound;
        `reply_markup` (an inline keyboard) is attached to the last part when the text has to be split."""
        ids: List[Optional[int]] = []
        parts = split_message(text)
        for k, part in enumerate(parts):
            if self.dry_run:
                ids.append(None)
                continue
            payload = {"chat_id": self.chat_id, "text": part, "parse_mode": "HTML",
                       "disable_web_page_preview": disable_preview, "disable_notification": silent}
            if reply_markup and k == len(parts) - 1:
                payload["reply_markup"] = reply_markup
            data = self._post("sendMessage", payload)
            ids.append(data["result"]["message_id"])
            self._sleep(1.1)   # stay under ~1 msg/sec per chat
        return ids
