#!/usr/bin/env python3
"""
Enhanced Daily Data Updater - ALL BUGS FIXED
Key Fixes:
1. Correct RSI calculation with EMA
2. ATR calculation implemented
3. Efficient incremental data retrieval
4. Bulk database operations
5. Proper concurrent processing
6. Better error handling and data quality
"""

import yfinance as yf
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta
import warnings
import hashlib
import json
from pathlib import Path
import asyncio
import concurrent.futures
from typing import List, Optional, Dict, Tuple

warnings.filterwarnings('ignore')

# Import shared infrastructure
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared import (
    config, db, setup_logging, retry_on_failure,
    performance_monitor, file_utils
)


class EnhancedDailyDataUpdater:
    """Enhanced daily data updater with all bugs fixed and performance optimized"""

    def __init__(self):
        """Initialize with shared infrastructure"""
        self.logger = setup_logging(__name__)
        self.logger.info("Enhanced Daily Data Updater initialized - ALL BUGS FIXED")

        # Configuration with better defaults
        self.batch_size = getattr(config, 'DATA_UPDATE_BATCH_SIZE', 100)  # Reduced for efficiency
        self.max_concurrent = getattr(config, 'MAX_CONCURRENT_UPDATES', 3)  # Actually used now
        self.rate_limit_delay = getattr(config, 'API_RATE_LIMIT_DELAY', 0.5)  # More conservative

        self.logger.info(
            f"Config - Batch: {self.batch_size}, Concurrent: {self.max_concurrent}, Rate limit: {self.rate_limit_delay}s")

        # Performance tracking
        self.stats = {
            'successful_updates': 0,
            'failed_updates': 0,
            'total_symbols': 0,
            'start_time': None,
            'api_calls': 0,
            'db_operations': 0,
            'cache_hits': 0
        }

        # Cache for technical indicator calculations
        self.indicator_cache = {}

    def safe_float(self, value):
        """Safely convert to float with better handling including Decimal types"""
        if value is None:
            return None
        try:
            # Handle Decimal types from PostgreSQL
            import decimal
            if isinstance(value, decimal.Decimal):
                result = float(value)
            else:
                result = float(value)

            if np.isnan(result) or np.isinf(result):
                return None
            return result
        except (ValueError, TypeError, decimal.InvalidOperation):
            return None

    def calculate_donchian_channels(self, data, period=20):
        """Calculate Donchian channels - UNCHANGED (working correctly)"""
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

    def calculate_rsi_fixed(self, data, period=14):
        """
        FIXED RSI calculation using proper EMA method
        FIX: Uses EMA instead of SMA for accurate RSI values
        """
        try:
            data = data.sort_values('date').copy()
            delta = data['close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)

            rsi_values = []
            avg_gain = None
            avg_loss = None

            for i in range(len(data)):
                if i < period:
                    # Not enough data for RSI
                    rsi_values.append(np.nan)
                elif i == period:
                    # First RSI calculation - use SMA
                    avg_gain = gain.iloc[1:i + 1].mean()  # Skip first NaN from diff()
                    avg_loss = loss.iloc[1:i + 1].mean()

                    if avg_loss == 0:
                        rsi_values.append(100)  # Maximum RSI when no losses
                    else:
                        rs = avg_gain / avg_loss
                        rsi = 100 - (100 / (1 + rs))
                        rsi_values.append(rsi)
                else:
                    # Subsequent RSI calculations - use EMA (Wilder's smoothing)
                    current_gain = gain.iloc[i]
                    current_loss = loss.iloc[i]

                    # Wilder's smoothing: α = 1/period
                    alpha = 1.0 / period
                    avg_gain = alpha * current_gain + (1 - alpha) * avg_gain
                    avg_loss = alpha * current_loss + (1 - alpha) * avg_loss

                    if avg_loss == 0:
                        rsi_values.append(100)
                    else:
                        rs = avg_gain / avg_loss
                        rsi = 100 - (100 / (1 + rs))
                        rsi_values.append(rsi)

            data['rsi_14'] = rsi_values
            return data
        except Exception as e:
            self.logger.warning(f"Fixed RSI calculation failed: {e}")
            data['rsi_14'] = np.nan
            return data

    def calculate_atr(self, data, period=14):
        """
        NEWLY IMPLEMENTED: ATR (Average True Range) calculation
        FIX: ATR was missing but used in screener for stop-loss calculations
        """
        try:
            data = data.sort_values('date').copy()

            # Calculate True Range components
            data['high_low'] = data['high'] - data['low']
            data['high_close_prev'] = abs(data['high'] - data['close'].shift(1))
            data['low_close_prev'] = abs(data['low'] - data['close'].shift(1))

            # True Range is the maximum of the three components
            data['true_range'] = data[['high_low', 'high_close_prev', 'low_close_prev']].max(axis=1)

            # Calculate ATR using Wilder's smoothing (same as RSI)
            atr_values = []
            atr = None

            for i in range(len(data)):
                if i < period:
                    atr_values.append(np.nan)
                elif i == period:
                    # First ATR - use SMA
                    atr = data['true_range'].iloc[1:i + 1].mean()  # Skip first NaN
                    atr_values.append(atr)
                else:
                    # Subsequent ATR - use Wilder's smoothing
                    current_tr = data['true_range'].iloc[i]
                    if not pd.isna(current_tr) and atr is not None:
                        alpha = 1.0 / period
                        atr = alpha * current_tr + (1 - alpha) * atr
                    atr_values.append(atr)

            data['atr_14'] = atr_values

            # Clean up temporary columns
            data.drop(['high_low', 'high_close_prev', 'low_close_prev', 'true_range'], axis=1, inplace=True)

            return data
        except Exception as e:
            self.logger.warning(f"ATR calculation failed: {e}")
            data['atr_14'] = np.nan
            return data

    def calculate_sma(self, data):
        """Calculate SMA - UNCHANGED (working correctly)"""
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

    def calculate_volume_indicators_fixed(self, data):
        """
        FIXED volume indicators calculation
        FIX: Properly handles missing volume data (None instead of 1.0)
        """
        try:
            data = data.sort_values('date').copy()
            data['volume_sma_10'] = data['volume'].rolling(window=10, min_periods=10).mean()

            volume_ratio_values = []
            for i in range(len(data)):
                current_volume = data['volume'].iloc[i]
                avg_volume = data['volume_sma_10'].iloc[i]

                if pd.isna(current_volume) or pd.isna(avg_volume) or avg_volume == 0:
                    volume_ratio_values.append(None)  # FIX: None instead of 1.0
                else:
                    ratio = current_volume / avg_volume
                    volume_ratio_values.append(ratio)

            data['volume_ratio'] = volume_ratio_values
            return data
        except Exception as e:
            self.logger.warning(f"Volume indicators calculation failed: {e}")
            data['volume_ratio'] = None
            data['volume_sma_10'] = np.nan
            return data

    def calculate_additional_indicators(self, data):
        """Calculate additional indicators - UNCHANGED (working correctly)"""
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

    def get_latest_date_for_symbol(self, symbol: str) -> Optional[datetime]:
        """Get the latest date we have data for a symbol"""
        try:
            query = "SELECT MAX(date) as latest_date FROM stock_prices WHERE symbol = %s"
            result = db.execute_dict_query(query, (symbol,))
            return result[0]['latest_date'] if result and result[0]['latest_date'] else None
        except Exception as e:
            self.logger.warning(f"Error getting latest date for {symbol}: {e}")
            return None

    def get_efficient_stock_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        ENHANCED: Get only necessary stock data (incremental updates)
        FIX: Only downloads recent data instead of full year for existing symbols
        """
        try:
            self.stats['api_calls'] += 1

            # Get latest date we have data for
            latest_date = self.get_latest_date_for_symbol(symbol)

            if latest_date:
                # Calculate days since last update
                days_since_update = (datetime.now().date() - latest_date).days

                if days_since_update <= 1:
                    self.logger.debug(f"{symbol}: Data is current (last update: {latest_date})")
                    return None  # No update needed

                # Get minimal period for incremental update
                if days_since_update <= 5:
                    period = "5d"
                elif days_since_update <= 30:
                    period = "1mo"
                elif days_since_update <= 90:
                    period = "3mo"
                else:
                    period = "1y"  # Full refresh for very stale data

                self.logger.debug(
                    f"{symbol}: Incremental update with {period} period ({days_since_update} days behind)")
            else:
                # New symbol - get full year
                period = "1y"
                self.logger.debug(f"{symbol}: New symbol, getting full year of data")

            # Get data from Yahoo Finance with retry logic
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period)

            if hist.empty:
                self.logger.warning(f"No data returned for {symbol}")
                return None

            # Process data
            hist.reset_index(inplace=True)
            hist['Date'] = pd.to_datetime(hist['Date']).dt.date

            hist.rename(columns={
                'Date': 'date',
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            }, inplace=True)

            hist['adj_close'] = hist['close']  # yfinance already provides adjusted data

            # Filter to only new data if we have existing data
            if latest_date:
                hist = hist[hist['date'] > latest_date]

            if hist.empty:
                self.logger.debug(f"{symbol}: No new data available")
                return None

            return hist[['date', 'open', 'high', 'low', 'close', 'adj_close', 'volume']]

        except Exception as e:
            self.logger.error(f"Failed to get data for {symbol}: {e}")
            return None

    def bulk_update_stock_prices(self, symbol: str, data: pd.DataFrame) -> int:
        """
        ENHANCED: Bulk database operations for better performance
        FIX: Single bulk insert instead of individual record inserts
        """
        try:
            if data.empty:
                return 0

            self.stats['db_operations'] += 1

            # Prepare all records at once
            records = []
            for _, row in data.iterrows():
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

            if not records:
                return 0

            # Bulk insert with proper conflict handling
            insert_query = """
                INSERT INTO stock_prices (
                    symbol, date, open, high, low, close, adj_close, volume, created_at, updated_at
                ) VALUES %s
                ON CONFLICT (symbol, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    adj_close = EXCLUDED.adj_close,
                    volume = EXCLUDED.volume,
                    updated_at = EXCLUDED.updated_at
            """

            # Use psycopg2's execute_values for bulk insert
            import psycopg2.extras
            with db.get_sync_connection() as conn:
                try:

                    with conn.cursor() as cursor:
                        psycopg2.extras.execute_values(
                            cursor, insert_query, records,
                            template=None, page_size=100
                        )
                    conn.commit()

                    self.logger.info(f"✅ Bulk inserted {len(records)} price records for {symbol}")
                    return len(records)

                except Exception as e:
                    conn.rollback()  # Rollback within context manager scope
                    self.logger.error(f"Database error in bulk_update_stock_prices for {symbol}: {e}")
                    raise
        except Exception as e:
            self.logger.error(f"Failed to bulk update prices for {symbol}: {e}")
            raise


    def get_full_price_data_for_indicators(self, symbol: str, min_periods: int = 50) -> Optional[pd.DataFrame]:
        """
        Get sufficient historical data for technical indicator calculations
        ENHANCED: Only gets what's needed for calculations
        """
        try:
            # Get enough historical data for indicators (need 50+ days for all indicators)
            query = """
                SELECT date, open, high, low, close, volume
                FROM stock_prices
                WHERE symbol = %s
                ORDER BY date DESC
                LIMIT %s
            """

            # Get more data than minimum to ensure we have enough for all indicators
            limit = max(min_periods * 2, 100)

            result = db.execute_dict_query(query, (symbol, limit))

            if not result:
                return None

            df = pd.DataFrame(result)
            df = df.sort_values('date')  # Ensure chronological order

            df = self.convert_decimal_columns(df)

            return df

        except Exception as e:
            self.logger.error(f"Failed to get price data for indicators for {symbol}: {e}")
            return None

    # Helper
    def convert_decimal_columns(self, data: pd.DataFrame) -> pd.DataFrame:
        """Convert Decimal columns to float for calculations"""
        try:
            # Convert common decimal columns to float
            decimal_columns = ['open', 'high', 'low', 'close', 'volume']

            for col in decimal_columns:
                if col in data.columns:
                    data[col] = pd.to_numeric(data[col], errors='coerce').astype(float)

            return data
        except Exception as e:
            self.logger.warning(f"Failed to convert decimal columns: {e}")
            return data

    def bulk_update_technical_indicators(self, symbol: str, data: pd.DataFrame) -> int:
        """
        ENHANCED: Calculate and bulk insert technical indicators
        FIX: Includes ATR calculation and bulk database operations
        """
        try:
            if len(data) < 50:
                self.logger.warning(f"Insufficient data for {symbol} technical indicators ({len(data)} days)")
                return 0

            # Calculate ALL indicators using FIXED methods
            data = self.calculate_donchian_channels(data)
            data = self.calculate_rsi_fixed(data)  # FIXED RSI
            data = self.calculate_atr(data)  # NEW ATR calculation
            data = self.calculate_sma(data)
            data = self.calculate_volume_indicators_fixed(data)  # FIXED volume handling
            data = self.calculate_additional_indicators(data)

            # Get latest technical indicators date
            query = "SELECT MAX(date) as latest_date FROM technical_indicators WHERE symbol = %s"
            result = db.execute_dict_query(query, (symbol,))
            latest_ti_date = result[0]['latest_date'] if result and result[0]['latest_date'] else None

            # ENHANCED: Better date filtering logic
            if latest_ti_date:
                # Only update data newer than what we have
                new_indicator_data = data[data['date'] > latest_ti_date]
            else:
                # No existing indicators - calculate for all available data
                # But limit to recent data to avoid overload
                cutoff_date = datetime.now().date() - timedelta(days=90)
                new_indicator_data = data[data['date'] >= cutoff_date]

            if new_indicator_data.empty:
                self.logger.debug(f"No new technical indicator data for {symbol}")
                return 0

            # Prepare bulk insert data
            records = []
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
                    None,  # macd (future enhancement)
                    None,  # macd_signal
                    None,  # macd_histogram
                    self.safe_float(row.get('donchian_high_20')),
                    self.safe_float(row.get('donchian_low_20')),
                    self.safe_float(row.get('atr_14')),  # NOW CALCULATED
                    self.safe_float(row.get('donchian_mid_20')),
                    self.safe_float(row.get('volume_sma_10')),
                    self.safe_float(row.get('volume_ratio')),
                    self.safe_float(row.get('price_position')),
                    self.safe_float(row.get('channel_width_pct')),
                    datetime.now(),
                    datetime.now()
                )
                records.append(record)

            if not records:
                self.logger.debug(f"No valid technical indicator records for {symbol}")
                return 0

            # Bulk insert technical indicators
            insert_query = """
                INSERT INTO technical_indicators (
                    symbol, date, sma_10, sma_20, sma_50, rsi_14,
                    macd, macd_signal, macd_histogram, donchian_high_20, donchian_low_20,
                    atr_14, donchian_mid_20, volume_sma_10, volume_ratio,
                    price_position, channel_width_pct, created_at, updated_at
                ) VALUES %s
                ON CONFLICT (symbol, date) DO UPDATE SET
                    sma_10 = EXCLUDED.sma_10,
                    sma_20 = EXCLUDED.sma_20,
                    sma_50 = EXCLUDED.sma_50,
                    rsi_14 = EXCLUDED.rsi_14,
                    donchian_high_20 = EXCLUDED.donchian_high_20,
                    donchian_low_20 = EXCLUDED.donchian_low_20,
                    atr_14 = EXCLUDED.atr_14,
                    donchian_mid_20 = EXCLUDED.donchian_mid_20,
                    volume_sma_10 = EXCLUDED.volume_sma_10,
                    volume_ratio = EXCLUDED.volume_ratio,
                    price_position = EXCLUDED.price_position,
                    channel_width_pct = EXCLUDED.channel_width_pct,
                    updated_at = EXCLUDED.updated_at
            """

            import psycopg2.extras
            # conn = db.get_connection()

            with db.get_sync_connection() as conn:
                try:
                    with conn.cursor() as cursor:
                        psycopg2.extras.execute_values(
                            cursor, insert_query, records,
                            template=None, page_size=100
                        )
                    conn.commit()

                    self.logger.info(f"✅ Bulk inserted {len(records)} technical indicator records for {symbol}")
                    return len(records)

                except Exception as e:
                    conn.rollback()  # Rollback within context manager scope
                    self.logger.error(f"Database error in bulk_update_technical_indicators for {symbol}: {e}")
                    raise

        except Exception as e:
            self.logger.error(f"Failed to bulk update technical indicators for {symbol}: {e}")
            return 0


    @retry_on_failure(max_retries=3, delay=1.0)
    def update_symbol_enhanced(self, symbol: str) -> bool:
        """
        ENHANCED: Update single symbol with better error handling and efficiency
        """
        try:
            self.logger.debug(f"Processing {symbol}")

            # ENHANCED: Get only necessary stock data
            new_price_data = self.get_efficient_stock_data(symbol)

            if new_price_data is None:
                # No new data needed
                self.stats['cache_hits'] += 1
                return True

            if new_price_data.empty:
                self.logger.warning(f"No valid data for {symbol}")
                return False

            # ENHANCED: Bulk update stock prices
            price_records = self.bulk_update_stock_prices(symbol, new_price_data)

            # Get full data for technical indicators
            full_data = self.get_full_price_data_for_indicators(symbol)

            if full_data is not None and len(full_data) >= 50:
                # ENHANCED: Bulk update technical indicators with FIXED calculations
                indicator_records = self.bulk_update_technical_indicators(symbol, full_data)
            else:
                self.logger.warning(f"Insufficient data for {symbol} technical indicators")
                indicator_records = 0

            self.logger.info(f"✅ {symbol}: {price_records} prices, {indicator_records} indicators")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update {symbol}: {e}")
            return False

    async def update_symbols_concurrent(self, symbols: List[str]) -> Dict[str, bool]:
        """
        NEW: Concurrent processing implementation
        FIX: Actually uses the max_concurrent setting for parallel processing
        """
        results = {}

        # Create thread pool for concurrent execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            # Submit all tasks
            future_to_symbol = {
                executor.submit(self.update_symbol_with_rate_limit, symbol): symbol
                for symbol in symbols
            }

            # Process completed tasks
            for future in concurrent.futures.as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    success = future.result()
                    results[symbol] = success

                    if success:
                        self.stats['successful_updates'] += 1
                    else:
                        self.stats['failed_updates'] += 1

                except Exception as e:
                    self.logger.error(f"Concurrent update failed for {symbol}: {e}")
                    results[symbol] = False
                    self.stats['failed_updates'] += 1

        return results

    def update_symbol_with_rate_limit(self, symbol: str) -> bool:
        """Update symbol with proper rate limiting"""
        try:
            # Rate limiting - ENHANCED from 0.1s to configurable
            time.sleep(self.rate_limit_delay)

            return self.update_symbol_enhanced(symbol)

        except Exception as e:
            self.logger.error(f"Rate-limited update failed for {symbol}: {e}")
            return False

    def get_symbols_to_update(self, limit: Optional[int] = None, test_symbols: Optional[List[str]] = None) -> List[str]:
        """Get list of symbols to update"""
        if test_symbols:
            return test_symbols

        query = "SELECT DISTINCT symbol FROM stock_prices ORDER BY symbol"
        if limit:
            query += f" LIMIT {limit}"

        result = db.execute_dict_query(query)
        return [row['symbol'] for row in result]

    # Enhanced reporting methods (keeping existing ones but with improvements)
    def generate_enhanced_lineage_report(self):
        """Enhanced data lineage report with new metrics"""
        try:
            # Original freshness analysis
            freshness_query = """
                SELECT 
                    'stock_prices' as table_name,
                    COUNT(*) as total_records,
                    MAX(date) as latest_date,
                    MIN(date) as earliest_date,
                    COUNT(DISTINCT symbol) as unique_symbols
                FROM stock_prices
                UNION ALL
                SELECT 
                    'technical_indicators' as table_name,
                    COUNT(*) as total_records,
                    MAX(date) as latest_date,
                    MIN(date) as earliest_date,
                    COUNT(DISTINCT symbol) as unique_symbols
                FROM technical_indicators
            """

            freshness_results = db.execute_dict_query(freshness_query)

            # Enhanced quality checks
            quality_checks = {
                "rsi_range_validation": self.validate_rsi_ranges(),
                "atr_availability": self.validate_atr_availability(),  # NEW
                "price_consistency": self.validate_price_consistency(),
                "volume_sanity": self.validate_volume_data_enhanced(),  # ENHANCED
                "missing_data_analysis": self.analyze_missing_data(),
                "indicator_accuracy": self.validate_indicator_accuracy()  # NEW
            }

            # Performance metrics
            performance_metrics = {
                "api_calls_made": self.stats.get('api_calls', 0),
                "db_operations": self.stats.get('db_operations', 0),
                "cache_hit_rate": (self.stats.get('cache_hits', 0) / max(1, self.stats.get('total_symbols', 1))) * 100,
                "avg_processing_time": self.calculate_avg_processing_time()
            }

            # Generate data hash
            data_hash = self.calculate_data_hash()

            # Create comprehensive report
            lineage_report = {
                "report_timestamp": datetime.now().isoformat(),
                "data_pipeline_version": "enhanced_daily_updater_v3.0",
                "processing_stats": self.stats,
                "performance_metrics": performance_metrics,
                "data_freshness": freshness_results,
                "data_quality_checks": quality_checks,
                "data_hash": data_hash,
                "symbols_processed": self.stats.get('total_symbols', 0),
                "success_rate": (self.stats.get('successful_updates', 0) / max(1, self.stats.get('total_symbols',
                                                                                                 1))) * 100,
                "enhancements_applied": [
                    "Fixed RSI calculation with proper EMA",
                    "Added ATR calculation",
                    "Implemented bulk database operations",
                    "Added concurrent processing",
                    "Enhanced error handling",
                    "Improved data efficiency"
                ]
            }

            frontend_data_path = file_utils.get_frontend_data_path()
            # Save report
            report_path = frontend_data_path / f"enhanced_lineage_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            report_path.parent.mkdir(exist_ok=True)

            with open(report_path, 'w') as f:
                json.dump(lineage_report, f, indent=2, default=str)

            # Also save latest version
            latest_path = frontend_data_path / "latest_data_lineage.json"
            with open(latest_path, 'w') as f:
                json.dump(lineage_report, f, indent=2, default=str)

            self.logger.info(f"Enhanced data lineage report saved: {report_path}")
            return lineage_report

        except Exception as e:
            self.logger.error(f"Failed to generate enhanced lineage report: {e}")
            return None

    def validate_atr_availability(self):
        """NEW: Validate ATR calculation is working"""
        try:
            query = """
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(CASE WHEN atr_14 IS NOT NULL AND atr_14 > 0 THEN 1 END) as valid_atr,
                    COUNT(CASE WHEN atr_14 IS NULL THEN 1 END) as null_atr,
                    AVG(atr_14) as avg_atr
                FROM technical_indicators 
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            """
            result = db.execute_dict_query(query)[0]

            atr_score = (result['valid_atr'] / max(1, result['total_records'])) * 100

            return {
                "atr_availability_score": round(atr_score, 2),
                "total_records": result['total_records'],
                "valid_atr_records": result['valid_atr'],
                "null_atr_records": result['null_atr'],
                "average_atr": round(float(result['avg_atr']), 4) if result['avg_atr'] else 0,
                "passed": atr_score >= 90
            }
        except Exception as e:
            return {"atr_availability_score": 0, "passed": False, "error": str(e)}

    def validate_volume_data_enhanced(self):
        """ENHANCED: Better volume validation"""
        try:
            query = """
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(CASE WHEN volume > 0 THEN 1 END) as positive_volume,
                    COUNT(CASE WHEN volume IS NULL THEN 1 END) as null_volume,
                    COUNT(CASE WHEN volume = 0 THEN 1 END) as zero_volume,
                    AVG(volume) as avg_volume,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY volume) as median_volume
                FROM stock_prices 
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            """

            result = db.execute_dict_query(query)[0]

            volume_score = (result['positive_volume'] / max(1, result['total_records'])) * 100

            return {
                "volume_sanity_score": round(volume_score, 2),
                "total_records": result['total_records'],
                "positive_volume_records": result['positive_volume'],
                "null_volume_records": result['null_volume'],
                "zero_volume_records": result['zero_volume'],
                "average_volume": int(result['avg_volume']) if result['avg_volume'] else 0,
                "median_volume": int(result['median_volume']) if result['median_volume'] else 0,
                "passed": volume_score >= 95
            }
        except Exception as e:
            return {"volume_sanity_score": 0, "passed": False, "error": str(e)}

    def validate_price_consistency(self):
        """Validate price data consistency (OHLC relationships)"""
        try:
            query = """
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(CASE WHEN high >= low AND high >= open AND high >= close THEN 1 END) as valid_high,
                    COUNT(CASE WHEN low <= high AND low <= open AND low <= close THEN 1 END) as valid_low,
                    COUNT(CASE WHEN open > 0 AND close > 0 AND high > 0 AND low > 0 THEN 1 END) as positive_prices
                FROM stock_prices 
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            """
            result = db.execute_dict_query(query)[0]

            consistency_score = (
                                        (result['valid_high'] + result['valid_low'] + result['positive_prices']) /
                                        (result['total_records'] * 3)
                                ) * 100

            return {
                "consistency_score": round(consistency_score, 2),
                "total_records": result['total_records'],
                "valid_high_records": result['valid_high'],
                "valid_low_records": result['valid_low'],
                "positive_price_records": result['positive_prices'],
                "passed": consistency_score >= 95
            }
        except Exception as e:
            return {"consistency_score": 0, "passed": False, "error": str(e)}

    def analyze_missing_data(self):
        """Analyze missing data patterns"""
        try:
            query = """
                SELECT 
                    symbol,
                    COUNT(*) as total_days,
                    COUNT(CASE WHEN volume IS NULL OR volume = 0 THEN 1 END) as missing_volume,
                    MAX(date) as latest_date,
                    MIN(date) as earliest_date
                FROM stock_prices
                WHERE date >= CURRENT_DATE - INTERVAL '90 days'
                GROUP BY symbol
                HAVING COUNT(CASE WHEN volume IS NULL OR volume = 0 THEN 1 END) > 0
                ORDER BY missing_volume DESC
                LIMIT 10
            """

            missing_data = db.execute_dict_query(query)

            # Overall missing data summary
            summary_query = """
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(CASE WHEN volume IS NULL OR volume = 0 THEN 1 END) as missing_volume_records,
                    COUNT(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL THEN 1 END) as missing_price_records
                FROM stock_prices
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            """

            summary = db.execute_dict_query(summary_query)[0]

            missing_percentage = (summary['missing_volume_records'] / max(1, summary['total_records'])) * 100

            return {
                "missing_data_percentage": round(missing_percentage, 2),
                "total_records_analyzed": summary['total_records'],
                "missing_volume_records": summary['missing_volume_records'],
                "missing_price_records": summary['missing_price_records'],
                "symbols_with_missing_data": len(missing_data),
                "top_missing_symbols": missing_data[:5],
                "passed": missing_percentage < 5
            }

        except Exception as e:
            return {"missing_data_percentage": 0, "passed": False, "error": str(e)}

    def calculate_data_hash(self):
        """Calculate hash of recent data for integrity checking"""
        try:
            query = """
                SELECT 
                    symbol,
                    date,
                    ROUND(close::numeric, 2) as close,
                    volume
                FROM stock_prices
                WHERE date >= CURRENT_DATE - INTERVAL '7 days'
                ORDER BY symbol, date
                LIMIT 1000
            """

            result = db.execute_dict_query(query)

            # Create a simple hash of the data
            import hashlib
            data_string = ""
            for row in result:
                data_string += f"{row['symbol']}{row['date']}{row['close']}{row['volume']}"

            data_hash = hashlib.md5(data_string.encode()).hexdigest()

            return {
                "hash": data_hash,
                "records_hashed": len(result),
                "hash_date": datetime.now().isoformat()
            }

        except Exception as e:
            return {"hash": "error", "error": str(e)}

    def validate_indicator_accuracy(self):
        """NEW: Validate technical indicator calculations"""
        try:
            query = """
                SELECT 
                    COUNT(*) as total_records,
                    COUNT(CASE WHEN rsi_14 BETWEEN 0 AND 100 THEN 1 END) as valid_rsi,
                    COUNT(CASE WHEN atr_14 > 0 THEN 1 END) as valid_atr,
                    COUNT(CASE WHEN donchian_high_20 >= donchian_low_20 THEN 1 END) as valid_donchian,
                    AVG(rsi_14) as avg_rsi
                FROM technical_indicators 
                WHERE date >= CURRENT_DATE - INTERVAL '7 days'
                AND rsi_14 IS NOT NULL
            """
            result = db.execute_dict_query(query)[0]

            if result['total_records'] == 0:
                return {"accuracy_score": 0, "passed": False, "error": "No recent indicator data"}

            # Calculate accuracy score
            accuracy_score = (
                                     (result['valid_rsi'] + result['valid_atr'] + result['valid_donchian']) /
                                     (result['total_records'] * 3)
                             ) * 100

            return {
                "accuracy_score": round(accuracy_score, 2),
                "total_records": result['total_records'],
                "valid_rsi": result['valid_rsi'],
                "valid_atr": result['valid_atr'],
                "valid_donchian": result['valid_donchian'],
                "average_rsi": round(float(result['avg_rsi']), 2) if result['avg_rsi'] else 0,
                "passed": accuracy_score >= 95
            }
        except Exception as e:
            return {"accuracy_score": 0, "passed": False, "error": str(e)}

    def calculate_avg_processing_time(self):
        """Calculate average processing time per symbol"""
        if self.stats.get('start_time') and self.stats.get('total_symbols', 0) > 0:
            duration = datetime.now() - self.stats['start_time']
            return round(duration.total_seconds() / self.stats['total_symbols'], 2)
        return 0

    # Keep existing methods for compatibility
    def validate_rsi_ranges(self):
        """Existing RSI validation - now validates FIXED RSI"""
        try:
            query = """
                SELECT 
                    COUNT(*) as total_rsi,
                    COUNT(CASE WHEN rsi_14 BETWEEN 0 AND 100 THEN 1 END) as valid_rsi,
                    COUNT(CASE WHEN rsi_14 IS NULL THEN 1 END) as null_rsi,
                    MIN(rsi_14) as min_rsi,
                    MAX(rsi_14) as max_rsi,
                    AVG(rsi_14) as avg_rsi
                FROM technical_indicators 
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            """
            result = db.execute_dict_query(query)[0]

            validation_score = (result['valid_rsi'] / max(1, result['total_rsi'])) * 100

            return {
                "validation_score": round(validation_score, 2),
                "total_records": result['total_rsi'],
                "valid_records": result['valid_rsi'],
                "null_records": result['null_rsi'],
                "range": f"{result['min_rsi']} - {result['max_rsi']}",
                "average_rsi": round(float(result['avg_rsi']), 2) if result['avg_rsi'] else 0,
                "passed": validation_score >= 95
            }
        except Exception as e:
            return {"validation_score": 0, "passed": False, "error": str(e)}

    # Keep other existing methods (validate_price_consistency, analyze_missing_data, etc.)
    # for compatibility...

    async def run_enhanced_update(self, limit: Optional[int] = None, test_symbols: Optional[List[str]] = None):
        """
        ENHANCED: Run the daily update with all improvements
        """
        try:
            self.stats['start_time'] = datetime.now()

            if test_symbols:
                self.logger.info(f"🧪 Running enhanced test update with symbols: {test_symbols}")

            self.logger.info(f"🚀 Starting ENHANCED daily update at {self.stats['start_time']}")
            self.logger.info(f"💡 Enhancements: Fixed RSI, Added ATR, Bulk Operations, Concurrent Processing")

            # Get symbols to update
            symbols = self.get_symbols_to_update(limit, test_symbols)
            self.stats['total_symbols'] = len(symbols)

            if not symbols:
                self.logger.warning("No symbols to update")
                return False

            # Process symbols with concurrent execution
            self.logger.info(f"Processing {len(symbols)} symbols with {self.max_concurrent} concurrent workers...")

            # Split symbols into batches for better memory management
            batch_size = self.batch_size
            results_summary = {}

            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(symbols) + batch_size - 1) // batch_size

                self.logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} symbols)")

                # Process batch concurrently
                batch_results = await self.update_symbols_concurrent(batch)
                results_summary.update(batch_results)

                # Progress reporting
                progress = ((i + len(batch)) / len(symbols)) * 100
                successful_in_batch = sum(1 for success in batch_results.values() if success)
                self.logger.info(
                    f"Batch {batch_num} completed: {successful_in_batch}/{len(batch)} successful, {progress:.1f}% total progress")

            # Generate enhanced lineage report
            self.logger.info("Generating enhanced data lineage report...")
            lineage_report = self.generate_enhanced_lineage_report()

            # Final report
            duration = datetime.now() - self.stats['start_time']
            success_rate = (self.stats['successful_updates'] / self.stats['total_symbols']) * 100

            self.logger.info(f"""
🎉 ENHANCED Daily update completed in {duration}:
✅ Successful updates: {self.stats['successful_updates']}
❌ Failed updates: {self.stats['failed_updates']}
📊 Total symbols: {self.stats['total_symbols']}
📈 Success rate: {success_rate:.1f}%
🚀 API calls: {self.stats['api_calls']}
💾 DB operations: {self.stats['db_operations']}
⚡ Cache hit rate: {(self.stats['cache_hits'] / max(1, self.stats['total_symbols'])) * 100:.1f}%

🔧 ENHANCEMENTS APPLIED:
   • Fixed RSI calculation with proper EMA
   • Added ATR (Average True Range) calculation
   • Implemented bulk database operations
   • Added concurrent processing ({self.max_concurrent} workers)
   • Enhanced error handling and data validation
   • Improved data retrieval efficiency
            """)

            return True

        except Exception as e:
            self.logger.error(f"Enhanced daily update failed: {e}")
            return False


def main():
    """Main execution function"""
    import argparse

    parser = argparse.ArgumentParser(description='Enhanced Daily Data Updater - ALL BUGS FIXED')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to update')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--config-test', action='store_true', help='Test configuration only')

    args = parser.parse_args()

    # Setup logging
    setup_logging(level='INFO')

    if args.config_test:
        print("🔧 Testing enhanced configuration and database connection...")

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

    # Run enhanced updater
    updater = EnhancedDailyDataUpdater()

    async def run_update():
        if args.test:
            # Test mode
            success = await updater.run_enhanced_update(test_symbols=args.test)
            if success:
                print("✅ Enhanced test completed successfully")
            else:
                print("❌ Enhanced test failed")
        else:
            # Normal update
            success = await updater.run_enhanced_update(limit=args.limit)
            if success:
                print("✅ Enhanced daily update completed successfully")
            else:
                print("❌ Enhanced daily update failed")

    # Run the async update
    import asyncio
    asyncio.run(run_update())


if __name__ == "__main__":
    main()