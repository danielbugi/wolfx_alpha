# mechanism/alerts/telegram_media.py
"""
Fetch the picture of a message that is already in a channel, for the Telegram Control Center.

A bot cannot read a channel's history and the ledger never stored the pictures (it keeps text only). What a bot CAN do is forward a channel
message silently into a private chat it shares with the owner: the reply to that call describes the message, including its photo, which can then be
downloaded; the forwarded copy is deleted straight away. That is what this does, so every picture the channel ever carried (old or new) can be
shown without storing anything new.

  * Read-only for the channel: nothing is posted to it, edited or deleted there. The only write is the short-lived silent copy in the owner's
    private chat, removed in the same call.
  * Results are cached in memory (small, bounded), keyed by the ledger row and its last edit, so opening the same message again costs nothing.
  * The bot token is used only to build the URL; it is never logged, returned or put in an error message (errors carry Telegram's description
    only, with the token redacted).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional, Tuple

API = "https://api.telegram.org"
FORWARD_GAP_SECONDS = 1.0          # at most one forward a second: Telegram rate-limits a bot's messages into one chat
MAX_CACHE_BYTES = 24_000_000
MAX_DOWNLOAD_BYTES = 20_000_000


class MediaError(Exception):
    """`code`: gone | no_photo | not_configured | telegram"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ImageCache:
    """A small thread-safe LRU of (bytes, mime) bounded by total size."""

    def __init__(self, max_bytes: int = MAX_CACHE_BYTES):
        self.max_bytes, self._d, self._size, self._lock = max_bytes, OrderedDict(), 0, threading.Lock()

    def get(self, key) -> Optional[Tuple[bytes, str]]:
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
        return None

    def put(self, key, value: Tuple[bytes, str]) -> None:
        with self._lock:
            if key in self._d:
                self._size -= len(self._d.pop(key)[0])
            self._d[key] = value
            self._size += len(value[0])
            while self._size > self.max_bytes and len(self._d) > 1:
                _k, (old, _m) = self._d.popitem(last=False)
                self._size -= len(old)


CACHE = ImageCache()
_FETCH_LOCK = threading.Lock()
_last_forward = [0.0]


def _redact(text: str, token: str) -> str:
    return (text or "").replace(token, "<token>") if token else (text or "")


def _mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/webp" if data[8:12] == b"WEBP" else "application/octet-stream"


def fetch_photo_via_forward(token: str, from_chat_id: str, message_id: int, owner_chat_id: str, http: Any = None,
                            sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> Tuple[bytes, str]:
    """Silently forward the channel message to the owner's private chat, download its (largest) photo, delete the copy. Returns (bytes, mime)."""
    if not token or not owner_chat_id:
        raise MediaError("not_configured", "Showing channel pictures needs the bot token and the owner's chat id (BOT_OWNER_ID).")
    if http is None:
        import requests as http                                                      # noqa: PLC0415 - imported lazily, like the rest of the clients
    api = f"{API}/bot{token}"

    def call(method: str, payload: dict) -> dict:
        try:
            body = http.post(f"{api}/{method}", json=payload, timeout=30).json()
        except Exception as e:                                                       # noqa: BLE001
            raise MediaError("telegram", f"Telegram could not be reached ({type(e).__name__}).") from None
        return body

    with _FETCH_LOCK:                                                                # one forward at a time, one a second
        wait = FORWARD_GAP_SECONDS - (clock() - _last_forward[0])
        if wait > 0:
            sleep(wait)
        fwd = call("forwardMessage", {"chat_id": owner_chat_id, "from_chat_id": from_chat_id, "message_id": int(message_id), "disable_notification": True})
        _last_forward[0] = clock()
        if not fwd.get("ok"):
            desc = _redact(str(fwd.get("description") or ""), token)
            if "not found" in desc.lower() or "can't be forwarded" in desc.lower():
                raise MediaError("gone", "Telegram no longer has this message (it may have been deleted in the channel).")
            raise MediaError("telegram", f"Telegram refused to hand over the picture: {desc[:120]}")
        copy_id = (fwd.get("result") or {}).get("message_id")
        try:
            photos = (fwd.get("result") or {}).get("photo") or []
            if not photos:
                raise MediaError("no_photo", "This message has no picture.")
            file_id = photos[-1].get("file_id")                                       # the largest size
            info = call("getFile", {"file_id": file_id})
            path = (info.get("result") or {}).get("file_path")
            if not info.get("ok") or not path:
                raise MediaError("telegram", "Telegram did not give a download path for the picture.")
            try:
                resp = http.get(f"{API}/file/bot{token}/{path}", timeout=60)
                data = resp.content
            except Exception as e:                                                   # noqa: BLE001
                raise MediaError("telegram", f"The picture could not be downloaded ({type(e).__name__}).") from None
            if not data or len(data) > MAX_DOWNLOAD_BYTES:
                raise MediaError("telegram", "The downloaded picture was empty or too large.")
            return data, _mime(data)
        finally:
            if copy_id is not None:                                                  # always remove the short-lived copy
                try:
                    call("deleteMessage", {"chat_id": owner_chat_id, "message_id": int(copy_id)})
                except MediaError:
                    pass
