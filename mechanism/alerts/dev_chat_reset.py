#!/usr/bin/env python3
# mechanism/alerts/dev_chat_reset.py
"""
Empty the DEV chat so everything can be replayed from a clean slate (working rule: build and test on the dev group, promote later).

    python mechanism/alerts/dev_chat_reset.py           # DRY RUN: shows what it would do, touches nothing
    python mechanism/alerts/dev_chat_reset.py --yes     # really deletes

Safety rules (each is tested in test_channel_tools.py):
  * it only ever targets TELEGRAM_DEV_CHAT_ID; if that id equals the production id, or is missing, it refuses;
  * the target must be a group / supergroup (never a channel), and its title is printed before anything is deleted;
  * nothing is deleted without --yes.
LIMIT (found the hard way, 2026-09-21): Telegram does not let a bot delete messages older than ~48 hours. In a chat with long history this
tool clears the recent messages, then stops after a run of refusals. For a truly clean slate create a NEW chat instead.
How it works: a bot cannot list a chat's history, so it posts one marker message to learn the newest message id, then tries to delete every
id from there down to 1. "missing" = that id no longer exists; "refused" = Telegram will not delete it (too old / a service message such as
"X joined") - those are reported so they can be removed by hand in the app. Message ids are per chat, so this can never touch another chat.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from alerts.telegram_client import TelegramClient, TelegramError, _load_env  # noqa: E402

MARKER = "<i>[reset] clearing this chat - it will be replayed from scratch.</i>"
GROUP_TYPES = ("group", "supergroup")


class ResetRefused(RuntimeError):
    pass


def check_target(dev_id: Optional[str], prod_id: Optional[str], chat_type: str) -> None:
    """The safety rules, separated so they can be tested without any network."""
    if not dev_id or not str(dev_id).strip():
        raise ResetRefused("TELEGRAM_DEV_CHAT_ID is not set - refusing to guess a chat")
    if prod_id and str(dev_id).strip() == str(prod_id).strip():
        raise ResetRefused("the dev chat id equals the PRODUCTION chat id - refusing")
    if chat_type not in GROUP_TYPES:
        raise ResetRefused(f"the target is a '{chat_type}', not a group - refusing (channels are never reset by this tool)")


GIVE_UP_AFTER = 150            # this many "refused" in a row => everything older is out of a bot's reach: stop instead of stalling


def sweep(tg, newest: int, floor: int = 1, progress: Callable[[int, Counter], None] = lambda i, c: None,
          give_up_after: int = GIVE_UP_AFTER) -> Dict:
    """Try to delete every id from `newest` down to `floor`. Telegram does not let a bot delete messages older than about 48 hours (it
    answers "message can't be deleted" for every one, even id 5), so a long run of refusals means the rest is out of reach: stop.
    Returns {'counts': Counter, 'refused': [ids], 'gave_up_at': id | None}."""
    counts: Counter = Counter()
    refused: List[int] = []
    streak, gave_up_at = 0, None
    for k, mid in enumerate(range(newest, floor - 1, -1)):
        result = tg.delete_message(mid)
        counts[result] += 1
        if result == "refused":
            refused.append(mid)
            streak += 1
            if give_up_after and streak >= give_up_after:
                gave_up_at = mid
                break
        else:
            streak = 0
        if k % 100 == 99:
            progress(mid, counts)
    return {"counts": counts, "refused": refused, "gave_up_at": gave_up_at}


def main() -> int:
    ap = argparse.ArgumentParser(description="Empty the DEV chat (never production)")
    ap.add_argument("--yes", action="store_true", help="really delete (without it nothing is touched)")
    ap.add_argument("--floor", type=int, default=1, help="lowest message id to try (default 1)")
    args = ap.parse_args()

    _load_env()
    dev, prod = os.getenv("TELEGRAM_DEV_CHAT_ID"), os.getenv("TELEGRAM_CHAT_ID")
    try:
        tg = TelegramClient.from_env("dev", dry_run=False)          # the dev chat is always open; production is locked anyway
        info = tg.chat_info()
        check_target(dev, prod, info.get("type", ""))
    except (ResetRefused, TelegramError) as e:
        print(f"REFUSED: {e}")
        return 2
    print(f"Target: {info.get('title')!r} ({info.get('type')}) - the DEV chat.")
    if not args.yes:
        print("DRY RUN - nothing was touched. Add --yes to post a marker, learn the newest message id and delete every message below it.")
        return 0
    marker_id = tg.send_message(MARKER, silent=True)[0]
    print(f"Marker is message #{marker_id}: sweeping #{marker_id} down to #{args.floor} ...")
    out = sweep(tg, marker_id, args.floor, lambda mid, c: print(f"  ... at #{mid}: {dict(c)}", flush=True))
    c = out["counts"]
    print(f"\nDONE. deleted={c['deleted']}  already-gone={c['missing']}  refused={c['refused']}")
    if out["gave_up_at"]:
        print(f"Stopped at #{out['gave_up_at']}: Telegram refused {GIVE_UP_AFTER} messages in a row. It does not let a bot delete messages older "
              "than about 48 hours, so everything below is out of reach. For a truly clean slate create a NEW chat (recommended) or delete the old "
              "messages by hand in the Telegram app.")
    elif out["refused"]:
        print(f"Telegram refused {len(out['refused'])} message(s) (too old, or service messages like 'joined'): delete them by hand if they bother you.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
