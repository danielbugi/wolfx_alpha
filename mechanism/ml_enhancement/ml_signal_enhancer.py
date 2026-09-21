# !/usr/bin/env python3

# automation/ml_enhancement/ml_signal_enhancer.py

"""
Combined Donchian Screener with Integrated ML Enhancement
FIXES ALL BUGS: Date consistency, data validation, signal verification
"""

import pandas as pd
import numpy as np
import json
import os
import joblib
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sys
from pathlib import Path
import re
from collections import defaultdict

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from shared import db, setup_logging, config

    logger = setup_logging("combined_screener")
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

# Single source of truth for features/breakouts/integrity rules -- the SAME module the trainer's
# dataset builder uses (ml_training/features/price_features.py), so train and serve cannot drift.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from ml_training.features import price_features as pf  # noqa: E402


def safe_float(value):
    """Safely convert decimal/numeric values to float"""
    if value is None:
        return None
    try:
        result = float(value)
        if np.isnan(result) or np.isinf(result):
            return None
        return result
    except (ValueError, TypeError):
        return None


def format_for_json(value):
    """Format value for JSON output, handling NaN, None, and decimals"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if np.isnan(value) or np.isinf(value):
            return None
        return value
    if hasattr(value, '__float__'):  # Decimal type
        try:
            float_val = float(value)
            if np.isnan(float_val) or np.isinf(float_val):
                return None
            return float_val
        except (ValueError, TypeError):
            return None
    return value


class CombinedDonchianScreener:
    """Combined Donchian screener with integrated ML enhancement - BUGS FIXED"""

    def __init__(self):
        logger.info("Combined Donchian Screener with ML Enhancement - ALL BUGS FIXED")

        # Initialize ML components
        self.ml_model = None
        self.ml_meta = {}
        self.ml_feature_names = []
        self.ml_model_loaded = False
        self.ml_model_version = None

        # Try to load ML model
        self._load_ml_model()

        if self.ml_model_loaded:
            logger.info("SUCCESS: ML model loaded and integrated")
        else:
            logger.warning("WARNING: ML model not available - will run without ML enhancement")

    def _find_project_root(self):
        """Find the project root directory dynamically"""
        current_dir = Path.cwd()
        required_dirs = ['mechanism', 'ml_training']

        search_dir = current_dir
        for _ in range(5):
            has_required = all((search_dir / req_dir).exists() for req_dir in required_dirs)
            if has_required:
                return search_dir
            parent = search_dir.parent
            if parent == search_dir:
                break
            search_dir = parent

        return current_dir

    def _find_model_files(self):
        """Find ML model files dynamically"""
        project_root = self._find_project_root()

        possible_model_dirs = [
            project_root / "ml_training" / "models",
            project_root / "models",
            project_root / "mechanism" / "models"
        ]

        all_model_files = []

        for model_dir in possible_model_dirs:
            if model_dir.exists():
                pattern = "momentum_predictor_v*.joblib"
                model_files = list(model_dir.glob(pattern))
                main_models = [f for f in model_files if "_scaler" not in f.name and "_features" not in f.name]

                for model_file in main_models:
                    base_name = model_file.stem
                    features_file = model_file.parent / f"{base_name}_features.json"
                    meta_file = model_file.parent / f"{base_name}_meta.json"

                    if not features_file.exists():
                        continue
                    # Only models trained by the honest-evaluation trainer carry a _meta.json
                    # contract (feature_set_version + holdout metrics). Older models (no meta) were
                    # trained on unvalidated data with train/serve feature skew -- see CLAUDE.md
                    # "ML audit 2026-09-20" -- so they are never auto-served.
                    if not meta_file.exists():
                        logger.warning(f"Ignoring legacy model {model_file.name}: no _meta.json contract")
                        continue
                    all_model_files.append(model_file)

        return all_model_files

    def _extract_timestamp_from_filename(self, filepath):
        """Extract timestamp from model filename for sorting"""
        filename = filepath.name
        match = re.search(r'momentum_predictor_v(\d{8}_\d{4})\.joblib', filename)
        if match:
            return match.group(1)
        return "00000000_0000"

    def _load_ml_model(self):
        """Load the latest ML model"""
        try:
            model_files = self._find_model_files()

            if not model_files:
                logger.warning("No ML models found - running without ML enhancement")
                return False

            # Get latest model
            latest_model = sorted(model_files, key=self._extract_timestamp_from_filename)[-1]
            logger.info(f"Loading ML model: {latest_model.name}")

            # Load main model
            self.ml_model = joblib.load(str(latest_model))
            self.ml_model_version = latest_model.stem

            # Feature names + training contract. No scaler: the model is a tree ensemble trained on raw
            # features with NaN kept as NaN.
            features_path = latest_model.parent / f"{latest_model.stem}_features.json"
            with open(features_path, 'r') as f:
                self.ml_feature_names = json.load(f)
            with open(latest_model.parent / f"{latest_model.stem}_meta.json", 'r') as f:
                self.ml_meta = json.load(f)

            if self.ml_meta.get('feature_set_version') != pf.FEATURE_SET_VERSION:
                logger.error(f"Model {latest_model.name} was trained on feature set "
                             f"{self.ml_meta.get('feature_set_version')!r}, code provides {pf.FEATURE_SET_VERSION!r} "
                             f"-- not serving it")
                self.ml_model = None
                return False

            self.ml_model_loaded = True
            logger.info(f"SUCCESS: ML model loaded ({len(self.ml_feature_names)} features)")
            return True

        except Exception as e:
            logger.error(f"Failed to load ML model: {e}")
            return False

    def get_all_symbols(self) -> List[str]:
        """Get ALL symbols from database"""
        try:
            query = "SELECT DISTINCT symbol FROM stock_prices ORDER BY symbol"
            results = db.execute_dict_query(query)
            symbols = [row['symbol'] for row in results]
            logger.info(f"Found {len(symbols)} symbols to screen")
            return symbols
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return []

    def get_stock_data_with_indicators(self, symbol: str, screening_date: str = None):
        """
        Get stock data with technical indicators for a specific date
        FIX: Uses exact screening date to ensure data consistency
        """
        try:
            if screening_date is None:
                screening_date = datetime.now().date()
            elif isinstance(screening_date, str):
                screening_date = datetime.strptime(screening_date, '%Y-%m-%d').date()

            # Get stock data with technical indicators for the EXACT screening date
            query = """
            SELECT 
                p.date, p.open, p.high, p.low, p.close, p.volume,
                t.donchian_high_20, t.donchian_low_20, t.donchian_mid_20,
                t.sma_10, t.sma_20, t.sma_50, t.rsi_14, t.atr_14, 
                t.volume_ratio, t.price_position, t.channel_width_pct,
                t.macd, t.bollinger_upper, t.bollinger_lower
            FROM stock_prices p
            LEFT JOIN technical_indicators t ON p.symbol = t.symbol AND p.date = t.date
            WHERE p.symbol = %s
            AND p.date <= %s
            ORDER BY p.date DESC
            LIMIT 5
            """

            results = db.execute_dict_query(query, (symbol, screening_date))
            return results

        except Exception as e:
            logger.error(f"Error getting stock data for {symbol}: {e}")
            return []

    def get_fundamentals(self, symbol: str, screening_date: str = None) -> Dict:
        """
        Get fundamental data for the screening date
        FIX: Uses exact screening date for consistency
        """
        try:
            if screening_date is None:
                screening_date = datetime.now().date()
            elif isinstance(screening_date, str):
                screening_date = datetime.strptime(screening_date, '%Y-%m-%d').date()

            query = """
            SELECT 
                market_cap, sector, industry, pe_ratio, pb_ratio, beta, 
                dividend_yield, quality_grade, growth_score, profitability_score, 
                financial_health_score, valuation_score, overall_quality_score
            FROM daily_fundamentals
            WHERE symbol = %s
            AND date <= %s
            ORDER BY date DESC
            LIMIT 1
            """

            results = db.execute_dict_query(query, (symbol, screening_date))
            if results:
                return results[0]

            return {}
        except Exception as e:
            logger.error(f"Error getting fundamentals for {symbol}: {e}")
            return {}

    def validate_breakout_signal(self, symbol: str, signal_data: Dict, screening_date: str) -> bool:
        """
        Validate that a breakout signal is still valid
        FIX: Prevents invalid signals from being enhanced
        """
        try:
            # Get current data to validate signal
            current_data = self.get_stock_data_with_indicators(symbol, screening_date)
            if len(current_data) < 2:
                return False

            latest = current_data[0]
            previous = current_data[1]

            # Check if required data exists
            if (safe_float(latest['donchian_high_20']) is None or
                    safe_float(latest['donchian_low_20']) is None):
                return False

            current_price = safe_float(latest['close'])
            donchian_high = safe_float(latest['donchian_high_20'])
            donchian_low = safe_float(latest['donchian_low_20'])
            prev_donchian_high = safe_float(previous['donchian_high_20'])
            prev_donchian_low = safe_float(previous['donchian_low_20'])
            prev_close = safe_float(previous['close'])

            if None in [current_price, donchian_high, donchian_low, prev_donchian_high, prev_donchian_low, prev_close]:
                return False

            # Calculate distances
            distance_to_high = ((donchian_high - current_price) / current_price) * 100
            distance_to_low = ((current_price - donchian_low) / current_price) * 100

            # Validate breakout type
            signal_type = signal_data.get('type', '')

            if signal_type == 'bullish_breakout':
                # Must still be above previous Donchian high
                return current_price > prev_donchian_high and prev_close <= prev_donchian_high
            elif signal_type == 'bearish_breakout':
                # Must still be below previous Donchian low
                return current_price < prev_donchian_low and prev_close >= prev_donchian_low
            elif signal_type == 'near_bullish':
                # Must still be within 3% of high
                return 0 < distance_to_high <= 3.0
            elif signal_type == 'near_bearish':
                # Must still be within 3% of low
                return 0 < distance_to_low <= 3.0

            return False

        except Exception as e:
            logger.warning(f"Signal validation failed for {symbol}: {e}")
            return False

    def _unavailable(self, reason: str, detail: str = '') -> Dict:
        """A prediction that is explicitly NOT available. Never substitutes a default probability."""
        return {
            'ml_momentum_probability': None,
            'ml_confidence': reason,
            'ml_prediction_available': False,
            'ml_error': detail or reason,
        }

    def _load_price_window(self, symbol: str, screening_date) -> pd.DataFrame:
        """Recent OHLCV bars straight from stock_prices (the same source the training dataset is built
        from). Enough bars for the longest feature lookback plus the discontinuity check."""
        rows = db.execute_dict_query(
            "SELECT date, open, high, low, close, volume FROM stock_prices "
            "WHERE symbol = %s AND date <= %s ORDER BY date DESC LIMIT %s",
            (symbol, screening_date, pf.LOOKBACK_BARS + 70))
        px = pd.DataFrame(rows)
        if px.empty:
            return px
        for col in pf.OHLCV:
            px[col] = pd.to_numeric(px[col], errors='coerce')
        px['date'] = pd.to_datetime(px['date'])
        return px.dropna(subset=['close']).sort_values('date').reset_index(drop=True)

    def predict_ml_momentum(self, symbol: str, signal_data: Dict, stock_data: List[Dict], fundamentals: Dict) -> Dict:
        """
        Score one ACTUAL breakout with the model, using the shared feature module
        (ml_training/features/price_features.py) on stock_prices -- identical code to training.

        Returns an explicit "unavailable" result (ml_prediction_available=False, reason in ml_confidence)
        instead of a number whenever the model was not trained for the situation or the inputs cannot be
        trusted: no model, near-breakout (never trained on), signal no longer valid, price
        discontinuity in the lookback, illiquid, or any model feature missing. `stock_data` is accepted
        for call-compatibility and intentionally unused: features come from stock_prices, not from
        technical_indicators rows that may sit on an older price basis.
        """
        if not self.ml_model_loaded:
            return self._unavailable('no_model', 'No validated model loaded')

        try:
            signal_type = signal_data.get('type', '')
            if signal_type not in ('bullish_breakout', 'bearish_breakout'):
                # The model was trained only on bars that actually broke the channel.
                return self._unavailable('not_a_breakout', f'model is only defined for breakouts, got {signal_type}')

            screening_date = signal_data.get('screening_date', datetime.now().date())
            if not self.validate_breakout_signal(symbol, signal_data, screening_date):
                return self._unavailable('invalid_signal', 'Signal no longer valid')

            px = self._load_price_window(symbol, screening_date)
            if len(px) < pf.LOOKBACK_BARS // 2:
                return self._unavailable('insufficient_history', f'{len(px)} bars')

            disc = pf.find_discontinuities(px)
            if pf.contaminated_mask(len(px), disc['pos'], forward=0)[-1]:
                return self._unavailable('data_discontinuity',
                                         'price series has a discontinuity inside the feature lookback')

            ind = pf.compute_indicators(px)
            direction = pf.detect_breakouts(ind)[-1]
            expected = 1 if signal_type == 'bullish_breakout' else -1
            if direction != expected:
                return self._unavailable('signal_mismatch', 'stock_prices does not reproduce the breakout')
            if not ind['dollar_vol_20'].iloc[-1] >= pf.MIN_DOLLAR_VOLUME_20:
                return self._unavailable('illiquid', '20-day dollar volume below the tradability floor')

            row = pf.build_breakout_features(ind, np.array([len(px) - 1]), np.array([direction]),
                                             (fundamentals or {}).get('sector'))
            missing = [f for f in self.ml_feature_names if f not in row.columns]
            if missing:
                logger.error(f"Model {self.ml_model_version} needs features the code does not produce: {missing}")
                return self._unavailable('feature_mismatch', f'missing features {missing}')

            probability = float(self.ml_model.predict_proba(row[self.ml_feature_names])[0, 1])
            probability_pct = probability * 100

            # Confidence tiers (unchanged thresholds -- known to be uncalibrated relative to the base
            # rate; tracked as a separate fix in CLAUDE.md "ML audit 2026-09-20").
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
                'ml_raw_probability': probability,
                'ml_model_version': self.ml_model_version,
                'ml_target': self.ml_meta.get('target'),
            }

        except Exception as e:
            logger.warning(f"ML prediction failed for {symbol}: {e}")
            return self._unavailable('error', str(e))

    def get_trade_recommendation(self, ml_result: Dict, signal_type: str) -> str:
        """Generate trade recommendation based on ML prediction"""
        probability = ml_result.get('ml_raw_probability', 0.5)
        confidence = ml_result.get('ml_confidence', 'unknown')

        if probability >= 0.8 and confidence in ['high', 'very_high']:
            return 'strong_buy' if 'bullish' in signal_type else 'strong_sell'
        elif probability >= 0.7:
            return 'buy' if 'bullish' in signal_type else 'sell'
        elif probability >= 0.5:
            return 'hold'
        else:
            return 'avoid'

    def calculate_risk_score(self, ml_result: Dict, signal_data: Dict) -> int:
        """Calculate risk score (0-100, higher = more risky)"""
        probability = ml_result.get('ml_raw_probability', 0.5)

        base_risk = 50

        # Risk adjustment based on ML confidence
        if probability >= 0.8:
            risk_adjustment = -20
        elif probability >= 0.6:
            risk_adjustment = -10
        elif probability <= 0.4:
            risk_adjustment = 20
        else:
            risk_adjustment = 0

        # Volume risk adjustment
        volume_ratio = signal_data.get('volume_ratio', 1.0)
        volume_risk = 10 if volume_ratio < 0.8 else -5

        total_risk = base_risk + risk_adjustment + volume_risk
        return max(0, min(100, int(total_risk)))

    def format_market_cap(self, market_cap) -> str:
        """Format market cap for display"""
        market_cap = safe_float(market_cap)
        if not market_cap or market_cap == 0:
            return "Unknown"

        if market_cap >= 1_000_000_000_000:
            return f"${market_cap / 1_000_000_000_000:.1f}T"
        elif market_cap >= 1_000_000_000:
            return f"${market_cap / 1_000_000_000:.1f}B"
        elif market_cap >= 1_000_000:
            return f"${market_cap / 1_000_000:.1f}M"
        else:
            return f"${market_cap:,.0f}"

    def calculate_signal_strength(self, breakout_type: str, distance_to_high: float, distance_to_low: float) -> str:
        """Calculate signal strength for frontend display"""
        if 'breakout' in breakout_type and 'near' not in breakout_type:
            return "very_strong"

        distance = distance_to_high if 'bullish' in breakout_type else distance_to_low

        if distance <= 0.5:
            return "very_strong"
        elif distance <= 1.0:
            return "strong"
        elif distance <= 2.0:
            return "medium"
        else:
            return "weak"

    def get_color_code(self, breakout_type: str, ml_confidence: str = None) -> str:
        """Get color code for frontend styling with ML enhancement"""
        if ml_confidence in ['very_high', 'high']:
            return '#10b981'  # Green for high confidence
        elif ml_confidence == 'medium':
            return '#3b82f6'  # Blue for medium confidence
        elif ml_confidence in ['low', 'very_low']:
            return '#f59e0b'  # Orange for low confidence

        # Fallback to original colors
        color_map = {
            'bullish_breakout': '#22c55e',
            'bearish_breakout': '#ef4444',
            'near_bullish': '#3b82f6',
            'near_bearish': '#f59e0b'
        }
        return color_map.get(breakout_type, '#6b7280')

    def get_summary_text(self, breakout_type: str, distance_to_high: float, distance_to_low: float,
                         price: float, ml_result: Dict = None) -> str:
        """Get summary text with ML enhancement"""
        base_text = ""
        if breakout_type == 'bullish_breakout':
            base_text = f"Bullish breakout at ${price:.2f}"
        elif breakout_type == 'bearish_breakout':
            base_text = f"Bearish breakout at ${price:.2f}"
        elif breakout_type == 'near_bullish':
            base_text = f"Near bullish breakout - {distance_to_high:.1f}% away"
        elif breakout_type == 'near_bearish':
            base_text = f"Near bearish breakout - {distance_to_low:.1f}% away"

        # Add ML enhancement to summary
        if ml_result and ml_result.get('ml_prediction_available'):
            ml_prob = ml_result.get('ml_momentum_probability')
            ml_conf = ml_result.get('ml_confidence')
            base_text += f" | AI: {ml_prob:.1f}% ({ml_conf})"

        return base_text

    def check_donchian_breakout(self, symbol: str, screening_date: str = None) -> Optional[Dict]:
        """
        Check for Donchian breakouts with integrated ML enhancement
        FIX: Uses consistent data throughout the process
        """
        try:
            if screening_date is None:
                screening_date = datetime.now().date().isoformat()

            # Get stock data for the screening date
            stock_data = self.get_stock_data_with_indicators(symbol, screening_date)
            if len(stock_data) < 2:
                return None

            latest = stock_data[0]
            previous = stock_data[1]

            # Skip if missing Donchian data
            if (safe_float(latest['donchian_high_20']) is None or
                    safe_float(latest['donchian_low_20']) is None or
                    safe_float(previous['donchian_high_20']) is None or
                    safe_float(previous['donchian_low_20']) is None):
                return None

            # Get fundamentals for the same date
            fundamentals = self.get_fundamentals(symbol, screening_date)

            current_price = safe_float(latest['close'])
            donchian_high = safe_float(latest['donchian_high_20'])
            donchian_low = safe_float(latest['donchian_low_20'])
            donchian_mid = safe_float(latest['donchian_mid_20'])
            prev_donchian_high = safe_float(previous['donchian_high_20'])
            prev_donchian_low = safe_float(previous['donchian_low_20'])
            prev_close = safe_float(previous['close'])

            # Calculate distances and percentages
            distance_to_high = ((donchian_high - current_price) / current_price) * 100
            distance_to_low = ((current_price - donchian_low) / current_price) * 100
            price_change_pct = ((current_price - prev_close) / prev_close) * 100
            channel_width = donchian_high - donchian_low
            channel_width_pct = (channel_width / donchian_mid) * 100 if donchian_mid else 0
            price_position = ((current_price - donchian_low) / channel_width) * 100 if channel_width > 0 else 50

            breakout_type = None
            urgency = "none"

            # Detect breakout type
            if (current_price > prev_donchian_high and prev_close <= prev_donchian_high):
                breakout_type = "bullish_breakout"
                urgency = "immediate"
            elif (current_price < prev_donchian_low and prev_close >= prev_donchian_low):
                breakout_type = "bearish_breakout"
                urgency = "immediate"
            elif (0 < distance_to_high <= 3.0):
                breakout_type = "near_bullish"
                if distance_to_high <= 1.0:
                    urgency = "very_high"
                elif distance_to_high <= 2.0:
                    urgency = "high"
                else:
                    urgency = "medium"
            elif (0 < distance_to_low <= 3.0):
                breakout_type = "near_bearish"
                if distance_to_low <= 1.0:
                    urgency = "very_high"
                elif distance_to_low <= 2.0:
                    urgency = "high"
                else:
                    urgency = "medium"

            if not breakout_type:
                return None

            # Create base signal data
            signal_data = {
                'symbol': symbol,
                'type': breakout_type,
                'urgency': urgency,
                'timestamp': datetime.now().isoformat(),
                'screening_date': screening_date,
                'current_price': round(current_price, 2),
                'donchian_high': round(donchian_high, 2),
                'donchian_low': round(donchian_low, 2),
                'distance_to_breakout': round(distance_to_high if 'bullish' in breakout_type else distance_to_low, 2),
                'volume_ratio': round(safe_float(latest['volume_ratio']), 2) if latest['volume_ratio'] else 1.0
            }

            # INTEGRATED ML ENHANCEMENT using SAME data
            ml_result = self.predict_ml_momentum(symbol, signal_data, stock_data, fundamentals)

            # Skip signals that are no longer valid (major bug fix)
            if (ml_result.get('ml_confidence') == 'invalid_signal' or
                    ml_result.get('ml_error') == 'Signal no longer valid'):
                logger.debug(f"Skipping {symbol}: Signal no longer valid")
                return None

            # Calculate trading parameters
            atr_value = safe_float(latest['atr_14'])
            if atr_value and atr_value > 0:
                atr = atr_value
            else:
                atr = current_price * 0.02

            if 'bullish' in breakout_type:
                stop_loss = round(current_price - (atr * 2), 2)
                target_price = round(current_price + (atr * 3), 2)
            else:
                stop_loss = round(current_price + (atr * 2), 2)
                target_price = round(current_price - (atr * 3), 2)

            # Build complete signal with ML enhancement
            enhanced_signal = {
                # Core identification
                'symbol': symbol,
                'type': breakout_type,
                'urgency': urgency,
                'timestamp': datetime.now().isoformat(),
                'screening_date': screening_date,

                # Price data
                'current_price': round(current_price, 2),
                'open_price': round(safe_float(latest['open']), 2) if latest['open'] else None,
                'high_price': round(safe_float(latest['high']), 2) if latest['high'] else None,
                'low_price': round(safe_float(latest['low']), 2) if latest['low'] else None,
                'price_change_pct': round(price_change_pct, 2),

                # Donchian channel data
                'donchian_high': round(donchian_high, 2),
                'donchian_low': round(donchian_low, 2),
                'donchian_mid': round(donchian_mid, 2) if donchian_mid else None,
                'channel_width': round(channel_width, 2),
                'channel_width_pct': round(channel_width_pct, 2),
                'price_position_in_channel': round(price_position, 1),

                # Distance analysis
                'distance_to_high_pct': round(distance_to_high, 2),
                'distance_to_low_pct': round(distance_to_low, 2),
                'distance_to_breakout': round(distance_to_high if 'bullish' in breakout_type else distance_to_low, 2),

                # Volume data
                'volume': int(safe_float(latest['volume'])) if latest['volume'] else 0,
                'volume_formatted': f"{int(safe_float(latest['volume'])):,}" if latest['volume'] else "0",
                'volume_ratio': round(safe_float(latest['volume_ratio']), 2) if latest['volume_ratio'] else 1.0,

                # Technical indicators
                'rsi_14': format_for_json(latest['rsi_14']),
                'atr_14': format_for_json(latest['atr_14']),
                'sma_20': format_for_json(latest['sma_20']),
                'sma_50': format_for_json(latest['sma_50']),

                # Trading suggestions
                'stop_loss_price': stop_loss,
                'target_price': target_price,
                'reward_risk_ratio': 3.0,
                'position_size_suggestion': "2-3% of portfolio",

                # Fundamental data
                'market_cap': format_for_json(fundamentals.get('market_cap', 0)),
                'market_cap_formatted': self.format_market_cap(fundamentals.get('market_cap', 0)),
                'sector': fundamentals.get('sector', 'Unknown'),
                'industry': fundamentals.get('industry', 'Unknown'),
                'pe_ratio': format_for_json(fundamentals.get('pe_ratio')),
                'pb_ratio': format_for_json(fundamentals.get('pb_ratio')),
                'beta': format_for_json(fundamentals.get('beta')),
                'dividend_yield': format_for_json(fundamentals.get('dividend_yield')),
                'quality_grade': fundamentals.get('quality_grade'),

                # Quality scores
                'growth_score': format_for_json(fundamentals.get('growth_score')),
                'profitability_score': format_for_json(fundamentals.get('profitability_score')),
                'financial_health_score': format_for_json(fundamentals.get('financial_health_score')),
                'valuation_score': format_for_json(fundamentals.get('valuation_score')),
                'overall_quality_score': format_for_json(fundamentals.get('overall_quality_score')),

                # ML Enhancement (integrated)
                'ml_momentum_probability': ml_result.get('ml_momentum_probability'),
                'ml_confidence': ml_result.get('ml_confidence', 'unknown'),
                'ml_prediction_available': ml_result.get('ml_prediction_available', False),
                'ml_trade_recommendation': self.get_trade_recommendation(ml_result, breakout_type),
                'ml_risk_score': self.calculate_risk_score(ml_result, signal_data),
                'ml_predicted_momentum_days': int(5 + (ml_result.get('ml_raw_probability', 0.5) * 15)),
                'ml_model_version': self.ml_model_version,

                # Display helpers (ML-enhanced)
                'display_name': symbol,
                'signal_strength': self.calculate_signal_strength(breakout_type, distance_to_high, distance_to_low),
                'color_code': self.get_color_code(breakout_type, ml_result.get('ml_confidence')),
                'icon': 'trending-up' if 'bullish' in breakout_type else 'trending-down',
                'summary_text': self.get_summary_text(breakout_type, distance_to_high, distance_to_low, current_price,
                                                      ml_result)
            }

            return enhanced_signal

        except Exception as e:
            logger.error(f"Error checking {symbol}: {e}")
            return None

    def screen_symbols(self, symbols: List[str]) -> List[Dict]:
        """Screen symbols for breakouts with integrated ML enhancement"""
        results = []
        screening_date = datetime.now().date().isoformat()

        logger.info(f"Screening {len(symbols)} symbols with integrated ML enhancement...")

        for i, symbol in enumerate(symbols, 1):
            try:
                enhanced_signal = self.check_donchian_breakout(symbol, screening_date)
                if enhanced_signal:
                    results.append(enhanced_signal)
                    ml_info = ""
                    if enhanced_signal.get('ml_prediction_available'):
                        ml_prob = enhanced_signal.get('ml_momentum_probability')
                        ml_conf = enhanced_signal.get('ml_confidence')
                        ml_info = f" | ML: {ml_prob:.1f}% ({ml_conf})"

                    logger.info(
                        f"Found {enhanced_signal['type']} for {symbol} at ${enhanced_signal['current_price']:.2f}{ml_info}")

                # Progress update
                if i % 100 == 0:
                    logger.info(f"Screened {i}/{len(symbols)}, found {len(results)} signals")

            except Exception as e:
                logger.warning(f"Error screening {symbol}: {e}")
                continue

        # Sort results by ML momentum probability if available
        if self.ml_model_loaded:
            results.sort(key=lambda x: x.get('ml_momentum_probability', 0), reverse=True)
            logger.info("Results sorted by ML momentum probability")

        return results

    def clean_data_for_json(self, data):
        """Clean data structure for JSON output"""
        if isinstance(data, dict):
            return {k: self.clean_data_for_json(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.clean_data_for_json(item) for item in data]
        elif isinstance(data, float):
            if np.isnan(data) or np.isinf(data):
                return None
            return round(data, 6)
        elif hasattr(data, '__float__'):
            try:
                float_val = float(data)
                if np.isnan(float_val) or np.isinf(float_val):
                    return None
                return round(float_val, 6)
            except (ValueError, TypeError):
                return None
        else:
            return data

    def save_results(self, results: List[Dict]) -> bool:
        """Save comprehensive results with ML enhancement"""
        try:
            # Create output directories
            output_dir = "breakout_results"
            frontend_dir = "frontend_data"
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(frontend_dir, exist_ok=True)

            # Organize results by type
            bullish_breakouts = [r for r in results if r['type'] == 'bullish_breakout']
            bearish_breakouts = [r for r in results if r['type'] == 'bearish_breakout']
            near_bullish = [r for r in results if r['type'] == 'near_bullish']
            near_bearish = [r for r in results if r['type'] == 'near_bearish']

            # ML-enhanced sorting
            def sort_key(x):
                # Primary: ML momentum probability (if available)
                ml_prob = x.get('ml_momentum_probability', 0) or 0
                # Secondary: urgency
                urgency_order = {'immediate': 0, 'very_high': 1, 'high': 2, 'medium': 3, 'low': 4}
                urgency_score = urgency_order.get(x['urgency'], 5)
                # Tertiary: distance to breakout
                distance = x['distance_to_breakout']

                return (-ml_prob, urgency_score, distance)

            bullish_breakouts.sort(key=sort_key)
            bearish_breakouts.sort(key=sort_key)
            near_bullish.sort(key=sort_key)
            near_bearish.sort(key=sort_key)

            # Calculate enhanced statistics
            total_signals = len(results)
            ml_enhanced_count = len([r for r in results if r.get('ml_prediction_available')])
            high_confidence_ml = len([r for r in results if r.get('ml_confidence') in ['high', 'very_high']])

            # ML statistics
            ml_probabilities = [r.get('ml_momentum_probability') for r in results
                                if r.get('ml_momentum_probability') is not None]
            avg_ml_probability = round(np.mean(ml_probabilities), 1) if ml_probabilities else None

            # Sector breakdown
            sector_counts = {}
            for result in results:
                sector = result.get('sector', 'Unknown')
                sector_counts[sector] = sector_counts.get(sector, 0) + 1

            # ML confidence breakdown
            ml_confidence_counts = {}
            for result in results:
                confidence = result.get('ml_confidence', 'no_model')
                ml_confidence_counts[confidence] = ml_confidence_counts.get(confidence, 0) + 1

            # Trade recommendations breakdown
            trade_recommendations = {}
            for result in results:
                rec = result.get('ml_trade_recommendation', 'unknown')
                trade_recommendations[rec] = trade_recommendations.get(rec, 0) + 1

            # Create comprehensive output
            output_data = {
                'metadata': {
                    'timestamp': datetime.now().isoformat(),
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'screening_date': datetime.now().date().isoformat(),
                    'version': '3.0_ml_integrated',
                    'data_source': 'combined_donchian_screener',
                    'ml_enhanced': self.ml_model_loaded,
                    'ml_model_version': self.ml_model_version
                },
                'summary': {
                    'total_signals': total_signals,
                    'bullish_breakouts': len(bullish_breakouts),
                    'bearish_breakouts': len(bearish_breakouts),
                    'near_bullish': len(near_bullish),
                    'near_bearish': len(near_bearish),
                    'ml_enhanced_signals': ml_enhanced_count,
                    'high_confidence_ml_signals': high_confidence_ml,
                    'average_ml_probability': avg_ml_probability,
                    'sector_breakdown': sector_counts,
                    'ml_confidence_breakdown': ml_confidence_counts,
                    'trade_recommendations': trade_recommendations
                },
                'signals': {
                    'bullish_breakouts': bullish_breakouts,
                    'bearish_breakouts': bearish_breakouts,
                    'near_bullish': near_bullish,
                    'near_bearish': near_bearish
                },
                'ai_insights': {
                    'top_ai_picks': results[:20] if self.ml_model_loaded else [],
                    'high_confidence_signals': [r for r in results if r.get('ml_confidence') in ['high', 'very_high']],
                    'strong_buy_recommendations': [r for r in results if
                                                   r.get('ml_trade_recommendation') == 'strong_buy'],
                    'strong_sell_recommendations': [r for r in results if
                                                    r.get('ml_trade_recommendation') == 'strong_sell']
                },
                'top_signals': {
                    'most_urgent': sorted(results, key=lambda x: (
                        {'immediate': 0, 'very_high': 1, 'high': 2, 'medium': 3}.get(x['urgency'], 4),
                        x['distance_to_breakout']
                    ))[:10],
                    'closest_to_breakout': sorted(results, key=lambda x: x['distance_to_breakout'])[:10],
                    'highest_ml_probability': sorted(results,
                                                     key=lambda x: x.get('ml_momentum_probability', 0),
                                                     reverse=True)[:10] if self.ml_model_loaded else []
                }
            }

            # Clean the output data
            output_data = self.clean_data_for_json(output_data)

            # Save to multiple locations
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

            # Save timestamped file in breakout_results
            filename = f"{output_dir}/donchian_breakouts_ml_enhanced_{timestamp}.json"
            with open(filename, 'w') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            # Save latest file for frontend in frontend_data
            latest_filename = f"{frontend_dir}/latest_breakouts_ml_enhanced.json"
            with open(latest_filename, 'w') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            # Also save in breakout_results for backward compatibility
            latest_filename_br = f"{output_dir}/latest_breakouts.json"
            with open(latest_filename_br, 'w') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            logger.info(f"ML-enhanced results saved to {filename}")
            logger.info(f"Latest ML-enhanced results saved to {latest_filename}")

            return True

        except Exception as e:
            logger.error(f"Error saving results: {e}")
            return False

    def run_screening(self, test_symbols: Optional[List[str]] = None):
        """Run the combined screening with ML enhancement"""
        start_time = datetime.now()

        if test_symbols:
            symbols = test_symbols
            logger.info(f"Test mode with symbols: {symbols}")
        else:
            symbols = self.get_all_symbols()

        if not symbols:
            logger.error("No symbols to screen")
            return

        logger.info(f"Starting Combined Donchian + ML screening at {start_time}")
        if self.ml_model_loaded:
            logger.info(f"ML Model: {self.ml_model_version} with {len(self.ml_feature_names)} features")

        # Screen for breakouts with integrated ML enhancement
        results = self.screen_symbols(symbols)

        # Save results
        if results:
            self.save_results(results)

        # Report results
        end_time = datetime.now()
        duration = end_time - start_time

        bullish_breakouts = len([r for r in results if r['type'] == 'bullish_breakout'])
        bearish_breakouts = len([r for r in results if r['type'] == 'bearish_breakout'])
        near_bullish = len([r for r in results if r['type'] == 'near_bullish'])
        near_bearish = len([r for r in results if r['type'] == 'near_bearish'])

        ml_enhanced = len([r for r in results if r.get('ml_prediction_available')])
        high_confidence = len([r for r in results if r.get('ml_confidence') in ['high', 'very_high']])

        logger.info(f"""
Combined Screening completed in {duration}
Symbols screened: {len(symbols)}
Total signals found: {len(results)}

ACTUAL BREAKOUTS: {bullish_breakouts + bearish_breakouts}
  Bullish: {bullish_breakouts}
  Bearish: {bearish_breakouts}

NEAR BREAKOUTS: {near_bullish + near_bearish}
  Near Bullish: {near_bullish}
  Near Bearish: {near_bearish}

ML ENHANCEMENT:
  ML Enhanced Signals: {ml_enhanced}
  High Confidence ML: {high_confidence}
  Model Version: {self.ml_model_version or 'Not loaded'}
        """)

        # Show top ML-enhanced results
        if results and self.ml_model_loaded:
            logger.info("Top ML-enhanced signals:")
            for i, result in enumerate(results[:10], 1):
                symbol = result['symbol']
                current_price = result['current_price']
                signal_type = result['type'].replace('_', ' ').title()
                ml_prob = result.get('ml_momentum_probability')
                ml_conf = result.get('ml_confidence')
                ml_rec = result.get('ml_trade_recommendation')

                if ml_prob is not None:
                    logger.info(f"  {i}. {symbol}: {signal_type} at ${current_price:.2f}")
                    logger.info(f"     ML: {ml_prob:.1f}% probability ({ml_conf}) - {ml_rec}")
                else:
                    logger.info(f"  {i}. {symbol}: {signal_type} at ${current_price:.2f} - ML: N/A")
        elif results:
            logger.info("Top signals (no ML enhancement available):")
            for i, result in enumerate(results[:10], 1):
                symbol = result['symbol']
                current_price = result['current_price']
                signal_type = result['type'].replace('_', ' ').title()
                urgency = result['urgency']
                distance = result['distance_to_breakout']

                logger.info(f"  {i}. {symbol}: {signal_type} - {distance}% away at ${current_price:.2f} ({urgency})")
        else:
            logger.info("No breakout signals found")


def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description='Combined Donchian Screener with ML Enhancement')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    args = parser.parse_args()

    screener = CombinedDonchianScreener()

    if args.test:
        screener.run_screening(test_symbols=args.test)
    else:
        screener.run_screening()


if __name__ == "__main__":
    main()