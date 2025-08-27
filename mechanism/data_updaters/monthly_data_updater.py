# -*- coding: utf-8 -*-
# mechanism/data_updaters/monthly_data_updater.py
"""
Monthly data updater for multi-timeframe analysis
Updates monthly technical indicators following weekly_data_updater.py patterns
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings

# Import shared infrastructure
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from shared import (
    config, db, setup_logging, retry_on_failure,
    timing_decorator, date_utils, data_validation,
    format_number, safe_divide
)

warnings.filterwarnings('ignore')

logger = setup_logging("monthly_data_updater")


class MonthlyDataUpdater:
    """Monthly data updater using shared infrastructure"""

    def __init__(self):
        self.batch_size = config.data_batch_size
        self.max_concurrent = config.max_concurrent_updates

        logger.info("Monthly Data Updater initialized with shared infrastructure")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_to_update(self, limit: Optional[int] = None) -> List[str]:
        """Get symbols for monthly update"""
        try:
            query = """
            SELECT DISTINCT symbol 
            FROM daily_fundamentals 
            WHERE symbol IS NOT NULL 
            ORDER BY symbol
            """

            if limit:
                query += f" LIMIT {limit}"

            results = db.execute_query(query)
            symbols = [row[0] for row in results]

            logger.info(f"Found {len(symbols)} symbols for monthly update")
            return symbols

        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return []

    def get_daily_data_for_monthly_calc(self, symbol: str, start_date: datetime) -> pd.DataFrame:
        """Get daily data for monthly calculations"""
        try:
            query = """
            SELECT sp.date, sp.open, sp.high, sp.low, sp.close, sp.volume
            FROM stock_prices sp
            WHERE sp.symbol = %s AND sp.date >= %s
            ORDER BY sp.date ASC
            """

            results = db.execute_query(query, (symbol, start_date))

            if not results:
                return pd.DataFrame()

            df = pd.DataFrame(results, columns=[
                'date', 'open', 'high', 'low', 'close', 'volume'
            ])

            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)

            return df

        except Exception as e:
            logger.error(f"Error getting daily data for {symbol}: {e}")
            return pd.DataFrame()

    def calculate_monthly_indicators(self, symbol: str, daily_data: pd.DataFrame) -> List[Dict]:
        """Calculate monthly indicators - FIXED decimal/float type issue"""
        try:
            if daily_data.empty or len(daily_data) < 252:  # Need ~1 year of data
                return []

            # Convert all numeric columns to float to avoid Decimal/float mixing
            for col in ['open', 'high', 'low', 'close', 'volume']:
                daily_data[col] = pd.to_numeric(daily_data[col], errors='coerce').astype(float)

            # Resample to monthly (end of month)
            monthly = daily_data.resample('M').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()

            if len(monthly) < 12:  # Need 12 months for calculations
                return []

            monthly_indicators = []

            for i in range(11, len(monthly)):  # Start from 12th month
                month_end = monthly.index[i]
                month_data = monthly.iloc[max(0, i - 11):i + 1]  # 12 months

                # Monthly OHLCV - ensure all are float
                monthly_open = float(monthly.iloc[i]['open'])
                monthly_high = float(monthly.iloc[i]['high'])
                monthly_low = float(monthly.iloc[i]['low'])
                monthly_close = float(monthly.iloc[i]['close'])
                monthly_volume = int(float(monthly.iloc[i]['volume']))  # Convert via float first

                # Monthly Donchian (12-month) - ensure float
                donchian_high = float(month_data['high'].max())
                donchian_low = float(month_data['low'].min())
                donchian_mid = float((donchian_high + donchian_low) / 2.0)

                # Monthly trend analysis - all float operations
                if i >= 5:  # Need 6 months for trend
                    six_month_data = monthly['close'].iloc[i - 5:i + 1].astype(float)

                    if len(six_month_data) >= 6:
                        start_price = float(six_month_data.iloc[0])
                        end_price = float(six_month_data.iloc[-1])
                        avg_price = float(six_month_data.mean())

                        # Calculate trend slope as percentage change
                        trend_change_pct = ((end_price - start_price) / start_price) * 100.0

                        # Determine trend direction and strength
                        if trend_change_pct > 10.0:  # >10% gain over 6 months
                            trend_direction = 'bullish'
                            trend_strength = min(100.0, abs(trend_change_pct))
                        elif trend_change_pct < -10.0:  # >10% loss over 6 months
                            trend_direction = 'bearish'
                            trend_strength = min(100.0, abs(trend_change_pct))
                        else:
                            trend_direction = 'sideways'
                            trend_strength = abs(trend_change_pct)
                    else:
                        trend_direction = 'unknown'
                        trend_strength = 0.0
                else:
                    trend_direction = 'unknown'
                    trend_strength = 0.0

                monthly_indicators.append({
                    'symbol': symbol,
                    'month_ending_date': month_end.date(),
                    'donchian_high_12m': donchian_high,
                    'donchian_low_12m': donchian_low,
                    'donchian_mid_12m': donchian_mid,
                    'monthly_open': monthly_open,
                    'monthly_high': monthly_high,
                    'monthly_low': monthly_low,
                    'monthly_close': monthly_close,
                    'monthly_volume': monthly_volume,
                    'trend_direction': trend_direction,
                    'trend_strength_6m': float(trend_strength)
                })

            return monthly_indicators

        except Exception as e:
            logger.error(f"Error calculating monthly indicators for {symbol}: {e}")
            return []

    def update_symbol_monthly(self, symbol: str) -> bool:
        """Update monthly data for one symbol"""
        try:
            # Get 2 years of daily data for monthly calculations
            start_date = datetime.now().date() - timedelta(days=730)

            daily_data = self.get_daily_data_for_monthly_calc(symbol, start_date)

            if daily_data.empty:
                logger.warning(f"No daily data found for {symbol}")
                return False

            monthly_indicators = self.calculate_monthly_indicators(symbol, daily_data)

            if not monthly_indicators:
                logger.warning(f"No monthly indicators calculated for {symbol}")
                return False

            # Insert monthly indicators
            success_count = 0
            for indicator in monthly_indicators:
                if db.insert_monthly_indicators(indicator):
                    success_count += 1

            logger.info(f"Updated {success_count} monthly records for {symbol}")
            return success_count > 0

        except Exception as e:
            logger.error(f"Error updating monthly data for {symbol}: {e}")
            return False

    @timing_decorator()
    def run_monthly_update(self, limit: Optional[int] = None, test_symbols: Optional[List[str]] = None) -> Dict:
        """Run monthly update"""
        try:
            logger.info("📅 Starting monthly data update")
            start_time = datetime.now()

            if test_symbols:
                symbols = test_symbols
            else:
                symbols = self.get_symbols_to_update(limit=limit)

            if not symbols:
                return {'success': False, 'message': 'No symbols found'}

            stats = {
                'start_time': start_time,
                'total_symbols': len(symbols),
                'successful_updates': 0,
                'failed_updates': 0
            }

            logger.info(f"Updating monthly data for {len(symbols)} symbols...")

            for i, symbol in enumerate(symbols, 1):
                logger.info(f"Processing {symbol} ({i}/{len(symbols)})")

                if self.update_symbol_monthly(symbol):
                    stats['successful_updates'] += 1
                    logger.info(f"✅ Monthly update successful for {symbol}")
                else:
                    stats['failed_updates'] += 1
                    logger.warning(f"⚠️ Monthly update failed for {symbol}")

                if i % 50 == 0:
                    progress = (i / len(symbols)) * 100
                    logger.info(f"📈 Progress: {progress:.1f}% ({i}/{len(symbols)})")

            duration = datetime.now() - start_time
            success_rate = (stats['successful_updates'] / stats['total_symbols']) * 100

            logger.info(f"""
📅 Monthly update completed in {duration}:
✅ Successful updates: {stats['successful_updates']}
❌ Failed updates: {stats['failed_updates']}
📊 Total symbols: {stats['total_symbols']}
📈 Success rate: {success_rate:.1f}%""")

            return {'success': success_rate > 75, 'stats': stats}

        except Exception as e:
            logger.error(f"Monthly update failed: {e}")
            return {'success': False, 'message': str(e)}

    def get_last_completed_month_date(self) -> datetime:
        """Get the last day of the last completed month"""
        from datetime import datetime
        import calendar

        now = datetime.now()

        # If we're in first 5 days of month, target previous month
        # Otherwise, target current month if we're near the end
        if now.day <= 5:
            # Use previous month
            if now.month == 1:
                target_year = now.year - 1
                target_month = 12
            else:
                target_year = now.year
                target_month = now.month - 1
        else:
            # Use current month
            target_year = now.year
            target_month = now.month

        # Get last day of target month
        last_day = calendar.monthrange(target_year, target_month)[1]
        return datetime(target_year, target_month, last_day)

    def get_last_monthly_date(self, symbol: str) -> Optional[datetime]:
        """Get the last month we have data for"""
        try:
            query = "SELECT MAX(month_ending_date) FROM monthly_technical_indicators WHERE symbol = %s"
            results = db.execute_query(query, (symbol,))

            if results and results[0][0]:
                return results[0][0]
            return None

        except Exception as e:
            logger.error(f"Error getting last monthly date for {symbol}: {e}")
            return None

    def should_run_monthly_update(self) -> Tuple[bool, str]:
        """Check if monthly update should run - returns (should_run, reason)"""
        try:
            now = datetime.now()

            # Get last completed month
            last_completed_month = self.get_last_completed_month_date()

            # Only run if:
            # 1. We're in first 5 days of new month, OR
            # 2. We're in last 3 days of current month
            if 5 < now.day < 28:
                return False, f"Too early in month (day {now.day}) - wait until month end or beginning"

            # Check if we already have data for the target month
            symbols_sample = self.get_symbols_to_update(limit=3)
            if not symbols_sample:
                return False, "No symbols found to check"

            up_to_date_count = 0
            for symbol in symbols_sample:
                last_monthly_date = self.get_last_monthly_date(symbol)
                if last_monthly_date and last_monthly_date >= last_completed_month.date():
                    up_to_date_count += 1

            # If majority already up to date, skip
            if up_to_date_count >= len(symbols_sample) * 0.7:
                return False, f"Monthly data already current (checked {up_to_date_count}/{len(symbols_sample)} symbols)"

            return True, f"Monthly update needed (target month: {last_completed_month.strftime('%Y-%m')})"

        except Exception as e:
            logger.error(f"Error checking monthly update timing: {e}")
            return True, "Error checking timing - running as fallback"

    def run_monthly_update_with_timing(self, limit: Optional[int] = None,
                                       test_symbols: Optional[List[str]] = None,
                                       force: bool = False) -> Dict:
        """Run monthly update with smart timing logic"""
        try:
            # Check timing unless forced
            if not force:
                should_run, reason = self.should_run_monthly_update()

                if not should_run:
                    logger.info(f"⏭️ Skipping monthly update: {reason}")
                    return {
                        'success': True,
                        'skipped': True,
                        'reason': reason,
                        'message': 'Monthly update skipped - no action needed'
                    }
                else:
                    logger.info(f"🔄 {reason}")
            else:
                logger.info("🔧 Force mode: Running monthly update regardless of timing")

            # Run the actual update
            return self.run_monthly_update(limit=limit, test_symbols=test_symbols)

        except Exception as e:
            logger.error(f"Monthly update with timing failed: {e}")
            return {'success': False, 'message': str(e)}


def main():
    """Main execution function with timing logic"""
    import argparse

    parser = argparse.ArgumentParser(description='Monthly Data Updater with Smart Timing')
    parser.add_argument('--limit', type=int, help='Limit number of symbols')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--force', action='store_true', help='Force update regardless of timing')
    parser.add_argument('--check-timing', action='store_true', help='Check timing without running')

    args = parser.parse_args()

    updater = MonthlyDataUpdater()

    if args.check_timing:
        should_run, reason = updater.should_run_monthly_update()
        logger.info(f"⏰ Timing check: {'SHOULD RUN' if should_run else 'SKIP'}")
        logger.info(f"📋 Reason: {reason}")
        return

    if args.test:
        logger.info(f"🧪 Running monthly test with symbols: {args.test}")
        result = updater.run_monthly_update(test_symbols=args.test)
    else:
        # Use smart timing
        result = updater.run_monthly_update_with_timing(limit=args.limit, force=args.force)

    if result.get('skipped'):
        logger.info(f"⏭️ {result['message']}")
    elif result['success']:
        logger.info("✅ Monthly update completed successfully")
    else:
        logger.warning("⚠️ Monthly update completed with failures")


if __name__ == "__main__":
    main()