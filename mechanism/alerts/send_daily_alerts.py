#!/usr/bin/env python3
# mechanism/alerts/send_daily_alerts.py
"""
Daily momentum shortlist -> Telegram.

    python mechanism/alerts/send_daily_alerts.py                    # DRY RUN: builds and prints, sends nothing
    python mechanism/alerts/send_daily_alerts.py --send             # sends to the DEVELOPMENT channel
    python mechanism/alerts/send_daily_alerts.py --send --to prod   # sends to the production channel

Safety: dry-run is the default; --send targets the dev channel unless --to prod is given; the ledger (`alerts`
table) only records alerts that were actually sent; a stale or half-loaded price table aborts the run.
Settings come from .env (TELEGRAM_*, ALERTS_*); the bot token is never printed.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))                      # `shared`, `alerts`
sys.path.insert(0, str(MECH.parent))               # ml_training

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

from shared import db, market_calendar  # noqa: E402
from alerts.alert_builder import build_alerts, rank_top_movers, template_from_env  # noqa: E402
from alerts.message_format import format_card, format_header  # noqa: E402
from alerts.news_links import fetch_news  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError  # noqa: E402

MAX_DATA_AGE_DAYS = 4          # last price date older than this (calendar days, Jerusalem today) => abort
MIN_COVERAGE = 0.80            # symbols priced on the session date vs the previous session


def load_prices_window(session_date, days_back=50) -> pd.DataFrame:
    rows = db.execute_dict_query(
        "SELECT symbol, date, close, volume FROM stock_prices WHERE date >= %s::date - %s::int AND date <= %s",
        (session_date, days_back, session_date))
    df = pd.DataFrame(rows)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    return df


def load_history(symbol: str, session_date) -> pd.DataFrame:
    rows = db.execute_dict_query(
        "SELECT date, open, high, low, close, volume FROM stock_prices WHERE symbol = %s AND date <= %s "
        "ORDER BY date DESC LIMIT 330", (symbol, session_date))
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def sector_lookup(symbol: str) -> dict:
    rows = db.execute_dict_query(
        "SELECT sector, market_cap FROM daily_fundamentals WHERE symbol = %s AND sector IS NOT NULL "
        "ORDER BY date DESC LIMIT 1", (symbol,))
    if not rows:
        return {}
    r = rows[0]
    return {"sector": r["sector"], "market_cap": float(r["market_cap"]) if r["market_cap"] else None}


def resolve_session(date_arg, tz):
    """(session_date, universe_count, notes). Aborts (SystemExit) on stale or half-loaded data."""
    latest = db.execute_dict_query("SELECT MAX(date) AS d FROM stock_prices")[0]["d"]
    session = pd.Timestamp(date_arg).date() if date_arg else latest
    today = datetime.now(tz).date()
    age = (today - session).days
    if not date_arg and age > MAX_DATA_AGE_DAYS:
        raise SystemExit(f"ABORT: newest price date is {session} ({age} days before {today}); run the data pipeline first.")
    cnt = db.execute_dict_query(
        "SELECT date, COUNT(*) AS n FROM stock_prices WHERE date <= %s GROUP BY date ORDER BY date DESC LIMIT 2", (session,))
    if len(cnt) < 2:
        raise SystemExit("ABORT: fewer than two sessions of prices.")
    n_now, n_prev = cnt[0]["n"], cnt[1]["n"]
    if cnt[0]["date"] != session or n_now < MIN_COVERAGE * n_prev:
        raise SystemExit(f"ABORT: only {n_now} symbols priced on {session} vs {n_prev} the session before (partial update).")
    notes = []
    if age > 1 + (2 if today.weekday() in (5, 6, 0) else 0):
        notes.append(f"newest data is from {session} ({age} days ago) - the last session may be missing")
    return session, n_now, notes


def main():
    ap = argparse.ArgumentParser(description="Daily momentum shortlist -> Telegram (dry-run by default)")
    ap.add_argument("--send", action="store_true", help="actually send (dev channel unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod"], default="dev")
    ap.add_argument("--top", type=int, default=int(os.getenv("ALERTS_TOP_N", "15")))
    ap.add_argument("--news", type=int, default=int(os.getenv("ALERTS_MAX_NEWS_LINKS", "3")), help="news links per alert (0 = off)")
    ap.add_argument("--date", help="session date YYYY-MM-DD (default: newest in stock_prices)")
    ap.add_argument("--plan", help="runner | balanced | ladder (default: ALERTS_PLAN or balanced)")
    ap.add_argument("--force", action="store_true", help="ignore ALERTS_SKIP_WEEKDAYS")
    args = ap.parse_args()

    tz = ZoneInfo(os.getenv("ALERTS_TIMEZONE", "Asia/Jerusalem"))
    now_local = datetime.now(tz)
    skip = {d.strip().lower()[:3] for d in os.getenv("ALERTS_SKIP_WEEKDAYS", "").split(",") if d.strip()}
    if args.send and not args.force and now_local.strftime("%a").lower() in skip:
        print(f"Skipping: {now_local:%A} is in ALERTS_SKIP_WEEKDAYS")
        return 0

    # Trading-day gate: only post when a US session completed that this channel has not been sent yet (weekends, NYSE
    # holidays, a second run the same day). --force / an explicit --date bypass it.
    state_key = f"alerts:{args.to}"
    gate = market_calendar.check_new_session(state_key, force=args.force or bool(args.date))
    if not gate.run:
        if args.send:
            print(f"Skipping: {gate.reason}")
            return 0
        print(f"NOTE (dry run): a real --send would be skipped - {gate.reason}")

    template = template_from_env(args.plan)
    session, universe_n, notes = resolve_session(args.date, tz)
    if not args.date:
        market_calendar.require_data_current(session, gate)
    print(f"Session {session} | universe {universe_n} symbols | plan template: {args.plan or os.getenv('ALERTS_PLAN') or 'balanced'}")

    movers = rank_top_movers(load_prices_window(session), session, pool=max(args.top * 3, 45))
    alerts, skipped = build_alerts(movers, lambda s: load_history(s, session), sector_lookup, top_n=args.top, template=template)
    if args.news > 0:
        for a in alerts:
            a["news"] = fetch_news(a["symbol"], n=args.news)
    now_utc = datetime.now(timezone.utc)

    header = format_header(session, now_local, len(alerts), universe_n, template, notes)
    cards = [format_card(i, a, now_utc, template) for i, a in enumerate(alerts, 1)]

    bar = "=" * 78
    print(f"\n{bar}\n{header}\n{bar}")
    for c in cards:
        print(f"\n{c}\n{'-' * 78}")
    if skipped:
        print("\nSkipped candidates: " + "; ".join(f"{s} ({r})" for s, r in skipped))

    if not args.send:
        print("\nDRY RUN - nothing was sent and nothing was recorded. Use --send to post to the dev channel.")
        return 0

    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        tg.send_message(header)
        for a, card in zip(alerts, cards):
            ids = tg.send_message(card)
            p = a["plan"]
            db.execute_insert(
                """INSERT INTO alerts (alert_date, symbol, source, channel, entry_ref, atr, stop, tp1, tp2, tp3, trail_pct, plan, flags, telegram_message_id)
                   VALUES (%s,%s,'top_gainers',%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                   ON CONFLICT (alert_date, symbol, channel, source) DO UPDATE SET entry_ref=EXCLUDED.entry_ref, stop=EXCLUDED.stop,
                   tp1=EXCLUDED.tp1, tp2=EXCLUDED.tp2, tp3=EXCLUDED.tp3, plan=EXCLUDED.plan, flags=EXCLUDED.flags,
                   telegram_message_id=EXCLUDED.telegram_message_id, sent_at=NOW()""",
                (session, a["symbol"], args.to, p["entry"], p["atr"], p["stop"], p["tps"][0]["price"], p["tps"][1]["price"],
                 p["tps"][2]["price"], p["trail_pct"], json.dumps(p),
                 json.dumps({"flags": a["flags"], "flag_count": a["flag_count"], "facts": {k: (v if not isinstance(v, (pd.Timestamp,)) else str(v)) for k, v in a["facts"].items()},
                                           "mtf": a["mtf"]}, default=str), ids[-1]))
        market_calendar.mark_processed(state_key, session)
        print(f"\nSENT {len(alerts)} alerts + header to the {args.to.upper()} channel and recorded them in the ledger.")
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
