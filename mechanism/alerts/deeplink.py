# mechanism/alerts/deeplink.py
"""
Deep links into the bot's private chat: https://t.me/<bot>?start=<payload>.

Telegram allows only A-Z a-z 0-9 _ - in a start payload (max 64 chars). Two kinds are used now:
  * 'inv_<code>' - a one-time invitation (access.py). The public channel never carries one.
  * anything else (e.g. 'help', the neutral "Private assistant" button under the channel header) - opens the bot, nothing more.
Per-ticker links ('lv_AAPL') and 'list' were removed on 2026-09-21: strategy content is no longer offered to the public channel.
"""
from __future__ import annotations

import re
from typing import Optional

_PAYLOAD_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def link(bot_username: str, payload: str = "help") -> str:
    if not _PAYLOAD_OK.match(payload):
        raise ValueError("invalid start payload")
    return f"https://t.me/{bot_username}?start={payload}"


def is_invite(payload: Optional[str]) -> bool:
    return bool(payload) and payload.startswith("inv_")
