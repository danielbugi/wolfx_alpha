#!/usr/bin/env python3
# mechanism/alerts/send_daily_digest.py
"""
"First Light" digest -> Telegram (long side only): Breakout and Near breakout x (top gainers, top ATR, top volume).

    python mechanism/alerts/send_daily_digest.py                    # DRY RUN: builds and prints, sends nothing
    python mechanism/alerts/send_daily_digest.py --send             # sends to the DEVELOPMENT channel
    python mechanism/alerts/send_daily_digest.py --send --to prod   # sends to the production channel
    python mechanism/alerts/send_daily_digest.py --snapshot-only    # only refresh the bot's Postgres snapshot
    python mechanism/alerts/send_daily_digest.py --report --image   # DRY RUN of the ONE daily market report (card + report)
    python mechanism/alerts/send_daily_digest.py --report --image --send --to owner   # preview in the owner's private chat

--report (owner decision 2026-10-21) replaces the separate digest / board / top-gainers / health / earnings posts with ONE
report: the market-card photo with a short caption, then a single message carrying every section (market_report.py). Same
analysis, same snapshot for the bot. `--to owner` sends to the owner's private chat only: no snapshot, no session mark.

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
from alerts.digest_format import (PRIVATE_ASSISTANT_BUTTON, SHOW_N, format_caption, format_header, group_messages,  # noqa: E402
                                  header_keyboard, headline)
from alerts import deeplink  # noqa: E402
from alerts import market_report as mr  # noqa: E402
from alerts.market_card import render_market_card  # noqa: E402
from alerts.market_context import build_card_data, load_tiles  # noqa: E402
from alerts.send_daily_alerts import resolve_session  # noqa: E402
from alerts.snapshot import save_snapshot  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError, text_length  # noqa: E402
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
        if v[0]["close"] is None or v[1]["close"] is None:
            continue  # a vendor gap for this bar -- omit the line rather than fabricate/crash
        last, prev = float(v[0]["close"]), float(v[1]["close"])
        parts.append(f"{label} {(last / prev - 1) * 100:+.1f}%" if sym == "^GSPC" else f"{label} {last:.1f}")
    return parts


def sp500_change(session):
    """The S&P 500's change on the session in %, or None when the tile has no honest value (missing / stale bar)."""
    t = load_tiles(db, session)[0]
    if t.value == "n/a" or not t.delta:
        return None
    return t.direction * float(t.delta.rstrip("%"))


def main():
    ap = argparse.ArgumentParser(description="First Light digest -> Telegram (dry-run by default)")
    ap.add_argument("--send", action="store_true", help="actually send (dev channel unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod", "owner"], default="dev",
                    help="owner = the owner's private chat with the bot (preview; no snapshot, no session mark)")
    ap.add_argument("--top", type=int, default=15, help="rows per list (the depth of the lists; also what the bot treats as 'in the lists')")
    ap.add_argument("--show", type=int, default=SHOW_N, help="rows of each list shown open; ranks below sit in a collapsed quote")
    ap.add_argument("--min-dv", type=float, default=dbld.DEFAULT_MIN_DV, help="min 20-day avg dollar volume for the ranked lists")
    ap.add_argument("--date", help="session date YYYY-MM-DD (default: newest in stock_prices)")
    ap.add_argument("--force", action="store_true", help="ignore ALERTS_SKIP_WEEKDAYS")
    ap.add_argument("--buttons", action="store_true",
                    help="inline buttons under the header: three educational popups + one neutral 'Private assistant "
                         "(invite only)' link (the popups only answer while run_bot.py is running)")
    ap.add_argument("--snapshot-only", action="store_true", help="write the bot snapshot to Postgres, send nothing")
    ap.add_argument("--image", action="store_true",
                    help="render the market-performance card (saved under reports/first_light/) and send it first")
    ap.add_argument("--report", action="store_true",
                    help="the ONE daily market report (card + one message) instead of the separate digest messages")
    args = ap.parse_args()
    if args.to == "owner" and args.snapshot_only:
        ap.error("--snapshot-only has nothing to send")

    tz = ZoneInfo(os.getenv("ALERTS_TIMEZONE", "Asia/Jerusalem"))
    now_local = datetime.now(tz)
    skip = {d.strip().lower()[:3] for d in os.getenv("ALERTS_SKIP_WEEKDAYS", "").split(",") if d.strip()}
    if args.send and not args.force and now_local.strftime("%a").lower() in skip:
        print(f"Skipping: {now_local:%A} is in ALERTS_SKIP_WEEKDAYS")
        return 0

    # Trading-day gate: only post when a US session completed that this channel has not been sent yet (weekends, NYSE
    # holidays, a second run the same day). --force / an explicit --date bypass it; --snapshot-only is never blocked.
    state_key = f"digest:{args.to}"
    gate = market_calendar.check_new_session(state_key, force=args.force or bool(args.date) or args.to == "owner")
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

    if args.report:
        return send_report(args, session, rows, universe_n, breadth, digest, index_lines, state_key, skipped)

    hook = headline(sp500_change(session), breadth["up"], breadth["down"], universe_n)
    card_png = None
    if args.image:
        try:                                                        # the image is decoration: never let it stop the digest
            card = build_card_data(db, session, rows, universe_n, breadth)
            card.headline = hook
            card_png = render_market_card(card)
            CARD_DIR.mkdir(parents=True, exist_ok=True)
            path = CARD_DIR / f"first_light_{session}.png"
            path.write_bytes(card_png)
            print(f"Market card saved: {path} ({len(card_png) / 1024:.0f} KB)")
        except Exception as ex:                                     # noqa: BLE001
            print(f"WARNING: market card not rendered ({type(ex).__name__}: {ex}); sending the text-only digest.")
    # With the market card the photo's caption IS the opening (hook, counts, the stars) and carries the buttons, so there is no separate header
    # message; without a card the text header opens the digest.
    caption = format_caption(session, digest, hook, notes) if card_png else None
    messages = [] if card_png else [format_header(session, now_local, digest, universe_n, breadth, index_lines, notes, with_market=True, hook=hook)]
    for cat in dbld.LONG_CATEGORIES:
        messages += group_messages(cat, digest, args.top, args.show)

    bar = "=" * 78
    if caption:
        print(f"\n{bar}\n[photo caption + buttons]\n{caption}\n{bar}   [{len(caption)} chars]")
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
        # only the opening (the photo, or the text header) carries buttons; the group messages stay plain (no per-ticker / strategy buttons in a public channel)
        keyboards = [None if card_png else keyboard] + [None for _ in messages[1:]]
        if card_png:                                                # the photo goes first and is the one that notifies
            tg.send_photo(card_png, caption, reply_markup=keyboard, kind="digest_card")
        for k, m in enumerate(messages):
            tg.send_message(m, silent=k > 0 or bool(card_png), reply_markup=keyboards[k],   # one notification only
                            kind="digest_header" if k == 0 and not card_png else "digest_list")
        n = save_snapshot(db, session, rows, digest, universe_n, breadth, index_lines, args.top)
        market_calendar.mark_processed(state_key, session)
        sent = f"1 photo + {len(messages)} messages" if card_png else f"{len(messages)} messages"
        print(f"\nSENT {sent} to the {args.to.upper()} channel; snapshot saved ({n} stocks). No alert-ledger rows written.")
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


def next_session_after(session):
    """The next US session on the calendar after `session` (whose reporters the report lists), or None."""
    sessions, _source = market_calendar.get_sessions()
    later = sorted(d for d in sessions if d > pd.Timestamp(session).date())
    return later[0] if later else None


# The movers rows (owner decision 2026-10-22): the day's breakout stocks, as the Momentum Board's lists rank them -- top 5 by
# % gain and top 5 by volume surge (the digest's breakout "gainers" and "volume" lists, the same ones the board and the bot
# read). Names and numbers only in the post.
MOVERS_N = 5
MACRO_SYMBOLS = {"GC=F": "gold", "CL=F": "oil", "DX-Y.NYB": "dollar", "BTC-USD": "btc"}
INDEX_SYMBOLS = ("^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^TNX")


def market_moves(session):
    """{symbol: (last close, previous close)} for the index / macro symbols, using only the bar ON the session date and the one
    before it; a symbol without a bar on the session date is left out (never filled from another day)."""
    syms = list(INDEX_SYMBOLS) + list(MACRO_SYMBOLS)
    rows = db.execute_dict_query(
        "SELECT symbol, date, close FROM market_index_prices WHERE symbol = ANY(%s) AND date <= %s AND date > %s::date - 10 "
        "ORDER BY symbol, date DESC", (syms, session, session))
    out, by = {}, {}
    for r in rows:
        by.setdefault(r["symbol"], []).append(r)
    for sym, v in by.items():
        if len(v) >= 2 and v[0]["date"] == pd.Timestamp(session).date() and v[0]["close"] is not None and v[1]["close"] is not None:
            out[sym] = (float(v[0]["close"]), float(v[1]["close"]))
    return out


def build_brief(session, rows, universe_n, breadth, digest):
    """The public market brief's numbers: market-wide facts only (see market_report.py). Movers come from the stocks analysed here,
    restricted to large liquid companies; sectors are the same equal-weighted averages the card draws."""
    from alerts.market_context import load_sectors
    from alerts.send_earnings_today import load_todays_reporters

    mv = market_moves(session)
    pct = lambda sym: (mv[sym][0] / mv[sym][1] - 1) * 100 if sym in mv and mv[sym][1] else None   # noqa: E731
    vix = (mv["^VIX"][0], pct("^VIX")) if "^VIX" in mv else None
    tnx = (mv["^TNX"][0], (mv["^TNX"][0] - mv["^TNX"][1]) * 100) if "^TNX" in mv else None
    bars, _unclassified = load_sectors(db, rows)
    breakouts = digest["boards"]["breakout"]
    gainers = [r for r in breakouts["gainers"][:MOVERS_N] if r["ret1_pct"] > 0]
    volume = [r for r in breakouts["volume"][:MOVERS_N] if r.get("rvol") is not None]
    day = next_session_after(session)
    caps = {r["symbol"]: r.get("mcap") for r in rows}
    reporters = load_todays_reporters(day) if day else []
    reporters.sort(key=lambda r: (-(caps.get(r["symbol"]) or 0.0), r["symbol"]))
    total = max(universe_n, breadth["up"] + breadth["down"], 1)
    return mr.Brief(
        session=pd.Timestamp(session).date(),
        index_pct={s: pct(s) for s in ("^GSPC", "^IXIC", "^DJI", "^RUT")}, vix=vix, tnx=tnx,
        macro_pct={name: pct(sym) for sym, name in MACRO_SYMBOLS.items()},
        up_share=breadth["up"] / total * 100 if universe_n else None,
        sectors=[(b.name, b.change_pct) for b in bars],
        gainers=[(r["symbol"], r["ret1_pct"]) for r in gainers],
        volume=[(r["symbol"], r["ret1_pct"], r["rvol"]) for r in volume],
        reporting_day=day, reporting=[r["symbol"] for r in reporters[:mr.REPORTING_N]])


def send_report(args, session, rows, universe_n, breadth, digest, index_lines, state_key, skipped) -> int:
    brief = build_brief(session, rows, universe_n, breadth, digest)
    text = mr.format_report(brief)
    card_png = None
    if args.image:
        try:                                                        # the image is decoration: never let it stop the post
            card = build_card_data(db, session, rows, universe_n, breadth)
            card.headline = mr.card_headline(brief)
            card.public = True
            card_png = render_market_card(card)
            CARD_DIR.mkdir(parents=True, exist_ok=True)
            path = CARD_DIR / f"market_daily_{session}.png"
            path.write_bytes(card_png)
            print(f"Market card saved: {path} ({len(card_png) / 1024:.0f} KB)")
        except Exception as ex:                                     # noqa: BLE001
            print(f"WARNING: market card not rendered ({type(ex).__name__}: {ex}); sending the text only.")
    caption = mr.caption(brief) if card_png else None
    bar = "=" * 78
    if caption:
        print(f"\n{bar}\n[photo caption]\n{caption}\n{bar}   [{text_length(caption)} parsed chars]")
    print(f"\n{bar}\n[market brief + Request access button]\n{text}\n{bar}   [{text_length(text)} parsed chars]")
    if skipped:
        print("\nSkipped: " + "; ".join(f"{v} x {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])))
    if text_length(text) > 4096 or (caption and text_length(caption) > 1024):
        raise SystemExit("ABORT: the brief exceeds Telegram's limits; nothing sent.")
    if not args.send:
        print("\nDRY RUN - nothing was sent.")
        return 0
    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        username = os.getenv("TELEGRAM_BOT_USERNAME") or tg.get_me()["username"]
        keyboard = {"inline_keyboard": [[{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink.link(username, "ch")}]]}
        if card_png:                                                # the photo notifies; the brief follows silently under it
            tg.send_photo(card_png, caption, kind="report_card")
        tg.send_message(text, disable_preview=True, silent=bool(card_png), reply_markup=keyboard, kind="market_report")
        if args.to == "owner":
            print("\nSENT the preview to the owner's private chat (no snapshot, no session mark).")
            return 0
        n = save_snapshot(db, session, rows, digest, universe_n, breadth, index_lines, args.top)
        market_calendar.mark_processed(state_key, session)
        print(f"\nSENT the market brief ({'photo + ' if card_png else ''}1 message) to the {args.to.upper()} channel; snapshot saved ({n} stocks).")
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
