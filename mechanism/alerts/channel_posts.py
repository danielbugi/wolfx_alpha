#!/usr/bin/env python3
# mechanism/alerts/channel_posts.py
"""
The channel's fixed posts: the pinned "Start here" post and the promotion post for the assistant (FUNNEL_PLAN.md section 5 and 8).

    python mechanism/alerts/channel_posts.py --start-here --promo              # DRY RUN: prints the texts, sends nothing
    python mechanism/alerts/channel_posts.py --start-here --promo --send        # to the DEV group (always open)
    python mechanism/alerts/channel_posts.py --start-here --promo --send --to prod   # production: LOCKED until PROD_SENDING_ENABLED=1

`--start-here` also pins that post. The promo image comes from promo_assets.py. Copy rules (public text): educational tone, no advice words, no
performance claims, the disclaimer is always present; a test scans it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from alerts.telegram_client import TelegramClient, TelegramError  # noqa: E402

START_HERE = (
    "<b>First Light — Stocks &amp; Info</b>\n"
    "Every trading day we scan about 2,900 liquid US stocks and post the market card plus two lists.\n\n"
    "<b>The two lists</b>\n"
    "<b>Breakout</b> — closed above the highest high of the prior 20 sessions.\n"
    "<b>Near breakout</b> — within 3% below that high, not through it yet.\n"
    "Each list is ranked three ways: top gainers, top ATR (today's range against its 14-day average) and top volume (today's volume against "
    "its 50-day median).\n\n"
    "<b>How to read a row</b>\n"
    "★ = the stock is in more than one list · vol 3.1× = volume against its 50-day median · range 2.6× ATR = today's range against its "
    "14-day average.\n\n"
    "<b>The private assistant</b> (free during the beta, by invitation)\n"
    "Follow your own watchlist and portfolio from the day you add a stock, with news and a chart for each one. "
    "Tap <b>Private assistant</b> under the daily post to request access.\n\n"
    "<i>Educational information from public price data. Not investment advice and not a suggestion to trade any security. "
    "Everyone makes their own decisions.</i>")

PROMO_CAPTION = (
    "<b>Meet the First Light assistant</b>\n"
    "Your private stock assistant, built on the daily First Light scan: today's lists, a card for every stock with news and a chart, and a "
    "watchlist and portfolio that follow each stock from the day you add it.\n\n"
    "Free during the beta · by invitation. Tap <b>Private assistant</b> under today's post to request access.\n"
    "<i>Example screens. Educational data, not investment advice.</i>")


def post_start_here(tg: TelegramClient, pin: bool = True) -> list:
    ids = tg.send_message(START_HERE, silent=True)
    if pin and ids and ids[0]:
        try:
            tg.pin_message(ids[0], silent=True)
        except TelegramError as e:                                    # e.g. the bot lacks the pin right: the post is still there
            print(f"  (could not pin: {e})")
    return ids


def post_promo(tg: TelegramClient, png: bytes) -> object:
    return tg.send_photo(png, PROMO_CAPTION, silent=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Post the channel's Start-here and promotion posts (dry run by default)")
    ap.add_argument("--start-here", action="store_true", help="the pinned 'Start here' post")
    ap.add_argument("--promo", action="store_true", help="the promotion image for the assistant")
    ap.add_argument("--send", action="store_true", help="really send (dev unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod"], default="dev")
    ap.add_argument("--no-pin", action="store_true", help="do not pin the Start-here post")
    args = ap.parse_args()
    if not (args.start_here or args.promo):
        ap.error("choose --start-here and/or --promo")
    png = None
    if args.promo:
        from alerts.promo_assets import render_promo
        png = render_promo()
    if not args.send:
        if args.start_here:
            print(f"--- START HERE ({len(START_HERE)} chars) ---\n{START_HERE}\n")
        if args.promo:
            print(f"--- PROMO caption ({len(PROMO_CAPTION)} chars) + image {len(png) / 1024:.0f} KB ---\n{PROMO_CAPTION}\n")
        print("DRY RUN - nothing was sent. Add --send (dev) or --send --to prod (locked until launch).")
        return 0
    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        if args.start_here:
            print("start-here:", post_start_here(tg, pin=not args.no_pin))
        if args.promo:
            print("promo:", post_promo(tg, png))
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
