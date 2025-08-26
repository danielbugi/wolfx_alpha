# automation/ml_generators/historical_breakouts_generator.py
"""
Refactored historical breakout data generator using shared infrastructure
Generates high-quality training data for ML models with enhanced features
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import warnings

# Import shared infrastructure
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from shared import (
    config, db, setup_logging, retry_on_failure,
    timing_decorator, data_validation, format_number,
    calculate_percentage_change, safe_divide
)

warnings.filterwarnings('ignore')

# Setup logging for this module
logger = setup_logging("historical_breakouts_generator")


class HistoricalBreakoutGenerator:
    """Enhanced historical breakout data generator for ML training"""

    def __init__(self):
        """Initialize the generator with shared configuration"""
        self.ml_config = config.get_ml_config()
        self.criteria = config.get_screening_criteria()

        logger.info("Historical Breakout Generator initialized with shared infrastructure")
        logger.info(f"ML config: {self.ml_config}")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_with_sufficient_data(self, min_days: int = None) -> List[str]:
        """Get symbols with sufficient historical data for analysis"""
        try:
            min_data_points = min_days or self.ml_config['min_data_points']

            query = """
            SELECT 
                symbol,
                COUNT(*) as data_points,
                MIN(date) as first_date,
                MAX(date) as last_date
            FROM stock_prices 
            WHERE volume > %s 
            AND close > %s
            GROUP BY symbol
            HAVING COUNT(*) >= %s
            AND MAX(date) >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY data_points DESC
            """

            results = db.execute_dict_query(query, (
                self.criteria['min_volume'],
                self.criteria['min_price'],
                min_data_points
            ))

            symbols = [row['symbol'] for row in results]
            logger.info(f"Found {len(symbols)} symbols with sufficient data (≥{min_data_points} days)")

            if results:
                avg_data_points = np.mean([row['data_points'] for row in results])
                logger.info(f"Average data points per symbol: {avg_data_points:.0f}")

            return symbols

        except Exception as e:
            logger.error(f"Error getting symbols with sufficient data: {e}")
            return []

    @retry_on_failure(max_retries=2, delay=0.5)
    def get_stock_data_with_indicators(self, symbol: str, min_days: int = None) -> Optional[pd.DataFrame]:
        """Get complete stock data with technical indicators for a symbol"""
        try:
            # Calculate required days (add buffer for indicators)
            required_days = (min_days or self.ml_config['min_data_points']) + 100

            query = """
            SELECT 
                p.date,
                p.symbol,
                p.open,
                p.high,
                p.low,
                p.close,
                p.volume,
                t.sma_10,
                t.sma_20,
                t.sma_50,
                t.rsi_14,
                t.macd,
                t.macd_signal,
                t.macd_histogram,
                t.bollinger_upper,
                t.bollinger_lower,
                t.donchian_high_20,
                t.donchian_low_20,
                t.donchian_mid_20,
                t.atr_14,
                t.volume_sma_10,
                t.volume_ratio
            FROM stock_prices p
            LEFT JOIN technical_indicators t ON p.symbol = t.symbol AND p.date = t.date
            WHERE p.symbol = %s
            AND p.volume > %s
            AND p.date >= CURRENT_DATE - INTERVAL '%s days'
            ORDER BY p.date
            """

            results = db.execute_dict_query(query, (symbol, self.criteria['min_volume'], required_days))

            if not results:
                return None

            # Convert to DataFrame
            df = pd.DataFrame(results)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)

            # Validate data quality
            if len(df) < self.ml_config['min_data_points']:
                logger.warning(f"Insufficient data for {symbol}: {len(df)} days")
                return None

            logger.debug(f"Retrieved {len(df)} days of data for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Error getting stock data for {symbol}: {e}")
            return None

    def identify_breakouts(self, data: pd.DataFrame) -> pd.DataFrame:
        """Identify historical Donchian breakouts with enhanced detection"""
        try:
            breakouts = []

            for i in range(1, len(data)):
                current = data.iloc[i]
                previous = data.iloc[i - 1]

                # Skip if missing Donchian data
                if (pd.isna(current['donchian_high_20']) or pd.isna(current['donchian_low_20']) or
                        pd.isna(previous['donchian_high_20']) or pd.isna(previous['donchian_low_20'])):
                    continue

                breakout_type = None
                breakout_strength = 0

                # Upward breakout detection
                if (current['close'] > current['donchian_high_20'] and
                        previous['close'] <= previous['donchian_high_20']):
                    breakout_type = 'bullish'

                    # Calculate breakout strength
                    if current['donchian_high_20'] > 0:
                        breakout_strength = (current['close'] - current['donchian_high_20']) / current[
                            'donchian_high_20'] * 100

                # Downward breakout detection
                elif (current['close'] < current['donchian_low_20'] and
                      previous['close'] >= previous['donchian_low_20']):
                    breakout_type = 'bearish'

                    # Calculate breakout strength
                    if current['donchian_low_20'] > 0:
                        breakout_strength = (current['donchian_low_20'] - current['close']) / current[
                            'donchian_low_20'] * 100

                if breakout_type:
                    breakout_row = current.copy()
                    breakout_row['breakout_type'] = breakout_type
                    breakout_row['breakout_index'] = i
                    breakout_row['breakout_strength'] = breakout_strength

                    # Add additional context from previous days
                    if i >= 5:
                        recent_data = data.iloc[i - 5:i]
                        breakout_row['avg_volume_5d'] = recent_data['volume'].mean()
                        breakout_row['price_trend_5d'] = calculate_percentage_change(
                            recent_data.iloc[0]['close'], recent_data.iloc[-1]['close']
                        )

                    breakouts.append(breakout_row)

            if breakouts:
                breakouts_df = pd.DataFrame(breakouts)
                logger.info(f"Identified {len(breakouts_df)} breakouts")
                return breakouts_df
            else:
                return pd.DataFrame()

        except Exception as e:
            logger.error(f"Error identifying breakouts: {e}")
            return pd.DataFrame()

    def calculate_enhanced_outcomes(self, data: pd.DataFrame, breakouts: pd.DataFrame) -> pd.DataFrame:
        """Calculate enhanced outcomes with additional ML features"""
        try:
            if breakouts.empty:
                return breakouts

            outcomes = []
            lookforward_days = self.ml_config['lookforward_days']

            for _, breakout in breakouts.iterrows():
                breakout_idx = breakout['breakout_index']
                breakout_price = breakout['close']
                breakout_date = breakout['date']

                # Get future data for outcome calculation
                future_data = data.iloc[breakout_idx + 1:breakout_idx + 1 + lookforward_days]

                if len(future_data) < 3:  # Need at least 3 days to calculate meaningful outcomes
                    continue

                # Calculate various outcome metrics
                returns = (future_data['close'] / breakout_price - 1) * 100
                max_gain = returns.max() if not returns.empty else 0
                max_loss = returns.min() if not returns.empty else 0

                # Calculate days to peak/trough
                if breakout['breakout_type'] == 'bullish':
                    peak_idx = returns.idxmax() if not returns.empty else None
                    days_to_peak = (peak_idx - breakout_idx) if peak_idx is not None else len(future_data)
                    success = max_gain > 2.0  # 2% gain threshold for bullish
                else:  # bearish
                    trough_idx = returns.idxmin() if not returns.empty else None
                    days_to_peak = (trough_idx - breakout_idx) if trough_idx is not None else len(future_data)
                    success = max_loss < -2.0  # 2% loss threshold for bearish

                # Calculate additional ML features
                # Volatility during outcome period
                volatility = returns.std() if len(returns) > 1 else 0

                # Price action classification
                final_return = returns.iloc[-1] if not returns.empty else 0

                # Volume confirmation during breakout
                volume_confirmation = breakout.get('volume_ratio', 1.0) > 1.5

                # Technical strength at breakout
                rsi_at_breakout = breakout.get('rsi_14', 50)
                price_vs_ma20 = safe_divide(breakout['close'], breakout.get('sma_20', breakout['close']), 1.0)
                price_vs_ma50 = safe_divide(breakout['close'], breakout.get('sma_50', breakout['close']), 1.0)

                outcome = {
                    'symbol': breakout['symbol'],
                    'date': breakout_date,
                    'breakout_type': breakout['breakout_type'],
                    'entry_price': breakout_price,
                    'volume_ratio': breakout.get('volume_ratio', 1.0),
                    'atr_pct': safe_divide(breakout.get('atr_14', 0), breakout_price, 0) * 100,
                    'rsi_value': rsi_at_breakout,
                    'price_change_pct': calculate_percentage_change(
                        data.iloc[breakout_idx - 1]['close'] if breakout_idx > 0 else breakout_price,
                        breakout_price
                    ),
                    'success': success,
                    'max_gain_10d': max_gain,
                    'max_loss_10d': abs(max_loss),
                    'days_to_peak': min(days_to_peak, lookforward_days),
                    'final_return_10d': final_return,
                    'volatility_10d': volatility,
                    'volume_confirmation': volume_confirmation,
                    'breakout_strength': breakout.get('breakout_strength', 0),
                    'price_vs_ma20': price_vs_ma20,
                    'price_vs_ma50': price_vs_ma50,
                    'rsi_14': rsi_at_breakout,
                    'tech_volume_ratio': breakout.get('volume_ratio', 1.0),
                    'macd_value': breakout.get('macd', 0),
                    'macd_signal': breakout.get('macd_signal', 0),
                    'price_position': safe_divide(
                        breakout['close'] - breakout.get('donchian_low_20', 0),
                        breakout.get('donchian_high_20', 1) - breakout.get('donchian_low_20', 0),
                        0.5
                    ) * 100,
                    'overall_quality_score': None,  # Will be linked later
                    'quality_grade': None,
                    'sector': None
                }

                outcomes.append(outcome)

            if outcomes:
                outcomes_df = pd.DataFrame(outcomes)
                logger.info(f"Calculated outcomes for {len(outcomes_df)} breakouts")

                # Log success rates
                if len(outcomes_df) > 0:
                    bullish_success_rate = (outcomes_df[
                                                (outcomes_df['breakout_type'] == 'bullish') & (outcomes_df['success'])
                                                ].shape[0] /
                                            outcomes_df[outcomes_df['breakout_type'] == 'bullish'].shape[0] * 100) if \
                    outcomes_df[outcomes_df['breakout_type'] == 'bullish'].shape[0] > 0 else 0

                    bearish_success_rate = (outcomes_df[
                                                (outcomes_df['breakout_type'] == 'bearish') & (outcomes_df['success'])
                                                ].shape[0] /
                                            outcomes_df[outcomes_df['breakout_type'] == 'bearish'].shape[0] * 100) if \
                    outcomes_df[outcomes_df['breakout_type'] == 'bearish'].shape[0] > 0 else 0

                    logger.debug(
                        f"Success rates - Bullish: {bullish_success_rate:.1f}%, Bearish: {bearish_success_rate:.1f}%")

                return outcomes_df
            else:
                return pd.DataFrame()

        except Exception as e:
            logger.error(f"Error calculating breakout outcomes: {e}")
            return pd.DataFrame()

    @timing_decorator()
    def link_fundamentals_data(self, breakouts: pd.DataFrame) -> pd.DataFrame:
        """Link breakouts with fundamentals data for enhanced ML features"""
        if breakouts.empty:
            return breakouts

        try:
            enhanced_breakouts = []

            # Group by symbol for efficient querying
            for symbol in breakouts['symbol'].unique():
                symbol_breakouts = breakouts[breakouts['symbol'] == symbol]

                # Get fundamentals for this symbol
                query = """
                    SELECT overall_quality_score, quality_grade, sector,
                           market_cap, pe_ratio, pb_ratio, beta, dividend_yield
                    FROM daily_fundamentals 
                    WHERE symbol = %s 
                    ORDER BY date DESC 
                    LIMIT 1
                """

                results = db.execute_dict_query(query, (symbol,))

                for _, breakout in symbol_breakouts.iterrows():
                    breakout_dict = breakout.to_dict()

                    if results:
                        fundamental = results[0]
                        breakout_dict.update({
                            'overall_quality_score': fundamental.get('overall_quality_score'),
                            'quality_grade': fundamental.get('quality_grade'),
                            'sector': fundamental.get('sector'),
                            'market_cap': fundamental.get('market_cap'),
                            'pe_ratio': fundamental.get('pe_ratio'),
                            'pb_ratio': fundamental.get('pb_ratio'),
                            'beta': fundamental.get('beta'),
                            'dividend_yield': fundamental.get('dividend_yield')
                        })

                    enhanced_breakouts.append(breakout_dict)

            if enhanced_breakouts:
                enhanced_df = pd.DataFrame(enhanced_breakouts)
                logger.info(f"Linked fundamentals for {len(enhanced_df)} breakouts")
                return enhanced_df
            else:
                return breakouts

        except Exception as e:
            logger.error(f"Error linking fundamentals: {e}")
            return breakouts

    @timing_decorator()
    def save_to_breakouts_table(self, breakouts: pd.DataFrame) -> bool:
        """Save breakouts to the breakouts table"""
        if breakouts.empty:
            return True

        try:
            records = []
            for _, row in breakouts.iterrows():
                record = (
                    row['symbol'],
                    row['date'],
                    row['breakout_type'],
                    float(row['entry_price']),
                    float(row.get('volume_ratio', 1.0)),
                    float(row.get('atr_pct', 0)),
                    float(row.get('rsi_value', 50)),
                    float(row.get('price_change_pct', 0)),
                    bool(row.get('success', False)),
                    float(row.get('max_gain_10d', 0)),
                    float(row.get('max_loss_10d', 0)),
                    int(row.get('days_to_peak', 10)),
                    datetime.now()
                )
                records.append(record)

            query = """
                INSERT INTO breakouts (
                    symbol, date, breakout_type, entry_price, volume_ratio,
                    atr_pct, rsi_value, price_change_pct, success,
                    max_gain_10d, max_loss_10d, days_to_peak, created_at
                )
                VALUES %s
                ON CONFLICT (symbol, date, breakout_type) 
                DO UPDATE SET
                    entry_price = EXCLUDED.entry_price,
                    volume_ratio = EXCLUDED.volume_ratio,
                    atr_pct = EXCLUDED.atr_pct,
                    rsi_value = EXCLUDED.rsi_value,
                    price_change_pct = EXCLUDED.price_change_pct,
                    success = EXCLUDED.success,
                    max_gain_10d = EXCLUDED.max_gain_10d,
                    max_loss_10d = EXCLUDED.max_loss_10d,
                    days_to_peak = EXCLUDED.days_to_peak,
                    created_at = EXCLUDED.created_at
            """

            rows_inserted = db.bulk_insert(query, records)
            logger.info(f"Successfully saved {rows_inserted} breakout records")
            return True

        except Exception as e:
            logger.error(f"Error saving breakouts: {e}")
            return False

    @timing_decorator()
    def save_to_ml_training_table(self, breakouts: pd.DataFrame) -> bool:
        """Save enhanced breakouts to ml_training_data table"""
        if breakouts.empty:
            return True

        try:
            records = []
            for _, row in breakouts.iterrows():
                record = (
                    row['symbol'],
                    row['date'],
                    row['breakout_type'],
                    float(row['entry_price']),
                    float(row.get('volume_ratio', 1.0)),
                    float(row.get('atr_pct', 0)),
                    float(row.get('rsi_value', 50)),
                    float(row.get('price_change_pct', 0)),
                    bool(row.get('success', False)),
                    float(row.get('max_gain_10d', 0)),
                    float(row.get('max_loss_10d', 0)),
                    int(row.get('days_to_peak', 10)),
                    datetime.now(),
                    data_validation.clean_numeric_data(row.get('overall_quality_score')),
                    row.get('quality_grade'),
                    row.get('sector'),
                    float(row.get('rsi_14', 50)) if row.get('rsi_14') else None,
                    float(row.get('tech_volume_ratio', 1.0)) if row.get('tech_volume_ratio') else None,
                    # Additional ML features
                    float(row.get('final_return_10d', 0)),
                    float(row.get('volatility_10d', 0)),
                    bool(row.get('volume_confirmation', False)),
                    float(row.get('breakout_strength', 0)),
                    float(row.get('price_vs_ma20', 1.0)),
                    float(row.get('price_vs_ma50', 1.0)),
                    float(row.get('macd_value', 0)),
                    float(row.get('price_position', 50))
                )
                records.append(record)

            query = """
                INSERT INTO ml_training_data (
                    symbol, date, breakout_type, entry_price, volume_ratio,
                    atr_pct, rsi_value, price_change_pct, success,
                    max_gain_10d, max_loss_10d, days_to_peak, created_at,
                    overall_quality_score, quality_grade, sector, rsi_14, tech_volume_ratio,
                    final_return_10d, volatility_10d, volume_confirmation, breakout_strength,
                    price_vs_ma20, price_vs_ma50, macd_value, price_position
                )
                VALUES %s
                ON CONFLICT (symbol, date, breakout_type) 
                DO UPDATE SET
                    entry_price = EXCLUDED.entry_price,
                    volume_ratio = EXCLUDED.volume_ratio,
                    atr_pct = EXCLUDED.atr_pct,
                    rsi_value = EXCLUDED.rsi_value,
                    price_change_pct = EXCLUDED.price_change_pct,
                    success = EXCLUDED.success,
                    max_gain_10d = EXCLUDED.max_gain_10d,
                    max_loss_10d = EXCLUDED.max_loss_10d,
                    days_to_peak = EXCLUDED.days_to_peak,
                    overall_quality_score = EXCLUDED.overall_quality_score,
                    quality_grade = EXCLUDED.quality_grade,
                    sector = EXCLUDED.sector,
                    rsi_14 = EXCLUDED.rsi_14,
                    tech_volume_ratio = EXCLUDED.tech_volume_ratio,
                    final_return_10d = EXCLUDED.final_return_10d,
                    volatility_10d = EXCLUDED.volatility_10d,
                    volume_confirmation = EXCLUDED.volume_confirmation,
                    breakout_strength = EXCLUDED.breakout_strength,
                    price_vs_ma20 = EXCLUDED.price_vs_ma20,
                    price_vs_ma50 = EXCLUDED.price_vs_ma50,
                    macd_value = EXCLUDED.macd_value,
                    price_position = EXCLUDED.price_position,
                    created_at = EXCLUDED.created_at
            """

            rows_inserted = db.bulk_insert(query, records)
            logger.info(f"Successfully saved {rows_inserted} ML training records")
            return True

        except Exception as e:
            logger.error(f"Error saving ML training data: {e}")
            return False

    @timing_decorator()
    def process_symbol(self, symbol: str) -> int:
        """Process a single symbol for historical breakouts"""
        try:
            logger.info(f"Processing {symbol}")

            # Get stock data with indicators
            data = self.get_stock_data_with_indicators(symbol)
            if data is None or len(data) < self.ml_config['min_data_points']:
                logger.warning(f"Insufficient data for {symbol}")
                return 0

            # Identify breakouts
            breakouts = self.identify_breakouts(data)
            if breakouts.empty:
                logger.debug(f"No breakouts found for {symbol}")
                return 0

            # Calculate outcomes
            breakouts_with_outcomes = self.calculate_enhanced_outcomes(data, breakouts)
            if breakouts_with_outcomes.empty:
                logger.warning(f"No valid outcomes calculated for {symbol}")
                return 0

            # Link with fundamentals data
            enhanced_breakouts = self.link_fundamentals_data(breakouts_with_outcomes)

            # Save to both tables
            breakouts_saved = self.save_to_breakouts_table(enhanced_breakouts)
            ml_saved = self.save_to_ml_training_table(enhanced_breakouts)

            if breakouts_saved and ml_saved:
                logger.info(f"✅ Successfully processed {len(enhanced_breakouts)} breakouts for {symbol}")
                return len(enhanced_breakouts)
            else:
                logger.error(f"Failed to save breakouts for {symbol}")
                return 0

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            return 0

    @timing_decorator()
    def generate_historical_breakouts(self, symbols: Optional[List[str]] = None, limit: Optional[int] = None):
        """Generate historical breakout data for ML training"""
        start_time = datetime.now()
        logger.info(f"🚀 Starting historical breakout generation at {start_time}")

        # Get symbols to process
        if symbols is None:
            symbols = self.get_symbols_with_sufficient_data()

        if limit:
            symbols = symbols[:limit]

        if not symbols:
            logger.warning("No symbols to process")
            return {
                'success': False,
                'message': 'No symbols found',
                'symbols_processed': 0,
                'total_breakouts': 0
            }

        total_breakouts = 0
        processed_symbols = 0
        errors = 0

        for i, symbol in enumerate(symbols, 1):
            try:
                breakout_count = self.process_symbol(symbol)
                total_breakouts += breakout_count
                processed_symbols += 1

                if i % 10 == 0:
                    logger.info(f"Processed {i}/{len(symbols)} symbols, found {total_breakouts} total breakouts")

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")
                errors += 1
                continue

        end_time = datetime.now()
        duration = end_time - start_time

        # Generate summary report
        avg_breakouts = total_breakouts / processed_symbols if processed_symbols > 0 else 0
        success_rate = (processed_symbols / len(symbols) * 100) if symbols else 0

        logger.info(f"""
        🎉 Historical breakout generation completed in {duration}:
        ✅ Symbols processed: {processed_symbols}
        📊 Total breakouts generated: {total_breakouts}
        📈 Average breakouts per symbol: {avg_breakouts:.1f}
        📋 Success rate: {success_rate:.1f}%
        ⚠️ Errors: {errors}
        """)

        return {
            'success': errors < len(symbols) * 0.1,  # Success if <10% errors
            'duration_seconds': duration.total_seconds(),
            'symbols_processed': processed_symbols,
            'symbols_failed': errors,
            'total_breakouts': total_breakouts,
            'avg_breakouts_per_symbol': avg_breakouts,
            'success_rate': success_rate
        }


def main():
    """Main function to generate historical breakouts"""
    import argparse

    parser = argparse.ArgumentParser(description='Enhanced Historical Breakouts Generator')
    parser.add_argument('--limit', type=int, help='Limit number of symbols to process')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--min-days', type=int, help='Minimum days of data required')
    parser.add_argument('--config-test', action='store_true', help='Test configuration')

    args = parser.parse_args()

    if args.config_test:
        # Test configuration
        print("🔧 Testing Historical Breakouts Generator configuration...")

        validation = config.validate_config()
        print(f"Configuration validation: {validation}")

        ml_config = config.get_ml_config()
        print(f"ML config: {ml_config}")

        db_status = db.test_connection()
        if db_status['connected']:
            print(f"✅ Database connection successful")
            print(f"   ML training records: {format_number(db_status['stats'].get('breakouts', 0))}")
        else:
            print(f"❌ Database connection failed: {db_status['error']}")

        return

    # Initialize generator
    generator = HistoricalBreakoutGenerator()

    if args.test:
        # Test mode with specific symbols
        logger.info(f"🧪 Running test with symbols: {args.test}")
        result = generator.generate_historical_breakouts(symbols=args.test)

    else:
        # Full generation
        result = generator.generate_historical_breakouts(limit=args.limit)

    # Log final result
    if result['success']:
        logger.info("🎉 Historical breakout generation completed successfully!")
        logger.info(f"📊 Generated {result['total_breakouts']} training samples")
    else:
        logger.warning("⚠️ Historical breakout generation completed with issues")


if __name__ == "__main__":
    main()