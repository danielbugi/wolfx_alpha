#!/usr/bin/env python3
"""
Fixed Feature Builder - ALL BUGS RESOLVED
Key Fixes:
1. Fixed Bollinger position calculation with proper entry_price
2. Added ATR fallback calculation when missing
3. Fixed division by zero issues
4. Used shared database module for consistency
5. Added proper data validation
6. Fixed SQL injection risks
7. Standardized error handling
8. Optimized database queries
"""

import pandas as pd
import numpy as np
import os
import sys
import json
from datetime import datetime, timedelta
import warnings
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings('ignore')

# Use shared database module for consistency
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
try:
    from shared import db, setup_logging

    logger = setup_logging("feature_builder")
except ImportError as e:
    print(f"ERROR: Could not import shared modules: {e}")
    sys.exit(1)

# Import ML config
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'config'))
try:
    from ml_config import ml_config

    print(f"SUCCESS: Using ML configuration")
except ImportError as e:
    print(f"ERROR: Could not import ml_config: {e}")
    sys.exit(1)


class FixedSwingTradingFeatureBuilder:
    """
    FIXED: Build comprehensive features for swing trading momentum breakout prediction
    All bugs resolved and optimized for production use
    """

    def __init__(self):
        """Initialize with proper error handling and validation"""
        self.logger = setup_logging("FixedFeatureBuilder")
        self.logger.info("Fixed Feature Builder initialized - ALL BUGS RESOLVED")

        # Test database connection using shared module
        try:
            # Test connection
            test_query = "SELECT 1 as test"
            result = db.execute_dict_query(test_query)
            if result:
                self.logger.info("Database connection verified using shared module")
            else:
                raise ConnectionError("Database test query failed")
        except Exception as e:
            self.logger.error(f"Database connection failed: {e}")
            raise ConnectionError(f"Cannot connect to database: {e}")

    def safe_float(self, value, default=None):
        """Safely convert to float with proper default handling"""
        if value is None:
            return default
        try:
            result = float(value)
            if np.isnan(result) or np.isinf(result):
                return default
            return result
        except (ValueError, TypeError):
            return default

    def calculate_atr_fallback(self, symbol: str, date: str, period: int = 14) -> Optional[float]:
        """
        FIXED: Calculate ATR fallback when database ATR is missing
        Uses same logic as ATR database fixer for consistency
        """
        try:
            # Get recent price data for ATR calculation
            query = """
                SELECT date, high, low, close
                FROM stock_prices
                WHERE symbol = %s AND date <= %s
                ORDER BY date DESC
                LIMIT %s
            """

            price_data = db.execute_dict_query(query, (symbol, date, period + 5))

            if len(price_data) < period + 1:
                return None

            # Sort chronologically for TR calculation
            price_data = sorted(price_data, key=lambda x: x['date'])

            # Calculate True Range
            true_ranges = []
            for i in range(1, len(price_data)):
                current = price_data[i]
                previous = price_data[i - 1]

                high = self.safe_float(current['high'])
                low = self.safe_float(current['low'])
                close_prev = self.safe_float(previous['close'])

                if all(x is not None for x in [high, low, close_prev]):
                    high_low = high - low
                    high_close_prev = abs(high - close_prev)
                    low_close_prev = abs(low - close_prev)

                    true_range = max(high_low, high_close_prev, low_close_prev)
                    true_ranges.append(true_range)

            if len(true_ranges) < period:
                return None

            # Use simple average for fallback (good enough for ML features)
            recent_trs = true_ranges[-period:]
            atr = sum(recent_trs) / len(recent_trs)

            self.logger.debug(f"Calculated fallback ATR for {symbol}: {atr:.4f}")
            return round(atr, 4)

        except Exception as e:
            self.logger.warning(f"ATR fallback calculation failed for {symbol}: {e}")
            return None

    def load_momentum_breakouts(self, limit: Optional[int] = None) -> Optional[pd.DataFrame]:
        """FIXED: Load breakouts with momentum scores using shared db module"""
        self.logger.info("Loading breakouts with momentum scores...")

        try:
            query = """
                SELECT 
                    symbol, date, breakout_type, entry_price, original_success,
                    momentum_score, momentum_category, total_return_pct, analysis_days
                FROM momentum_scores
                WHERE momentum_score > 0
                ORDER BY date DESC
            """

            # FIXED: Use parameterized query for limit
            if limit:
                query += " LIMIT %s"
                results = db.execute_dict_query(query, (limit,))
            else:
                results = db.execute_dict_query(query)

            if not results:
                self.logger.warning("No momentum-labeled breakouts found")
                return None

            df = pd.DataFrame(results)
            self.logger.info(f"SUCCESS: Loaded {len(df)} breakouts with momentum scores")
            return df

        except Exception as e:
            self.logger.error(f"Failed to load momentum breakouts: {e}")
            return None

    def get_combined_features(self, symbol: str, date: str, entry_price: float, lookback_days: int = 20) -> Dict:
        """
        FIXED: Get technical and fundamental features in a single optimized query
        Includes proper ATR fallback and entry_price for correct calculations
        """
        try:
            # OPTIMIZED: Single query for technical and fundamental data
            query = """
                SELECT 
                    -- Technical indicators
                    t.date, t.sma_10, t.sma_20, t.sma_50, t.rsi_14, t.macd, t.macd_signal, 
                    t.macd_histogram, t.bollinger_upper, t.bollinger_lower, t.atr_14, 
                    t.donchian_high_20, t.donchian_low_20, t.donchian_mid_20, 
                    t.volume_sma_10, t.volume_ratio, t.price_position, t.channel_width_pct,

                    -- Fundamental data
                    f.market_cap, f.pe_ratio, f.pb_ratio, f.ps_ratio, f.peg_ratio, f.beta, 
                    f.dividend_yield, f.sector, f.industry, f.growth_score, f.profitability_score, 
                    f.financial_health_score, f.valuation_score, f.overall_quality_score, f.quality_grade

                FROM technical_indicators t
                LEFT JOIN daily_fundamentals f ON t.symbol = f.symbol AND t.date = f.date
                WHERE t.symbol = %s AND t.date <= %s AND t.date >= %s - INTERVAL '%s days'
                ORDER BY t.date DESC 
                LIMIT 1
            """

            results = db.execute_dict_query(query, (symbol, date, date, lookback_days))

            if not results:
                self.logger.warning(f"No data found for {symbol} on {date}")
                return {}

            latest = results[0]
            features = {}

            # FIXED: Technical features with proper validation and defaults
            features['rsi_14'] = self.safe_float(latest['rsi_14'], 50.0)
            features['macd'] = self.safe_float(latest['macd'], 0.0)
            features['volume_ratio'] = self.safe_float(latest['volume_ratio'], 1.0)
            features['price_position'] = self.safe_float(latest['price_position'], 50.0)
            features['channel_width_pct'] = self.safe_float(latest['channel_width_pct'], 5.0)

            # FIXED: ATR with proper fallback calculation
            atr_value = self.safe_float(latest['atr_14'])
            if atr_value is None or atr_value <= 0:
                # Calculate fallback ATR when missing
                atr_value = self.calculate_atr_fallback(symbol, date)
                if atr_value is None:
                    # Last resort: estimate from price volatility
                    atr_value = entry_price * 0.02  # 2% of entry price as rough estimate
                    self.logger.debug(f"Used price-based ATR fallback for {symbol}: {atr_value:.4f}")

            features['atr_14'] = atr_value

            # Moving averages with validation
            features['sma_10'] = self.safe_float(latest['sma_10'], 0.0)
            features['sma_20'] = self.safe_float(latest['sma_20'], 0.0)
            features['sma_50'] = self.safe_float(latest['sma_50'], 0.0)

            # FIXED: SMA relationships with proper division by zero protection
            if features['sma_20'] != 0:  # FIXED: was > 0, now != 0
                features['sma_10_vs_20'] = (features['sma_10'] - features['sma_20']) / features['sma_20'] * 100
            else:
                features['sma_10_vs_20'] = 0.0

            # FIXED: Bollinger position calculation using actual entry_price
            bollinger_upper = self.safe_float(latest['bollinger_upper'])
            bollinger_lower = self.safe_float(latest['bollinger_lower'])

            if bollinger_upper and bollinger_lower and bollinger_upper > bollinger_lower:
                bb_range = bollinger_upper - bollinger_lower
                # FIXED: Use entry_price instead of price_position
                features['bollinger_position'] = ((entry_price - bollinger_lower) / bb_range) * 100
            else:
                features['bollinger_position'] = 50.0

            # Donchian position (already correctly calculated in database)
            features['donchian_position'] = features['price_position']

            # FIXED: Fundamental features with proper validation
            features['growth_score'] = self.safe_float(latest['growth_score'], 5.0) / 10.0
            features['profitability_score'] = self.safe_float(latest['profitability_score'], 5.0) / 10.0
            features['financial_health_score'] = self.safe_float(latest['financial_health_score'], 5.0) / 10.0
            features['overall_quality_score'] = self.safe_float(latest['overall_quality_score'], 5.0) / 10.0

            # Grade encoding with validation
            grade_mapping = {'A': 1.0, 'B': 0.75, 'C': 0.5, 'D': 0.25, 'F': 0.0}
            quality_grade = latest['quality_grade'] if latest['quality_grade'] else 'C'
            features['quality_grade_numeric'] = grade_mapping.get(quality_grade, 0.5)

            # FIXED: Valuation ratios with proper bounds and validation
            features['pe_ratio'] = max(0, min(100, self.safe_float(latest['pe_ratio'], 15.0)))
            features['pb_ratio'] = max(0, min(20, self.safe_float(latest['pb_ratio'], 2.0)))
            features['beta'] = self.safe_float(latest['beta'], 1.0)

            # Market cap (log scale) with validation
            market_cap = self.safe_float(latest['market_cap'], 1000000000)
            features['log_market_cap'] = np.log10(max(1000000, market_cap))

            # Store sector for encoding
            features['sector'] = latest['sector'] if latest['sector'] else 'Unknown'

            return features

        except Exception as e:
            self.logger.error(f"Error getting combined features for {symbol}: {e}")
            return {}

    def build_features_for_breakout(self, symbol: str, date: str, breakout_type: str, entry_price: float) -> Dict:
        """
        FIXED: Build complete feature set for a breakout with proper validation
        """
        try:
            features = {}

            # Basic features with validation
            features['is_bullish'] = 1 if breakout_type == 'bullish' else 0
            features['entry_price'] = self.safe_float(entry_price, 0.0)

            # FIXED: Get combined features with entry_price for correct calculations
            combined_features = self.get_combined_features(symbol, date, entry_price)

            if not combined_features:
                self.logger.warning(f"No features available for {symbol} on {date}")
                return {}

            features.update(combined_features)

            # FIXED: Interaction features with proper validation
            if 'overall_quality_score' in features and 'volume_ratio' in features:
                features['quality_volume'] = features['overall_quality_score'] * features['volume_ratio']

            if 'overall_quality_score' in features and 'rsi_14' in features:
                features['quality_rsi'] = features['overall_quality_score'] * (features['rsi_14'] / 100.0)

            # FIXED: Sector encoding with validation
            if 'sector' in features:
                major_sectors = ['Technology', 'Healthcare', 'Financial Services', 'Consumer Cyclical', 'Industrials']
                for sector in major_sectors:
                    sector_key = f'sector_{sector.lower().replace(" ", "_")}'
                    features[sector_key] = 1 if features['sector'] == sector else 0
                # Remove original sector string
                del features['sector']

            return features

        except Exception as e:
            self.logger.error(f"Error building features for {symbol}: {e}")
            return {}

    def validate_features(self, features: Dict) -> Tuple[bool, List[str]]:
        """
        NEW: Validate feature quality and completeness
        """
        issues = []

        # Check for required features
        required_features = ['is_bullish', 'entry_price', 'rsi_14', 'atr_14', 'overall_quality_score']
        for feature in required_features:
            if feature not in features:
                issues.append(f"Missing required feature: {feature}")
            elif features[feature] is None:
                issues.append(f"Required feature is None: {feature}")

        # Validate feature ranges
        if 'rsi_14' in features:
            rsi = features['rsi_14']
            if rsi < 0 or rsi > 100:
                issues.append(f"RSI out of range: {rsi}")

        if 'atr_14' in features:
            atr = features['atr_14']
            if atr <= 0:
                issues.append(f"ATR invalid: {atr}")

        if 'entry_price' in features:
            price = features['entry_price']
            if price <= 0:
                issues.append(f"Entry price invalid: {price}")

        # Check for suspicious values
        if 'pe_ratio' in features:
            pe = features['pe_ratio']
            if pe > 1000:  # Extremely high P/E
                issues.append(f"Suspicious P/E ratio: {pe}")

        return len(issues) == 0, issues

    def build_training_dataset(self, momentum_df: pd.DataFrame, save_to_db: bool = True) -> Optional[pd.DataFrame]:
        """
        FIXED: Build complete ML training dataset with validation and error handling
        """
        self.logger.info(f"Building features for {len(momentum_df)} breakouts...")

        training_data = []
        validation_failures = []

        for i, (_, breakout) in enumerate(momentum_df.iterrows()):
            if i % 10 == 0:
                self.logger.info(f"   Processing {i + 1}/{len(momentum_df)}")

            try:
                features = self.build_features_for_breakout(
                    breakout['symbol'],
                    breakout['date'],
                    breakout['breakout_type'],
                    breakout['entry_price']
                )

                if not features:
                    self.logger.warning(f"No features for {breakout['symbol']} - skipping")
                    continue

                # FIXED: Validate features before adding to training data
                is_valid, issues = self.validate_features(features)
                if not is_valid:
                    validation_failures.append({
                        'symbol': breakout['symbol'],
                        'date': breakout['date'],
                        'issues': issues
                    })
                    self.logger.warning(f"Feature validation failed for {breakout['symbol']}: {issues}")
                    continue

                # Add targets with validation
                features['target_momentum_score'] = int(breakout['momentum_score'])
                features['target_momentum_category'] = breakout['momentum_category']
                features['target_binary'] = 1 if breakout['momentum_score'] >= 65 else 0
                features['target_return_pct'] = self.safe_float(breakout['total_return_pct'], 0.0)

                # Add metadata
                features['symbol'] = breakout['symbol']
                features['date'] = breakout['date']
                features['breakout_type'] = breakout['breakout_type']

                training_data.append(features)

            except Exception as e:
                self.logger.error(f"Error processing {breakout['symbol']}: {e}")
                continue

        if not training_data:
            self.logger.error("No valid training data generated")
            return None

        training_df = pd.DataFrame(training_data)

        # Report validation results
        feature_count = len([col for col in training_df.columns
                             if not col.startswith(('target_', 'symbol', 'date', 'breakout_type'))])

        self.logger.info(f"SUCCESS: Built features for {len(training_df)} breakouts")
        self.logger.info(f"Feature count: {feature_count}")

        if validation_failures:
            self.logger.warning(f"Validation failures: {len(validation_failures)}")

        # FIXED: Save to database with proper error handling
        if save_to_db:
            success = self.save_training_dataset(training_df)
            if not success:
                self.logger.error("Failed to save training dataset to database")

        return training_df

    def save_training_dataset(self, training_df: pd.DataFrame) -> bool:
        """
        FIXED: Save training dataset using shared database module with proper error handling
        """
        self.logger.info("Saving training dataset to database...")

        try:
            # Create table if not exists
            create_table_query = """
                CREATE TABLE IF NOT EXISTS enhanced_ml_training_data (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(10),
                    date DATE,
                    breakout_type VARCHAR(10),
                    features JSONB,
                    target_momentum_score INTEGER,
                    target_momentum_category VARCHAR(20),
                    target_binary INTEGER,
                    target_return_pct NUMERIC(8,2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol, date)
                );
            """

            db.execute_insert(create_table_query, ())

            # FIXED: Insert data using shared db module
            insert_query = """
                INSERT INTO enhanced_ml_training_data 
                (symbol, date, breakout_type, features, target_momentum_score, 
                 target_momentum_category, target_binary, target_return_pct)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date) DO UPDATE SET
                    features = EXCLUDED.features,
                    target_momentum_score = EXCLUDED.target_momentum_score,
                    target_momentum_category = EXCLUDED.target_momentum_category,
                    target_binary = EXCLUDED.target_binary,
                    target_return_pct = EXCLUDED.target_return_pct
            """

            success_count = 0
            for _, row in training_df.iterrows():
                try:
                    # Extract features (exclude metadata and targets)
                    feature_cols = [col for col in training_df.columns
                                    if not col.startswith(('target_', 'symbol', 'date', 'breakout_type'))]
                    features_dict = row[feature_cols].to_dict()

                    # FIXED: Convert numpy types to Python types for JSON
                    for key, value in features_dict.items():
                        if pd.isna(value):
                            features_dict[key] = None
                        elif isinstance(value, (np.integer, np.floating)):
                            features_dict[key] = float(value)
                        elif isinstance(value, np.bool_):
                            features_dict[key] = bool(value)

                    # FIXED: Proper JSON serialization
                    features_json = json.dumps(features_dict)

                    db.execute_insert(insert_query, (
                        row['symbol'],
                        row['date'],
                        row['breakout_type'],
                        features_json,
                        row['target_momentum_score'],
                        row['target_momentum_category'],
                        row['target_binary'],
                        row['target_return_pct']
                    ))

                    success_count += 1

                except Exception as e:
                    self.logger.warning(f"Failed to save record for {row['symbol']}: {e}")
                    continue

            self.logger.info(f"SUCCESS: Saved {success_count}/{len(training_df)} training records to database")
            return success_count > 0

        except Exception as e:
            self.logger.error(f"Error saving training dataset: {e}")
            return False


def main():
    """Main function with comprehensive error handling"""
    print("FIXED FEATURE ENGINEERING PIPELINE - ALL BUGS RESOLVED")
    print("=" * 60)

    try:
        builder = FixedSwingTradingFeatureBuilder()
        momentum_df = builder.load_momentum_breakouts(limit=500)

        if momentum_df is None or len(momentum_df) == 0:
            print("ERROR: No momentum-labeled breakouts found")
            print("Make sure you have run the momentum labeling step first")
            return

        training_df = builder.build_training_dataset(momentum_df, save_to_db=True)

        if training_df is not None and len(training_df) > 0:
            print(f"\n🎉 SUCCESS! Training dataset ready: {len(training_df)} samples")
            print(f"Target distribution:")
            print(training_df['target_momentum_category'].value_counts())
            print(f"Binary target distribution:")
            print(training_df['target_binary'].value_counts())

            # Feature quality report
            feature_cols = [col for col in training_df.columns
                            if not col.startswith(('target_', 'symbol', 'date', 'breakout_type'))]
            print(f"Features generated: {len(feature_cols)}")

            # Check for missing values
            missing_features = training_df[feature_cols].isnull().sum()
            if missing_features.any():
                print(f"Features with missing values:")
                for feature, count in missing_features[missing_features > 0].items():
                    print(f"  {feature}: {count} missing")
            else:
                print("✅ No missing feature values")

            print(f"🚀 Ready for model training!")
        else:
            print("❌ ERROR: Failed to generate training dataset")

    except Exception as e:
        print(f"❌ ERROR: Feature engineering failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()