# automation/data_updaters/daily_data_updater.py
"""
Fixed daily data updater with robust RSI calculation
Updates stock prices and technical indicators with improved error handling
"""

import yfinance as yf
import pandas as pd
import numpy as np
import time
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

# Setup logging for this module
logger = setup_logging("daily_data_updater")


class DailyDataUpdater:
    """Enhanced daily data updater with fixed RSI calculation"""

    def __init__(self):
        """Initialize the updater with shared configuration"""
        self.batch_size = config.data_batch_size
        self.max_concurrent = config.max_concurrent_updates
        self.rate_limit_delay = config.rate_limit_delay

        logger.info("Daily Data Updater initialized with shared infrastructure")
        logger.info(f"Batch size: {self.batch_size}, Max concurrent: {self.max_concurrent}")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_to_update(self, limit: Optional[int] = None) -> List[str]:
        """Get symbols that need daily updates"""
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

            logger.info(f"Found {len(symbols)} symbols to update")
            return symbols

        except Exception as e:
            logger.error(f"Error getting symbols to update: {e}")
            return []

    @retry_on_failure(max_retries=2, delay=0.5)
    def get_last_price_date(self, symbol: str) -> Optional[datetime]:
        """Get the last date we have price data for a symbol"""
        try:
            query = "SELECT MAX(date) FROM stock_prices WHERE symbol = %s"
            results = db.execute_query(query, (symbol,))

            if results and results[0][0]:
                return results[0][0]
            return None

        except Exception as e:
            logger.error(f"Error getting last price date for {symbol}: {e}")
            return None

    @timing_decorator()
    @retry_on_failure(max_retries=3, delay=2.0, exceptions=(Exception,))
    def fetch_daily_data(self, symbol: str, start_date: str) -> Optional[pd.DataFrame]:
        """Fetch daily price data from Yahoo Finance"""
        try:
            logger.debug(f"Fetching data for {symbol} from {start_date}")

            ticker = yf.Ticker(symbol)
            data = ticker.history(start=start_date, auto_adjust=True)

            if data.empty:
                logger.warning(f"No data returned for {symbol}")
                return None

            # Reset index and add symbol
            data = data.reset_index()
            data['symbol'] = symbol

            # Rename columns to match schema
            column_mapping = {
                'Date': 'date',
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            }

            data = data.rename(columns=column_mapping)

            # Select required columns
            required_columns = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume']
            data = data[required_columns]

            # Convert date and add adj_close
            data['date'] = pd.to_datetime(data['date']).dt.date
            data['adj_close'] = data['close']  # auto_adjust=True means close = adj_close

            # Validate data
            validation = data_validation.validate_price_data(data)
            if not validation['valid']:
                logger.warning(f"Data validation failed for {symbol}: {validation['error']}")
                return None

            logger.debug(f"Successfully fetched {len(data)} records for {symbol}")
            return data

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {e}")
            raise

    @timing_decorator()
    def calculate_technical_indicators(self, symbol: str, data: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators with robust RSI calculation"""
        try:
            # Sort by date for proper calculation
            data = data.sort_values('date').copy()

            # Simple Moving Averages
            data['sma_10'] = data['close'].rolling(window=10, min_periods=10).mean()
            data['sma_20'] = data['close'].rolling(window=20, min_periods=20).mean()
            data['sma_50'] = data['close'].rolling(window=50, min_periods=50).mean()

            # Fixed RSI calculation
            try:
                # Calculate price changes
                data['price_diff'] = data['close'].diff()

                # Calculate RSI manually to avoid pandas Series issues
                rsi_values = []

                for i in range(len(data)):
                    if i < 14:  # Need at least 14 periods
                        rsi_values.append(np.nan)
                        continue

                    # Get last 14 price changes
                    recent_changes = data['price_diff'].iloc[i - 13:i + 1]

                    # Calculate average gains and losses
                    gains = recent_changes[recent_changes > 0]
                    losses = recent_changes[recent_changes < 0].abs()

                    avg_gain = gains.mean() if len(gains) > 0 else 0
                    avg_loss = losses.mean() if len(losses) > 0 else 0

                    # Calculate RSI
                    if avg_loss == 0:
                        rsi = 100.0
                    elif avg_gain == 0:
                        rsi = 0.0
                    else:
                        rs = avg_gain / avg_loss
                        rsi = 100 - (100 / (1 + rs))

                    rsi_values.append(round(rsi, 2))

                data['rsi_14'] = rsi_values

                # Clean up temporary column
                data = data.drop(['price_diff'], axis=1)

            except Exception as e:
                logger.warning(f"RSI calculation failed for {symbol}: {e}")
                data['rsi_14'] = np.nan

            # MACD
            try:
                ema_12 = data['close'].ewm(span=12, min_periods=12).mean()
                ema_26 = data['close'].ewm(span=26, min_periods=26).mean()
                data['macd'] = ema_12 - ema_26
                data['macd_signal'] = data['macd'].ewm(span=9, min_periods=9).mean()
                data['macd_histogram'] = data['macd'] - data['macd_signal']
            except Exception as e:
                logger.warning(f"MACD calculation failed for {symbol}: {e}")
                data[['macd', 'macd_signal', 'macd_histogram']] = np.nan

            # Bollinger Bands
            try:
                bb_middle = data['close'].rolling(window=20, min_periods=20).mean()
                bb_std = data['close'].rolling(window=20, min_periods=20).std()
                data['bollinger_upper'] = bb_middle + (bb_std * 2)
                data['bollinger_lower'] = bb_middle - (bb_std * 2)
            except Exception as e:
                logger.warning(f"Bollinger Bands calculation failed for {symbol}: {e}")
                data[['bollinger_upper', 'bollinger_lower']] = np.nan

            # Donchian Channels
            try:
                data['donchian_high_20'] = data['high'].rolling(window=20, min_periods=20).max()
                data['donchian_low_20'] = data['low'].rolling(window=20, min_periods=20).min()
                data['donchian_mid_20'] = (data['donchian_high_20'] + data['donchian_low_20']) / 2
            except Exception as e:
                logger.warning(f"Donchian Channels calculation failed for {symbol}: {e}")
                data[['donchian_high_20', 'donchian_low_20', 'donchian_mid_20']] = np.nan

            # ATR
            try:
                data['high_low'] = data['high'] - data['low']
                data['high_close'] = np.abs(data['high'] - data['close'].shift())
                data['low_close'] = np.abs(data['low'] - data['close'].shift())
                data['true_range'] = data[['high_low', 'high_close', 'low_close']].max(axis=1)
                data['atr_14'] = data['true_range'].rolling(window=14, min_periods=14).mean()

                # Clean up temporary columns
                data = data.drop(['high_low', 'high_close', 'low_close', 'true_range'], axis=1)
            except Exception as e:
                logger.warning(f"ATR calculation failed for {symbol}: {e}")
                data['atr_14'] = np.nan

            # Volume indicators - fixed calculation
            try:
                data['volume_sma_10'] = data['volume'].rolling(window=10, min_periods=10).mean()

                # Calculate volume ratio manually to avoid pandas Series issues
                volume_ratio = []
                for i in range(len(data)):
                    vol = data.iloc[i]['volume']
                    vol_sma = data.iloc[i]['volume_sma_10']

                    if pd.isna(vol_sma) or vol_sma == 0 or pd.isna(vol):
                        ratio = 1.0
                    else:
                        ratio = float(vol) / float(vol_sma)

                    volume_ratio.append(round(ratio, 2))

                data['volume_ratio'] = volume_ratio

            except Exception as e:
                logger.warning(f"Volume indicators calculation failed for {symbol}: {e}")
                data['volume_sma_10'] = np.nan
                data['volume_ratio'] = 1.0

            # Additional indicators - improved calculation
            try:
                # Price position in Donchian channel and channel width
                price_position_values = []
                channel_width_values = []

                for i in range(len(data)):
                    close_val = data.iloc[i]['close']
                    high_val = data.iloc[i]['donchian_high_20']
                    low_val = data.iloc[i]['donchian_low_20']

                    # Price position calculation
                    if pd.isna(high_val) or pd.isna(low_val) or high_val == low_val:
                        position = 0.0
                    else:
                        try:
                            position = ((float(close_val) - float(low_val)) / (float(high_val) - float(low_val))) * 100
                            position = max(0, min(100, position))  # Clamp between 0-100
                        except (ValueError, ZeroDivisionError):
                            position = 0.0

                    price_position_values.append(round(position, 2))

                    # Channel width percentage calculation
                    if pd.isna(high_val) or pd.isna(low_val):
                        width_pct = 0.0
                    else:
                        try:
                            mid_val = (float(high_val) + float(low_val)) / 2
                            if mid_val == 0:
                                width_pct = 0.0
                            else:
                                width_pct = ((float(high_val) - float(low_val)) / mid_val) * 100
                        except (ValueError, ZeroDivisionError):
                            width_pct = 0.0

                    channel_width_values.append(round(width_pct, 2))

                data['price_position'] = price_position_values
                data['channel_width_pct'] = channel_width_values

            except Exception as e:
                logger.warning(f"Additional indicators calculation failed for {symbol}: {e}")
                data['price_position'] = 0.0
                data['channel_width_pct'] = 0.0

            # Add metadata
            data['symbol'] = symbol
            data['updated_at'] = datetime.now()

            logger.debug(f"Calculated technical indicators for {symbol}")
            return data

        except Exception as e:
            logger.error(f"Error calculating technical indicators for {symbol}: {e}")
            return data

    @timing_decorator()
    def update_stock_prices(self, data: pd.DataFrame) -> bool:
        """Insert or update stock prices using shared database manager"""
        if data.empty:
            return False

        try:
            # Prepare data for bulk insert
            records = []
            for _, row in data.iterrows():
                # Clean and validate numeric data
                record = (
                    row['symbol'],
                    row['date'],
                    data_validation.clean_numeric_data(row['open']),
                    data_validation.clean_numeric_data(row['high']),
                    data_validation.clean_numeric_data(row['low']),
                    data_validation.clean_numeric_data(row['close']),
                    data_validation.clean_numeric_data(row['adj_close']),
                    int(data_validation.clean_numeric_data(row['volume'])) if not pd.isna(row['volume']) else None,
                    datetime.now(),  # created_at
                    datetime.now()  # updated_at
                )
                records.append(record)

            # Use shared database manager for bulk insert
            query = """
                INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume, created_at, updated_at)
                VALUES %s
                ON CONFLICT (symbol, date) 
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    adj_close = EXCLUDED.adj_close,
                    volume = EXCLUDED.volume,
                    updated_at = EXCLUDED.updated_at
            """

            rows_inserted = db.bulk_insert(query, records)
            logger.info(f"Successfully updated {rows_inserted} price records")
            return True

        except Exception as e:
            logger.error(f"Error updating stock prices: {e}")
            return False

    @timing_decorator()
    def update_technical_indicators(self, data: pd.DataFrame) -> bool:
        """Insert or update technical indicators using shared database manager"""
        if data.empty:
            return False

        try:
            records = []
            for _, row in data.iterrows():
                # Include records if ANY major indicator is calculated
                # Don't skip just because RSI failed - Donchian channels are more important
                has_indicators = (
                        not pd.isna(row.get('donchian_high_20')) or
                        not pd.isna(row.get('sma_20')) or
                        not pd.isna(row.get('bollinger_upper'))
                )
                if not has_indicators:
                    continue

                record = (
                    row['symbol'],
                    row['date'],
                    data_validation.clean_numeric_data(row.get('sma_10')),
                    data_validation.clean_numeric_data(row.get('sma_20')),
                    data_validation.clean_numeric_data(row.get('sma_50')),
                    data_validation.clean_numeric_data(row.get('rsi_14')),
                    data_validation.clean_numeric_data(row.get('macd')),
                    data_validation.clean_numeric_data(row.get('macd_signal')),
                    data_validation.clean_numeric_data(row.get('macd_histogram')),
                    data_validation.clean_numeric_data(row.get('bollinger_upper')),
                    data_validation.clean_numeric_data(row.get('bollinger_lower')),
                    data_validation.clean_numeric_data(row.get('atr_14')),
                    data_validation.clean_numeric_data(row.get('donchian_high_20')),
                    data_validation.clean_numeric_data(row.get('donchian_low_20')),
                    data_validation.clean_numeric_data(row.get('donchian_mid_20')),
                    int(data_validation.clean_numeric_data(row.get('volume_sma_10'))) if not pd.isna(
                        row.get('volume_sma_10')) else None,
                    data_validation.clean_numeric_data(row.get('volume_ratio', 1.0)),
                    data_validation.clean_numeric_data(row.get('price_position', 0.0)),
                    data_validation.clean_numeric_data(row.get('channel_width_pct', 0.0)),
                    datetime.now(),  # created_at
                    datetime.now()  # updated_at
                )
                records.append(record)

            if not records:
                logger.warning("No valid technical indicator records to insert")
                return False

            query = """
                INSERT INTO technical_indicators (
                    symbol, date, sma_10, sma_20, sma_50, rsi_14, macd, macd_signal, macd_histogram,
                    bollinger_upper, bollinger_lower, atr_14, donchian_high_20, donchian_low_20, donchian_mid_20,
                    volume_sma_10, volume_ratio, price_position, channel_width_pct, created_at, updated_at
                )
                VALUES %s
                ON CONFLICT (symbol, date) 
                DO UPDATE SET
                    sma_10 = EXCLUDED.sma_10,
                    sma_20 = EXCLUDED.sma_20,
                    sma_50 = EXCLUDED.sma_50,
                    rsi_14 = EXCLUDED.rsi_14,
                    macd = EXCLUDED.macd,
                    macd_signal = EXCLUDED.macd_signal,
                    macd_histogram = EXCLUDED.macd_histogram,
                    bollinger_upper = EXCLUDED.bollinger_upper,
                    bollinger_lower = EXCLUDED.bollinger_lower,
                    atr_14 = EXCLUDED.atr_14,
                    donchian_high_20 = EXCLUDED.donchian_high_20,
                    donchian_low_20 = EXCLUDED.donchian_low_20,
                    donchian_mid_20 = EXCLUDED.donchian_mid_20,
                    volume_sma_10 = EXCLUDED.volume_sma_10,
                    volume_ratio = EXCLUDED.volume_ratio,
                    price_position = EXCLUDED.price_position,
                    channel_width_pct = EXCLUDED.channel_width_pct,
                    updated_at = EXCLUDED.updated_at
            """

            rows_inserted = db.bulk_insert(query, records)
            logger.info(f"Successfully updated {rows_inserted} technical indicator records")
            return True

        except Exception as e:
            logger.error(f"Error updating technical indicators: {e}")
            return False

    @timing_decorator()
    def update_symbol(self, symbol: str) -> bool:
        """Update a single symbol with latest data"""
        try:
            # Validate symbol
            if not data_validation.validate_symbol(symbol):
                logger.warning(f"Invalid symbol format: {symbol}")
                return False

            logger.info(f"Processing {symbol}")

            # Get last date we have data for
            last_date = self.get_last_price_date(symbol)

            # Determine start date for fetching new price data
            if last_date:
                start_date = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                # Start from 2 years ago if no data
                start_date = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')

            # Check if we need to update
            today = datetime.now().date()
            if last_date and last_date >= today:
                logger.debug(f"{symbol} is already up to date")
                # Still calculate technical indicators even if prices are up to date
            else:
                # Fetch and update new price data
                price_data = self.fetch_daily_data(symbol, start_date)
                if price_data is not None and not price_data.empty:
                    if not self.update_stock_prices(price_data):
                        logger.error(f"Failed to update prices for {symbol}")
                        return False
                else:
                    logger.debug(f"No new price data for {symbol}")

            # ALWAYS update technical indicators with sufficient historical data
            # Get extended historical data for proper technical indicators calculation
            extended_start = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
            historical_data = self.fetch_daily_data(symbol, extended_start)

            if historical_data is not None and not historical_data.empty:
                logger.debug(f"Calculating technical indicators for {symbol} with {len(historical_data)} days of data")

                # Calculate technical indicators for all historical data
                indicator_data = self.calculate_technical_indicators(symbol, historical_data)

                # Update technical indicators for recent dates (last 30 days)
                recent_cutoff = (datetime.now() - timedelta(days=30)).date()
                recent_indicator_data = indicator_data[
                    indicator_data['date'] >= recent_cutoff
                    ]

                if not recent_indicator_data.empty:
                    logger.debug(f"Updating {len(recent_indicator_data)} days of technical indicators for {symbol}")
                    if not self.update_technical_indicators(recent_indicator_data):
                        logger.warning(f"Failed to update technical indicators for {symbol}")
                        # Don't return False here - price update might have succeeded
                else:
                    # Fallback: try updating last 10 days with valid Donchian data
                    logger.debug(f"No recent data in last 30 days, trying fallback for {symbol}")
                    valid_data = indicator_data[~pd.isna(indicator_data['donchian_high_20'])]
                    if not valid_data.empty:
                        last_valid = valid_data.tail(10)
                        logger.debug(f"Updating {len(last_valid)} days of fallback data for {symbol}")
                        if not self.update_technical_indicators(last_valid):
                            logger.warning(f"Failed to update fallback technical indicators for {symbol}")
                    else:
                        logger.warning(f"No valid technical indicator data calculated for {symbol}")
            else:
                logger.warning(f"Could not get historical data for technical indicators for {symbol}")

            logger.info(f"✅ Successfully updated {symbol}")
            return True

        except Exception as e:
            logger.error(f"Error updating {symbol}: {e}")
            return False

    @timing_decorator()
    def run_daily_update(self, symbols: Optional[List[str]] = None, limit: Optional[int] = None):
        """Run daily update for specified symbols or all symbols"""
        start_time = datetime.now()
        logger.info(f"🚀 Starting daily update at {start_time}")

        # Get symbols to update
        if symbols is None:
            symbols = self.get_symbols_to_update(limit)

        if not symbols:
            logger.warning("No symbols to update")
            return {
                'success': False,
                'message': 'No symbols found',
                'symbols_processed': 0,
                'symbols_successful': 0,
                'symbols_failed': 0
            }

        successful_updates = 0
        failed_updates = 0

        # Process symbols with rate limiting
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"Processing {symbol} ({i}/{len(symbols)})")

            try:
                if self.update_symbol(symbol):
                    successful_updates += 1
                else:
                    failed_updates += 1

                # Rate limiting to avoid overwhelming APIs
                if i < len(symbols):  # Don't delay after last symbol
                    time.sleep(self.rate_limit_delay)

            except Exception as e:
                logger.error(f"Unexpected error processing {symbol}: {e}")
                failed_updates += 1
                continue

        end_time = datetime.now()
        duration = end_time - start_time

        # Log results
        success_rate = (successful_updates / len(symbols) * 100) if symbols else 0
        logger.info(f"""
        🎉 Daily update completed in {duration}:
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
            'success_rate': success_rate
        }

    @timing_decorator()
    def fix_technical_indicators_bulk(self, symbols: Optional[List[str]] = None, limit: Optional[int] = None):
        """Fix technical indicators for all symbols - comprehensive update"""
        start_time = datetime.now()
        logger.info(f"🔧 Starting bulk technical indicators fix at {start_time}")

        # Get symbols to fix
        if symbols is None:
            symbols = self.get_symbols_to_update(limit)

        if not symbols:
            logger.warning("No symbols to fix")
            return {
                'success': False,
                'message': 'No symbols found',
                'symbols_processed': 0,
                'symbols_successful': 0,
                'symbols_failed': 0
            }

        successful_fixes = 0
        failed_fixes = 0

        for i, symbol in enumerate(symbols, 1):
            logger.info(f"Fixing technical indicators for {symbol} ({i}/{len(symbols)})")

            try:
                # Get sufficient historical data (1 year)
                historical_start = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
                historical_data = self.fetch_daily_data(symbol, historical_start)

                if historical_data is not None and not historical_data.empty:
                    logger.debug(f"Processing {len(historical_data)} days of data for {symbol}")

                    # Calculate all technical indicators
                    indicator_data = self.calculate_technical_indicators(symbol, historical_data)

                    # Update technical indicators for last 30 days (was 60, now more lenient)
                    recent_cutoff = (datetime.now() - timedelta(days=30)).date()
                    recent_data = indicator_data[indicator_data['date'] >= recent_cutoff]

                    if not recent_data.empty:
                        if self.update_technical_indicators(recent_data):
                            successful_fixes += 1
                            logger.info(f"✅ Fixed {symbol} - updated {len(recent_data)} days")
                        else:
                            failed_fixes += 1
                            logger.error(f"❌ Failed to update indicators for {symbol}")
                    else:
                        # Try with ALL calculated data if recent filtering fails
                        logger.warning(f"No data in last 30 days for {symbol}, trying all calculated data...")
                        all_valid_data = indicator_data[~pd.isna(indicator_data['donchian_high_20'])]

                        if not all_valid_data.empty:
                            # Take the last 10 days with valid data
                            last_valid_data = all_valid_data.tail(10)
                            if self.update_technical_indicators(last_valid_data):
                                successful_fixes += 1
                                logger.info(f"✅ Fixed {symbol} - updated {len(last_valid_data)} days (all available)")
                            else:
                                failed_fixes += 1
                                logger.error(f"❌ Failed to update indicators for {symbol}")
                        else:
                            logger.warning(f"No valid Donchian data calculated for {symbol}")
                            failed_fixes += 1
                else:
                    logger.warning(f"Could not get historical data for {symbol}")
                    failed_fixes += 1

                # Small delay to avoid overwhelming the system
                if i < len(symbols):
                    time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error fixing {symbol}: {e}")
                failed_fixes += 1
                continue

        end_time = datetime.now()
        duration = end_time - start_time
        success_rate = (successful_fixes / len(symbols) * 100) if symbols else 0

        logger.info(f"""
        🎉 Bulk technical indicators fix completed in {duration}:
        ✅ Successful fixes: {successful_fixes}
        ❌ Failed fixes: {failed_fixes}
        📊 Total symbols: {len(symbols)}
        📈 Success rate: {success_rate:.1f}%
        """)

        return {
            'success': failed_fixes < len(symbols) * 0.1,  # Success if <10% failures
            'duration': duration.total_seconds(),
            'symbols_processed': len(symbols),
            'symbols_successful': successful_fixes,
            'symbols_failed': failed_fixes,
            'success_rate': success_rate
        }


def main():
    """Main function to run daily updates"""
    import argparse

    parser = argparse.ArgumentParser(description='Enhanced Daily Data Updater with Fixed Technical Indicators')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to update')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols (e.g., --test AAPL MSFT)')
    parser.add_argument('--config-test', action='store_true', help='Test configuration and database connection')
    parser.add_argument('--fix-indicators', action='store_true', help='Fix technical indicators for all symbols')

    args = parser.parse_args()

    if args.config_test:
        # Test configuration and database
        print("🔧 Testing configuration and database connection...")

        validation = config.validate_config()
        print(f"Configuration validation: {validation}")

        db_status = db.test_connection()
        if db_status['connected']:
            print(f"✅ Database connection successful")
            print(f"   Database: {db_status['database']}")
            print(f"   Records: {format_number(sum(db_status['stats'].values()))}")
        else:
            print(f"❌ Database connection failed: {db_status['error']}")

        return

    # Initialize updater
    updater = DailyDataUpdater()

    if args.fix_indicators:
        # Fix technical indicators for all symbols
        logger.info("🔧 Running bulk technical indicators fix")
        result = updater.fix_technical_indicators_bulk(limit=args.limit)

        if result['success']:
            logger.info("🎉 Bulk technical indicators fix completed successfully!")
        else:
            logger.warning("⚠️ Bulk fix completed with some failures")

    elif args.test:
        # Test mode with specific symbols
        logger.info(f"🧪 Running test update with symbols: {args.test}")
        result = updater.run_daily_update(symbols=args.test)

        if result['success']:
            logger.info(f"✅ Test completed successfully")
        else:
            logger.error(f"❌ Test completed with errors")

    else:
        # Full update
        logger.info("🚀 Running daily update")
        result = updater.run_daily_update(limit=args.limit)

        if result['success']:
            logger.info("🎉 Daily update completed successfully!")
        else:
            logger.warning("⚠️ Daily update completed with some failures")


if __name__ == "__main__":
    main()