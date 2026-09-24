#!/usr/bin/env python3
# mechanism/alerts/send_channel_notices.py
"""
The channel's recurring NOTICES, twice a day: the commercial post for the private assistant (four rotating variants) in both slots, and in slot 1 only,
first, the one-line disclaimer notice that points readers to the pinned message. All silent. Scheduled at two times a day (slot 1 = midday, slot 2 =
evening; see run_first_light_notice.ps1); unlike the digest they do not depend on a US session, so they run every day.

    python mechanism/alerts/send_channel_notices.py --slot 1                      # DRY RUN: prints both posts, sends nothing
    python mechanism/alerts/send_channel_notices.py --slot 1 --send               # to the DEV channel
    python mechanism/alerts/send_channel_notices.py --slot 2 --send --to prod     # production: LOCKED until PROD_SENDING_ENABLED=1
    python mechanism/alerts/send_channel_notices.py --slot 1 --send --force       # send again even if this slot already went out today

Same safety rules as the other senders: dry run by default, dev by default, the production lock lives inside TelegramClient.from_env, and the token is
never printed. A (target, day, slot) is sent once: a second run of the same slot the same day is skipped (state in data/notice_state.json, which is a
git-ignored output directory). Nothing here touches the assistant, its screens or its tables.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

from alerts import channel_content as cx  # noqa: E402
from alerts import deeplink  # noqa: E402
from alerts.digest_format import PRIVATE_ASSISTANT_BUTTON  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError, text_length  # noqa: E402

STATE_FILE = MECH.parent / "data" / "notice_state.json"
KEEP_DAYS = 14
KINDS = ("disclaimer", "assistant")            # sent in this order, a couple of seconds apart


def variant_index(day: date, slot: int) -> int:
    """Which assistant variant a (day, slot) shows: it advances by one every slot, so consecutive notices differ."""
    return day.toordinal() * 2 + (slot - 1)


def kinds_for(slot: int):
    """The one-line disclaimer notice goes out once a day (slot 1); every slot carries the assistant post. Fewer non-data posts = fewer reasons to mute."""
    return KINDS if slot == 1 else KINDS[1:]


def build_posts(day: date, slot: int):
    ctx = cx.Ctx(session=day, notice_index=variant_index(day, slot))
    posts = [cx.build_post(k, ctx) for k in kinds_for(slot)]
    for p in posts:
        if p is None or text_length(p.text) > 4096:
            raise SystemExit(f"ABORT: the '{p.kind if p else '?'}' notice could not be built within Telegram's limits; nothing sent.")
    return posts


def _key(target: str, day: date, slot: int) -> str:
    return f"{target}:{day.isoformat()}:{slot}"


def already_sent(target: str, day: date, slot: int, state_file: Path = STATE_FILE) -> bool:
    try:
        return _key(target, day, slot) in json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False                                    # no state yet (or unreadable): treat as not sent


def mark_sent(target: str, day: date, slot: int, state_file: Path = STATE_FILE) -> None:
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    cutoff = (day - timedelta(days=KEEP_DAYS)).isoformat()
    state = {k: v for k, v in state.items() if k.split(":")[1] >= cutoff}
    state[_key(target, day, slot)] = datetime.now().isoformat(timespec="seconds")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="The channel's twice-daily notices (dry-run by default)")
    ap.add_argument("--slot", type=int, choices=[1, 2], required=True, help="1 = midday run, 2 = evening run")
    ap.add_argument("--send", action="store_true", help="really send (dev unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod"], default="dev")
    ap.add_argument("--force", action="store_true", help="send even if this slot already went out today")
    ap.add_argument("--date", help="pretend it is this day (YYYY-MM-DD; picks the variant), for previews")
    args = ap.parse_args()

    tz = ZoneInfo(os.getenv("ALERTS_TIMEZONE", "Asia/Jerusalem"))
    day = date.fromisoformat(args.date) if args.date else datetime.now(tz).date()
    posts = build_posts(day, args.slot)
    for p in posts:
        print(f"\n{'=' * 78}\n[{p.kind}]{' + assistant button' if p.button else ''}\n{p.text}\n{'=' * 78}   [{text_length(p.text)} chars]")
    if not args.send:
        print("\nDRY RUN - nothing was sent. Use --send (dev) or --send --to prod (locked until launch).")
        return 0
    if already_sent(args.to, day, args.slot) and not args.force:
        print(f"Skipping: slot {args.slot} was already sent to {args.to} on {day}. Use --force to send it again.")
        return 0
    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        username = os.getenv("TELEGRAM_BOT_USERNAME") or tg.get_me()["username"]
        keyboard = {"inline_keyboard": [[{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink.link(username, "ch")}]]}
        for p in posts:
            tg.send_message(p.text, disable_preview=p.disable_preview, silent=True, reply_markup=keyboard if p.button else None, kind=p.kind)
        mark_sent(args.to, day, args.slot)
        print(f"\nSENT {len(posts)} notices [{', '.join(p.kind for p in posts)}] to {args.to.upper()} (slot {args.slot}, {day}).")
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
