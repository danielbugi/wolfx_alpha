#!/usr/bin/env python3
"""
Market Index / Macro Data Updater

Fetches daily OHLCV for a small, fixed set of market indices, commodities,
and macro tickers (S&P 500, Nasdaq, Russell 2000, Dow, VIX, 10-Year Treasury
yield, Gold, Crude Oil, the Dollar Index, Bitcoin) into a dedicated
market_index_prices table -- distinct from stock_prices, which only ever
holds individual equity/ETF constituents.

Why this exists: the dashboard's "Market Overview" card and sector heatmap
were entirely derived from the internal ~1,000-3,000 stock universe -- there
was no ingestion anywhere of real index-level market data. Added per the
dashboard data-representation review, 2026-09-19 (see CLAUDE.md).

Always uses yfinance directly, regardless of DATA_PROVIDER. Unlike the main
price/fundamentals updaters, these symbols (^GSPC, ^VIX, ^TNX, GC=F, CL=F,
DX-Y.NYB) are index/futures/FX-index tickers, not tradable equities --
Tiingo's /tiingo/daily/{symbol}/prices endpoint (shared/tiingo_client.py) is
built for stocks and ETFs and has no concept of a bare index or futures
continuous contract, and Alpaca's feed is equities-only too. yfinance's
general fragility (CLAUDE.md §6a) is an acceptable tradeoff for a ~10-symbol
daily fetch, unlike the 3,000-symbol main universe it was replaced for.
"""
import sys
import os
from datetime import datetime, date as date_type
from typing import Optional, List, Tuple

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from shared import config, db, setup_logging, retry_on_failure, ProgressBar
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

# (yahoo symbol, display name, category) -- category is stored nowhere yet,
# kept here as documentation for whoever wires up the frontend card order.
MARKET_INDEX_SYMBOLS: List[Tuple[str, str, str]] = [
    ('^GSPC', 'S&P 500', 'index'),
    ('^IXIC', 'Nasdaq Composite', 'index'),
    ('^RUT', 'Russell 2000', 'index'),
    ('^DJI', 'Dow Jones Industrial Average', 'index'),
    ('^VIX', 'CBOE Volatility Index', 'volatility'),
    ('^TNX', '10-Year Treasury Yield', 'rates'),
    ('GC=F', 'Gold Futures', 'commodity'),
    ('CL=F', 'Crude Oil WTI Futures', 'commodity'),
    ('DX-Y.NYB', 'US Dollar Index', 'currency'),
    ('BTC-USD', 'Bitcoin', 'crypto'),
]


class MarketIndexUpdater:
    """Incremental daily OHLCV updater for the fixed macro/index symbol list."""

    def __init__(self):
        self.logger = setup_logging(__name__)

    @retry_on_failure(max_retries=2, delay=1.0)
    def get_latest_date(self, symbol: str) -> Optional[date_type]:
        rows = db.execute_dict_query(
            "SELECT MAX(date) as max_date FROM market_index_prices WHERE symbol = %s",
            (symbol,)
        )
        return rows[0]['max_date'] if rows and rows[0]['max_date'] else None

    def _period_for(self, latest_date: Optional[date_type]) -> Optional[str]:
        """Same incremental-period bucketing daily_data_updater.py uses --
        None means 'already current, skip the fetch'."""
        if latest_date is None:
            return '1y'
        days_since = (datetime.now().date() - latest_date).days
        if days_since <= 0:
            return None
        if days_since <= 5:
            return '5d'
        if days_since <= 30:
            return '1mo'
        if days_since <= 90:
            return '3mo'
        return '1y'

    def fetch_symbol(self, symbol: str, period: str) -> Optional[pd.DataFrame]:
        try:
            hist = yf.Ticker(symbol).history(period=period)
        except Exception as e:
            self.logger.error(f"{symbol}: fetch failed: {e}")
            return None

        if hist is None or hist.empty:
            return None

        hist = hist.reset_index()
        hist['Date'] = pd.to_datetime(hist['Date']).dt.date
        hist.rename(columns={
            'Date': 'date', 'Open': 'open', 'High': 'high',
            'Low': 'low', 'Close': 'close', 'Volume': 'volume'
        }, inplace=True)
        return hist[['date', 'open', 'high', 'low', 'close', 'volume']]

    def update_symbol(self, symbol: str, display_name: str) -> int:
        latest_date = self.get_latest_date(symbol)
        period = self._period_for(latest_date)
        if period is None:
            self.logger.debug(f"{symbol}: already current (last date {latest_date})")
            return 0

        hist = self.fetch_symbol(symbol, period)
        if hist is None:
            self.logger.warning(f"{symbol} ({display_name}): no data returned from yfinance")
            return 0

        if latest_date:
            hist = hist[hist['date'] > latest_date]
        if hist.empty:
            return 0

        now = datetime.now()
        records = []
        for _, row in hist.iterrows():
            vol = row['volume']
            records.append((
                symbol,
                row['date'],
                float(row['open']) if pd.notna(row['open']) else None,
                float(row['high']) if pd.notna(row['high']) else None,
                float(row['low']) if pd.notna(row['low']) else None,
                float(row['close']) if pd.notna(row['close']) else None,
                # ^VIX/^TNX/DX-Y.NYB report zero/no real volume -- store as
                # NULL rather than a misleading 0.
                int(vol) if pd.notna(vol) and vol else None,
                now,
                now,
            ))

        insert_query = """
            INSERT INTO market_index_prices (
                symbol, date, open, high, low, close, volume, created_at, updated_at
            ) VALUES %s
            ON CONFLICT (symbol, date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                updated_at = EXCLUDED.updated_at
        """
        import psycopg2.extras
        with db.get_sync_connection() as conn:
            with conn.cursor() as cursor:
                psycopg2.extras.execute_values(cursor, insert_query, records, page_size=100)
            conn.commit()

        return len(records)

    def run(self) -> dict:
        start = datetime.now()
        self.logger.info(f"Starting market index update at {start}")

        progress = ProgressBar(len(MARKET_INDEX_SYMBOLS), prefix="Market indices", logger=self.logger)
        total_rows = 0
        failed = 0
        for symbol, display_name, _category in MARKET_INDEX_SYMBOLS:
            try:
                rows_written = self.update_symbol(symbol, display_name)
                total_rows += rows_written
                progress.log(f"✅ {symbol} ({display_name}): {rows_written} row(s) written")
                progress.update(True)
            except Exception as e:
                failed += 1
                progress.log(f"❌ {symbol} ({display_name}): {e}", level="error")
                progress.update(False)
        progress.close()

        duration = datetime.now() - start
        self.logger.info(
            f"Market index update completed in {duration}: {total_rows} row(s) written, "
            f"{failed}/{len(MARKET_INDEX_SYMBOLS)} symbol(s) failed"
        )
        return {
            'success': failed == 0,
            'rows_written': total_rows,
            'symbols_failed': failed,
            'symbols_total': len(MARKET_INDEX_SYMBOLS),
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Market Index / Macro Data Updater')
    parser.add_argument('--config-test', action='store_true', help='Test configuration and database connection')
    args = parser.parse_args()

    setup_logging(level='INFO')

    if args.config_test:
        print("🔧 Testing database connection...")
        health = db.get_health_status()
        if health.get('connected'):
            print(f"✅ Database connection successful ({health.get('database')})")
        else:
            print(f"❌ Database connection failed: {health.get('error')}")
        return

    updater = MarketIndexUpdater()
    result = updater.run()
    if result['success']:
        print("🎉 Market index update completed successfully!")
    else:
        print(f"⚠️ Market index update completed with {result['symbols_failed']} symbol failure(s)")


if __name__ == "__main__":
    main()
