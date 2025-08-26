#!/usr/bin/env python3
"""
Fixed Daily Data Updater - Complete Version
Now includes working technical indicators calculation
"""

import yfinance as yf
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')

# Import shared infrastructure
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared import (
    config, db, setup_logging, retry_on_failure,
    performance_monitor, file_utils
)


class DailyDataUpdater:
    """Enhanced daily data updater with fixed technical indicators"""

    def __init__(self):
        """Initialize with shared infrastructure"""
        self.logger = setup_logging(__name__)
        self.logger.info("Daily Data Updater initialized with shared infrastructure")

        # Configuration with defaults
        self.batch_size = getattr(config, 'DATA_UPDATE_BATCH_SIZE', 1000)
        self.max_concurrent = getattr(config, 'MAX_CONCURRENT_UPDATES', 5)
        self.logger.info(f"Batch size: {self.batch_size}, Max concurrent: {self.max_concurrent}")

        # Performance tracking
        self.stats = {
            'successful_updates': 0,
            'failed_updates': 0,
            'total_symbols': 0,
            'start_time': None
        }

    def safe_float(self, value):
        """Safely convert to float"""
        try:
            return float(value) if value is not None else None
        except (ValueError, TypeError):
            return None

    def calculate_donchian_channels(self, data, period=20):
        try:
            data = data.sort_values('date').copy()
            data['donchian_high_20'] = data['high'].rolling(window=period, min_periods=period).max()
            data['donchian_low_20'] = data['low'].rolling(window=period, min_periods=period).min()
            data['donchian_mid_20'] = (data['donchian_high_20'] + data['donchian_low_20']) / 2
            return data
        except Exception as e:
            self.logger.warning(f"Donchian calculation failed: {e}")
            data['donchian_high_20'] = np.nan
            data['donchian_low_20'] = np.nan
            data['donchian_mid_20'] = np.nan
            return data

    def calculate_rsi(self, data, period=14):
        try:
            data = data.sort_values('date').copy()
            delta = data['close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(window=period, min_periods=period).mean()
            avg_loss = loss.rolling(window=period, min_periods=period).mean()
            rsi_values = []
            for i in range(len(data)):
                gain_val = avg_gain.iloc[i] if i < len(avg_gain) else np.nan
                loss_val = avg_loss.iloc[i] if i < len(avg_loss) else np.nan
                if pd.isna(gain_val) or pd.isna(loss_val) or loss_val == 0:
                    rsi_values.append(np.nan)
                else:
                    rs = gain_val / loss_val
                    rsi = 100 - (100 / (1 + rs))
                    rsi_values.append(rsi)
            data['rsi_14'] = rsi_values
            return data
        except Exception as e:
            self.logger.warning(f"RSI calculation failed: {e}")
            data['rsi_14'] = np.nan
            return data

    def calculate_sma(self, data):
        try:
            data = data.sort_values('date').copy()
            data['sma_10'] = data['close'].rolling(window=10, min_periods=10).mean()
            data['sma_20'] = data['close'].rolling(window=20, min_periods=20).mean()
            data['sma_50'] = data['close'].rolling(window=50, min_periods=50).mean()
            return data
        except Exception as e:
            self.logger.warning(f"SMA calculation failed: {e}")
            data['sma_10'] = np.nan
            data['sma_20'] = np.nan
            data['sma_50'] = np.nan
            return data

    def calculate_volume_indicators(self, data):
        try:
            data = data.sort_values('date').copy()
            data['volume_sma_10'] = data['volume'].rolling(window=10, min_periods=10).mean()
            volume_ratio_values = []
            for i in range(len(data)):
                current_volume = data['volume'].iloc[i]
                avg_volume = data['volume_sma_10'].iloc[i]
                if pd.isna(current_volume) or pd.isna(avg_volume) or avg_volume == 0:
                    volume_ratio_values.append(1.0)
                else:
                    ratio = current_volume / avg_volume
                    volume_ratio_values.append(ratio)
            data['volume_ratio'] = volume_ratio_values
            return data
        except Exception as e:
            self.logger.warning(f"Volume indicators calculation failed: {e}")
            data['volume_ratio'] = 1.0
            data['volume_sma_10'] = np.nan
            return data

    def calculate_additional_indicators(self, data):
        try:
            data = data.sort_values('date').copy()
            price_position_values = []
            channel_width_values = []
            for i in range(len(data)):
                close_val = data['close'].iloc[i]
                high_val = data['donchian_high_20'].iloc[i]
                low_val = data['donchian_low_20'].iloc[i]
                if pd.isna(close_val) or pd.isna(high_val) or pd.isna(low_val) or high_val == low_val:
                    price_position_values.append(np.nan)
                    channel_width_values.append(np.nan)
                else:
                    position = ((close_val - low_val) / (high_val - low_val)) * 100
                    price_position_values.append(position)
                    width_pct = ((high_val - low_val) / close_val) * 100
                    channel_width_values.append(width_pct)
            data['price_position'] = price_position_values
            data['channel_width_pct'] = channel_width_values
            return data
        except Exception as e:
            self.logger.warning(f"Additional indicators calculation failed: {e}")
            data['price_position'] = np.nan
            data['channel_width_pct'] = np.nan
            return data

    def get_stock_data(self, symbol, period="1y"):
        """Get stock data from Yahoo Finance"""
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period)

            if hist.empty:
                self.logger.warning(f"No data returned for {symbol}")
                return None

            # Reset index to get date as column
            hist.reset_index(inplace=True)
            hist['Date'] = pd.to_datetime(hist['Date']).dt.date

            # Rename columns to match database schema
            hist.rename(columns={
                'Date': 'date',
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            }, inplace=True)

            # Calculate adjusted close
            hist['adj_close'] = hist['close']  # yfinance already provides adjusted data

            return hist[['date', 'open', 'high', 'low', 'close', 'adj_close', 'volume']]

        except Exception as e:
            self.logger.error(f"Failed to get data for {symbol}: {e}")
            return None

    def update_stock_prices(self, symbol, data):
        """Update stock prices in database"""
        try:
            # Get latest date in database for this symbol
            query = "SELECT MAX(date) as latest_date FROM stock_prices WHERE symbol = %s"
            result = db.execute_dict_query(query, (symbol,))
            latest_date = result[0]['latest_date'] if result else None

            # Filter for new data only
            if latest_date:
                new_data = data[data['date'] > latest_date]
            else:
                new_data = data

            if new_data.empty:
                self.logger.debug(f"No new price data for {symbol}")
                return 0

            # Prepare records for insertion
            records = []
            for _, row in new_data.iterrows():
                record = (
                    symbol,
                    row['date'],
                    self.safe_float(row['open']),
                    self.safe_float(row['high']),
                    self.safe_float(row['low']),
                    self.safe_float(row['close']),
                    self.safe_float(row['adj_close']),
                    int(row['volume']) if pd.notna(row['volume']) else 0,
                    datetime.now(),
                    datetime.now()
                )
                records.append(record)

            # Insert records
            if records:
                insert_query = """
                INSERT INTO stock_prices (
                    symbol, date, open, high, low, close, adj_close, volume, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    adj_close = EXCLUDED.adj_close,
                    volume = EXCLUDED.volume,
                    updated_at = EXCLUDED.updated_at
                """

                for record in records:
                    db.execute_insert(insert_query, record)

                self.logger.info(f"Successfully updated {len(records)} price records")
                return len(records)

            return 0

        except Exception as e:
            self.logger.error(f"Failed to update prices for {symbol}: {e}")
            raise

    def update_technical_indicators(self, symbol, data):
        """Update technical indicators - FIXED VERSION"""
        try:
            if len(data) < 50:  # Need enough data for calculations
                self.logger.warning(f"Insufficient data for {symbol} technical indicators ({len(data)} days)")
                return 0

            # Calculate all indicators using FIXED methods
            data = self.calculate_donchian_channels(data)
            data = self.calculate_rsi(data)
            data = self.calculate_sma(data)
            data = self.calculate_volume_indicators(data)
            data = self.calculate_additional_indicators(data)

            # Get latest technical indicators date for this symbol
            query = "SELECT MAX(date) as latest_date FROM technical_indicators WHERE symbol = %s"
            result = db.execute_dict_query(query, (symbol,))
            latest_ti_date = result[0]['latest_date'] if result else None

            # More lenient filtering - update last 30 days to ensure freshness
            cutoff_date = datetime.now().date() - timedelta(days=30)

            if latest_ti_date and latest_ti_date > cutoff_date:
                # Update from latest indicators date
                new_indicator_data = data[data['date'] > latest_ti_date]
            else:
                # Update all recent data if no recent indicators
                new_indicator_data = data[data['date'] >= cutoff_date]

            if new_indicator_data.empty:
                self.logger.debug(f"No new technical indicator data for {symbol}")
                return 0

            # Insert technical indicators
            records_inserted = 0
            for _, row in new_indicator_data.iterrows():
                # Skip rows with insufficient indicator data
                if pd.isna(row.get('donchian_high_20')) and pd.isna(row.get('rsi_14')):
                    continue

                record = (
                    symbol,
                    row['date'],
                    self.safe_float(row.get('sma_10')),
                    self.safe_float(row.get('sma_20')),
                    self.safe_float(row.get('sma_50')),
                    self.safe_float(row.get('rsi_14')),
                    None,  # macd
                    None,  # macd_signal
                    None,  # macd_histogram
                    self.safe_float(row.get('donchian_high_20')),
                    self.safe_float(row.get('donchian_low_20')),
                    None,  # atr_14
                    self.safe_float(row.get('donchian_mid_20')),
                    self.safe_float(row.get('volume_sma_10')),
                    self.safe_float(row.get('volume_ratio')),
                    self.safe_float(row.get('price_position')),
                    self.safe_float(row.get('channel_width_pct')),
                    datetime.now(),
                    datetime.now()
                )

                insert_query = """
                INSERT INTO technical_indicators (
                    symbol, date, sma_10, sma_20, sma_50, rsi_14,
                    macd, macd_signal, macd_histogram, donchian_high_20, donchian_low_20,
                    atr_14, donchian_mid_20, volume_sma_10, volume_ratio,
                    price_position, channel_width_pct, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date) DO UPDATE SET
                    sma_10 = EXCLUDED.sma_10,
                    sma_20 = EXCLUDED.sma_20,
                    sma_50 = EXCLUDED.sma_50,
                    rsi_14 = EXCLUDED.rsi_14,
                    donchian_high_20 = EXCLUDED.donchian_high_20,
                    donchian_low_20 = EXCLUDED.donchian_low_20,
                    donchian_mid_20 = EXCLUDED.donchian_mid_20,
                    volume_sma_10 = EXCLUDED.volume_sma_10,
                    volume_ratio = EXCLUDED.volume_ratio,
                    price_position = EXCLUDED.price_position,
                    channel_width_pct = EXCLUDED.channel_width_pct,
                    updated_at = EXCLUDED.updated_at
                """

                try:
                    db.execute_insert(insert_query, record)
                    records_inserted += 1
                except Exception as e:
                    self.logger.warning(f"Failed to insert indicator for {symbol} on {row['date']}: {e}")
                    continue

            if records_inserted > 0:
                self.logger.info(f"Successfully updated {records_inserted} technical indicator records")

            return records_inserted

        except Exception as e:
            self.logger.error(f"Failed to update technical indicators for {symbol}: {e}")
            return 0

    @retry_on_failure(max_retries=3)
    def update_symbol(self, symbol):
        """Update single symbol - prices and technical indicators"""
        try:
            self.logger.info(f"Processing {symbol}")

            # Get stock data
            data = self.get_stock_data(symbol)
            if data is None or data.empty:
                raise Exception(f"No data available for {symbol}")

            # Update stock prices
            price_records = self.update_stock_prices(symbol, data)

            # Update technical indicators
            indicator_records = self.update_technical_indicators(symbol, data)

            self.logger.info(f"✅ Successfully updated {symbol}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update {symbol}: {e}")
            return False

    def get_symbols_to_update(self, limit=None, test_symbols=None):
        """Get list of symbols to update"""
        if test_symbols:
            return test_symbols

        query = "SELECT DISTINCT symbol FROM stock_prices ORDER BY symbol"
        if limit:
            query += f" LIMIT {limit}"

        result = db.execute_dict_query(query)
        return [row['symbol'] for row in result]

    def run_update(self, limit=None, test_symbols=None):
        """Run the daily update process"""
        try:
            self.stats['start_time'] = datetime.now()

            if test_symbols:
                self.logger.info(f"🧪 Running test update with symbols: {test_symbols}")

            self.logger.info(f"🚀 Starting daily update at {self.stats['start_time']}")

            # Get symbols to update
            symbols = self.get_symbols_to_update(limit, test_symbols)
            self.stats['total_symbols'] = len(symbols)

            # Process symbols
            for i, symbol in enumerate(symbols, 1):
                self.logger.info(f"Processing {symbol} ({i}/{len(symbols)})")

                success = self.update_symbol(symbol)

                if success:
                    self.stats['successful_updates'] += 1
                else:
                    self.stats['failed_updates'] += 1

                # Rate limiting
                time.sleep(0.1)  # Avoid overwhelming Yahoo Finance

                # Progress reporting
                if i % 50 == 0:
                    progress = (i / len(symbols)) * 100
                    self.logger.info(f"Progress: {progress:.1f}% ({i}/{len(symbols)})")

            # Final report
            duration = datetime.now() - self.stats['start_time']
            success_rate = (self.stats['successful_updates'] / self.stats['total_symbols']) * 100

            self.logger.info(f"""
        🎉 Daily update completed in {duration}:
        ✅ Successful updates: {self.stats['successful_updates']}
        ❌ Failed updates: {self.stats['failed_updates']}
        📊 Total symbols: {self.stats['total_symbols']}
        📈 Success rate: {success_rate:.1f}%""")

            return True

        except Exception as e:
            self.logger.error(f"Daily update failed: {e}")
            return False


def main():
    """Main execution function"""
    import argparse

    parser = argparse.ArgumentParser(description='Enhanced Daily Data Updater')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to update')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--config-test', action='store_true', help='Test configuration only')

    args = parser.parse_args()

    # Setup logging
    setup_logging(level='INFO')

    if args.config_test:
        print("🔧 Testing configuration and database connection...")

        # Test configuration
        validation = config.validate_config()
        print(f"Configuration validation: {validation}")

        # Test database
        health = db.get_health_status()
        if health.get('connected'):
            print(f"✅ Database connection successful")
            print(f"   Database: {health.get('database')}")
            print(f"   Records: {health.get('record_counts', {}).get('stock_prices', 0):,}")
        else:
            print(f"❌ Database connection failed: {health.get('error')}")

        return

    # Run updater
    updater = DailyDataUpdater()

    if args.test:
        # Test mode
        success = updater.run_update(test_symbols=args.test)
        if success:
            print("✅ Test completed successfully")
        else:
            print("❌ Test failed")
    else:
        # Normal update
        success = updater.run_update(limit=args.limit)
        if success:
            print("✅ Daily update completed successfully")
        else:
            print("❌ Daily update failed")


if __name__ == "__main__":
    main()