#!/usr/bin/env python3
"""
Earnings Calendar Updater

Batch-fetches each symbol's earnings report dates (past actuals + upcoming estimates) from
yfinance and stores them in earnings_calendar -- the same call backend/services/earnings_service.py
already makes on demand for chart markers, just persisted across the whole universe instead of one
symbol at a time in an in-memory cache. Two consumers depend on this table:
  1. quarterly_fundamentals_updater.get_symbols_to_update() -- an event-driven staleness gate
     ("re-check only once we know a report actually happened"), replacing a blind day-count timer.
  2. The channel's daily "who reports today" post (mechanism/alerts) -- see DATA_ML_MILESTONES.md M3.

Deliberately duplicates earnings_service.py's small yfinance-fetch function rather than importing
it: backend/ and mechanism/ are on a path to becoming separate deployable services (see
DATA_ML_MILESTONES.md M6), so a data updater in mechanism/ should not depend on backend/ code.
"""

import sys
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from shared import db, setup_logging, retry_on_failure, ProgressBar
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

EARNINGS_DATES_LIMIT = 12          # matches earnings_service.py's default -- a few years back + upcoming
REFRESH_AFTER_DAYS = 14            # periodic re-check even with a future date on file (dates can shift)


def _safe_float(value: Any) -> Optional[float]:
    """NaN/None/unparsable -> None, everything else -> plain float."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(f) or np.isinf(f)) else f


class EarningsCalendarUpdater:
    """Batch earnings-date fetch + upsert, one row per (symbol, report_date)."""

    def __init__(self):
        self.logger = setup_logging(__name__)
        self.rate_limit_delay = 0.5   # matches fundamentals_updater.py's pace on the same provider
        self.logger.info("Earnings Calendar Updater initialized")

    def fetch_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """Rows for one symbol, or [] on any failure -- never raises (the caller must keep going)."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.get_earnings_dates(limit=EARNINGS_DATES_LIMIT)
            if df is None or df.empty:
                return []
            rows = []
            for idx, row in df.iterrows():
                report_date = idx.date() if hasattr(idx, "date") else idx
                rows.append({
                    'symbol': symbol,
                    'report_date': report_date,
                    'eps_estimate': _safe_float(row.get('EPS Estimate')),
                    'eps_actual': _safe_float(row.get('Reported EPS')),
                    'surprise_pct': _safe_float(row.get('Surprise(%)')),
                })
            return rows
        except Exception as e:
            self.logger.warning(f"Earnings-date fetch failed for {symbol}: {e}")
            return []

    def upsert_row(self, row: Dict[str, Any]) -> bool:
        query = """
            INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, report_date) DO UPDATE SET
                eps_estimate = EXCLUDED.eps_estimate,
                eps_actual = EXCLUDED.eps_actual,
                surprise_pct = EXCLUDED.surprise_pct,
                fetched_at = EXCLUDED.fetched_at
        """
        params = (row['symbol'], row['report_date'], row['eps_estimate'], row['eps_actual'],
                  row['surprise_pct'], datetime.now())
        try:
            db.execute_insert(query, params)
            return True
        except Exception as e:
            self.logger.error(f"Database upsert failed for {row['symbol']} {row['report_date']}: {e}")
            return False

    def update_symbol(self, symbol: str) -> bool:
        rows = self.fetch_symbol(symbol)
        if not rows:
            self.logger.warning(f"No earnings-date data available for {symbol}")
            return False
        success_count = sum(1 for row in rows if self.upsert_row(row))
        self.logger.info(f"✅ {symbol}: upserted {success_count}/{len(rows)} earnings dates")
        return success_count > 0

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_to_update(self, limit: Optional[int] = None) -> List[str]:
        """
        Symbols whose known calendar is missing or has gone stale:
          - no earnings_calendar row at all yet, OR
          - the newest report_date on file is already in the past (we no longer know what's next --
            a report may have happened since and shifted the estimate for the one after it), OR
          - periodic refresh even with a future date already on file (REFRESH_AFTER_DAYS): yfinance's
            forecasted dates get confirmed/moved as the real date approaches, so a date fetched weeks
            ago can no longer be trusted at face value.
        """
        try:
            query = """
            SELECT sp.symbol
            FROM (SELECT DISTINCT symbol FROM stock_prices) sp
            LEFT JOIN (
                SELECT symbol, MAX(report_date) AS last_known_date, MAX(fetched_at) AS last_fetched
                FROM earnings_calendar
                GROUP BY symbol
            ) ec ON ec.symbol = sp.symbol
            WHERE sp.symbol IS NOT NULL
            AND sp.symbol != ''
            AND sp.symbol NOT IN (SELECT symbol FROM inactive_symbols)
            AND (
                ec.last_known_date IS NULL
                OR ec.last_known_date < CURRENT_DATE
                OR ec.last_fetched <= NOW() - INTERVAL '%s days'
            )
            ORDER BY sp.symbol
            """
            params = (REFRESH_AFTER_DAYS,)
            if limit:
                query += " LIMIT %s"
                params = (REFRESH_AFTER_DAYS, limit)

            results = db.execute_dict_query(query, params)
            symbols = [row['symbol'] for row in results]
            self.logger.info(
                f"Found {len(symbols)} symbols due for an earnings-calendar check "
                f"(no data yet, calendar expired, or not re-checked in {REFRESH_AFTER_DAYS} days)"
            )
            return symbols
        except Exception as e:
            self.logger.error(f"Error getting symbols: {e}")
            return []

    def run_update(self, symbols: Optional[List[str]] = None, limit: Optional[int] = None):
        start_time = datetime.now()
        self.logger.info(f"🚀 Starting earnings calendar update at {start_time}")

        if symbols is None:
            symbols = self.get_symbols_to_update(limit)

        if not symbols:
            self.logger.info("No symbols due for an earnings-calendar check")
            return {'success': True, 'message': 'Nothing due', 'symbols_processed': 0,
                    'symbols_successful': 0, 'symbols_failed': 0}

        successful_updates = 0
        failed_updates = 0

        progress = ProgressBar(len(symbols), prefix="Earnings calendar", logger=self.logger)
        for i, symbol in enumerate(symbols, 1):
            try:
                success = self.update_symbol(symbol)
                if success:
                    successful_updates += 1
                    progress.log(f"✅ {symbol}: earnings calendar updated")
                else:
                    failed_updates += 1
                    progress.log(f"⚠️ {symbol}: earnings calendar update failed", level="warning")

                if i < len(symbols):
                    time.sleep(self.rate_limit_delay)

            except Exception as e:
                progress.log(f"Unexpected error processing {symbol}: {e}", level="error")
                failed_updates += 1
                success = False

            progress.update(success)

        progress.close()

        duration = datetime.now() - start_time
        success_rate = (successful_updates / len(symbols) * 100) if symbols else 0
        self.logger.info(f"""
        🎉 Earnings calendar update completed in {duration}:
        ✅ Successful updates: {successful_updates}
        ❌ Failed updates: {failed_updates}
        📊 Total symbols: {len(symbols)}
        📈 Success rate: {success_rate:.1f}%
        """)

        return {
            'success': failed_updates == 0,
            'duration': duration.total_seconds(),
            'symbols_processed': len(symbols),
            'symbols_successful': successful_updates,
            'symbols_failed': failed_updates,
            'success_rate': success_rate,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Earnings Calendar Updater')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--limit', type=int, help='Limit number of symbols (debug)')
    args = parser.parse_args()

    updater = EarningsCalendarUpdater()
    if args.test:
        updater.run_update(symbols=args.test)
    else:
        updater.run_update(limit=args.limit)


if __name__ == "__main__":
    main()
