#!/usr/bin/env python3
# mechanism/alerts/send_channel_posts.py
"""
The channel's ONE extra post per session (CHANNEL_CONTENT_REPORT_2026-09-21.md section 3), sent after the daily digest.

    python mechanism/alerts/send_channel_posts.py                    # DRY RUN: builds today's post, prints it, sends nothing
    python mechanism/alerts/send_channel_posts.py --all              # dry run of EVERY kind (saves the images under reports/first_light/posts/)
    python mechanism/alerts/send_channel_posts.py --kind health      # a specific kind
    python mechanism/alerts/send_channel_posts.py --send             # to the DEV channel
    python mechanism/alerts/send_channel_posts.py --send --to prod   # production: LOCKED until PROD_SENDING_ENABLED=1
    python mechanism/alerts/send_channel_posts.py --all --send --to owner --force   # review every kind in YOUR private chat with the bot

Rhythm: at most one extra post per session (Mon sector rotation / macro, Tue gaps / news, Wed market health, Thu near highs / aligned timeframes,
Fri education or promotion; first Thursday of the month = base rates), and a weekly recap on Sunday. The news post is off until
CHANNEL_NEWS_ENABLED=1 (its data licence is not checked); the monthly list scoreboard (first Friday) is off until CHANNEL_SCOREBOARD_ENABLED=1
(its numbers may be unflattering - publishing them is the owner's decision). Same safety rules as the digest: dry run by default, trading-day gate, stale data
aborts, production lock, the token is never printed. Extra posts are silent (only the digest notifies). Nothing here touches the assistant.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

import pandas as pd  # noqa: E402

from shared import db, market_calendar  # noqa: E402
from alerts import channel_content as cx  # noqa: E402
from alerts import deeplink  # noqa: E402
from alerts import board as bd  # noqa: E402
from alerts import channel_news  # noqa: E402
from alerts import digest_builder as dbld  # noqa: E402
from alerts import market_context as mc  # noqa: E402
from alerts import market_stats as ms  # noqa: E402
from alerts import scoreboard  # noqa: E402
from alerts.digest_format import PRIVATE_ASSISTANT_BUTTON  # noqa: E402
from alerts.send_daily_alerts import resolve_session  # noqa: E402
from alerts.send_daily_digest import analyse_universe, load_universe_history  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError, text_length  # noqa: E402

PREVIEW_DIR = MECH.parent / "reports" / "first_light" / "posts"
RECAP_SESSIONS = 5


# ------------------------------------------------------------------ loading the facts
def load_base_rates() -> dict:
    """Long 20-day-high breakouts with a matured 20-session outcome, all years (ml_breakout_dataset_v2)."""
    rows = db.execute_dict_query(
        "SELECT EXTRACT(year FROM date)::int AS yr, count(*) AS n, avg(stopped) AS stopped, avg(tp3_hit) AS tp3 "
        "FROM ml_breakout_dataset_v2 WHERE direction = 1 AND plan_r IS NOT NULL AND stopped IS NOT NULL GROUP BY 1 ORDER BY 1")
    if not rows:
        return {}
    n = sum(int(r["n"]) for r in rows)
    w = lambda k: sum(float(r[k]) * int(r["n"]) for r in rows) / n           # noqa: E731  (case-weighted mean)
    return {"n": n, "stopped": w("stopped"), "tp3": w("tp3"), "first_year": rows[0]["yr"],
            "by_year": [{"year": r["yr"], "n": int(r["n"]), "stopped": float(r["stopped"])} for r in rows]}


def load_recap(session, w: ms.Wide, sector_of: dict) -> dict:
    """The last five stored digest snapshots up to `session` (exact digest counts) + names that stayed in the breakout group."""
    runs = db.execute_dict_query(
        "SELECT session_date, counts, up_n, down_n FROM digest_runs WHERE session_date <= %s AND session_date > %s::date - 9 "
        "ORDER BY session_date DESC LIMIT %s", (session, session, RECAP_SESSIONS))
    runs = sorted(runs, key=lambda r: r["session_date"])
    days = [{"date": r["session_date"], "breakout": int(r["counts"].get("breakout", 0)), "near": int(r["counts"].get("near_breakout", 0)),
             "up_pct": (r["up_n"] / (r["up_n"] + r["down_n"]) * 100) if (r["up_n"] + r["down_n"]) else None} for r in runs]
    persist_min = 3
    persistent = []
    if len(days) >= persist_min:
        persistent = [r["symbol"] for r in db.execute_dict_query(
            "SELECT symbol FROM digest_stocks WHERE session_date = ANY(%s) AND category = 'breakout' GROUP BY symbol "
            "HAVING count(*) >= %s ORDER BY count(*) DESC, symbol", ([d["date"] for d in days], persist_min))]
    bars, _ = ms.sector_changes(w, sector_of, RECAP_SESSIONS)
    return {"days": days, "persistent": persistent, "persist_min": persist_min, "sectors": bars, "sector_sessions": RECAP_SESSIONS}


def sp500_change(session):
    t = mc.load_tiles(db, session)[0]
    if t.value == "n/a" or not t.delta:
        return None
    return t.direction * float(t.delta.rstrip("%"))


def load_scoreboard(w: ms.Wide, session) -> dict:
    """Stored snapshots (the lists' stocks per session + every liquid stock per session) measured against the prices already loaded."""
    where = "session_date <= %s AND session_date > %s::date - 120"
    listed = db.execute_dict_query(
        f"SELECT session_date, symbol, category FROM digest_stocks WHERE list_ranks IS NOT NULL AND category IN ('breakout', 'near_breakout') AND {where}",
        (session, session))
    universe: dict = {}
    for r in db.execute_dict_query(f"SELECT session_date, symbol FROM digest_stocks WHERE {where}", (session, session)):
        universe.setdefault(r["session_date"], []).append(r["symbol"])
    return scoreboard.compute(pd.DataFrame(listed, columns=["session_date", "symbol", "category"]), universe, w.close)


def load_board(session, w: ms.Wide):
    """The momentum board: stocks that were in the channel's lists in the previous BOARD_SESSIONS stored sessions, measured on `session`."""
    listed = db.execute_dict_query(
        "SELECT session_date, symbol, category FROM digest_stocks WHERE list_ranks IS NOT NULL AND category IN ('breakout', 'near_breakout') "
        "AND session_date IN (SELECT session_date FROM digest_runs WHERE session_date < %s ORDER BY session_date DESC LIMIT %s)",
        (session, bd.BOARD_SESSIONS))
    return bd.compute(pd.DataFrame(listed, columns=["session_date", "symbol", "category"]), w, session)


def pick_movers(rows, digest, limit: int = 8) -> list:
    """Stocks worth a headline check: the multi-list (star) stocks first, then each group's top gainers. [{symbol, ret1_pct, group}]"""
    by_sym = {r["symbol"]: r for r in rows}
    label = {"breakout": "breakout", "near_breakout": "near breakout"}
    out, seen = [], set()
    for stage in ("multi", "gainers"):
        for cat in dbld.LONG_CATEGORIES:
            board = digest["boards"][cat]
            syms = list(board["multi"]) if stage == "multi" else [r["symbol"] for r in board["gainers"][:3]]
            for s in syms:
                if s not in seen and s in by_sym and len(out) < limit:
                    seen.add(s)
                    out.append({"symbol": s, "ret1_pct": by_sym[s]["ret1_pct"], "group": label[cat]})
    return out


def load_context(session, top_n: int = 5, with_news: bool = False, with_scoreboard: bool = False) -> cx.Ctx:
    t0 = time.time()
    df = load_universe_history(session)
    rows, universe_n, breadth, _skipped = analyse_universe(df, session)
    digest = dbld.build_digest(rows, top_n=top_n)
    w = ms.to_wide(df, [r["symbol"] for r in rows])
    sector_of = {r["symbol"]: r["sector"] for r in db.execute_dict_query(
        "SELECT DISTINCT ON (symbol) symbol, sector FROM daily_fundamentals WHERE sector IS NOT NULL ORDER BY symbol, date DESC")}
    bars20, unclassified = ms.sector_changes(w, sector_of, 20)
    ts = pd.Timestamp(session)
    news = None
    if with_news:
        movers = pick_movers(rows, digest)
        got = channel_news.collect([m["symbol"] for m in movers])
        news = {"movers": movers, **got}
        print(f"News: {len(got['items'])} of {len(movers)} movers have fresh headlines ({got['failed']} fetches failed, "
              f"{got['dropped_advice']} advice-like headlines dropped)")
    print(f"Facts ready in {time.time() - t0:.0f}s | {universe_n:,} liquid stocks | groups {digest['counts']}")
    return cx.Ctx(
        session=ts.date(), universe_n=universe_n, up_n=breadth["up"], down_n=breadth["down"], counts=digest["counts"],
        health=ms.health(w), sp500_pct=sp500_change(session), table=ms.last_session_table(w),
        breakout_symbols=[r["symbol"] for r in rows if r["cat"] == "breakout"], sector_bars=bars20, sector_unclassified=unclassified,
        macro_tiles=mc.load_macro_tiles(db, session), base=load_base_rates(), recap=load_recap(session, w, sector_of),
        news=news, scoreboard=load_scoreboard(w, session) if with_scoreboard else None, board=load_board(session, w),
        week_number=int(ts.isocalendar().week))


# ------------------------------------------------------------------ sending
def button(username: str) -> dict:
    return {"inline_keyboard": [[{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink.link(username, "ch")}]]}


def send_post(tg: TelegramClient, post: cx.Post, username) -> None:
    kb = button(username) if post.button and username else None
    if post.image:
        if text_length(post.text) > cx.CAPTION_LIMIT:
            raise SystemExit(f"ABORT: the '{post.kind}' caption is {text_length(post.text)} chars (limit {cx.CAPTION_LIMIT}); nothing sent.")
        tg.send_photo(post.image, post.text, silent=True, reply_markup=kb, kind=post.kind)
    else:
        tg.send_message(post.text, disable_preview=post.disable_preview, silent=True, reply_markup=kb, kind=post.kind)


def save_preview(post: cx.Post, session) -> None:
    if post.image:
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        path = PREVIEW_DIR / f"{post.kind}_{session}.png"
        path.write_bytes(post.image)
        print(f"  image saved: {path} ({len(post.image) / 1024:.0f} KB)")


def show(post: cx.Post) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n[{post.kind}]{' + image' if post.image else ''}{' + assistant button' if post.button else ''}\n{post.text}\n{bar}   [{len(post.text)} chars]")


def main() -> int:
    ap = argparse.ArgumentParser(description="The channel's extra post of the session (dry-run by default)")
    ap.add_argument("--send", action="store_true", help="really send (dev unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod", "owner"], default="dev", help="owner = your private chat with the bot (for review)")
    ap.add_argument("--kind", choices=["auto", *cx.KINDS], default="auto")
    ap.add_argument("--all", action="store_true", help="build every kind (review); with --send only to --to owner")
    ap.add_argument("--date", help="session date YYYY-MM-DD (default: newest in stock_prices)")
    ap.add_argument("--force", action="store_true", help="ignore the trading-day gate")
    args = ap.parse_args()
    if args.all and args.send and args.to != "owner":
        ap.error("--all --send is only allowed with --to owner (the channel gets one extra post per session)")

    tz = ZoneInfo(os.getenv("ALERTS_TIMEZONE", "Asia/Jerusalem"))
    now_local = datetime.now(tz)
    news_enabled = os.getenv("CHANNEL_NEWS_ENABLED", "").strip() == "1"
    session, cover_n, _notes = resolve_session(args.date, tz)
    if args.all or args.kind != "auto":
        kinds = list(cx.KINDS) if args.all else [args.kind]
    elif now_local.weekday() == 6:                                # Sunday: the weekly recap
        kinds = ["recap"]
    else:
        kinds = cx.pick_kinds(pd.Timestamp(session).date(), news_enabled, os.getenv("CHANNEL_SCOREBOARD_ENABLED", "").strip() == "1")
    state_key = f"{'recap' if kinds == ['recap'] else 'posts'}:{args.to}"

    gate = market_calendar.check_new_session(state_key, force=args.force or bool(args.date) or args.all or args.kind != "auto")
    if not gate.run:
        if args.send:
            print(f"Skipping: {gate.reason}")
            return 0
        print(f"NOTE (dry run): a real --send would be skipped - {gate.reason}")
    if not args.date:
        market_calendar.require_data_current(session, gate)
    print(f"Session {session} | {cover_n} symbols priced | trying kinds: {', '.join(kinds)}")
    ctx = load_context(session, with_news='news' in kinds, with_scoreboard='scoreboard' in kinds)

    built = []
    auto = not (args.all or args.kind != "auto")
    if auto and kinds != ["recap"] and os.getenv("CHANNEL_BOARD_ENABLED", "1").strip() != "0":
        post = cx.build_post("board", ctx)                       # the daily momentum board comes first; the rotating post follows it
        if post:
            built.append(post)
        else:
            print("  (board: nothing honest to post today - no listed stock closed higher, or no stored lists yet)")
    for kind in kinds:
        post = cx.build_post(kind, ctx)
        if post:
            built.append(post)
            if not args.all:
                break
        elif not args.all:
            print(f"  ({kind}: nothing honest to post today, trying the next kind)")
    if not built:
        print("Nothing to post for this session.")
        return 0
    for p in built:
        show(p)
        save_preview(p, session)
    if not args.send:
        print("\nDRY RUN - nothing was sent. Use --send (dev) or --send --to prod (locked until launch).")
        return 0
    try:
        tg = TelegramClient.from_env(args.to, dry_run=False)
        username = os.getenv("TELEGRAM_BOT_USERNAME") or tg.get_me()["username"]
        for p in built:
            send_post(tg, p, username)
        if args.to != "owner":
            market_calendar.mark_processed(state_key, pd.Timestamp(session).date())
        print(f"\nSENT {len(built)} post(s) [{', '.join(p.kind for p in built)}] to {args.to.upper()}.")
    except TelegramError as e:
        raise SystemExit(f"ABORT: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
