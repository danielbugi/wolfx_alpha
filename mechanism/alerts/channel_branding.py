#!/usr/bin/env python3
# mechanism/alerts/channel_branding.py
"""
The channel's public name and description (owner decision 2026-10-22: "First Light ⚡ Market Daily"). Telegram search matches words
in a channel's title and description, so both carry the plain words people search for (US stock market, S&P 500, Nasdaq, daily).

    python mechanism/alerts/channel_branding.py                     # DRY RUN: prints what would be set, changes nothing
    python mechanism/alerts/channel_branding.py --apply --to prod   # sets the title, description and photo of the production channel

The bot must be an admin of the channel with the "change channel info" right. The public @username cannot be changed by a bot
(Telegram allows that only from the owner's account), so it is printed as a suggestion only. Production is still behind the
PROD_SENDING_ENABLED lock inside TelegramClient.from_env.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

from alerts.telegram_client import TelegramClient, TelegramError  # noqa: E402

TITLE = "First Light ⚡ Market Daily"                      # Telegram limit: 128 characters
DESCRIPTION = (                                              # Telegram limit: 255 characters
    "📊 The US stock market in 60 seconds, every trading day after the close.\n"
    "S&P 500 · Nasdaq · Dow · sectors · top movers · gold, oil, Bitcoin.\n"
    "⚡ Sharp. Daily. No noise.\n"
    "Educational, not advice. first-light.finance")
USERNAME_SUGGESTIONS = ("FirstLightMarkets", "FirstLightDaily", "FirstLightMarketDaily")


def main() -> int:
    ap = argparse.ArgumentParser(description="Set the channel's title and description (dry run by default)")
    ap.add_argument("--apply", action="store_true", help="really set them")
    ap.add_argument("--to", choices=["dev", "prod"], default="dev")
    args = ap.parse_args()
    assert len(TITLE) <= 128 and len(DESCRIPTION) <= 255, "Telegram limits"
    print(f"Title ({len(TITLE)}/128): {TITLE}\nDescription ({len(DESCRIPTION)}/255):\n{DESCRIPTION}\n")
    print("Public @username (owner sets it in Telegram; a bot cannot): suggestions " + ", ".join("@" + u for u in USERNAME_SUGGESTIONS))
    if not args.apply:
        print("\nDRY RUN - nothing changed. Add --apply (dev) or --apply --to prod.")
        return 0
    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        tg._post("setChatTitle", {"chat_id": tg.chat_id, "title": TITLE})
        tg._post("setChatDescription", {"chat_id": tg.chat_id, "description": DESCRIPTION})
        from alerts.brand_assets import render_channel_avatar
        tg._post("setChatPhoto", {"chat_id": tg.chat_id}, files={"photo": ("channel_avatar.png", render_channel_avatar(), "image/png")})
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    print(f"\nSET the title, description and photo of the {args.to.upper()} channel. The bot's own photo is set in BotFather "
          "(/setuserpic) with brand_assets.py's bot_avatar.png -- a bot cannot change its own picture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
