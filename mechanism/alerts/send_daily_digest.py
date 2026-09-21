#!/usr/bin/env python3
# mechanism/alerts/send_daily_digest.py
"""
"First Light" digest -> Telegram (long side only): Breakout and Near breakout x (top gainers, top ATR, top volume).

    python mechanism/alerts/send_daily_digest.py                    # DRY RUN: builds and prints, sends nothing
    python mechanism/alerts/send_daily_digest.py --send             # sends to the DEVELOPMENT channel
    python mechanism/alerts/send_daily_digest.py --send --to prod   # sends to the production channel
    python mechanism/alerts/send_daily_digest.py --snapshot-only    # only refresh the bot's Postgres snapshot

Same safety rules as send_daily_alerts.py (dry-run default, dev channel unless --to prod, stale / half-loaded price
data aborts, the token is never printed). A real send also saves the day's snapshot (digest_runs / digest_stocks) that
run_bot.py reads; a dry run writes nothing. Nothing is written to the `alerts` ledger yet (that is the scoreboard slice).
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))                      # `shared`, `alerts`
sys.path.insert(0, str(MECH.parent))               # ml_training

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

from shared import db, market_calendar  # noqa: E402
from alerts import digest_builder as dbld  # noqa: E402
from alerts.alert_builder import Skip  # noqa: E402
from alerts.digest_format import format_caption, format_group, format_header, header_keyboard  # noqa: E402
from alerts.market_card import render_market_card  # noqa: E402
from alerts.market_context import build_card_data  # noqa: E402
from alerts.send_daily_alerts import resolve_session  # noqa: E402
from alerts.snapshot import save_snapshot  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError  # noqa: E402
from ml_training.features import price_features as pf  # noqa: E402

CALENDAR_DAYS_BACK = 460       # ~316 trading bars: SMA200 + 52-week range + a margin
CARD_DIR = MECH.parent / "reports" / "first_light"      # rendered market cards (reports/ is a git-ignored output directory)


def load_universe_history(session_date) -> pd.DataFrame:
    rows = db.execute_dict_query(
        "SELECT symbol, date, open, high, low, close, volume FROM stock_prices "
        "WHERE date >= %s::date - %s::int AND date <= %s ORDER BY symbol, date",
        (session_date, CALENDAR_DAYS_BACK, session_date))
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df


def analyse_universe(df: pd.DataFrame, session_date):
    """(rows, universe_n, breadth, skipped). Only symbols priced on the session date with a 20-day $ volume >= the
    universe floor; breadth = how many of them closed up / down vs the previous close."""
    session_ts = pd.Timestamp(session_date)
    rows, skipped, universe_n, breadth = [], {}, 0, {"up": 0, "down": 0}
    groups = list(df.groupby("symbol", sort=True))
    for k, (sym, g) in enumerate(groups, 1):
        if k % 500 == 0:
            print(f"  ... {k}/{len(groups)} symbols analysed")
        if g["date"].iloc[-1] != session_ts:
            skipped["no bar on the session date"] = skipped.get("no bar on the session date", 0) + 1
            continue
        dv20 = float((g["close"] * g["volume"]).iloc[-21:-1].mean())       # prior 20 sessions, same as analyze()
        if not dv20 >= pf.MIN_DOLLAR_VOLUME_20:
            skipped["below the $1M/day liquidity floor"] = skipped.get("below the $1M/day liquidity floor", 0) + 1
            continue
        universe_n += 1                                                    # liquid and priced on the session date
        if len(g) >= 2:
            step = g["close"].iloc[-1] - g["close"].iloc[-2]
            breadth["up"] += int(step > 0)
            breadth["down"] += int(step < 0)
        try:
            r = dbld.analyze(g)
        except Skip as s:
            skipped[str(s.args[0])] = skipped.get(str(s.args[0]), 0) + 1
            continue
        except Exception as ex:                       # one bad symbol must not kill the run
            key = f"error: {type(ex).__name__}: {ex}"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        r["symbol"] = sym
        rows.append(r)
    return rows, universe_n, breadth, skipped


def attach_market_caps(rows) -> int:
    """Put the latest known market value on each row (in memory only: it is a label for the message, not part of the snapshot).
    Returns how many stocks got one; a stock without a market cap simply gets no size tag."""
    caps = {r["symbol"]: float(r["market_cap"]) for r in db.execute_dict_query(
        "SELECT DISTINCT ON (symbol) symbol, market_cap FROM daily_fundamentals WHERE market_cap IS NOT NULL AND market_cap > 0 "
        "ORDER BY symbol, date DESC")}
    n = 0
    for r in rows:
        if r["symbol"] in caps:
            r["mcap"] = caps[r["symbol"]]
            n += 1
    return n


def index_line(session_date):
    """['S&P 500 +0.4%', 'VIX (volatility index) 14.8'] from market_index_prices, only rows dated on the session date."""
    rows = db.execute_dict_query(
        "SELECT symbol, date, close FROM market_index_prices WHERE symbol IN ('^GSPC','^VIX') AND date <= %s "
        "ORDER BY symbol, date DESC", (session_date,))
    by = {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r)
    parts = []
    for sym, label in (("^GSPC", "S&P 500"), ("^VIX", "VIX (volatility index)")):
        v = by.get(sym, [])
        if len(v) < 2 or v[0]["date"] != pd.Timestamp(session_date).date():
            continue
        last, prev = float(v[0]["close"]), float(v[1]["close"])
        parts.append(f"{label} {(last / prev - 1) * 100:+.1f}%" if sym == "^GSPC" else f"{label} {last:.1f}")
    return parts


def main():
    ap = argparse.ArgumentParser(description="First Light digest -> Telegram (dry-run by default)")
    ap.add_argument("--send", action="store_true", help="actually send (dev channel unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod"], default="dev")
    ap.add_argument("--top", type=int, default=5, help="rows per list")
    ap.add_argument("--min-dv", type=float, default=dbld.DEFAULT_MIN_DV, help="min 20-day avg dollar volume for the ranked lists")
    ap.add_argument("--date", help="session date YYYY-MM-DD (default: newest in stock_prices)")
    ap.add_argument("--force", action="store_true", help="ignore ALERTS_SKIP_WEEKDAYS")
    ap.add_argument("--buttons", action="store_true",
                    help="inline buttons under the header: three educational popups + one neutral 'Private assistant "
                         "(invite only)' link (the popups only answer while run_bot.py is running)")
    ap.add_argument("--snapshot-only", action="store_true", help="write the bot snapshot to Postgres, send nothing")
    ap.add_argument("--image", action="store_true",
                    help="render the market-performance card (saved under reports/first_light/) and send it first")
    args = ap.parse_args()

    tz = ZoneInfo(os.getenv("ALERTS_TIMEZONE", "Asia/Jerusalem"))
    now_local = datetime.now(tz)
    skip = {d.strip().lower()[:3] for d in os.getenv("ALERTS_SKIP_WEEKDAYS", "").split(",") if d.strip()}
    if args.send and not args.force and now_local.strftime("%a").lower() in skip:
        print(f"Skipping: {now_local:%A} is in ALERTS_SKIP_WEEKDAYS")
        return 0

    # Trading-day gate: only post when a US session completed that this channel has not been sent yet (weekends, NYSE
    # holidays, a second run the same day). --force / an explicit --date bypass it; --snapshot-only is never blocked.
    state_key = f"digest:{args.to}"
    gate = market_calendar.check_new_session(state_key, force=args.force or bool(args.date))
    if not gate.run and not args.snapshot_only:
        if args.send:
            print(f"Skipping: {gate.reason}")
            return 0
        print(f"NOTE (dry run): a real --send would be skipped - {gate.reason}")

    session, cover_n, notes = resolve_session(args.date, tz)
    if not args.date:
        market_calendar.require_data_current(session, gate)
    print(f"Session {session} | {cover_n} symbols priced | loading history ...")
    t0 = time.time()
    df = load_universe_history(session)
    print(f"Loaded {len(df):,} rows in {time.time() - t0:.0f}s; analysing ...")
    rows, universe_n, breadth, skipped = analyse_universe(df, session)
    attach_market_caps(rows)
    digest = dbld.build_digest(rows, top_n=args.top, min_dv=args.min_dv)
    print(f"Analysed in {time.time() - t0:.0f}s total | liquid universe {universe_n:,} | groups {digest['counts']}")

    index_lines = index_line(session)
    if args.snapshot_only:
        n = save_snapshot(db, session, rows, digest, universe_n, breadth, index_lines, args.top)
        print(f"SNAPSHOT saved: {n} stocks for {session} (nothing sent).")
        return 0

    card_png = None
    if args.image:
        try:                                                        # the image is decoration: never let it stop the digest
            card_png = render_market_card(build_card_data(db, session, rows, universe_n, breadth))
            CARD_DIR.mkdir(parents=True, exist_ok=True)
            path = CARD_DIR / f"first_light_{session}.png"
            path.write_bytes(card_png)
            print(f"Market card saved: {path} ({len(card_png) / 1024:.0f} KB)")
        except Exception as ex:                                     # noqa: BLE001
            print(f"WARNING: market card not rendered ({type(ex).__name__}: {ex}); sending the text-only digest.")
    messages = [format_header(session, now_local, digest, universe_n, breadth, index_lines, notes, with_market=card_png is None)]
    messages += [format_group(cat, digest, args.top) for cat in dbld.LONG_CATEGORIES]

    bar = "=" * 78
    for m in messages:
        print(f"\n{bar}\n{m}\n{bar}   [{len(m)} chars]")
    if skipped:
        print("\nSkipped: " + "; ".join(f"{v} x {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])))

    if not args.send:
        print("\nDRY RUN - nothing was sent. Use --send to post to the dev channel.")
        return 0
    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        username = (os.getenv("TELEGRAM_BOT_USERNAME") or tg.get_me()["username"]) if args.buttons else None
        keyboard = header_keyboard(username) if username else None
        # only the header carries buttons; the group messages stay plain (no per-ticker / strategy buttons in a public channel)
        keyboards = [keyboard] + [None for _ in dbld.LONG_CATEGORIES]
        if card_png:                                                # the photo goes first and is the one that notifies
            tg.send_photo(card_png, format_caption(session, digest))
        for k, m in enumerate(messages):
            tg.send_message(m, silent=k > 0 or bool(card_png), reply_markup=keyboards[k])   # one notification only
        n = save_snapshot(db, session, rows, digest, universe_n, breadth, index_lines, args.top)
        market_calendar.mark_processed(state_key, session)
        sent = f"1 photo + {len(messages)} messages" if card_png else f"{len(messages)} messages"
        print(f"\nSENT {sent} to the {args.to.upper()} channel; snapshot saved ({n} stocks). No alert-ledger rows written.")
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
