#!/usr/bin/env python3
# mechanism/data_updaters/repopulate_from_tiingo.py
"""
One-time full-history repopulation of stock_prices from Tiingo.

Why this exists: stock_prices' existing ~3-year history (since 2023-07-11)
was fetched across three different providers over time as the pipeline
migrated -- yfinance, then Alpaca (2026-09-14), now Tiingo (2026-09-17).
Different providers' split/dividend-adjustment math can disagree slightly,
which risks small discontinuities in the adjusted-close series right at
each provider-transition date -- exactly the kind of thing that quietly
distorts rolling indicators (Donchian channels, SMAs, RSI) computed across
that boundary. This script re-fetches each symbol's full history from a
single provider (Tiingo) to eliminate that, and extends the window to 10
years per the user's explicit choice (2026-09-18 conversation) -- both a
consistency fix and a meaningful upgrade for ML training data (more
distinct market regimes to learn breakout patterns from, not just the last
3 years).

Reuses EnhancedDailyDataUpdater's existing, already-tested upsert logic
(bulk_update_stock_prices, bulk_update_technical_indicators) rather than
duplicating it -- this script only supplies a different (longer, Tiingo-
sourced) DataFrame per symbol than that class's own incremental path does.
bulk_update_technical_indicators is self-healing against gaps (compares
against the actual existing date set, not just MAX(date) -- see its
docstring), so calling it after the price backfill naturally computes
indicators for the newly-extended older date range too.

Usage:
    python repopulate_from_tiingo.py --test AAPL PLTR   # a few symbols
    python repopulate_from_tiingo.py                    # full universe
    python repopulate_from_tiingo.py --years 5           # shorter window
"""
import sys
import os
import time
import argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared import config, db, setup_logging

if config.data_provider != 'tiingo':
    print(f"ERROR: DATA_PROVIDER is '{config.data_provider}', not 'tiingo'. "
          f"This script is specifically a Tiingo repopulation -- set "
          f"DATA_PROVIDER=tiingo in .env first.")
    sys.exit(1)

from shared.tiingo_client import get_daily_bars
from data_updaters.daily_data_updater import EnhancedDailyDataUpdater

RATE_LIMIT_DELAY = 0.4  # Tiingo Power plan: 10,000 req/hour (~166/min) -- 0.4s keeps well under that


def get_symbols_to_repopulate(limit=None):
    query = """
        SELECT DISTINCT symbol
        FROM stock_prices
        WHERE symbol IS NOT NULL AND symbol != ''
        ORDER BY symbol
    """
    if limit:
        query += f" LIMIT {limit}"
    return [row['symbol'] for row in db.execute_dict_query(query)]


def get_new_symbols_from_file(path, only_new=True):
    """
    Load a symbol list from one of symbol_scraper.py's stock_lists CSV/TXT/
    JSON outputs, optionally filtered to symbols NOT already in stock_prices
    -- used for onboarding the Russell 3000 universe expansion (2026-09-18)
    without re-processing the ~1,000 symbols the other repopulation run is
    already handling. get_symbols_to_update() in daily_data_updater.py (and
    every other updater) sources its universe from `DISTINCT symbol FROM
    stock_prices`, not from stock_lists/ directly -- so a symbol only
    "joins the pipeline" once it has at least one stock_prices row. That's
    exactly what this script's repopulate_symbol() call gives it.
    """
    import pandas as pd
    if path.endswith('.csv'):
        symbols = pd.read_csv(path)['symbol'].tolist()
    elif path.endswith('.txt'):
        with open(path) as f:
            symbols = [line.strip() for line in f if line.strip()]
    elif path.endswith('.json'):
        import json
        with open(path) as f:
            symbols = json.load(f)['symbols']
    else:
        raise ValueError(f"Unsupported file format: {path}")

    symbols = sorted(set(symbols))

    if only_new:
        existing = {row['symbol'] for row in db.execute_dict_query(
            "SELECT DISTINCT symbol FROM stock_prices"
        )}
        symbols = [s for s in symbols if s not in existing]

    return symbols


def repopulate_symbol(updater: EnhancedDailyDataUpdater, symbol: str, start_date: date) -> bool:
    try:
        price_data = get_daily_bars(symbol, start_date=start_date)
        if price_data is None or price_data.empty:
            updater.logger.warning(f"{symbol}: no data from Tiingo, skipping")
            return False

        price_records = updater.bulk_update_stock_prices(symbol, price_data)

        full_data = updater.get_full_price_data_for_indicators(symbol)
        indicator_records = 0
        if full_data is not None and len(full_data) >= 50:
            indicator_records = updater.bulk_update_technical_indicators(symbol, full_data)

        updater.logger.info(f"✅ {symbol}: {price_records} prices repopulated, {indicator_records} new indicator rows")
        return True

    except Exception as e:
        updater.logger.error(f"❌ {symbol}: repopulation failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Repopulate stock_prices from Tiingo (single-provider consistency + extended history)')
    parser.add_argument('--test', nargs='+', help='Repopulate only these specific symbols')
    parser.add_argument('--limit', type=int, help='Limit number of symbols (for a partial run)')
    parser.add_argument('--years', type=int, default=10, help='Years of history to pull (default: 10)')
    parser.add_argument('--from-file', help='Load symbols from a stock_lists CSV/TXT/JSON file instead of the DB')
    parser.add_argument('--only-new', action='store_true', help='With --from-file: only symbols not already in stock_prices')
    args = parser.parse_args()

    setup_logging(level='INFO')
    updater = EnhancedDailyDataUpdater()

    start_date = date.today() - timedelta(days=365 * args.years)
    updater.logger.info(f"Repopulating from Tiingo, start_date={start_date} ({args.years} years)")

    if args.test:
        symbols = args.test
    elif args.from_file:
        symbols = get_new_symbols_from_file(args.from_file, only_new=args.only_new)
    else:
        symbols = get_symbols_to_repopulate(args.limit)
    updater.logger.info(f"Found {len(symbols)} symbols to repopulate")

    start_time = time.time()
    successful = 0
    failed = 0

    for i, symbol in enumerate(symbols, 1):
        updater.logger.info(f"Processing {symbol} ({i}/{len(symbols)})")
        if repopulate_symbol(updater, symbol, start_date):
            successful += 1
        else:
            failed += 1

        if i < len(symbols):
            time.sleep(RATE_LIMIT_DELAY)

    duration = time.time() - start_time
    updater.logger.info(f"""
    🎉 Tiingo repopulation completed in {duration / 60:.1f} minutes:
    ✅ Successful: {successful}
    ❌ Failed: {failed}
    📊 Total: {len(symbols)}
    """)


if __name__ == "__main__":
    main()
