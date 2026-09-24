#!/usr/bin/env python3
# mechanism/alerts/send_earnings_today.py
"""
The channel's pre-market "who reports today" post (DATA_ML_MILESTONES.md M3). Own schedule, 11:00
Israel time -- a few hours before the US pre-market, not right before it (confirmed with the owner).
Built from earnings_calendar (see DATA_ML_MILESTONES.md M2), restricted to the covered/liquid universe
(symbols the digest actually classified in its most recent session), with sector and last close from
daily_fundamentals / stock_prices as context. No before/after-market timing -- the stored calendar only
has the report DATE (yfinance's Ticker.get_earnings_dates() intraday times were not kept -- see the
schema note in mechanism/add_earnings_calendar_table.sql).

Trading-day gate, per the owner's explicit requirement: if the market is closed today, nobody reports
and nobody trades -- not relevant to post. Uses market_calendar.is_trading_day(), which is a plain
"is this calendar date an actual US session" check -- distinct from the digest's
check_new_session ("has a session COMPLETED"), since this post is about TODAY, sent same-day before
the close, not about a session that has already finished.

    python mechanism/alerts/send_earnings_today.py                  # DRY RUN: prints the post, sends nothing
    python mechanism/alerts/send_earnings_today.py --send            # to the DEV channel
    python mechanism/alerts/send_earnings_today.py --send --to prod  # production: LOCKED until PROD_SENDING_ENABLED=1
    python mechanism/alerts/send_earnings_today.py --date 2026-09-23 # preview a different day

Same safety rules as the other channel senders: dry run by default, dev by default, the production lock
lives inside TelegramClient.from_env, the token is never logged. A (target, day) is sent once -- a
second run the same day is skipped unless --force. Nothing here touches the assistant, its screens or
its tables (channel content only, per CLAUDE.md's "a channel carries only data, promotion, news and
information" rule).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

from shared import db  # noqa: E402
from shared import market_calendar  # noqa: E402
from alerts import channel_content as cx  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError, text_length  # noqa: E402

STATE_FILE = MECH.parent / "data" / "earnings_today_state.json"
KEEP_DAYS = 14


def _key(target: str, day: date) -> str:
    return f"{target}:{day.isoformat()}"


def already_sent(target: str, day: date, state_file: Path = STATE_FILE) -> bool:
    try:
        return _key(target, day) in json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False


def mark_sent(target: str, day: date, state_file: Path = STATE_FILE) -> None:
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    cutoff = (day - timedelta(days=KEEP_DAYS)).isoformat()
    state = {k: v for k, v in state.items() if k.split(":")[1] >= cutoff}
    state[_key(target, day)] = datetime.now().isoformat(timespec="seconds")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=1), encoding="utf-8")


def load_todays_reporters(day: date) -> List[Dict]:
    """Today's earnings_calendar reporters restricted to the covered/liquid universe -- symbols the
    digest actually classified in its most recent stored session -- with sector and last close for
    context. Returns [] on any DB problem rather than raising (a missing extra post is never worse than
    a crashed scheduled job)."""
    try:
        rows = db.execute_dict_query(
            """
            SELECT ec.symbol, ec.eps_estimate, df.sector, sp.close
            FROM earnings_calendar ec
            JOIN (SELECT DISTINCT symbol FROM digest_stocks
                  WHERE session_date = (SELECT MAX(session_date) FROM digest_stocks)) cov
                ON cov.symbol = ec.symbol
            LEFT JOIN LATERAL (
                SELECT sector FROM daily_fundamentals
                WHERE symbol = ec.symbol AND sector IS NOT NULL ORDER BY date DESC LIMIT 1
            ) df ON TRUE
            LEFT JOIN LATERAL (
                SELECT close FROM stock_prices WHERE symbol = ec.symbol ORDER BY date DESC LIMIT 1
            ) sp ON TRUE
            WHERE ec.report_date = %s
            ORDER BY ec.symbol
            """,
            (day,))
        return [{"symbol": r["symbol"], "eps_estimate": float(r["eps_estimate"]) if r["eps_estimate"] is not None else None,
                 "sector": r["sector"], "close": float(r["close"]) if r["close"] is not None else None} for r in rows]
    except Exception as e:
        print(f"[send_earnings_today] failed to load reporters: {e}", file=sys.stderr)
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="The channel's pre-market 'who reports today' post (dry-run by default)")
    ap.add_argument("--send", action="store_true", help="really send (dev unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod"], default="dev")
    ap.add_argument("--force", action="store_true", help="send even if already sent today, or on a non-trading day")
    ap.add_argument("--date", help="pretend it is this day (YYYY-MM-DD), for previews")
    args = ap.parse_args()

    tz = ZoneInfo(os.getenv("ALERTS_TIMEZONE", "Asia/Jerusalem"))
    day = date.fromisoformat(args.date) if args.date else datetime.now(tz).astimezone(ZoneInfo("America/New_York")).date()

    if not args.force and not market_calendar.is_trading_day(day):
        msg = f"the market is closed on {day} -- nobody reports, nobody trades"
        if args.send:
            print(f"Skipping: {msg}")
            return 0
        print(f"NOTE (dry run): a real --send would be skipped - {msg}")

    reporters = load_todays_reporters(day)
    ctx = cx.Ctx(session=day, earnings_today=reporters)
    post = cx.build_post("earnings_today", ctx)

    if post is None:
        print(f"Nothing to post: no covered-universe reporters found for {day}.")
        return 0
    if text_length(post.text) > 4096:
        raise SystemExit("ABORT: the earnings-today post exceeds Telegram's limit; nothing sent.")

    print(f"\n{'=' * 78}\n[{post.kind}]\n{post.text}\n{'=' * 78}   [{text_length(post.text)} chars]")

    if not args.send:
        print("\nDRY RUN - nothing was sent. Use --send (dev) or --send --to prod (locked until launch).")
        return 0
    if not market_calendar.is_trading_day(day) and not args.force:
        return 0  # already explained above; do not send on a non-trading day
    if already_sent(args.to, day) and not args.force:
        print(f"Skipping: already sent to {args.to} on {day}. Use --force to send it again.")
        return 0

    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        tg.send_message(post.text, disable_preview=post.disable_preview, silent=True, kind=post.kind)
        mark_sent(args.to, day)
        print(f"\nSENT [{post.kind}] to {args.to.upper()} ({day}).")
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
