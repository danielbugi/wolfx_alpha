#!/usr/bin/env python3
# mechanism/alerts/backfill_snapshots.py
"""
Reconstruct the First Light snapshot (digest_runs / digest_stocks) for PAST sessions from stock_prices, so history exists for the
list scoreboard and the recap posts (CHANNEL_CONTENT_MILESTONES.md M0.2).

    python mechanism/alerts/backfill_snapshots.py --sessions 60          # the last 60 completed sessions (skips those already stored)
    python mechanism/alerts/backfill_snapshots.py --sessions 60 --replace   # overwrite the stored ones too

Honesty rules: a backfilled session is what the digest rules produce from the CURRENT (vendor-adjusted) price series, which can differ
slightly from what was visible on the day (dividend / split restatements). Nothing is sent anywhere; only the two snapshot tables are written.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

import pandas as pd  # noqa: E402

from shared import db  # noqa: E402
from alerts import digest_builder as dbld  # noqa: E402
from alerts.send_daily_digest import CALENDAR_DAYS_BACK, analyse_universe, index_line  # noqa: E402
from alerts.snapshot import save_snapshot  # noqa: E402

MIN_SYMBOLS_PER_SESSION = 1500        # a date with far fewer priced symbols is a partial load, not a session


def pick_sessions(counts: dict, n: int, have: set, replace: bool = False) -> list:
    """counts = {date: symbols priced}. The newest `n` sessions that look complete, oldest first; those in `have` are skipped
    unless replace. (Pure, so it is unit-tested.)"""
    complete = sorted(d for d, c in counts.items() if c >= MIN_SYMBOLS_PER_SESSION)[-n:]
    return [d for d in complete if replace or d not in have]


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill past digest snapshots from stock_prices")
    ap.add_argument("--sessions", type=int, default=60, help="how many of the newest sessions to cover")
    ap.add_argument("--replace", action="store_true", help="overwrite sessions that already have a snapshot")
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    counts = {r["date"]: r["n"] for r in db.execute_dict_query(
        "SELECT date, count(*) AS n FROM stock_prices WHERE date >= CURRENT_DATE - 400 GROUP BY date")}
    have = {r["session_date"] for r in db.execute_dict_query("SELECT session_date FROM digest_runs")}
    todo = pick_sessions(counts, args.sessions, have, args.replace)
    if not todo:
        print("Nothing to do: every requested session already has a snapshot.")
        return 0
    first, last = todo[0], todo[-1]
    print(f"Backfilling {len(todo)} sessions {first} -> {last} (history from {CALENDAR_DAYS_BACK} days before the first)")
    rows = db.execute_dict_query(
        "SELECT symbol, date, open, high, low, close, volume FROM stock_prices WHERE date >= %s::date - %s::int AND date <= %s "
        "ORDER BY symbol, date", (first, CALENDAR_DAYS_BACK, last))
    df = pd.DataFrame(rows)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    print(f"Loaded {len(df):,} rows")
    for k, session in enumerate(todo, 1):
        t0 = time.time()
        part = df[df["date"] <= pd.Timestamp(session)]
        stocks, universe_n, breadth, _skipped = analyse_universe(part, session)
        digest = dbld.build_digest(stocks, top_n=args.top)
        n = save_snapshot(db, session, stocks, digest, universe_n, breadth, index_line(session), args.top)
        print(f"[{k}/{len(todo)}] {session}: {n} stocks, groups {digest['counts']} ({time.time() - t0:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
