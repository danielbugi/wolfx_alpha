# -*- coding: utf-8 -*-
# mechanism/screeners/multi_timeframe_screener.py - FULLY FIXED VERSION
"""
Multi-timeframe screener with ML enhancement integration - ALL BUGS FIXED
FIXES: Correct breakout detection logic, proper data fetching, fundamental data inclusion
"""

import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings

# Import shared infrastructure
import sys
import os

# Fix import paths for your directory structure
current_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from shared import (
    config, db, setup_logging, retry_on_failure,
    timing_decorator, date_utils, data_validation,
    format_number, safe_divide
)

# ML prediction logging — records every ML-enhanced signal to the
# ml_predictions table so ml_training/evaluation/performance_tracker.py has
# something to evaluate. Previously never wired up (see MILESTONES.md,
# Milestone 2/4): the model was never retrained because nothing ever
# recorded whether its predictions actually panned out.
PREDICTION_LOGGING_AVAILABLE = False
performance_tracker = None
try:
    project_root = os.path.dirname(parent_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from ml_training.evaluation.performance_tracker import MLPerformanceTracker

    performance_tracker = MLPerformanceTracker()
    PREDICTION_LOGGING_AVAILABLE = True
except ImportError as e:
    print(f"ML prediction logging not available: {e}")

# FIXED ML Import - Updated for your mechanism directory structure
ML_AVAILABLE = False
ml_enhancer_class = None

try:
    # Your ML enhancer is in mechanism/ml_enhancement/ml_signal_enhancer.py
    # Add the path to sys.path
    ml_enhancer_path = os.path.join(parent_dir, "ml_enhancement")  # mechanism/ml_enhancement

    if ml_enhancer_path not in sys.path:
        sys.path.insert(0, ml_enhancer_path)

    # Import the ML enhancer
    import ml_signal_enhancer

    # Check if it has the CombinedDonchianScreener or MLMomentumEnhancer class
    if hasattr(ml_signal_enhancer, 'CombinedDonchianScreener'):
        ml_enhancer_class = ml_signal_enhancer.CombinedDonchianScreener
        print(f"ML Enhancement (CombinedDonchianScreener) imported from: {ml_enhancer_path}")
    elif hasattr(ml_signal_enhancer, 'MLMomentumEnhancer'):
        ml_enhancer_class = ml_signal_enhancer.MLMomentumEnhancer
        print(f"ML Enhancement (MLMomentumEnhancer) imported from: {ml_enhancer_path}")
    else:
        print(f"ML enhancer found but no suitable class detected")
        ml_enhancer_class = None

    if ml_enhancer_class:
        ML_AVAILABLE = True

except ImportError as e:
    print(f"ML Enhancement not available: {e}")
    ML_AVAILABLE = False

warnings.filterwarnings('ignore')
logger = setup_logging("multi_timeframe_ml_screener")


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


class MultiTimeframeMLScreener:
    """Enhanced multi-timeframe screener with ML integration - ALL BUGS FIXED"""

    def __init__(self):
        """Initialize the enhanced screener"""
        self.batch_size = config.data_batch_size

        # Initialize ML enhancer if available
        self.failed = False  # set when a run aborts, so the CLI can exit non-zero (pipeline `set -e`)
        self.ml_enhancer = None
        if ML_AVAILABLE and ml_enhancer_class:
            try:
                self.ml_enhancer = ml_enhancer_class()

                # Check if it's the CombinedDonchianScreener (which has different methods)
                if hasattr(self.ml_enhancer, 'ml_model_loaded'):
                    # It's likely the CombinedDonchianScreener
                    logger.info("CombinedDonchianScreener ML Enhancement initialized")
                elif hasattr(self.ml_enhancer, 'model_loaded'):
                    # It's likely the MLMomentumEnhancer
                    logger.info("MLMomentumEnhancer ML Enhancement initialized")
                else:
                    logger.info("ML Enhancement initialized (unknown type)")

            except Exception as e:
                logger.error(f"ML Enhancement initialization failed: {e}")
                self.ml_enhancer = None

        logger.info("Multi-Timeframe ML Screener initialized")
        logger.info(f"ML Enhancement: {'Available' if self.ml_enhancer else 'Not Available'}")

    @timing_decorator()
    def screen_all_symbols(self) -> Dict:
        """Main screening function with ML enhancement"""
        try:
            logger.info("Starting multi-timeframe ML screening")
            start_time = datetime.now()

            # Get all signals with timeframe analysis
            all_signals = self.get_multi_timeframe_signals()

            if not all_signals:
                logger.warning("No multi-timeframe signals found")
                return self._create_empty_result()

            logger.info(f"Found {len(all_signals)} multi-timeframe signals")

            # Filter high-quality signals (Grade A, B, C)
            high_quality_signals = [
                signal for signal in all_signals
                if signal.get('alignment_grade', 'F') in ['A', 'B', 'C']
            ]

            logger.info(f"{len(high_quality_signals)} high-quality signals (Grade A-C)")

            # Enhance high-quality signals with ML predictions
            ml_enhanced_signals = []
            if self.ml_enhancer and high_quality_signals:
                try:
                    logger.info("Applying ML enhancement to high-quality signals...")
                    ml_enhanced_signals = self.enhance_signals_with_ml(high_quality_signals)
                    logger.info(f"ML enhancement completed: {len(ml_enhanced_signals)} signals processed")
                except Exception as e:
                    logger.error(f"ML enhancement failed: {e}")
                    ml_enhanced_signals = high_quality_signals  # Fallback to non-ML enhanced
            else:
                ml_enhanced_signals = high_quality_signals

            # Create comprehensive results
            results = self._create_enhanced_results(all_signals, ml_enhanced_signals)

            # _create_enhanced_results() swallows its own errors and returns an EMPTY result. Saving that
            # would silently replace the last good frontend_data/latest_*.json with nothing (this is
            # exactly what happened on 2026-09-20 when a null ML probability raised inside it). Fail
            # loudly instead and leave the previous output in place.
            if all_signals and results.get('summary', {}).get('total_signals', 0) == 0:
                raise RuntimeError(f"Result assembly failed for {len(all_signals)} signals; "
                                   f"previous output left untouched")

            # Save results
            self.save_results(results)

            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"Multi-timeframe ML screening completed in {duration:.1f}s")

            return results

        except Exception as e:
            logger.error(f"Error in multi-timeframe ML screening: {e}")
            self.failed = True
            return self._create_empty_result()

    def enhance_signals_with_ml(self, signals: List[Dict]) -> List[Dict]:
        """Enhance signals with ML predictions while preserving multi-timeframe data - FIXED"""
        try:
            if not self.ml_enhancer:
                logger.warning("ML enhancer not available")
                return signals

            logger.info(f"Enhancing {len(signals)} signals with ML predictions...")

            # FIXED: Process each signal individually with proper data fetching
            ml_predictions = []

            for signal in signals:
                try:
                    symbol = signal['symbol']

                    # Create ML-compatible signal format
                    ml_signal = {
                        'symbol': symbol,
                        'type': signal['signal_type'],
                        'urgency': signal['urgency'],
                        'current_price': signal['current_price'],
                        'volume_ratio': signal.get('volume_ratio', 1.0),
                        'rsi_14': signal.get('rsi_14', 50.0),
                        'distance_to_breakout': signal.get('distance_to_breakout', 0.0),
                        'screening_date': signal.get('screening_date', datetime.now().date())
                    }

                    # FIXED: Get the actual data needed for ML prediction
                    if hasattr(self.ml_enhancer, 'predict_ml_momentum'):
                        # Fetch fresh stock and fundamental data for ML prediction
                        stock_data = self.ml_enhancer.get_stock_data_with_indicators(symbol)
                        fundamentals = self.ml_enhancer.get_fundamentals(symbol)

                        # Call ML prediction with proper data
                        ml_result = self.ml_enhancer.predict_ml_momentum(
                            symbol, ml_signal, stock_data, fundamentals
                        )

                        # Combine original signal data with ML results
                        enhanced_signal = {**ml_signal, **ml_result}
                        ml_predictions.append(enhanced_signal)

                    elif hasattr(self.ml_enhancer, 'enhance_signal'):
                        # Alternative ML enhancement method
                        enhanced = self.ml_enhancer.enhance_signal(ml_signal)
                        ml_predictions.append(enhanced)
                    else:
                        # Fallback without ML enhancement
                        ml_predictions.append(ml_signal)

                except Exception as e:
                    logger.warning(f"ML prediction failed for {signal.get('symbol', 'UNKNOWN')}: {e}")
                    # Add signal without ML enhancement
                    fallback_signal = {
                        'symbol': signal['symbol'],
                        'type': signal['signal_type'],
                        'urgency': signal['urgency'],
                        'current_price': signal['current_price'],
                        'volume_ratio': signal.get('volume_ratio', 1.0),
                        'rsi_14': signal.get('rsi_14', 50.0),
                        'distance_to_breakout': signal.get('distance_to_breakout', 0.0),
                        'screening_date': signal.get('screening_date', datetime.now().date()),
                        'ml_momentum_probability': None,
                        'ml_confidence': 'error',
                        'ml_prediction_available': False
                    }
                    ml_predictions.append(fallback_signal)

            # Merge ML predictions back into multi-timeframe signals
            enhanced_signals = []
            for i, signal in enumerate(signals):
                if i < len(ml_predictions):
                    ml_data = ml_predictions[i]

                    # Preserve all multi-timeframe data and add ML predictions
                    enhanced_signal = signal.copy()

                    # FIXED: Properly extract ML results
                    ml_probability = ml_data.get('ml_momentum_probability')
                    ml_confidence = ml_data.get('ml_confidence', 'unknown')
                    ml_available = ml_data.get('ml_prediction_available', False)

                    enhanced_signal.update({
                        'ml_momentum_probability': ml_probability,
                        'ml_confidence': ml_confidence,
                        'ml_prediction_available': ml_available,
                        'ml_trade_recommendation': ml_data.get('ml_trade_recommendation', 'hold'),
                        'ml_risk_score': ml_data.get('ml_risk_score', 50.0) or 50.0,
                        'ml_predicted_momentum_days': ml_data.get('ml_predicted_momentum_days', 7.0) or 7.0,
                        'ml_model_version': ml_data.get('ml_model_version', 'unknown')
                    })

                    # Create combined score with null handling: Timeframe Alignment + ML Probability
                    alignment_score = enhanced_signal.get('alignment_score', 0) or 0

                    # Weighted combination: 60% timeframe alignment + 40% ML probability.
                    # A signal WITHOUT an ML score (no validated model, near-breakout, illiquid, ...)
                    # contributes 0 for the ML part. It used to fall back to the raw alignment score,
                    # i.e. 100% weight on alignment, which ranked unscored signals ABOVE scored ones.
                    # Treating "no score" as "no contribution" keeps scored/unscored comparable and
                    # matches strategy_calc.py; the ML fields themselves stay null (nothing is invented).
                    ml_part = ml_probability if (ml_probability is not None and ml_available) else 0
                    combined_score = (alignment_score * 0.6) + (ml_part * 0.4)

                    enhanced_signal['combined_score'] = round(combined_score, 1)

                    # Enhanced summary with both timeframe and ML info
                    signal_type_display = signal['signal_type'].replace('_', ' ').title()
                    price_display = f"${signal['current_price']:.2f}"
                    alignment_display = f"{alignment_score}% ({signal.get('alignment_grade', 'F')})"

                    if ml_probability is not None and ml_available:
                        ml_display = f"{ml_probability}% ({ml_confidence})"
                        enhanced_signal['summary_text'] = (
                            f"{signal_type_display} at {price_display} | "
                            f"Alignment: {alignment_display} | "
                            f"ML: {ml_display}"
                        )
                    else:
                        enhanced_signal['summary_text'] = (
                            f"{signal_type_display} at {price_display} | "
                            f"Alignment: {alignment_display} | "
                            f"ML: N/A"
                        )

                    enhanced_signals.append(enhanced_signal)
                else:
                    # Fallback for signals without ML enhancement
                    fallback_signal = signal.copy()
                    fallback_signal['ml_momentum_probability'] = None
                    fallback_signal['ml_confidence'] = 'not_processed'
                    fallback_signal['ml_prediction_available'] = False
                    fallback_signal['combined_score'] = signal.get('alignment_score', 0)

                    signal_type_display = signal['signal_type'].replace('_', ' ').title()
                    price_display = f"${signal['current_price']:.2f}"
                    alignment_display = f"{signal.get('alignment_score', 0)}% ({signal.get('alignment_grade', 'F')})"

                    fallback_signal['summary_text'] = (
                        f"{signal_type_display} at {price_display} | "
                        f"Alignment: {alignment_display} | "
                        f"ML: N/A"
                    )

                    enhanced_signals.append(fallback_signal)

            # Sort by combined score (highest first)
            enhanced_signals.sort(key=lambda x: x.get('combined_score', 0), reverse=True)

            # Log successful ML enhancements
            successful_ml = len([s for s in enhanced_signals if s.get('ml_prediction_available')])
            if successful_ml > 0:
                top_ml_prob = enhanced_signals[0].get('ml_momentum_probability', 0)
                logger.info(
                    f"ML enhancement completed. {successful_ml} signals enhanced. Top ML probability: {top_ml_prob}%")
            else:
                logger.warning("No signals were successfully enhanced with ML")

            # Log predictions so performance_tracker.py has something to
            # evaluate later (this is what determines whether/when the model
            # actually needs retraining, instead of it silently going stale).
            if PREDICTION_LOGGING_AVAILABLE and successful_ml > 0:
                try:
                    to_record = []
                    for s in enhanced_signals:
                        if not s.get('ml_prediction_available'):
                            continue
                        to_record.append({
                            'symbol': s.get('symbol'),
                            'date': s.get('screening_date', datetime.now().date()),
                            'type': s.get('signal_type'),
                            'current_price': s.get('current_price'),
                            'ml_momentum_probability': s.get('ml_momentum_probability'),
                            'ml_confidence_level': s.get('ml_confidence', 'unknown'),
                            'ml_trade_recommendation': s.get('ml_trade_recommendation', 'hold'),
                            'ml_risk_score': s.get('ml_risk_score', 50.0),
                        })
                    model_version = enhanced_signals[0].get('ml_model_version', 'unknown') if enhanced_signals else 'unknown'
                    performance_tracker.record_predictions(to_record, model_version=model_version)
                except Exception as e:
                    # Never let logging failures break the actual screening run
                    logger.warning(f"Failed to log ML predictions for performance tracking: {e}")

            return enhanced_signals

        except Exception as e:
            logger.error(f"Error enhancing signals with ML: {e}")
            # Ensure all signals have summary_text even if ML enhancement fails
            fallback_signals = []
            for signal in signals:
                fallback_signal = signal.copy()
                if 'summary_text' not in fallback_signal:
                    signal_type_display = signal['signal_type'].replace('_', ' ').title()
                    price_display = f"${signal['current_price']:.2f}"
                    alignment_display = f"{signal.get('alignment_score', 0)}% ({signal.get('alignment_grade', 'F')})"

                    fallback_signal['summary_text'] = (
                        f"{signal_type_display} at {price_display} | "
                        f"Alignment: {alignment_display}"
                    )
                    fallback_signal['combined_score'] = signal.get('alignment_score', 0)

                fallback_signals.append(fallback_signal)

            return fallback_signals

    def get_multi_timeframe_signals(self) -> List[Dict]:
        """Get all multi-timeframe signals"""
        try:
            # Get daily breakout signals
            daily_signals = self.get_daily_breakout_signals()

            if not daily_signals:
                logger.warning("No daily signals found")
                return []

            logger.info(f"Found {len(daily_signals)} daily breakout signals")

            # Enhance each signal with multi-timeframe context
            multi_timeframe_signals = []
            for signal in daily_signals:
                try:
                    enhanced_signal = self.enhance_signal_with_timeframes(signal)
                    if enhanced_signal:
                        multi_timeframe_signals.append(enhanced_signal)
                except Exception as e:
                    logger.error(f"Error enhancing {signal.get('symbol', 'UNKNOWN')}: {e}")
                    continue

            logger.info(f"Enhanced {len(multi_timeframe_signals)} signals with multi-timeframe analysis")

            return multi_timeframe_signals

        except Exception as e:
            logger.error(f"Error getting multi-timeframe signals: {e}")
            return []

    def get_daily_breakout_signals(self) -> List[Dict]:
        """
        Get daily breakout signals from database - COMPLETELY FIXED
        Uses correct breakout detection logic from working ml_donchian_screener.py
        """
        try:
            # FIXED QUERY - Get current AND previous data for proper breakout detection
            query = """
            WITH latest_data AS (
                SELECT 
                    sp.symbol,
                    sp.date as screening_date,
                    sp.close as current_price,
                    sp.open as open_price,
                    sp.high as high_price,
                    sp.low as low_price,
                    sp.volume,
                    ti.donchian_high_20,
                    ti.donchian_low_20,
                    ti.rsi_14,
                    ti.volume_ratio,
                    ti.atr_14,
                    ti.sma_20,
                    ti.sma_50,
                    ti.price_position,
                    ti.channel_width_pct,

                    -- Get previous day's data for breakout comparison
                    LAG(sp.close, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_close,
                    LAG(ti.donchian_high_20, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_donchian_high,
                    LAG(ti.donchian_low_20, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_donchian_low,

                    -- Price change calculation
                    CASE 
                        WHEN LAG(sp.close, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date) IS NOT NULL 
                        THEN ((sp.close - LAG(sp.close, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date)) 
                             / LAG(sp.close, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date)) * 100
                        ELSE 0 
                    END as price_change_pct,

                    ROW_NUMBER() OVER (PARTITION BY sp.symbol ORDER BY sp.date DESC) as rn
                FROM stock_prices sp
                JOIN technical_indicators ti ON sp.symbol = ti.symbol AND sp.date = ti.date
                WHERE ti.donchian_high_20 IS NOT NULL
                AND ti.donchian_low_20 IS NOT NULL
                AND sp.close IS NOT NULL
                AND ti.donchian_high_20 > 0
                AND ti.donchian_low_20 > 0
                AND sp.date >= (CURRENT_DATE - INTERVAL '10 days')  -- Get recent data
            ),
            current_data AS (
                SELECT * FROM latest_data WHERE rn = 1  -- Latest data only
            ),
            latest_fundamentals AS (
                SELECT 
                    symbol,
                    date,
                    sector,
                    industry,
                    market_cap,
                    shares_outstanding,
                    float_shares,
                    pe_ratio,
                    pb_ratio,
                    ps_ratio,
                    beta,
                    dividend_yield,
                    quality_grade,
                    growth_score,
                    profitability_score,
                    financial_health_score,
                    valuation_score,
                    overall_quality_score,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as fund_rn
                FROM daily_fundamentals
                WHERE date >= (CURRENT_DATE - INTERVAL '30 days')  -- Get recent fundamental data
            )
            SELECT 
                cd.*,
                -- FULL FUNDAMENTALS DATA (FIXED - get latest available)
                lf.sector,
                lf.industry,
                lf.market_cap,
                lf.shares_outstanding,
                lf.float_shares,
                lf.pe_ratio,
                lf.pb_ratio,
                lf.ps_ratio,
                lf.beta,
                lf.dividend_yield,
                lf.quality_grade,
                lf.growth_score,
                lf.profitability_score,
                lf.financial_health_score,
                lf.valuation_score,
                lf.overall_quality_score
            FROM current_data cd
            LEFT JOIN latest_fundamentals lf ON cd.symbol = lf.symbol AND lf.fund_rn = 1
            WHERE cd.prev_donchian_high IS NOT NULL 
            AND cd.prev_donchian_low IS NOT NULL
            AND cd.prev_close IS NOT NULL
            ORDER BY cd.symbol
            """

            logger.info("Executing FIXED query for daily breakout signals...")
            results = db.execute_dict_query(query)
            logger.info(f"Query returned {len(results) if results else 0} rows")

            if not results:
                logger.warning("No daily breakout data found")
                return []

            signals = []
            for row in results:
                symbol = row['symbol']
                current_price = safe_float(row['current_price'])
                prev_close = safe_float(row['prev_close'])
                donchian_high = safe_float(row['donchian_high_20'])
                donchian_low = safe_float(row['donchian_low_20'])
                prev_donchian_high = safe_float(row['prev_donchian_high'])
                prev_donchian_low = safe_float(row['prev_donchian_low'])

                # Skip if missing required data
                if None in [current_price, prev_close, donchian_high, donchian_low,
                            prev_donchian_high, prev_donchian_low]:
                    continue

                if donchian_high <= donchian_low or prev_donchian_high <= prev_donchian_low:
                    continue

                # CORRECT BREAKOUT DETECTION LOGIC (from working ml_donchian_screener.py)
                signal_type = None
                urgency = "none"

                # Calculate distances for near-breakout detection
                distance_to_high = ((donchian_high - current_price) / current_price) * 100
                distance_to_low = ((current_price - donchian_low) / current_price) * 100

                # FIXED: Proper breakout detection using PREVIOUS period comparison
                if (current_price > prev_donchian_high and prev_close <= prev_donchian_high):
                    signal_type = "bullish_breakout"
                    urgency = "immediate"
                elif (current_price < prev_donchian_low and prev_close >= prev_donchian_low):
                    signal_type = "bearish_breakout"
                    urgency = "immediate"
                elif (0 < distance_to_high <= 3.0):
                    signal_type = "near_bullish"
                    if distance_to_high <= 1.0:
                        urgency = "very_high"
                    elif distance_to_high <= 2.0:
                        urgency = "high"
                    else:
                        urgency = "medium"
                elif (0 < distance_to_low <= 3.0):
                    signal_type = "near_bearish"
                    if distance_to_low <= 1.0:
                        urgency = "very_high"
                    elif distance_to_low <= 2.0:
                        urgency = "high"
                    else:
                        urgency = "medium"

                if not signal_type:
                    continue

                # Calculate channel metrics
                donchian_mid = (donchian_high + donchian_low) / 2
                channel_width = donchian_high - donchian_low
                channel_width_pct = (channel_width / donchian_mid) * 100 if donchian_mid > 0 else 0
                price_position_in_channel = ((
                                                         current_price - donchian_low) / channel_width) * 100 if channel_width > 0 else 50

                distance_to_breakout = distance_to_high if 'bullish' in signal_type else distance_to_low

                # Calculate stop loss and target (like old screener)
                atr = safe_float(row['atr_14']) if row['atr_14'] else (current_price * 0.02)

                if signal_type in ['bullish_breakout', 'near_bullish']:
                    stop_loss_price = current_price - (2 * atr)
                    target_price = current_price + (6 * atr)
                else:
                    stop_loss_price = current_price + (2 * atr)
                    target_price = current_price - (6 * atr)

                # RICH SIGNAL DATA (matching old screener format)
                signal = {
                    'symbol': symbol,
                    'signal_type': signal_type,
                    'urgency': urgency,
                    'screening_date': row['screening_date'],
                    'timestamp': datetime.now().isoformat(),

                    # Price data
                    'current_price': current_price,
                    'prev_close': prev_close,
                    'open_price': safe_float(row['open_price']) if row['open_price'] else current_price,
                    'high_price': safe_float(row['high_price']) if row['high_price'] else current_price,
                    'low_price': safe_float(row['low_price']) if row['low_price'] else current_price,
                    'price_change_pct': safe_float(row['price_change_pct']) if row['price_change_pct'] else 0,

                    # Volume data
                    'volume': int(row['volume']) if row['volume'] else 0,
                    'volume_formatted': f"{int(row['volume']):,}" if row['volume'] else "0",
                    'volume_ratio': safe_float(row['volume_ratio']) if row['volume_ratio'] else 1.0,

                    # Donchian channel data
                    'donchian_high': donchian_high,
                    'donchian_low': donchian_low,
                    'donchian_mid': donchian_mid,
                    'prev_donchian_high': prev_donchian_high,
                    'prev_donchian_low': prev_donchian_low,
                    'channel_width': channel_width,
                    'channel_width_pct': round(channel_width_pct, 2),
                    'price_position_in_channel': round(price_position_in_channel, 1),
                    'distance_to_high_pct': round(distance_to_high, 2),
                    'distance_to_low_pct': round(distance_to_low, 2),
                    'distance_to_breakout': round(distance_to_breakout, 2),

                    # Technical indicators
                    'rsi_14': safe_float(row['rsi_14']) if row['rsi_14'] else 50.0,
                    'atr_14': atr,
                    'sma_20': safe_float(row['sma_20']) if row['sma_20'] else current_price,
                    'sma_50': safe_float(row['sma_50']) if row['sma_50'] else current_price,

                    # Trading suggestions
                    'stop_loss_price': round(stop_loss_price, 2),
                    'target_price': round(target_price, 2),
                    'reward_risk_ratio': 3.0,
                    'position_size_suggestion': "2-3% of portfolio",

                    # FULL FUNDAMENTALS (like old screener) - FIXED
                    'market_cap': safe_float(row['market_cap']) if row['market_cap'] else 0,
                    'market_cap_formatted': self.format_market_cap(row['market_cap']) if row['market_cap'] else "$0B",
                    'shares_outstanding': int(row['shares_outstanding']) if row['shares_outstanding'] else 0,
                    'float_shares': int(row['float_shares']) if row['float_shares'] else 0,
                    'sector': row['sector'] or 'Unknown',
                    'industry': row['industry'] or 'Unknown',
                    'pe_ratio': safe_float(row['pe_ratio']) if row['pe_ratio'] else None,
                    'pb_ratio': safe_float(row['pb_ratio']) if row['pb_ratio'] else None,
                    'ps_ratio': safe_float(row['ps_ratio']) if row['ps_ratio'] else None,
                    'beta': safe_float(row['beta']) if row['beta'] else None,
                    'dividend_yield': safe_float(row['dividend_yield']) if row['dividend_yield'] else None,
                    'quality_grade': row['quality_grade'] or 'C',
                    'growth_score': safe_float(row['growth_score']) if row['growth_score'] else None,
                    'profitability_score': safe_float(row['profitability_score']) if row[
                        'profitability_score'] else None,
                    'financial_health_score': safe_float(row['financial_health_score']) if row[
                        'financial_health_score'] else None,
                    'valuation_score': safe_float(row['valuation_score']) if row['valuation_score'] else None,
                    'overall_quality_score': safe_float(row['overall_quality_score']) if row[
                        'overall_quality_score'] else 50.0,

                    # Display formatting (like old screener)
                    'display_name': symbol,
                    'signal_strength': 'very_strong' if urgency == 'immediate' else 'strong',
                    'color_code': '#10b981' if signal_type in ['bullish_breakout', 'near_bullish'] else '#ef4444',
                    'icon': 'trending-up' if signal_type in ['bullish_breakout', 'near_bullish'] else 'trending-down'
                }
                signals.append(signal)

            logger.info(f"Found {len(signals)} daily breakout signals")

            # Debug: Count by type
            signal_counts = {}
            for s in signals:
                signal_type = s['signal_type']
                signal_counts[signal_type] = signal_counts.get(signal_type, 0) + 1

            logger.info(f"Signal breakdown: {signal_counts}")

            return signals

        except Exception as e:
            logger.error(f"Error getting daily breakout signals: {e}")
            import traceback
            traceback.print_exc()
            return []

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

    def _create_enhanced_results(self, all_signals: List[Dict], ml_enhanced_signals: List[Dict]) -> Dict:
        """Create comprehensive results structure - FIXED with rich summary"""
        try:
            # Categorize all signals by type
            signals_by_type = {
                'bullish_breakout': [s for s in all_signals if s['signal_type'] == 'bullish_breakout'],
                'bearish_breakout': [s for s in all_signals if s['signal_type'] == 'bearish_breakout'],
                'near_bullish': [s for s in all_signals if s['signal_type'] == 'near_bullish'],
                'near_bearish': [s for s in all_signals if s['signal_type'] == 'near_bearish']
            }

            # Get top AI picks (high combined scores)
            top_ai_picks = [
                               signal for signal in ml_enhanced_signals
                               if signal.get('combined_score', 0) >= 45  # Adjusted threshold
                           ][:20]

            # Get high confidence ML signals (high ML probability)
            # `ml_momentum_probability` is None when a signal has no validated ML score -- compare
            # only real scores (`.get(key, 0)` returns None, not 0, when the key exists as null).
            high_confidence_ml_signals = [
                signal for signal in ml_enhanced_signals
                if signal.get('ml_momentum_probability') is not None
                and signal['ml_momentum_probability'] >= 60  # High ML confidence
            ]

            # Calculate ML statistics; None (not 0) when nothing carries a score
            ml_probabilities = [s['ml_momentum_probability'] for s in ml_enhanced_signals
                                if s.get('ml_momentum_probability') is not None]
            average_ml_probability = round(np.mean(ml_probabilities), 1) if ml_probabilities else None

            # Create sector breakdown (like old screener)
            sector_breakdown = {}
            for signal in all_signals:
                sector = signal.get('sector', 'Unknown')
                if sector and sector != 'Unknown':
                    sector_breakdown[sector] = sector_breakdown.get(sector, 0) + 1

            # Create sector analysis for AI insights
            sector_analysis = self._analyze_sectors(ml_enhanced_signals)

            # RICH SUMMARY (matching old screener format)
            results = {
                'metadata': {
                    'generated_at': datetime.now().isoformat(),
                    'screening_type': 'multi_timeframe_ml_enhanced',
                    'total_symbols_screened': len(set(s['symbol'] for s in all_signals)),
                    'ml_enhancement_enabled': self.ml_enhancer is not None,
                    'version': 'multi_timeframe_v3.0_fixed'
                },
                'summary': {
                    'total_signals': float(len(all_signals)),
                    'bullish_breakouts': float(len(signals_by_type['bullish_breakout'])),
                    'bearish_breakouts': float(len(signals_by_type['bearish_breakout'])),
                    'near_bullish': float(len(signals_by_type['near_bullish'])),
                    'near_bearish': float(len(signals_by_type['near_bearish'])),
                    'ml_enhanced_signals': float(len(ml_enhanced_signals)),
                    'high_confidence_ml_signals': float(len(high_confidence_ml_signals)),
                    'average_ml_probability': average_ml_probability,
                    'sector_breakdown': {k: float(v) for k, v in sector_breakdown.items()},
                    'high_quality_signals': float(
                        len([s for s in all_signals if s.get('alignment_grade', 'F') in ['A', 'B', 'C']])),
                    'top_ai_picks': float(len(top_ai_picks))
                },
                'signals': {
                    'bullish_breakout': signals_by_type['bullish_breakout'],
                    'bearish_breakout': signals_by_type['bearish_breakout'],
                    'near_bullish': signals_by_type['near_bullish'],
                    'near_bearish': signals_by_type['near_bearish']
                },
                'ai_insights': {
                    'top_ai_picks': top_ai_picks,
                    'high_confidence_signals': high_confidence_ml_signals,
                    'ml_enhanced_signals': ml_enhanced_signals,
                    'sector_analysis': sector_analysis
                },
                'performance_metrics': {
                    'screening_time_seconds': 0,
                    'ml_enhancement_time_seconds': 0,
                    'average_alignment_score': round(np.mean([s.get('alignment_score', 0) for s in all_signals]), 1),
                    'average_ml_probability': average_ml_probability,
                    'grade_distribution': {
                        'A': len([s for s in all_signals if s.get('alignment_grade') == 'A']),
                        'B': len([s for s in all_signals if s.get('alignment_grade') == 'B']),
                        'C': len([s for s in all_signals if s.get('alignment_grade') == 'C']),
                        'D': len([s for s in all_signals if s.get('alignment_grade') == 'D']),
                        'F': len([s for s in all_signals if s.get('alignment_grade') == 'F'])
                    }
                }
            }

            return results

        except Exception as e:
            logger.error(f"Error creating enhanced results: {e}")
            import traceback
            traceback.print_exc()
            return self._create_empty_result()

    def enhance_signal_with_timeframes(self, signal: Dict) -> Optional[Dict]:
        """Enhance signal with weekly and monthly timeframe context"""
        try:
            symbol = signal['symbol']

            # Get weekly context
            weekly_context = self.get_latest_weekly_context(symbol)

            # Get monthly context
            monthly_context = self.get_latest_monthly_context(symbol)

            # Calculate alignment score and grade
            alignment_score, alignment_grade = self.calculate_timeframe_alignment_score(
                signal, weekly_context, monthly_context
            )

            # Enhanced signal with timeframe data
            enhanced_signal = signal.copy()
            enhanced_signal.update({
                'alignment_score': alignment_score,
                'alignment_grade': alignment_grade,
                'weekly_context': weekly_context,
                'monthly_context': monthly_context,
                'timestamp': datetime.now().isoformat()
            })

            return enhanced_signal

        except Exception as e:
            logger.error(f"Error enhancing signal for {signal.get('symbol', 'UNKNOWN')}: {e}")
            return None

    def get_latest_weekly_context(self, symbol: str) -> Optional[Dict]:
        """Get latest weekly context for symbol"""
        try:
            query = """
            SELECT 
                symbol,
                week_ending_date,
                weekly_close,
                donchian_high_20w,
                donchian_low_20w,
                rsi_14w,
                volume_ratio_weekly,
                price_position_weekly
            FROM weekly_technical_indicators
            WHERE symbol = %s 
            ORDER BY week_ending_date DESC 
            LIMIT 1
            """

            results = db.execute_dict_query(query, (symbol,))

            if not results:
                return None

            row = results[0]
            weekly_close = safe_float(row['weekly_close'])
            donchian_high_w = safe_float(row['donchian_high_20w']) if row['donchian_high_20w'] else None
            donchian_low_w = safe_float(row['donchian_low_20w']) if row['donchian_low_20w'] else None

            # Determine weekly trend
            if donchian_high_w and donchian_low_w:
                if weekly_close >= donchian_high_w * 0.95:  # Near or above weekly high
                    weekly_trend = 'bullish'
                elif weekly_close <= donchian_low_w * 1.05:  # Near or below weekly low
                    weekly_trend = 'bearish'
                else:
                    weekly_trend = 'consolidation'
            else:
                weekly_trend = 'unknown'

            return {
                'week_ending_date': row['week_ending_date'],
                'weekly_trend': weekly_trend,
                'weekly_close': weekly_close,
                'weekly_donchian_high': donchian_high_w,
                'weekly_donchian_low': donchian_low_w,
                'weekly_rsi': safe_float(row['rsi_14w']) if row['rsi_14w'] else None,
                'weekly_volume_ratio': safe_float(row['volume_ratio_weekly']) if row['volume_ratio_weekly'] else None
            }

        except Exception as e:
            logger.error(f"Error getting weekly context for {symbol}: {e}")
            return None

    def get_latest_monthly_context(self, symbol: str) -> Optional[Dict]:
        """Get latest monthly context for symbol"""
        try:
            query = """
            SELECT 
                symbol,
                month_ending_date,
                monthly_close,
                donchian_high_12m,
                donchian_low_12m, 
                trend_direction,
                trend_strength_6m
            FROM monthly_technical_indicators
            WHERE symbol = %s 
            ORDER BY month_ending_date DESC 
            LIMIT 1
            """

            results = db.execute_dict_query(query, (symbol,))

            if not results:
                return None

            row = results[0]

            return {
                'month_ending_date': row['month_ending_date'],
                'monthly_trend': row['trend_direction'] or 'unknown',
                'monthly_close': safe_float(row['monthly_close']),
                'monthly_donchian_high': safe_float(row['donchian_high_12m']) if row['donchian_high_12m'] else None,
                'monthly_donchian_low': safe_float(row['donchian_low_12m']) if row['donchian_low_12m'] else None,
                'trend_strength': safe_float(row['trend_strength_6m']) if row['trend_strength_6m'] else None
            }

        except Exception as e:
            logger.error(f"Error getting monthly context for {symbol}: {e}")
            return None

    def calculate_timeframe_alignment_score(self, daily_signal: Dict,
                                            weekly_context: Optional[Dict],
                                            monthly_context: Optional[Dict]) -> Tuple[int, str]:
        """Calculate alignment score (0-100) and quality grade"""
        try:
            score = 0
            factors = []

            daily_type = daily_signal['signal_type']

            # Daily signal base score
            if daily_type in ['bullish_breakout', 'bearish_breakout']:
                score += 40  # Actual breakout
                factors.append("Daily breakout confirmed")
            elif daily_type in ['near_bullish', 'near_bearish']:
                score += 25  # Near breakout
                factors.append("Daily near-breakout")

            # Weekly alignment bonus
            if weekly_context:
                weekly_trend = weekly_context['weekly_trend']

                if daily_type in ['bullish_breakout', 'near_bullish'] and weekly_trend == 'bullish':
                    score += 30
                    factors.append("Weekly trend aligned (bullish)")
                elif daily_type in ['bearish_breakout', 'near_bearish'] and weekly_trend == 'bearish':
                    score += 30
                    factors.append("Weekly trend aligned (bearish)")
                elif weekly_trend == 'consolidation':
                    score += 10
                    factors.append("Weekly consolidation")
            else:
                factors.append("No weekly data")

            # Monthly alignment bonus
            if monthly_context:
                monthly_trend = monthly_context['monthly_trend']

                if daily_type in ['bullish_breakout', 'near_bullish'] and monthly_trend == 'bullish':
                    score += 30
                    factors.append("Monthly trend aligned (bullish)")
                elif daily_type in ['bearish_breakout', 'near_bearish'] and monthly_trend == 'bearish':
                    score += 30
                    factors.append("Monthly trend aligned (bearish)")
                elif monthly_trend == 'sideways':
                    score += 10
                    factors.append("Monthly sideways")
            else:
                factors.append("No monthly data")

            # Cap score at 100
            score = min(100, score)

            # Assign grade based on score
            if score >= 80:
                grade = 'A'
            elif score >= 65:
                grade = 'B'
            elif score >= 50:
                grade = 'C'
            elif score >= 35:
                grade = 'D'
            else:
                grade = 'F'

            return score, grade

        except Exception as e:
            logger.error(f"Error calculating alignment score: {e}")
            return 0, 'F'

    def save_results(self, results: Dict) -> None:
        """Save results to JSON files"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Create directories if they don't exist
            os.makedirs("breakout_results", exist_ok=True)
            os.makedirs("frontend_data", exist_ok=True)

            # Save to breakout_results directory (existing pattern)
            results_file = f"breakout_results/multi_timeframe_ml_enhanced_{timestamp}.json"
            with open(results_file, 'w') as f:
                json.dump(results, f, indent=2, default=str)

            # Save to frontend_data directory (for frontend consumption)
            frontend_file = "frontend_data/latest_multi_timeframe_ml_enhanced.json"
            with open(frontend_file, 'w') as f:
                json.dump(results, f, indent=2, default=str)

            logger.info(f"Results saved to {results_file}")
            logger.info(f"Frontend data saved to {frontend_file}")

        except Exception as e:
            logger.error(f"Error saving results: {e}")

    def _create_empty_result(self) -> Dict:
        """Create empty result structure"""
        return {
            'metadata': {
                'generated_at': datetime.now().isoformat(),
                'screening_type': 'multi_timeframe_ml_enhanced',
                'total_symbols_screened': 0,
                'ml_enhancement_enabled': False,
                'version': 'multi_timeframe_v3.0_fixed'
            },
            'summary': {
                'total_signals': 0,
                'high_quality_signals': 0,
                'ml_enhanced_signals': 0,
                'top_ai_picks': 0,
                'high_confidence_signals': 0
            },
            'signals': {
                'bullish_breakout': [],
                'bearish_breakout': [],
                'near_bullish': [],
                'near_bearish': []
            },
            'ai_insights': {
                'top_ai_picks': [],
                'high_confidence_signals': [],
                'ml_enhanced_signals': [],
                'sector_analysis': {}
            }
        }

    def _analyze_sectors(self, signals: List[Dict]) -> Dict:
        """Analyze sector distribution and performance"""
        try:
            sector_data = {}

            for signal in signals:
                sector = signal.get('sector', 'Unknown')
                if sector not in sector_data:
                    sector_data[sector] = {
                        'count': 0,
                        'avg_alignment_score': 0,
                        'avg_ml_probability': 0,
                        'signals': []
                    }

                sector_data[sector]['count'] += 1
                sector_data[sector]['signals'].append(signal)

            # Calculate averages
            for sector, data in sector_data.items():
                if data['signals']:
                    data['avg_alignment_score'] = round(
                        np.mean([s.get('alignment_score', 0) for s in data['signals']]), 1
                    )
                    data['avg_ml_probability'] = round(
                        np.mean([s['ml_momentum_probability'] for s in data['signals']
                                 if s.get('ml_momentum_probability') is not None] or [np.nan]), 1
                    )
                    # Remove signals list to reduce JSON size
                    del data['signals']

            return sector_data

        except Exception as e:
            logger.error(f"Error analyzing sectors: {e}")
            return {}

    def debug_breakout_detection(self):
        """Debug method to check breakout detection"""
        try:
            query = """
            WITH latest_data AS (
                SELECT 
                    sp.symbol,
                    sp.close as current_price,
                    ti.donchian_high_20,
                    ti.donchian_low_20,
                    LAG(sp.close, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_close,
                    LAG(ti.donchian_high_20, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_donchian_high,
                    LAG(ti.donchian_low_20, 1) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_donchian_low,
                    ROW_NUMBER() OVER (PARTITION BY sp.symbol ORDER BY sp.date DESC) as rn
                FROM stock_prices sp
                JOIN technical_indicators ti ON sp.symbol = ti.symbol AND sp.date = ti.date
                WHERE ti.donchian_high_20 IS NOT NULL
                AND ti.donchian_low_20 IS NOT NULL
                AND sp.close IS NOT NULL
                AND ti.donchian_high_20 > 0
                AND ti.donchian_low_20 > 0
                AND sp.date >= (CURRENT_DATE - INTERVAL '5 days')
            )
            SELECT 
                symbol,
                current_price,
                donchian_high_20,
                donchian_low_20,
                prev_close,
                prev_donchian_high,
                prev_donchian_low,
                (current_price > prev_donchian_high AND prev_close <= prev_donchian_high) as is_bullish_breakout,
                (current_price < prev_donchian_low AND prev_close >= prev_donchian_low) as is_bearish_breakout,
                ((donchian_high_20 - current_price) / current_price) * 100 as distance_to_high_pct
            FROM latest_data
            WHERE rn = 1
            AND prev_donchian_high IS NOT NULL
            AND prev_donchian_low IS NOT NULL
            AND prev_close IS NOT NULL
            ORDER BY is_bullish_breakout DESC, distance_to_high_pct ASC
            LIMIT 20
            """

            results = db.execute_dict_query(query)

            print("\nDEBUG: Fixed Breakout Detection Analysis")
            print("=" * 60)
            bullish_count = sum(1 for r in results if r['is_bullish_breakout'])
            bearish_count = sum(1 for r in results if r['is_bearish_breakout'])
            near_bullish_count = sum(1 for r in results if 0 < r['distance_to_high_pct'] <= 3.0)

            print(f"Actual bullish breakouts in DB: {bullish_count}")
            print(f"Actual bearish breakouts in DB: {bearish_count}")
            print(f"Near bullish breakouts in DB: {near_bullish_count}")
            print("\nTop candidates:")

            for r in results[:10]:
                breakout_status = "BULLISH BREAKOUT" if r['is_bullish_breakout'] else \
                    "BEARISH BREAKOUT" if r['is_bearish_breakout'] else \
                        "NEAR BULLISH" if 0 < r['distance_to_high_pct'] <= 3.0 else "NONE"
                print(f"  {r['symbol']}: ${r['current_price']:.2f} vs prev_high ${r['prev_donchian_high']:.2f} "
                      f"({breakout_status})")

        except Exception as e:
            print(f"Debug query failed: {e}")


# MAIN EXECUTION
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Fixed Multi-Timeframe ML Screener')
    parser.add_argument('--test', nargs='*', help='Test specific symbols')
    parser.add_argument('--debug', action='store_true', help='Run debug breakout detection')
    parser.add_argument('--config-test', action='store_true', help='Test configuration')

    args = parser.parse_args()

    screener = MultiTimeframeMLScreener()

    if args.config_test:
        print("Configuration test passed")
        print(f"ML Enhancement Available: {screener.ml_enhancer is not None}")
        print(f"Database Connection: {'OK' if db.test_connection() else 'Failed'}")
    elif args.debug:
        print("Running debug breakout detection...")
        screener.debug_breakout_detection()
    elif args.test:
        print(f"Testing symbols: {args.test}")
        results = screener.screen_all_symbols()
        test_signals = [s for s in results['ai_insights']['ml_enhanced_signals'] if s['symbol'] in args.test]
        print(f"Found {len(test_signals)} signals for test symbols")
        for signal in test_signals:
            print(f"  {signal['symbol']}: {signal['summary_text']}")
    else:
        # Run full screening
        results = screener.screen_all_symbols()
        if screener.failed:
            print("SCREENING FAILED -- previous frontend_data output left untouched", file=sys.stderr)
            sys.exit(1)

        # Print summary - FIXED VERSION
        print("\n" + "=" * 60)
        print("MULTI-TIMEFRAME ML SCREENING RESULTS - FIXED")
        print("=" * 60)
        summary = results.get('summary', {})
        print(f"Total Signals: {summary.get('total_signals', 0)}")
        print(f"Bullish Breakouts: {summary.get('bullish_breakouts', 0)}")
        print(f"Bearish Breakouts: {summary.get('bearish_breakouts', 0)}")
        print(f"Near Bullish: {summary.get('near_bullish', 0)}")
        print(f"Near Bearish: {summary.get('near_bearish', 0)}")
        print(f"High-Quality Signals: {summary.get('high_quality_signals', 0)}")
        print(f"ML Enhanced Signals: {summary.get('ml_enhanced_signals', 0)}")
        print(f"High Confidence ML Signals: {summary.get('high_confidence_ml_signals', 0)}")
        print(f"Top AI Picks: {summary.get('top_ai_picks', 0)}")
        avg_ml = summary.get('average_ml_probability')
        print(f"Average ML Probability: {'n/a (no validated ML scores)' if avg_ml is None else f'{avg_ml}%'}")
        print("=" * 60)

        # Show sector breakdown if available
        if summary.get('sector_breakdown'):
            print("\nSECTOR BREAKDOWN:")
            for sector, count in summary['sector_breakdown'].items():
                print(f"  {sector}: {count}")

        if results['ai_insights']['top_ai_picks']:
            print("\nTOP AI PICKS:")
            for i, signal in enumerate(results['ai_insights']['top_ai_picks'][:5], 1):
                print(f"{i:2d}. {signal['symbol']:>6s} | {signal['summary_text']}")