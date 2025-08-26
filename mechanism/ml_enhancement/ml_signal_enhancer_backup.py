# automation/ml_enhancement/ml_signal_enhancer.py

import sys
import os
import json
import pandas as pd
import numpy as np
import psycopg2
from datetime import datetime
import joblib
import warnings
from pathlib import Path
import re

warnings.filterwarnings('ignore')

# Import your existing configuration
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'shared'))
try:
    from config import config

    print("SUCCESS: Using existing trading system configuration")
except ImportError as e:
    print(f"WARNING: Could not import existing config: {e}")
    # Fallback to environment variables
    from dotenv import load_dotenv

    load_dotenv()


class MLMomentumEnhancer:
    """
    Enhances donchian screener signals with ML momentum predictions
    Integrates seamlessly with your existing trading system
    """

    def __init__(self, model_path=None):
        # Database configuration
        if 'config' in globals():
            self.db_config = config.db_config
        else:
            self.db_config = {
                'host': os.getenv('DB_HOST', 'localhost'),
                'port': os.getenv('DB_PORT', '5432'),
                'database': os.getenv('DB_NAME', 'trading_production'),
                'user': os.getenv('DB_USER', 'trading_user'),
                'password': os.getenv('DB_PASSWORD', '')
            }

        # Model components
        self.model = None
        self.scaler = None
        self.feature_names = []
        self.model_loaded = False
        self.model_version = None

        # Try to load the latest model
        if model_path:
            self.load_model(model_path)
        else:
            self.load_latest_model()

    def find_project_root(self):
        """Find the project root directory dynamically"""
        current_dir = Path.cwd()

        # Look for characteristic files/directories that indicate project root
        project_markers = [
            'ml_training',
            'mechanism',
            '.env',
            'breakout_results',
            'frontend_data'
        ]

        # Start from current directory and go up
        search_dir = current_dir
        for _ in range(5):  # Limit search to 5 levels up
            # Check if this directory has project markers
            markers_found = sum(1 for marker in project_markers if (search_dir / marker).exists())

            if markers_found >= 2:  # Need at least 2 markers to confirm
                print(f"Found project root: {search_dir}")
                return search_dir

            # Go up one level
            parent = search_dir.parent
            if parent == search_dir:  # Reached filesystem root
                break
            search_dir = parent

        # Fallback to current directory
        print(f"Using current directory as project root: {current_dir}")
        return current_dir

    def find_model_files(self):
        """Find model files dynamically in the project"""
        print("Searching for ML model files...")

        # Find project root first
        project_root = self.find_project_root()

        # Look in multiple possible locations
        possible_model_dirs = [
            project_root / "ml_training" / "models",
            project_root / "models",
            project_root / "mechanism" / "models",
            project_root / "models" / "production"
        ]

        all_model_files = []

        for model_dir in possible_model_dirs:
            if model_dir.exists():
                print(f"Checking directory: {model_dir}")

                # Look for momentum predictor files with any timestamp
                pattern = "momentum_predictor_v*.joblib"
                model_files = list(model_dir.glob(pattern))

                # Filter out scaler and features files to get main model files
                main_models = [f for f in model_files if "_scaler" not in f.name and "_features" not in f.name]

                for model_file in main_models:
                    print(f"  Found model: {model_file.name}")

                    # Check for corresponding files
                    base_name = model_file.stem
                    scaler_file = model_file.parent / f"{base_name}_scaler.joblib"
                    features_file = model_file.parent / f"{base_name}_features.json"

                    scaler_exists = scaler_file.exists()
                    features_exists = features_file.exists()

                    print(f"    Scaler: {scaler_file.name} ({'✓' if scaler_exists else '✗'})")
                    print(f"    Features: {features_file.name} ({'✓' if features_exists else '✗'})")

                    # Only include models with all required files
                    if scaler_exists and features_exists:
                        all_model_files.append(model_file)
                        print(f"    ✓ Complete model set found")
                    else:
                        print(f"    ✗ Incomplete model set - skipping")

        if not all_model_files:
            print("ERROR: No complete model sets found!")
            print("\nSearched in:")
            for model_dir in possible_model_dirs:
                print(f"  - {model_dir} ({'exists' if model_dir.exists() else 'not found'})")
            print("\nExpected pattern: momentum_predictor_v{timestamp}.joblib")
            print("Required files for each model:")
            print("  - momentum_predictor_v{timestamp}.joblib")
            print("  - momentum_predictor_v{timestamp}_scaler.joblib")
            print("  - momentum_predictor_v{timestamp}_features.json")

        return all_model_files

    def extract_timestamp_from_filename(self, filepath):
        """Extract timestamp from model filename for sorting"""
        filename = filepath.name
        # Look for pattern like momentum_predictor_v20250713_1934.joblib
        match = re.search(r'momentum_predictor_v(\d{8}_\d{4})\.joblib', filename)
        if match:
            return match.group(1)
        return "00000000_0000"  # Fallback for sorting

    def load_latest_model(self):
        """Load the most recent trained model dynamically"""
        model_files = self.find_model_files()

        if not model_files:
            print("WARNING: No trained models found!")
            print("\nTo create a model, run:")
            print("  python ml_training/scripts/ml_pipeline_runner.py full")
            return False

        # Sort by timestamp to get the latest
        latest_model = sorted(model_files, key=self.extract_timestamp_from_filename)[-1]
        print(f"Using latest model: {latest_model}")

        return self.load_model(str(latest_model))

    def load_model(self, model_path):
        """Load trained model, scaler, and feature names"""
        try:
            model_path = Path(model_path)
            print(f"Loading ML model: {model_path.name}")

            # Load main model
            if not model_path.exists():
                print(f"ERROR: Model file not found: {model_path}")
                return False

            self.model = joblib.load(str(model_path))
            print(f"SUCCESS: Loaded model from {model_path.name}")

            # Extract model version from filename
            self.model_version = model_path.stem

            # Load scaler - exact naming pattern
            scaler_path = model_path.parent / f"{model_path.stem}_scaler.joblib"

            if scaler_path.exists():
                self.scaler = joblib.load(str(scaler_path))
                print(f"SUCCESS: Loaded scaler from {scaler_path.name}")
            else:
                print(f"ERROR: Scaler not found: {scaler_path}")
                return False

            # Load feature names - exact naming pattern
            features_path = model_path.parent / f"{model_path.stem}_features.json"

            if features_path.exists():
                with open(features_path, 'r') as f:
                    self.feature_names = json.load(f)
                print(f"SUCCESS: Loaded {len(self.feature_names)} feature names")
            else:
                print(f"ERROR: Features file not found: {features_path}")
                return False

            self.model_loaded = True
            print(f"SUCCESS: ML model fully loaded and ready!")
            print(f"Model version: {self.model_version}")
            return True

        except Exception as e:
            print(f"ERROR: Failed to load model: {e}")
            print(f"Model path: {model_path}")
            import traceback
            traceback.print_exc()
            return False

    def get_db_connection(self):
        """Create database connection"""
        try:
            conn = psycopg2.connect(**self.db_config)
            return conn
        except Exception as e:
            print(f"ERROR: Database connection failed: {e}")
            return None

    def extract_ml_features(self, signal):
        """Extract ML features from a donchian screener signal"""
        features = {}

        # Basic signal features
        features['is_bullish'] = 1 if signal.get('type') in ['bullish_breakout', 'near_bullish'] else 0
        features['entry_price'] = signal.get('current_price', 0)

        # Technical features from your signal
        features['volume_ratio'] = signal.get('volume_ratio', 1.0)
        features['price_position'] = signal.get('distance_to_breakout', 0)

        # Get additional features from database
        additional_features = self.get_features_from_db(
            signal.get('symbol'),
            signal.get('screening_date', datetime.now().date())
        )
        features.update(additional_features)

        # Fill missing features with defaults
        default_features = {
            'rsi_14': 50, 'macd': 0, 'channel_width_pct': 5, 'atr_14': 1,
            'sma_10': 0, 'sma_20': 0, 'sma_50': 0, 'sma_10_vs_20': 0,
            'bollinger_position': 50, 'donchian_position': 50,
            'growth_score': 0.5, 'profitability_score': 0.5,
            'financial_health_score': 0.5, 'overall_quality_score': 0.5,
            'quality_grade_numeric': 0.5, 'pe_ratio': 15, 'pb_ratio': 2,
            'beta': 1.0, 'log_market_cap': 9, 'quality_rsi': 0.25,
            'quality_volume': 0.5, 'sector_technology': 0, 'sector_healthcare': 0,
            'sector_financial_services': 0, 'sector_consumer_cyclical': 0,
            'sector_industrials': 0
        }

        # Add missing features
        for feature_name in self.feature_names:
            if feature_name not in features:
                features[feature_name] = default_features.get(feature_name, 0)

        return features

    def get_features_from_db(self, symbol, date):
        """Get additional features from database for ML prediction"""
        conn = self.get_db_connection()
        if not conn:
            return {}

        features = {}

        try:
            # Get technical indicators
            tech_query = """
                SELECT rsi_14, macd, volume_ratio, price_position, channel_width_pct,
                       atr_14, sma_10, sma_20, sma_50, bollinger_upper, bollinger_lower,
                       donchian_high_20, donchian_low_20
                FROM technical_indicators
                WHERE symbol = %s AND date <= %s
                ORDER BY date DESC LIMIT 1
            """

            tech_df = pd.read_sql(tech_query, conn, params=[symbol, date])
            if len(tech_df) > 0:
                tech = tech_df.iloc[0]
                features.update({
                    'rsi_14': tech['rsi_14'] or 50,
                    'macd': tech['macd'] or 0,
                    'volume_ratio': tech['volume_ratio'] or 1,
                    'price_position': tech['price_position'] or 50,
                    'channel_width_pct': tech['channel_width_pct'] or 5,
                    'atr_14': tech['atr_14'] or 1,
                    'sma_10': tech['sma_10'] or 0,
                    'sma_20': tech['sma_20'] or 0,
                    'sma_50': tech['sma_50'] or 0,
                })

                # SMA relationships
                if features['sma_20'] > 0:
                    features['sma_10_vs_20'] = (features['sma_10'] - features['sma_20']) / features['sma_20'] * 100
                else:
                    features['sma_10_vs_20'] = 0

                # Bollinger and Donchian positions
                if tech['bollinger_upper'] and tech['bollinger_lower']:
                    bb_range = tech['bollinger_upper'] - tech['bollinger_lower']
                    if bb_range > 0:
                        entry_price = features.get('entry_price', 0)
                        features['bollinger_position'] = (entry_price - tech['bollinger_lower']) / bb_range * 100
                    else:
                        features['bollinger_position'] = 50
                else:
                    features['bollinger_position'] = 50

                features['donchian_position'] = tech['price_position'] or 50

            # Get fundamentals
            fund_query = """
                SELECT growth_score, profitability_score, financial_health_score,
                       overall_quality_score, quality_grade, pe_ratio, pb_ratio,
                       beta, market_cap, sector
                FROM daily_fundamentals
                WHERE symbol = %s AND date <= %s
                ORDER BY date DESC LIMIT 1
            """

            fund_df = pd.read_sql(fund_query, conn, params=[symbol, date])
            if len(fund_df) > 0:
                fund = fund_df.iloc[0]

                # Quality scores (normalized)
                features.update({
                    'growth_score': (fund['growth_score'] or 5) / 10,
                    'profitability_score': (fund['profitability_score'] or 5) / 10,
                    'financial_health_score': (fund['financial_health_score'] or 5) / 10,
                    'overall_quality_score': (fund['overall_quality_score'] or 5) / 10,
                })

                # Grade encoding
                grade_mapping = {'A': 1.0, 'B': 0.75, 'C': 0.5, 'D': 0.25, 'F': 0.0}
                features['quality_grade_numeric'] = grade_mapping.get(fund['quality_grade'], 0.5)

                # Valuation ratios
                features['pe_ratio'] = min(100, max(0, fund['pe_ratio'] or 15))
                features['pb_ratio'] = min(20, max(0, fund['pb_ratio'] or 2))
                features['beta'] = fund['beta'] or 1.0

                # Market cap
                market_cap = fund['market_cap'] or 1000000000
                features['log_market_cap'] = np.log10(max(1000000, market_cap))

                # Interaction features
                features['quality_rsi'] = features.get('overall_quality_score', 0.5) * (
                        features.get('rsi_14', 50) / 100)
                features['quality_volume'] = features.get('overall_quality_score', 0.5) * features.get('volume_ratio',
                                                                                                       1)

                # Sector encoding
                sector = fund['sector'] or 'Unknown'
                major_sectors = ['Technology', 'Healthcare', 'Financial Services', 'Consumer Cyclical', 'Industrials']
                for sector_name in major_sectors:
                    features[f'sector_{sector_name.lower().replace(" ", "_")}'] = 1 if sector == sector_name else 0

            conn.close()

        except Exception as e:
            print(f"WARNING: Error getting features for {symbol}: {e}")
            if conn:
                conn.close()

        return features

    def predict_momentum(self, signal):
        """Main method to get ML prediction for a signal - matches the interface expected by screener"""
        prediction = self.predict_momentum_probability(signal)

        if prediction.get('ml_prediction_available', False):
            return {
                'momentum_probability': prediction['ml_momentum_probability'],
                'confidence_level': prediction['ml_confidence'],
                'trade_recommendation': self.get_trade_recommendation(prediction, signal),
                'risk_score': self.calculate_risk_score(prediction, signal),
                'predicted_momentum_days': self.get_predicted_momentum_days(prediction),
                'model_version': self.model_version
            }
        else:
            return None

    def predict_momentum_probability(self, signal):
        """Predict momentum probability for a single signal"""
        if not self.model_loaded:
            return {
                'ml_momentum_probability': 50.0,
                'ml_confidence': 'unknown',
                'ml_prediction_available': False,
                'ml_error': 'Model not loaded'
            }

        try:
            # Extract features
            features = self.extract_ml_features(signal)

            # Create feature vector
            feature_vector = []
            for feature_name in self.feature_names:
                feature_vector.append(features.get(feature_name, 0))

            # Make prediction
            X = np.array(feature_vector).reshape(1, -1)
            X_scaled = self.scaler.transform(X)

            probability = self.model.predict_proba(X_scaled)[0, 1]  # Probability of high momentum
            probability_pct = probability * 100

            # Determine confidence level
            if probability >= 0.8:
                confidence = 'very_high'
            elif probability >= 0.7:
                confidence = 'high'
            elif probability >= 0.6:
                confidence = 'medium'
            elif probability >= 0.5:
                confidence = 'low'
            else:
                confidence = 'very_low'

            return {
                'ml_momentum_probability': round(probability_pct, 1),
                'ml_confidence': confidence,
                'ml_prediction_available': True,
                'ml_raw_probability': probability
            }

        except Exception as e:
            print(f"WARNING: Error predicting momentum for {signal.get('symbol', 'unknown')}: {e}")
            return {
                'ml_momentum_probability': 50.0,
                'ml_confidence': 'error',
                'ml_prediction_available': False,
                'ml_error': str(e)
            }

    def get_trade_recommendation(self, ml_result, signal):
        """Generate trade recommendation based on ML prediction and signal type"""
        probability = ml_result.get('ml_raw_probability', 0.5)
        confidence = ml_result.get('ml_confidence', 'unknown')
        signal_type = signal.get('type', '')

        if probability >= 0.8 and confidence in ['high', 'very_high']:
            return 'strong_buy' if 'bullish' in signal_type else 'strong_sell'
        elif probability >= 0.7:
            return 'buy' if 'bullish' in signal_type else 'sell'
        elif probability >= 0.5:
            return 'hold'
        else:
            return 'avoid'

    def calculate_risk_score(self, ml_result, signal):
        """Calculate risk score (0-100, higher = more risky)"""
        probability = ml_result.get('ml_raw_probability', 0.5)

        # Base risk
        base_risk = 50

        # Adjust for ML confidence
        if probability >= 0.8:
            risk_adjustment = -20  # Lower risk for high confidence
        elif probability >= 0.6:
            risk_adjustment = -10
        elif probability <= 0.4:
            risk_adjustment = 20  # Higher risk for low confidence
        else:
            risk_adjustment = 0

        # Adjust for volume
        volume_ratio = signal.get('volume_ratio', 1.0)
        volume_risk = 10 if volume_ratio < 0.8 else -5

        total_risk = base_risk + risk_adjustment + volume_risk
        return max(0, min(100, int(total_risk)))

    def get_predicted_momentum_days(self, ml_result):
        """Get predicted momentum duration in days"""
        probability = ml_result.get('ml_raw_probability', 0.5)
        predicted_days = int(5 + (probability * 15))  # 5-20 days based on probability
        return predicted_days

    def enhance_signal(self, signal):
        """Enhance a single signal with ML predictions"""
        # Get ML momentum prediction
        ml_result = self.predict_momentum_probability(signal)

        # Add ML enhancements to signal
        enhanced_signal = signal.copy()
        enhanced_signal.update(ml_result)

        # Add derived ML features
        enhanced_signal['ml_trade_recommendation'] = self.get_trade_recommendation(ml_result, signal)
        enhanced_signal['ml_risk_score'] = self.calculate_risk_score(ml_result, signal)
        enhanced_signal['ml_predicted_momentum_days'] = self.get_predicted_momentum_days(ml_result)
        enhanced_signal['ml_model_version'] = self.model_version

        # Add color coding for frontend
        confidence = ml_result.get('ml_confidence', 'unknown')
        color_map = {
            'very_high': '#10b981',  # Green
            'high': '#3b82f6',  # Blue
            'medium': '#f59e0b',  # Yellow
            'low': '#ef4444',  # Red
            'very_low': '#6b7280',  # Gray
            'unknown': '#6b7280',  # Gray
            'error': '#dc2626'  # Dark red
        }
        enhanced_signal['ml_color_code'] = color_map.get(confidence, '#6b7280')

        # Add summary text
        prob_pct = ml_result.get('ml_momentum_probability', 50)
        enhanced_signal['ml_summary'] = f"AI predicts {prob_pct:.1f}% momentum probability ({confidence} confidence)"

        return enhanced_signal

    def enhance_signals_batch(self, signals):
        """Enhance multiple signals with ML predictions"""
        if not self.model_loaded:
            print("WARNING: No ML model loaded. Signals will not be enhanced.")
            return signals

        print(f"Enhancing {len(signals)} signals with AI momentum predictions...")

        enhanced_signals = []
        for i, signal in enumerate(signals):
            if i % 100 == 0 and i > 0:
                print(f"   Processed {i}/{len(signals)} signals")

            enhanced_signal = self.enhance_signal(signal)
            enhanced_signals.append(enhanced_signal)

        # Sort by ML momentum probability (highest first)
        enhanced_signals.sort(key=lambda x: x.get('ml_momentum_probability', 0), reverse=True)

        print(f"SUCCESS: Enhanced all {len(enhanced_signals)} signals with ML predictions")
        return enhanced_signals


def main():
    """Test the ML enhancer"""
    print("ML MOMENTUM ENHANCER TEST")
    print("=" * 50)

    enhancer = MLMomentumEnhancer()

    if not enhancer.model_loaded:
        print("ERROR: No model loaded.")
        return

    # Test with sample signal
    test_signal = {
        'symbol': 'AAPL',
        'type': 'bullish_breakout',
        'current_price': 175.50,
        'volume_ratio': 1.2,
        'distance_to_breakout': 0.5,
        'screening_date': datetime.now().date()
    }

    enhanced = enhancer.enhance_signal(test_signal)

    print(f"Test Enhancement Results:")
    print(f"   Symbol: {enhanced['symbol']}")
    print(f"   ML Probability: {enhanced.get('ml_momentum_probability', 'N/A')}%")
    print(f"   ML Confidence: {enhanced.get('ml_confidence', 'N/A')}")
    print(f"   Trade Recommendation: {enhanced.get('ml_trade_recommendation', 'N/A')}")
    print(f"   Risk Score: {enhanced.get('ml_risk_score', 'N/A')}")
    print(f"   Summary: {enhanced.get('ml_summary', 'N/A')}")


if __name__ == "__main__":
    main()