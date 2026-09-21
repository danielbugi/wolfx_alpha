# -*- coding: utf-8 -*-
# mechanism/screeners/multi_timeframe_screener.py
"""
Multi-timeframe breakout screener combining daily/weekly/monthly analysis
Generates higher quality signals by checking timeframe alignment
"""

import pandas as pd
import json
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

logger = setup_logging("multi_timeframe_screener")


class MultiTimeframeScreener:
    """Enhanced screener using daily/weekly/monthly timeframe analysis"""

    def __init__(self):
        self.batch_size = config.data_batch_size
        logger.info("Multi-Timeframe Screener initialized")

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_symbols_for_screening(self, limit: Optional[int] = None) -> List[str]:
        """Get symbols that have data across all timeframes - FIXED for --all"""
        try:
            # Handle different limit scenarios
            if limit == -1:
                # Special value for --all flag
                query = """
                SELECT DISTINCT symbol
                FROM daily_fundamentals 
                WHERE symbol IS NOT NULL 
                AND symbol != ''
                ORDER BY symbol
                """
                params = ()
                logger.info("Screening ALL symbols (no limit)")

            elif limit is None:
                # Default limit when not specified
                limit = 50
                query = """
                SELECT DISTINCT symbol
                FROM daily_fundamentals 
                WHERE symbol IS NOT NULL 
                AND symbol != ''
                ORDER BY symbol
                LIMIT %s
                """
                params = (limit,)
                logger.info(f"No limit specified - using default limit of {limit} symbols")

            else:
                # Specific limit provided
                query = """
                SELECT DISTINCT symbol
                FROM daily_fundamentals 
                WHERE symbol IS NOT NULL 
                AND symbol != ''
                ORDER BY symbol
                LIMIT %s
                """
                params = (limit,)
                logger.info(f"Using specified limit of {limit} symbols")

            results = db.execute_query(query, params)
            symbols = [row[0] for row in results]

            logger.info(f"Found {len(symbols)} symbols for multi-timeframe screening")
            return symbols

        except Exception as e:
            logger.error(f"Error getting symbols for screening: {e}")
            return []

    def get_latest_daily_signal(self, symbol: str) -> Optional[Dict]:
        """Get latest daily breakout signal with FULL data - Enhanced version"""
        try:
            query = """
                SELECT 
                    ti.symbol,
                    ti.date,
                    sp.open as open_price,
                    sp.high as high_price, 
                    sp.low as low_price,
                    sp.close as current_price,
                    sp.volume,
                    ti.donchian_high_20,
                    ti.donchian_low_20,
                    ti.donchian_mid_20,
                    ti.rsi_14,
                    ti.volume_ratio,
                    ti.price_position,
                    ti.channel_width_pct,
                    ti.sma_20,
                    ti.sma_50,
                    ti.atr_14,
                    df.market_cap,
                    df.sector,
                    df.industry,
                    df.pe_ratio,
                    df.pb_ratio,
                    df.beta,
                    df.dividend_yield,
                    df.quality_grade,
                    df.growth_score,
                    df.profitability_score,
                    df.financial_health_score,
                    df.valuation_score,
                    df.overall_quality_score
                FROM technical_indicators ti
                INNER JOIN stock_prices sp ON ti.symbol = sp.symbol AND ti.date = sp.date
                LEFT JOIN LATERAL (
                    SELECT *
                    FROM daily_fundamentals df2 
                    WHERE df2.symbol = ti.symbol 
                    ORDER BY df2.date DESC 
                    LIMIT 1
                ) df ON true
                WHERE ti.symbol = %s 
                ORDER BY ti.date DESC 
                LIMIT 1
                """

            results = db.execute_dict_query(query, (symbol,))

            if not results:
                return None

            row = results[0]

            # Extract values with null handling
            current_price = float(row['current_price'])
            open_price = float(row['open_price']) if row['open_price'] else current_price
            high_price = float(row['high_price']) if row['high_price'] else current_price
            low_price = float(row['low_price']) if row['low_price'] else current_price
            volume = int(row['volume']) if row['volume'] else 0

            donchian_high = float(row['donchian_high_20']) if row['donchian_high_20'] else None
            donchian_low = float(row['donchian_low_20']) if row['donchian_low_20'] else None
            donchian_mid = float(row['donchian_mid_20']) if row['donchian_mid_20'] else None

            if not donchian_high or not donchian_low:
                return None

            # Calculate enhanced metrics (following your original screener pattern)
            price_change_pct = ((current_price - open_price) / open_price) * 100
            channel_width = donchian_high - donchian_low
            channel_width_pct = (channel_width / donchian_mid) * 100 if donchian_mid else 0

            distance_to_high_pct = abs((current_price - donchian_high) / donchian_high * 100)
            distance_to_low_pct = abs((current_price - donchian_low) / donchian_low * 100)

            price_position_in_channel = ((current_price - donchian_low) / channel_width) * 100

            # Determine signal type (your existing logic)
            if current_price >= donchian_high * 0.999:
                signal_type = 'bullish_breakout'
                urgency = 'immediate'
                distance_to_breakout = distance_to_high_pct
            elif current_price <= donchian_low * 1.001:
                signal_type = 'bearish_breakout'
                urgency = 'immediate'
                distance_to_breakout = distance_to_low_pct
            elif distance_to_high_pct <= 3.0:
                signal_type = 'near_bullish'
                urgency = 'medium' if distance_to_high_pct <= 1.5 else 'low'
                distance_to_breakout = distance_to_high_pct
            elif distance_to_low_pct <= 3.0:
                signal_type = 'near_bearish'
                urgency = 'medium' if distance_to_low_pct <= 1.5 else 'low'
                distance_to_breakout = distance_to_low_pct
            else:
                signal_type = 'consolidation'
                urgency = 'none'
                distance_to_breakout = min(distance_to_high_pct, distance_to_low_pct)

            # Calculate stop loss and target (3:1 reward/risk ratio)
            atr = float(row['atr_14']) if row['atr_14'] else channel_width * 0.1

            if signal_type in ['bullish_breakout', 'near_bullish']:
                stop_loss_price = current_price - (2 * atr)
                target_price = current_price + (6 * atr)  # 3:1 ratio
            else:
                stop_loss_price = current_price + (2 * atr)
                target_price = current_price - (6 * atr)

            reward_risk_ratio = abs((target_price - current_price) / (current_price - stop_loss_price))

            # Determine signal strength
            volume_ratio = float(row['volume_ratio']) if row['volume_ratio'] else 1.0
            rsi_14 = float(row['rsi_14']) if row['rsi_14'] else 50

            if signal_type in ['bullish_breakout', 'bearish_breakout'] and volume_ratio > 1.2:
                signal_strength = 'very_strong'
            elif signal_type in ['bullish_breakout', 'bearish_breakout']:
                signal_strength = 'strong'
            elif signal_type.startswith('near_'):
                signal_strength = 'moderate'
            else:
                signal_strength = 'weak'

            # UI/UX enhancements
            color_code = {
                'bullish_breakout': '#10b981',  # Green
                'near_bullish': '#3b82f6',  # Blue
                'bearish_breakout': '#ef4444',  # Red
                'near_bearish': '#f59e0b'  # Orange
            }.get(signal_type, '#6b7280')

            icon = {
                'bullish_breakout': 'trending-up',
                'near_bullish': 'arrow-up',
                'bearish_breakout': 'trending-down',
                'near_bearish': 'arrow-down'
            }.get(signal_type, 'minus')

            return {
                # Core identification
                'symbol': symbol,
                'date': row['date'],
                'signal_type': signal_type,
                'type': signal_type,  # Keep both for compatibility

                # Price data
                'current_price': current_price,
                'open_price': open_price,
                'high_price': high_price,
                'low_price': low_price,
                'price_change_pct': round(price_change_pct, 2),

                # Donchian analysis
                'donchian_high': donchian_high,
                'donchian_low': donchian_low,
                'donchian_mid': donchian_mid,
                'channel_width': round(channel_width, 2),
                'channel_width_pct': round(channel_width_pct, 2),
                'price_position_in_channel': round(price_position_in_channel, 1),
                'distance_to_high_pct': round(distance_to_high_pct, 2),
                'distance_to_low_pct': round(distance_to_low_pct, 2),
                'distance_to_breakout': round(distance_to_breakout, 2),

                # Volume analysis
                'volume': volume,
                'volume_formatted': f"{volume:,}",
                'volume_ratio': round(volume_ratio, 2),

                # Technical indicators
                'rsi_14': round(rsi_14, 2) if rsi_14 else None,
                'atr_14': round(atr, 4),
                'sma_20': float(row['sma_20']) if row['sma_20'] else None,
                'sma_50': float(row['sma_50']) if row['sma_50'] else None,
                'price_position': float(row['price_position']) if row['price_position'] else None,

                # Trading recommendations
                'stop_loss_price': round(stop_loss_price, 2),
                'target_price': round(target_price, 2),
                'reward_risk_ratio': round(reward_risk_ratio, 1),
                'position_size_suggestion': '2-3% of portfolio',  # Will be updated by alignment

                # Fundamental data
                'market_cap': int(row['market_cap']) if row['market_cap'] else None,
                'market_cap_formatted': f"${float(row['market_cap']) / 1e9:.1f}B" if row['market_cap'] else None,
                'sector': row['sector'],
                'industry': row['industry'],
                'pe_ratio': float(row['pe_ratio']) if row['pe_ratio'] else None,
                'pb_ratio': float(row['pb_ratio']) if row['pb_ratio'] else None,
                'beta': float(row['beta']) if row['beta'] else None,
                'dividend_yield': float(row['dividend_yield']) if row['dividend_yield'] else None,

                # Quality scores
                'quality_grade': row['quality_grade'],
                'growth_score': float(row['growth_score']) if row['growth_score'] else None,
                'profitability_score': float(row['profitability_score']) if row['profitability_score'] else None,
                'financial_health_score': float(row['financial_health_score']) if row[
                    'financial_health_score'] else None,
                'valuation_score': float(row['valuation_score']) if row['valuation_score'] else None,
                'overall_quality_score': float(row['overall_quality_score']) if row['overall_quality_score'] else None,

                # UI/UX
                'urgency': urgency,
                'timestamp': datetime.now().isoformat(),
                'screening_date': row['date'].isoformat(),
                'signal_strength': signal_strength,
                'color_code': color_code,
                'icon': icon,
                'summary_text': f"{signal_type.replace('_', ' ').title()} at ${current_price:.2f}",
                'display_name': symbol
            }

        except Exception as e:
            logger.error(f"Error getting enhanced daily signal for {symbol}: {e}")
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
            weekly_close = float(row['weekly_close'])
            donchian_high_w = float(row['donchian_high_20w']) if row['donchian_high_20w'] else None
            donchian_low_w = float(row['donchian_low_20w']) if row['donchian_low_20w'] else None

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
                'weekly_rsi': float(row['rsi_14w']) if row['rsi_14w'] else None,
                'weekly_volume_ratio': float(row['volume_ratio_weekly']) if row['volume_ratio_weekly'] else None
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
                'monthly_close': float(row['monthly_close']),
                'monthly_donchian_high': float(row['donchian_high_12m']) if row['donchian_high_12m'] else None,
                'monthly_donchian_low': float(row['donchian_low_12m']) if row['donchian_low_12m'] else None,
                'trend_strength': float(row['trend_strength_6m']) if row['trend_strength_6m'] else None
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
                    score -= 10
                    factors.append("Weekly trend conflict")
            else:
                factors.append("No weekly data")

            # Monthly alignment bonus
            if monthly_context:
                monthly_trend = monthly_context['monthly_trend']

                if daily_type in ['bullish_breakout', 'near_bullish'] and monthly_trend == 'bullish':
                    score += 25
                    factors.append("Monthly trend aligned (bullish)")
                elif daily_type in ['bearish_breakout', 'near_bearish'] and monthly_trend == 'bearish':
                    score += 25
                    factors.append("Monthly trend aligned (bearish)")
                elif monthly_trend == 'sideways':
                    score += 5
                    factors.append("Monthly sideways trend")
                else:
                    score -= 5
                    factors.append("Monthly trend conflict")
            else:
                factors.append("No monthly data")

            # RSI confluence bonus
            daily_rsi = daily_signal.get('rsi_14')
            weekly_rsi = weekly_context.get('weekly_rsi') if weekly_context else None

            if daily_rsi and weekly_rsi:
                rsi_diff = abs(daily_rsi - weekly_rsi)
                if rsi_diff < 10:  # Similar RSI levels
                    score += 5
                    factors.append("RSI confluence")

            # Cap score at 100
            score = min(100, max(0, score))

            # Assign quality grade
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

    @timing_decorator()
    def screen_multi_timeframe_signals(self, limit: Optional[int] = None,
                                       test_symbols: Optional[List[str]] = None) -> Dict:
        """Screen for multi-timeframe aligned signals - FIXED"""
        try:
            logger.info("🔍 Starting multi-timeframe screening")
            start_time = datetime.now()

            # Get symbols to screen - FIXED to handle test symbols
            if test_symbols:
                symbols = test_symbols
                logger.info(f"Using test symbols: {symbols}")
            else:
                symbols = self.get_symbols_for_screening(limit=limit)

            if not symbols:
                return {'success': False, 'message': 'No symbols found'}

            aligned_signals = []
            stats = {
                'symbols_screened': len(symbols),
                'signals_found': 0,
                'high_quality_signals': 0,
                'daily_only': 0,
                'weekly_enhanced': 0,
                'monthly_enhanced': 0,
                'full_alignment': 0
            }

            logger.info(f"Screening {len(symbols)} symbols for timeframe alignment...")

            for i, symbol in enumerate(symbols, 1):
                try:
                    logger.info(f"Processing {symbol} ({i}/{len(symbols)})")

                    # Get daily signal
                    daily_signal = self.get_latest_daily_signal(symbol)
                    if not daily_signal or daily_signal['signal_type'] == 'consolidation':
                        logger.info(f"No valid daily signal for {symbol}")
                        continue

                    logger.info(f"Daily signal for {symbol}: {daily_signal['signal_type']}")

                    # Get weekly and monthly context
                    weekly_context = self.get_latest_weekly_context(symbol)
                    monthly_context = self.get_latest_monthly_context(symbol)

                    logger.info(f"Weekly context: {'Yes' if weekly_context else 'No'}")
                    logger.info(f"Monthly context: {'Yes' if monthly_context else 'No'}")

                    # Calculate alignment score
                    alignment_score, quality_grade = self.calculate_timeframe_alignment_score(
                        daily_signal, weekly_context, monthly_context
                    )

                    logger.info(f"Alignment score: {alignment_score}, Grade: {quality_grade}")

                    # Only include signals with minimum quality
                    if alignment_score >= 20:  # Grade D or better

                        # Determine position sizing based on alignment
                        if alignment_score >= 80:
                            position_size = "4-6% of portfolio"
                            holding_period = "3-8 weeks"
                        elif alignment_score >= 65:
                            position_size = "3-4% of portfolio"
                            holding_period = "2-4 weeks"
                        elif alignment_score >= 50:
                            position_size = "2-3% of portfolio"
                            holding_period = "1-2 weeks"
                        else:
                            position_size = "1-2% of portfolio"
                            holding_period = "3-5 days"

                        signal = {
                            **daily_signal,
                            'alignment_score': alignment_score,
                            'quality_grade': quality_grade,
                            'weekly_context': weekly_context,
                            'monthly_context': monthly_context,
                            'recommended_position_size': position_size,
                            'suggested_holding_period': holding_period,
                            'timeframe_analysis': {
                                'has_weekly_data': weekly_context is not None,
                                'has_monthly_data': monthly_context is not None,
                                'weekly_trend': weekly_context['weekly_trend'] if weekly_context else 'unknown',
                                'monthly_trend': monthly_context['monthly_trend'] if monthly_context else 'unknown'
                            }
                        }

                        aligned_signals.append(signal)
                        stats['signals_found'] += 1

                        if quality_grade in ['A', 'B']:
                            stats['high_quality_signals'] += 1

                        # Update statistics
                        if weekly_context and monthly_context:
                            stats['full_alignment'] += 1
                        elif weekly_context:
                            stats['weekly_enhanced'] += 1
                        elif monthly_context:
                            stats['monthly_enhanced'] += 1
                        else:
                            stats['daily_only'] += 1

                        logger.info(f"✅ Added signal for {symbol}: Grade {quality_grade}, Score {alignment_score}")
                    else:
                        logger.info(f"⏭️ Skipping {symbol}: Low alignment score ({alignment_score})")

                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}")
                    continue

            # Sort signals by alignment score (best first)
            aligned_signals.sort(key=lambda x: x['alignment_score'], reverse=True)

            # Generate output
            output = {
                'metadata': {
                    'timestamp': datetime.now().isoformat(),
                    'screening_type': 'multi_timeframe',
                    'symbols_screened': stats['symbols_screened'],
                    'processing_time_seconds': (datetime.now() - start_time).total_seconds()
                },
                'summary': {
                    **stats,
                    'grade_a_signals': len([s for s in aligned_signals if s['quality_grade'] == 'A']),
                    'grade_b_signals': len([s for s in aligned_signals if s['quality_grade'] == 'B']),
                    'grade_c_signals': len([s for s in aligned_signals if s['quality_grade'] == 'C']),
                    'grade_d_signals': len([s for s in aligned_signals if s['quality_grade'] == 'D'])
                },
                'signals': {
                    'grade_a': [s for s in aligned_signals if s['quality_grade'] == 'A'],
                    'grade_b': [s for s in aligned_signals if s['quality_grade'] == 'B'],
                    'grade_c': [s for s in aligned_signals if s['quality_grade'] == 'C'],
                    'grade_d': [s for s in aligned_signals if s['quality_grade'] == 'D']
                },
                'top_picks': aligned_signals[:10]  # Top 10 by alignment score
            }

            duration = datetime.now() - start_time
            logger.info(f"""
    🎯 Multi-timeframe screening completed in {duration}:
    📊 Symbols screened: {stats['symbols_screened']}
    🔍 Signals found: {stats['signals_found']}
    ⭐ High quality (A/B): {stats['high_quality_signals']}
    📈 Full alignment: {stats['full_alignment']}""")

            return {
                'success': True,
                'data': output,
                'stats': stats
            }

        except Exception as e:
            logger.error(f"Multi-timeframe screening failed: {e}")
            return {'success': False, 'message': str(e)}

    def save_results(self, results: Dict) -> bool:
        """Save multi-timeframe screening results"""
        try:
            if not results['success']:
                return False

            # Generate timestamped filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"multi_timeframe_signals_{timestamp}.json"

            # Save to multiple locations (following your pattern)
            output_paths = [
                self.get_output_path('breakout_results', filename),
                self.get_output_path('frontend_data', 'latest_multi_timeframe_signals.json')
            ]

            for path in output_paths:
                try:
                    with open(path, 'w') as f:
                        json.dump(results['data'], f, indent=2, default=str)
                    logger.info(f"💾 Results saved: {path}")
                except Exception as e:
                    logger.error(f"Failed to save to {path}: {e}")

            return True

        except Exception as e:
            logger.error(f"Error saving results: {e}")
            return False

    def get_output_path(self, subdir: str, filename: str) -> str:
        """Get output path following your project structure - FIXED"""
        from pathlib import Path

        # Find project root (following your pattern)
        current_dir = Path(__file__).parent.parent.parent
        output_dir = current_dir / subdir
        output_dir.mkdir(exist_ok=True)

        return str(output_dir / filename)  # FIXED: Don't duplicate subdir


def main():
    """Main execution function - FIXED for --all"""
    import argparse

    parser = argparse.ArgumentParser(description='Multi-Timeframe Breakout Screener')
    parser.add_argument('--limit', type=int, default=50, help='Limit number of symbols to screen (default: 50)')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    parser.add_argument('--all', action='store_true', help='Screen all symbols (removes limit)')

    args = parser.parse_args()

    screener = MultiTimeframeScreener()

    # Handle different modes - FIXED
    if args.all:
        limit = -1  # Special value to indicate "all symbols"
        logger.info("🚀 Running multi-timeframe screening on ALL symbols")
    elif args.test:
        limit = None  # Will use default handling in screening method
        logger.info(f"🧪 Testing multi-timeframe screening with: {args.test}")
    else:
        limit = args.limit
        logger.info(f"🚀 Running multi-timeframe screening (limit: {limit})")

    results = screener.screen_multi_timeframe_signals(limit=limit, test_symbols=args.test)

    if results['success']:
        screener.save_results(results)
        logger.info("✅ Multi-timeframe screening completed successfully")

        stats = results['stats']
        logger.info(f"🎯 Found {stats['signals_found']} aligned signals")
        logger.info(f"⭐ High quality signals: {stats['high_quality_signals']}")

    else:
        logger.error("❌ Multi-timeframe screening failed")


if __name__ == "__main__":
    main()