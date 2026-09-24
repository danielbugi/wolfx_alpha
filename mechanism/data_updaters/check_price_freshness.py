#!/usr/bin/env python3
# mechanism/data_updaters/check_price_freshness.py
"""
Fail the pipeline when the daily price update did not actually land the session it was run for.

Why: the pipeline now starts 45 minutes after the US close (23:45 Israel, MARKET_SETTLE_MINUTES=30). An
end-of-day vendor that has not published the day's bar yet returns the OLDER bars without an error, so
daily_data_updater.py "succeeds" with nothing new, the session would be marked processed, and the digest
would then (correctly) refuse to send on stale data - with no retry, since the session is already marked.
Failing here instead leaves the session unmarked, so the backup trigger (01:00) re-runs it.

Rule: rows for --session must cover at least --min-coverage (default 0.90) of the symbols that had a row on
the previous stored date. Exit 0 = fresh, 1 = not fresh (prints why).

    python mechanism/data_updaters/check_price_freshness.py --session 2026-09-23
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared import db  # noqa: E402


def check(session: date, min_coverage: float) -> tuple[bool, str]:
    rows = db.execute_query(
        """
        SELECT
            (SELECT COUNT(*) FROM stock_prices WHERE date = %s),
            (SELECT COUNT(*) FROM stock_prices
              WHERE date = (SELECT MAX(date) FROM stock_prices WHERE date < %s)),
            (SELECT MAX(date) FROM stock_prices WHERE date < %s)
        """,
        (session, session, session),
    )
    have, prev_count, prev_date = rows[0]
    if not prev_count:
        return (have > 0), f"{have} rows for {session}; no earlier date to compare against"
    coverage = have / prev_count
    msg = (f"{have} rows for {session} vs {prev_count} on {prev_date} "
           f"= {coverage:.1%} coverage (need {min_coverage:.0%})")
    return coverage >= min_coverage, msg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--session", required=True, help="US session date the run is for (YYYY-MM-DD)")
    ap.add_argument("--min-coverage", type=float, default=0.90)
    args = ap.parse_args()
    ok, msg = check(date.fromisoformat(args.session), args.min_coverage)
    print(("FRESH: " if ok else "NOT FRESH: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
