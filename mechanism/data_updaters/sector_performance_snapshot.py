#!/usr/bin/env python3
"""
Sector Performance Snapshot

Persists one row per (date, sector) into sector_performance_daily, so the
dashboard's planned sector Heatmap/Trend toggle has history to draw a line
chart from -- backend/services/market_service.py's get_market_overview()
only ever computed this live, for "today", on every request.

Uses the same latest-price-vs-prev-close aggregate market_service.py already
runs (LATERAL joins against daily_fundamentals, not a raw stock_prices
scan -- see that file's comment on the anti-pattern this avoids), just
writing the result to a table instead of returning it over the API.

Run after the daily fundamentals updater (so sector tags are fresh) and
after the daily price updater (so "today" has a close to compare against
prev_close) -- see automation_pipeline.sh step ordering.
"""
import argparse
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from shared import db, setup_logging
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

# One row per (trading date, sector) for every trading date >= %(start)s.
# Keyed on the price bar's own date, not the wall-clock date the job runs on:
# a Saturday/Sunday run would otherwise stamp the same Friday bars under a
# non-trading date, and the dashboard's Trend chart compounds each day's
# avg_performance, so a duplicated day would be counted twice.
#
# The price window starts 10 days before %(start)s so LAG() has a previous
# close for the first date in range (covers weekends/holidays). Sector is
# each symbol's *latest* tag -- sectors are effectively static, and this is
# the same LATERAL lookup market_service.py uses, not a daily_fundamentals scan.
SECTOR_HISTORY_QUERY = """
    WITH px AS (
        SELECT
            sp.symbol,
            sp.date,
            sp.close,
            LAG(sp.close) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_close
        FROM stock_prices sp
        WHERE sp.date >= %(start)s::date - INTERVAL '10 days'
    ),
    sec AS (
        SELECT s.symbol, df.sector
        FROM (SELECT DISTINCT symbol FROM px) s
        LEFT JOIN LATERAL (
            SELECT sector
            FROM daily_fundamentals
            WHERE symbol = s.symbol
            ORDER BY date DESC
            LIMIT 1
        ) df ON true
    )
    SELECT
        px.date,
        COALESCE(sec.sector, 'Unknown') as sector,
        COUNT(*) as stock_count,
        AVG(((px.close - px.prev_close) / px.prev_close * 100)) as avg_performance
    FROM px
    JOIN sec ON sec.symbol = px.symbol
    WHERE px.date >= %(start)s::date
      AND px.close >= 1.0
      AND px.prev_close > 0
    GROUP BY px.date, COALESCE(sec.sector, 'Unknown')
    ORDER BY px.date, avg_performance DESC
"""

UPSERT_QUERY = """
    INSERT INTO sector_performance_daily (date, sector, stock_count, avg_performance)
    VALUES %s
    ON CONFLICT (date, sector) DO UPDATE SET
        stock_count = EXCLUDED.stock_count,
        avg_performance = EXCLUDED.avg_performance
"""


# The daily pipeline recomputes this many trailing calendar days on every run
# rather than only "today": the upsert is idempotent, so a missed pipeline
# day (or a day whose prices landed late) heals itself on the next run
# instead of leaving a permanent gap in the Trend chart.
DEFAULT_LOOKBACK_DAYS = 7


def run(days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    logger = setup_logging(__name__)
    start = (datetime.now() - timedelta(days=days)).date()

    rows = db.execute_dict_query(SECTOR_HISTORY_QUERY, {'start': start})
    if not rows:
        logger.warning("Sector performance snapshot: no rows computed (empty universe or no fresh prices)")
        return {'success': False, 'sectors_written': 0, 'dates_written': 0}

    records = [
        (row['date'], row['sector'], row['stock_count'], float(row['avg_performance']) if row['avg_performance'] is not None else None)
        for row in rows
    ]
    dates = sorted({r[0] for r in records})

    import psycopg2.extras
    with db.get_sync_connection() as conn:
        with conn.cursor() as cursor:
            psycopg2.extras.execute_values(cursor, UPSERT_QUERY, records, page_size=500)
        conn.commit()

    logger.info(
        f"Sector performance snapshot: {len(records)} row(s) written across "
        f"{len(dates)} trading date(s) ({dates[0]} .. {dates[-1]})"
    )

    return {
        'success': True,
        'sectors_written': len(records),
        'dates_written': len(dates),
        'first_date': str(dates[0]),
        'last_date': str(dates[-1]),
    }


def main():
    parser = argparse.ArgumentParser(description="Persist per-sector daily performance into sector_performance_daily")
    parser.add_argument(
        '--days', type=int, default=DEFAULT_LOOKBACK_DAYS,
        help=f"Trailing calendar days to (re)compute (default {DEFAULT_LOOKBACK_DAYS}). "
             "Use e.g. --days 120 once to backfill history for the Trend chart."
    )
    args = parser.parse_args()

    setup_logging(level='INFO')
    result = run(days=args.days)
    if result['success']:
        print(
            f"🎉 Sector performance snapshot completed: {result['sectors_written']} row(s) "
            f"across {result['dates_written']} trading date(s) "
            f"({result['first_date']} .. {result['last_date']})"
        )
    else:
        print("⚠️ Sector performance snapshot completed with no data written")


if __name__ == "__main__":
    main()
