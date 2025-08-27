# -*- coding: utf-8 -*-
# mechanism/data_updaters/weekly_data_updater.py
"""
Weekly data updater for multi-timeframe analysis
Updates weekly technical indicators following daily_data_updater.py patterns
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings

# Import shared infrastructure (matching your existing pattern)
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from shared import (
    config, db, setup_logging, retry_on_failure,
    timing_decorator, date_utils, data_validation,
    format_number, safe_divide
)

warnings.filterwarnings('ignore')

# Setup logging for this module (following your pattern)
logger = setup_logging("weekly_data_updater")


class WeeklyDataUpdater:
    """Weekly data updater using shared infrastructure - matches daily_data_updater.py style"""

    def __init__(self):
        """Initialize the updater with shared configuration"""
        self.batch_size = config.data_batch_size
        self.max_concurrent = config.max_concurrent_updates
        
        logger.info("Weekly Data Updater initialized with shared infrastructure")
        logger.info(f"Batch size: {self.batch_size}, Max concurrent: {self.max_concurrent}")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_to_update(self, limit: Optional[int] = None) -> List[str]:
        """Get symbols that need weekly updates - matches your existing pattern"""
        try:
            query = """
            SELECT DISTINCT symbol 
            FROM daily_fundamentals 
            WHERE symbol IS NOT NULL 
            AND symbol != ''
            ORDER BY symbol
            """

            if limit:
                query += f" LIMIT {limit}"

            results = db.execute_query(query)
            symbols = [row[0] for row in results]

            logger.info(f"Found {len(symbols)} symbols for weekly update")
            return symbols

        except Exception as e:
            logger.error(f"Error getting symbols to update: {e}")
            return []

    @retry_on_failure(max_retries=2, delay=0.5)
    def get_last_weekly_date(self, symbol: str) -> Optional[datetime]:
        """Get the last week we have data for - matches your get_last_price_date pattern"""
        try:
            query = "SELECT MAX(week_ending_date) FROM weekly_technical_indicators WHERE symbol = %s"
            results = db.execute_query(query, (symbol,))

            if results and results[0][0]:
                return results[0][0]
            return None

        except Exception as e:
            logger.error(f"Error getting last weekly date for {symbol}: {e}")
            return None

    @timing_decorator()
    def get_daily_data_for_weekly_calc(self, symbol: str, start_date: datetime) -> pd.DataFrame:
        """Get daily data for weekly calculations - uses your database patterns"""
        try:
            query = """
            SELECT sp.date, sp.open, sp.high, sp.low, sp.close, sp.volume,
                   ti.donchian_high_20, ti.donchian_low_20, ti.rsi_14
            FROM stock_prices sp
            LEFT JOIN technical_indicators ti ON sp.symbol = ti.symbol AND sp.date = ti.date
            WHERE sp.symbol = %s AND sp.date >= %s
            ORDER BY sp.date ASC
            """
            
            results = db.execute_query(query, (symbol, start_date))
            
            if not results:
                return pd.DataFrame()
                
            # Convert to DataFrame - matches your data handling pattern
            df = pd.DataFrame(results, columns=[
                'date', 'open', 'high', 'low', 'close', 'volume',
                'donchian_high_20', 'donchian_low_20', 'rsi_14'
            ])
            
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            
            return df

        except Exception as e:
            logger.error(f"Error getting daily data for {symbol}: {e}")
            return pd.DataFrame()

    def calculate_weekly_indicators(self, symbol: str, daily_data: pd.DataFrame) -> List[Dict]:
        """Calculate weekly indicators - DEBUG VERSION"""
        try:
            logger.info(f"DEBUG: Starting weekly calculation for {symbol}")
            logger.info(f"DEBUG: Daily data shape: {daily_data.shape}")
            logger.info(f"DEBUG: Daily data columns: {daily_data.columns.tolist()}")
            logger.info(f"DEBUG: Daily data date range: {daily_data.index.min()} to {daily_data.index.max()}")

            if daily_data.empty:
                logger.warning(f"DEBUG: No daily data for {symbol}")
                return []

            logger.info(f"DEBUG: Daily data length: {len(daily_data)} days")
            if len(daily_data) < 100:  # Need ~15 weeks minimum (more flexible)
                logger.warning(f"DEBUG: Insufficient daily data for {symbol}: {len(daily_data)} days (need ~140)")
                return []

            # Convert all numeric columns to float to avoid Decimal/float mixing
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in daily_data.columns:
                    daily_data[col] = pd.to_numeric(daily_data[col], errors='coerce').astype(float)

            logger.info(f"DEBUG: Data types after conversion: {daily_data.dtypes}")

            # Resample daily data to weekly (Friday close)
            try:
                weekly = daily_data.resample('W-FRI').agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                }).dropna()

                logger.info(f"DEBUG: Weekly resampled data shape: {weekly.shape}")
                logger.info(f"DEBUG: Weekly data length: {len(weekly)} weeks")
                logger.info(f"DEBUG: Weekly date range: {weekly.index.min()} to {weekly.index.max()}")

            except Exception as e:
                logger.error(f"DEBUG: Resample failed for {symbol}: {e}")
                return []

            if len(weekly) < 20:  # Need 20 weeks for Donchian calculation
                logger.warning(f"DEBUG: Insufficient weekly data for {symbol}: {len(weekly)} weeks (need 20+)")
                return []

            logger.info(f"DEBUG: Starting weekly indicator calculations for {len(weekly)} weeks")
            weekly_indicators = []

            # Start from 20th week (index 19) - but let's be more flexible for debugging
            start_index = min(19, len(weekly) - 1)  # Start from available data
            logger.info(f"DEBUG: Processing weeks from index {start_index} to {len(weekly) - 1}")

            for i in range(start_index, len(weekly)):
                try:
                    week_end = weekly.index[i]
                    logger.info(f"DEBUG: Processing week {i + 1}/{len(weekly)} ending {week_end.date()}")

                    # Use available data for Donchian (up to 20 weeks)
                    lookback_weeks = min(20, i + 1)
                    week_data = weekly.iloc[i - lookback_weeks + 1:i + 1]
                    logger.info(f"DEBUG: Using {lookback_weeks} weeks for Donchian calculation")

                    # Weekly OHLCV
                    weekly_open = float(weekly.iloc[i]['open'])
                    weekly_high = float(weekly.iloc[i]['high'])
                    weekly_low = float(weekly.iloc[i]['low'])
                    weekly_close = float(weekly.iloc[i]['close'])
                    weekly_volume = int(float(weekly.iloc[i]['volume']))

                    logger.info(
                        f"DEBUG: Weekly OHLCV: O={weekly_open}, H={weekly_high}, L={weekly_low}, C={weekly_close}")

                    # Calculate weekly Donchian channels
                    donchian_high = float(week_data['high'].max())
                    donchian_low = float(week_data['low'].min())
                    donchian_mid = (donchian_high + donchian_low) / 2

                    logger.info(f"DEBUG: Donchian: High={donchian_high}, Low={donchian_low}, Mid={donchian_mid}")

                    # Weekly moving averages (use available data)
                    sma_10w = float(weekly['close'].iloc[max(0, i - 9):i + 1].mean()) if i >= 9 else None
                    sma_20w = float(weekly['close'].iloc[max(0, i - 19):i + 1].mean()) if i >= 19 else float(
                        weekly['close'].iloc[0:i + 1].mean())

                    logger.info(f"DEBUG: SMAs: 10w={sma_10w}, 20w={sma_20w}")

                    # Weekly RSI calculation - use simplified approach for debugging
                    if i >= 13:  # Need 14+ weeks for RSI
                        rsi_data = weekly['close'].iloc[max(0, i - 20):i + 1]  # Use up to 21 weeks
                        rsi_14w = self.calculate_weekly_rsi(rsi_data)
                        logger.info(f"DEBUG: Weekly RSI calculated: {rsi_14w} (from {len(rsi_data)} weeks)")
                    else:
                        rsi_14w = None
                        logger.info(f"DEBUG: Skipping RSI - only {i + 1} weeks available")

                    # Weekly volume analysis
                    volume_sma_10w = int(weekly['volume'].iloc[max(0, i - 9):i + 1].mean()) if i >= 9 else None
                    volume_ratio_weekly = float(
                        weekly_volume / volume_sma_10w) if volume_sma_10w and volume_sma_10w > 0 else None

                    # Weekly position analysis
                    if donchian_high > donchian_low:
                        price_position_weekly = ((weekly_close - donchian_low) / (donchian_high - donchian_low)) * 100
                    else:
                        price_position_weekly = 50.0

                    indicator_dict = {
                        'symbol': symbol,
                        'week_ending_date': week_end.date(),
                        'donchian_high_20w': donchian_high,
                        'donchian_low_20w': donchian_low,
                        'donchian_mid_20w': donchian_mid,
                        'weekly_open': weekly_open,
                        'weekly_high': weekly_high,
                        'weekly_low': weekly_low,
                        'weekly_close': weekly_close,
                        'weekly_volume': weekly_volume,
                        'sma_10w': sma_10w,
                        'sma_20w': sma_20w,
                        'rsi_14w': rsi_14w,
                        'volume_sma_10w': volume_sma_10w,
                        'volume_ratio_weekly': volume_ratio_weekly,
                        'price_position_weekly': price_position_weekly
                    }

                    weekly_indicators.append(indicator_dict)
                    logger.info(f"DEBUG: Successfully created indicator for {week_end.date()}")

                except Exception as e:
                    logger.error(f"DEBUG: Error processing week {i} for {symbol}: {e}")
                    continue

            logger.info(f"DEBUG: Completed calculation for {symbol}: {len(weekly_indicators)} indicators created")
            return weekly_indicators

        except Exception as e:
            logger.error(f"DEBUG: Error calculating weekly indicators for {symbol}: {e}")
            import traceback
            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return []

    def calculate_weekly_rsi(self, prices: pd.Series, period: int = 14) -> Optional[float]:
        """Simplified weekly RSI calculation"""
        try:
            if len(prices) < period + 1:
                return None

            # Calculate price changes
            changes = prices.diff().dropna()

            if len(changes) < period:
                return None

            # Simple moving average approach
            gains = changes.clip(lower=0)
            losses = (-changes).clip(lower=0)

            # Calculate average gain and loss over period
            avg_gain = gains.tail(period).mean()
            avg_loss = losses.tail(period).mean()

            if avg_loss == 0:
                return 100.0

            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

            return float(rsi) if 0 <= rsi <= 100 else None

        except Exception as e:
            logger.error(f"Simplified weekly RSI calculation failed: {e}")
            return None

    @timing_decorator()
    def update_symbol_weekly(self, symbol: str) -> bool:
        """Update weekly data for one symbol - FIXED to get more data"""
        try:
            # Get last weekly update date
            last_weekly_date = self.get_last_weekly_date(symbol)

            # FIXED: Get much more historical data for weekly calculations
            if last_weekly_date:
                # Get 6 months of overlap to ensure we have enough data
                start_date = last_weekly_date - timedelta(weeks=26)  # 6 months back
            else:
                # For new symbols, get 2 years of data
                start_date = datetime.now().date() - timedelta(weeks=104)  # 2 years

            logger.info(f"Getting daily data for {symbol} from {start_date}")

            # Get daily data for weekly calculations
            daily_data = self.get_daily_data_for_weekly_calc(symbol, start_date)

            if daily_data.empty:
                logger.warning(f"No daily data found for {symbol}")
                return False

            logger.info(
                f"Retrieved {len(daily_data)} days of data for {symbol} (from {daily_data.index.min().date()} to {daily_data.index.max().date()})")

            # Calculate weekly indicators
            weekly_indicators = self.calculate_weekly_indicators(symbol, daily_data)

            if not weekly_indicators:
                logger.warning(f"No weekly indicators calculated for {symbol}")
                return False

            # Insert weekly indicators into database
            success_count = 0
            for indicator in weekly_indicators:
                # ADD THIS DEBUG LOGGING:
                logger.info(f"DEBUG BEFORE INSERT: Symbol={indicator['symbol']}, Date={indicator['week_ending_date']}")
                logger.info(
                    f"DEBUG BEFORE INSERT: RSI={indicator.get('rsi_14w')} (type: {type(indicator.get('rsi_14w'))})")
                logger.info(f"DEBUG BEFORE INSERT: All keys: {list(indicator.keys())}")

                if db.insert_weekly_indicators(indicator):
                    success_count += 1
                    logger.info(f"DEBUG AFTER INSERT: SUCCESS for {indicator['symbol']} on {indicator['week_ending_date']}")
                else:
                    logger.error(f"Failed to insert weekly indicator for {symbol} on {indicator['week_ending_date']}")
                    logger.error(f"DEBUG AFTER INSERT: FAILED for {indicator['symbol']} on {indicator['week_ending_date']}")

            logger.info(f"Updated {success_count} weekly records for {symbol}")
            return success_count > 0

        except Exception as e:
            logger.error(f"Error updating weekly data for {symbol}: {e}")
            return False

    @timing_decorator() 
    def run_weekly_update(self, limit: Optional[int] = None, test_symbols: Optional[List[str]] = None) -> Dict:
        """Run weekly update - matches your run_daily_update pattern"""
        try:
            logger.info("�️ Starting weekly data update")
            start_time = datetime.now()
            
            # Get symbols to update
            if test_symbols:
                symbols = test_symbols
            else:
                symbols = self.get_symbols_to_update(limit=limit)
            
            if not symbols:
                logger.error("No symbols found to update")
                return {'success': False, 'message': 'No symbols found'}
            
            # Initialize stats (following your pattern)
            stats = {
                'start_time': start_time,
                'total_symbols': len(symbols),
                'successful_updates': 0,
                'failed_updates': 0,
                'errors': []
            }
            
            logger.info(f"Updating weekly data for {len(symbols)} symbols...")
            
            # Process each symbol
            for i, symbol in enumerate(symbols, 1):
                try:
                    logger.info(f"Processing {symbol} ({i}/{len(symbols)})")
                    
                    success = self.update_symbol_weekly(symbol)
                    
                    if success:
                        stats['successful_updates'] += 1
                        logger.info(f"✅ Weekly update successful for {symbol}")
                    else:
                        stats['failed_updates'] += 1
                        logger.warning(f"⚠️ Weekly update failed for {symbol}")
                        
                except Exception as e:
                    stats['failed_updates'] += 1
                    error_msg = f"Failed to update {symbol}: {e}"
                    stats['errors'].append(error_msg)
                    logger.error(error_msg)
                
                # Progress reporting (every 50 symbols like your pattern)
                if i % 50 == 0:
                    progress = (i / len(symbols)) * 100
                    logger.info(f"� Progress: {progress:.1f}% ({i}/{len(symbols)})")
            
            # Final report (matching your reporting style)
            duration = datetime.now() - start_time
            success_rate = (stats['successful_updates'] / stats['total_symbols']) * 100
            
            logger.info(f"""
� Weekly update completed in {duration}:
✅ Successful updates: {stats['successful_updates']}
❌ Failed updates: {stats['failed_updates']}
� Total symbols: {stats['total_symbols']}
� Success rate: {success_rate:.1f}%""")

            return {
                'success': success_rate > 75,  # Following your success criteria
                'stats': stats
            }

        except Exception as e:
            logger.error(f"Weekly update failed: {e}")
            return {'success': False, 'message': str(e)}

    def get_last_completed_week_date(self) -> datetime:
        """Get the date of the last completed trading week (Friday or last trading day)"""
        from datetime import datetime, timedelta

        now = datetime.now()

        # Calculate last completed week
        days_since_friday = (now.weekday() + 3) % 7  # Monday=0, Friday=4

        if now.weekday() <= 4 and now.hour < 16:  # Monday-Friday before 4 PM ET
            # Market week not yet complete, use previous Friday
            last_friday = now - timedelta(days=days_since_friday + 7)
        else:
            # Weekend or after market close, use this week's Friday
            last_friday = now - timedelta(days=days_since_friday)

        return last_friday

    def should_run_weekly_update(self) -> Tuple[bool, str]:
        """Check if weekly update should run - returns (should_run, reason)"""
        try:
            now = datetime.now()

            # Get last completed week
            last_completed_week = self.get_last_completed_week_date()

            # Check if we already have data for this week
            symbols_sample = self.get_symbols_to_update(limit=3)  # Check 3 symbols
            if not symbols_sample:
                return False, "No symbols found to check"

            # Check multiple symbols to be sure
            up_to_date_count = 0
            for symbol in symbols_sample:
                last_weekly_date = self.get_last_weekly_date(symbol)
                if last_weekly_date and last_weekly_date >= last_completed_week.date():
                    up_to_date_count += 1

            # If majority of symbols are up to date, skip update
            if up_to_date_count >= len(symbols_sample) * 0.7:  # 70% threshold
                return False, f"Weekly data already current (checked {up_to_date_count}/{len(symbols_sample)} symbols)"

            # Check timing - only run on weekends or after market close Friday
            if now.weekday() < 4:  # Monday-Thursday
                return False, f"Too early in week (today: {now.strftime('%A')})"

            if now.weekday() == 4 and now.hour < 16:  # Friday before 4 PM ET
                return False, "Market still open on Friday"

            return True, f"Weekly update needed (last completed week: {last_completed_week.date()})"

        except Exception as e:
            logger.error(f"Error checking weekly update timing: {e}")
            return True, "Error checking timing - running as fallback"

    def run_weekly_update_with_timing(self, limit: Optional[int] = None,
                                      test_symbols: Optional[List[str]] = None,
                                      force: bool = False) -> Dict:
        """Run weekly update with smart timing logic"""
        try:
            # Check timing unless forced
            if not force:
                should_run, reason = self.should_run_weekly_update()

                if not should_run:
                    logger.info(f"⏭️ Skipping weekly update: {reason}")
                    return {
                        'success': True,
                        'skipped': True,
                        'reason': reason,
                        'message': 'Weekly update skipped - no action needed'
                    }
                else:
                    logger.info(f"🔄 {reason}")
            else:
                logger.info("🔧 Force mode: Running weekly update regardless of timing")

            # Run the actual update
            return self.run_weekly_update(limit=limit, test_symbols=test_symbols)

        except Exception as e:
            logger.error(f"Weekly update with timing failed: {e}")
            return {'success': False, 'message': str(e)}


def main():
    """Main execution function with timing logic"""
    import argparse

    parser = argparse.ArgumentParser(description='Weekly Data Updater with Smart Timing')
    parser.add_argument('--limit', type=int, help='Limit number of symbols')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--force', action='store_true', help='Force update regardless of timing')
    parser.add_argument('--check-timing', action='store_true', help='Check timing without running')

    args = parser.parse_args()

    updater = WeeklyDataUpdater()

    if args.check_timing:
        should_run, reason = updater.should_run_weekly_update()
        logger.info(f"⏰ Timing check: {'SHOULD RUN' if should_run else 'SKIP'}")
        logger.info(f"📋 Reason: {reason}")
        return

    if args.test:
        logger.info(f"🧪 Running weekly test with symbols: {args.test}")
        result = updater.run_weekly_update(test_symbols=args.test)
    else:
        # Use smart timing
        result = updater.run_weekly_update_with_timing(limit=args.limit, force=args.force)

    if result.get('skipped'):
        logger.info(f"⏭️ {result['message']}")
    elif result['success']:
        logger.info("✅ Weekly update completed successfully")
    else:
        logger.warning("⚠️ Weekly update completed with failures")





if __name__ == "__main__":
    main()
