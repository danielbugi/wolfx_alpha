#!/usr/bin/env python3
# mechanism/alerts/channel_posts.py
"""
The channel's fixed posts: the pinned "Start here" post and the promotion post for the assistant (FUNNEL_PLAN.md section 5 and 8).

    python mechanism/alerts/channel_posts.py --start-here --promo              # DRY RUN: prints the texts, sends nothing
    python mechanism/alerts/channel_posts.py --start-here --promo --send        # to the DEV group (always open)
    python mechanism/alerts/channel_posts.py --start-here --promo --send --to prod   # production: LOCKED until PROD_SENDING_ENABLED=1

`--start-here` also pins that post. The promo image comes from promo_assets.py. Copy rules (public text): educational tone, no advice words, no
performance claims, plain language (the reader is the public, not a trader); a test scans it.

THE PINNED POST IS WHERE EVERY DISCLAIMER LIVES. Individual channel posts and images carry no disclaimer text of their own (the twice-daily notice in
channel_content.post_disclaimer points readers here). The general disclaimer is the "Please read: important" block; the caveats that used to sit inside
single posts are the per-service notes (SERVICE_NOTES), one per kind of post. A test fails if a post kind has no note.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path
from typing import Dict, List, Tuple

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from alerts.telegram_client import TelegramClient, TelegramError  # noqa: E402

# (key, title, note). Plain words; every note says what that service does NOT tell you.
SERVICE_NOTES: List[Tuple[str, str, str]] = [
    ("lists", "Daily lists (Breakout, Near breakout)",
     "They describe what already happened at the close, ranked by a formula. A stock on a list is not a forecast and not a suggestion to trade it. "
     "A \"small cap\" tag means a market value under $2B: such stocks can move "
     "much more and be harder to trade."),
    ("momentum_board", "Momentum board",
     "It shows only stocks from recent lists that closed higher today, so it is the best of a group, not a record of how the lists did. The counts "
     "on the picture cover the whole group. Earlier lists say nothing about later ones."),
    ("market_health", "Market health",
     "It describes how broad today's move was; it does not predict tomorrow. Each percentage counts only stocks with enough price history for that "
     "measure."),
    ("top_gainers", "Top gainers",
     "Ranked by today's price change alone. A stock need not be near a breakout to appear here."),
    ("sector", "Sector rotation",
     "Each bar is the middle stock of its sector, so one outlier cannot move it. Sector tags are today's; earlier sector membership is not "
     "reconstructed, so older periods are approximate."),
    ("macro", "Beyond stocks", "Gold and crude oil are front-month futures. Bitcoin trades every day."),
    ("gaps", "Gaps and volume, near 52-week highs",
     "Volume is compared with a stock's own usual volume. On options-expiry days (the third Friday of a month) nearly every stock trades more."),
    ("aligned", "Breakouts on longer timeframes",
     "Counts only; names are in the private assistant. A stock without enough history for a measure is not counted in it."),
    ("base_rate", "Base rates",
     "History since 2018 built from today's index members, so stocks that dropped out are missing (survivor bias). It describes the past, is not a "
     "forecast, and results varied a lot by year."),
    ("recap", "Weekly recap and list scoreboard",
     "The recap counts the daily lists as posted. A scoreboard, when we post one, counts a stock once per session; consecutive days of the same stock "
     "overlap, so its numbers are descriptive, not independent tests."),
    ("news", "News",
     "Headlines are published by their sources and linked as they are. We do not write or edit them, and headlines that read as ratings or price "
     "calls are left out."),
    ("earnings_today", "Who reports today",
     "Covered symbols only, not every US company. No before/after-market timing given."),
    ("assistant", "The private assistant",
     "Your figures use the prices you enter or the last close, and show the change since the day you added a stock. ATR levels are an educational "
     "reference, not a plan for you."),
]

# which note covers which kind of channel post (the digest's own lists = "lists"); promo and the notices are not data posts
NOTE_FOR_KIND: Dict[str, str] = {
    "digest": "lists", "momentum_board": "momentum_board", "market_health": "market_health", "top_gainers": "top_gainers", "sector": "sector",
    "macro": "macro", "gaps": "gaps", "near_highs": "gaps", "aligned": "aligned", "base_rate": "base_rate", "recap": "recap", "scoreboard": "recap",
    "news": "news", "assistant": "assistant", "earnings_today": "earnings_today",
}


def _notes_block() -> str:
    rows = [f"<b>{html.escape(title)}</b>\n{html.escape(note, quote=False)}" for _key, title, note in SERVICE_NOTES]
    return "<blockquote expandable>" + "\n\n".join(rows) + "</blockquote>"


START_HERE = (
    "<b>First Light</b> · first-light.finance\n"
    "The US market, right after the close, every trading day.\n\n"
    "<b>What you get</b>\n"
    "After the US market closes we scan about 2,900 stocks for fresh highs, near-highs and how the market and its sectors did.\n\n"
    "<b>How fresh is it?</b>\n"
    "End of day, from the official US market close, posted after it. "
    "Nothing is live, prices may have moved since.\n\n"
    "<b>What you will find here</b>\n"
    "• <b>The market card and two lists.</b> <b>Breakout</b> = closed above its highest price of the last 20 trading days. "
    "<b>Near breakout</b> = within 3% below that level. Each list is ranked by biggest gain, biggest swing (ATR: the day's range against the usual "
    "range) and busiest trading (volume).\n"
    "• <b>Momentum board, top gainers and market health</b>, every session.\n"
    "• <b>One extra post a day</b> (sectors, gaps, yearly highs and more) and a <b>weekly recap</b> on Sundays.\n\n"
    "<b>Reading a row</b>\n"
    "★ = near the top of more than one list · vol 3.1× = about three times the usual trading volume.\n\n"
    "<b>The private assistant</b>\n"
    "Your own watchlist and portfolio, followed from the day you add each stock, with news and a chart for every name. "
    "Access on request, seats limited. Tap <b>Request access</b> under the daily post.\n\n"
    "<b>Please read: important</b>\n"
    "• This channel is educational information only. It is <b>not investment advice</b> and not a suggestion to trade any security. "
    "Nothing here is tailored to you.\n"
    "• A list describes what already happened. It does not say what happens next, and most breakouts do not run far.\n"
    "• All investing carries risk, including the loss of what you invest.\n"
    "• The data comes from public sources and can contain delays or mistakes. Check it before you rely on it.\n"
    "• Everyone makes their own decisions.\n\n"
    "<b>Notes for each post</b> (tap to expand)\n" + _notes_block() + "\n\n"
    "<i>* Not investment advice.</i>")

PROMO_CAPTION = (
    "<b>The channel shows what moved. The assistant shows what moved for you.</b>\n"
    "Today's lists, a card for every stock with news and a chart, and a watchlist and portfolio that follow each stock from the day you add it.\n\n"
    "Access on request · seats limited. Tap <b>Request access</b> under today's post.")


def post_start_here(tg: TelegramClient, pin: bool = True) -> list:
    ids = tg.send_message(START_HERE, silent=True, kind="start_here")
    if pin and ids and ids[0]:
        try:
            tg.pin_message(ids[0], silent=True)
        except TelegramError as e:                                    # e.g. the bot lacks the pin right: the post is still there
            print(f"  (could not pin: {e})")
    return ids


def post_promo(tg: TelegramClient, png: bytes) -> object:
    return tg.send_photo(png, PROMO_CAPTION, silent=True, kind="promo")


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
