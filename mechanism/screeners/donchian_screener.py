#!/usr/bin/env python3
"""
Complete Fixed Donchian Screener - NO FILTERS
Pure breakout detection with perfect JSON format and complete fundamental scores
"""

import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    from shared import db, setup_logging

    logger = setup_logging("fixed_donchian")
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

try:
    from ml_signal_enhancer import MLMomentumEnhancer
    ML_AVAILABLE = True
    print("SUCCESS: ML momentum enhancement available")
except ImportError as e:
    print(f"WARNING: ML enhancement not available: {e}")
    ML_AVAILABLE = False


def safe_float(value):
    """Safely convert decimal/numeric values to float"""
    if value is None:
        return None
    try:
        result = float(value)
        # Handle NaN and infinity
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


class CompleteDonchianScreener:
    """Complete Donchian breakout screener with perfect JSON output"""

    def __init__(self):
        logger.info("Complete Donchian Screener - NO FILTERS MODE")
        logger.info("Detecting breakouts and near breakouts with complete fundamental scores")

        self.ml_enhancer = None
        if ML_AVAILABLE:
            try:
                self.ml_enhancer = MLMomentumEnhancer()
                if self.ml_enhancer.model_loaded:
                    print("SUCCESS: ML model loaded and ready")
                else:
                    print("WARNING: ML model not loaded")
                    self.ml_enhancer = None
            except Exception as e:
                print(f"WARNING: Failed to initialize ML enhancer: {e}")
                self.ml_enhancer = None



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

    def get_fundamentals(self, symbol: str) -> Dict:
        """Get fundamental data for a symbol with proper fallback"""
        try:
            # First try to get records with quality scores
            query = """
            SELECT 
                market_cap, sector, industry, pe_ratio, pb_ratio, beta, 
                dividend_yield, quality_grade, growth_score, profitability_score, 
                financial_health_score, valuation_score, overall_quality_score
            FROM daily_fundamentals
            WHERE symbol = %s
            AND overall_quality_score IS NOT NULL
            ORDER BY date DESC
            LIMIT 1
            """

            results = db.execute_dict_query(query, (symbol,))
            if results:
                return results[0]

            # Fallback to any fundamentals data if no quality scores
            fallback_query = """
            SELECT 
                market_cap, sector, industry, pe_ratio, pb_ratio, beta, 
                dividend_yield, quality_grade, growth_score, profitability_score, 
                financial_health_score, valuation_score, overall_quality_score
            FROM daily_fundamentals
            WHERE symbol = %s
            ORDER BY date DESC
            LIMIT 1
            """

            fallback_results = db.execute_dict_query(fallback_query, (symbol,))
            if fallback_results:
                return fallback_results[0]

            return {}
        except Exception as e:
            logger.error(f"Error getting fundamentals for {symbol}: {e}")
            return {}

    def format_market_cap(self, market_cap) -> str:
        """Format market cap for display"""
        market_cap = safe_float(market_cap)
        if not market_cap or market_cap == 0:
            return "Unknown"

        if market_cap >= 1_000_000_000_000:  # Trillion
            return f"${market_cap / 1_000_000_000_000:.1f}T"
        elif market_cap >= 1_000_000_000:  # Billion
            return f"${market_cap / 1_000_000_000:.1f}B"
        elif market_cap >= 1_000_000:  # Million
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

    def get_color_code(self, breakout_type: str) -> str:
        """Get color code for frontend styling"""
        color_map = {
            'bullish_breakout': '#22c55e',  # Green
            'bearish_breakout': '#ef4444',  # Red
            'near_bullish': '#3b82f6',  # Blue
            'near_bearish': '#f59e0b'  # Orange
        }
        return color_map.get(breakout_type, '#6b7280')

    def get_icon(self, breakout_type: str) -> str:
        """Get icon name for frontend display"""
        icon_map = {
            'bullish_breakout': 'trending-up',
            'bearish_breakout': 'trending-down',
            'near_bullish': 'arrow-up',
            'near_bearish': 'arrow-down'
        }
        return icon_map.get(breakout_type, 'minus')

    def get_summary_text(self, breakout_type: str, distance_to_high: float, distance_to_low: float,
                         price: float) -> str:
        """Get summary text for frontend display"""
        if breakout_type == 'bullish_breakout':
            return f"Bullish breakout at ${price:.2f} - Price broke above Donchian high"
        elif breakout_type == 'bearish_breakout':
            return f"Bearish breakout at ${price:.2f} - Price broke below Donchian low"
        elif breakout_type == 'near_bullish':
            return f"Near bullish breakout - {distance_to_high:.1f}% away from Donchian high"
        elif breakout_type == 'near_bearish':
            return f"Near bearish breakout - {distance_to_low:.1f}% away from Donchian low"
        else:
            return "Unknown signal type"

    def check_donchian_breakout(self, symbol: str) -> Optional[Dict]:
        """Check for Donchian breakouts with comprehensive frontend data"""
        try:
            # Get recent price data with technical indicators
            query = """
            SELECT 
                p.date, p.open, p.high, p.low, p.close, p.volume,
                t.donchian_high_20, t.donchian_low_20, t.donchian_mid_20,
                t.sma_20, t.sma_50, t.rsi_14, t.atr_14, t.volume_ratio
            FROM stock_prices p
            LEFT JOIN technical_indicators t ON p.symbol = t.symbol AND p.date = t.date
            WHERE p.symbol = %s
            AND p.date >= CURRENT_DATE - INTERVAL '10 days'
            ORDER BY p.date DESC
            LIMIT 5
            """

            results = db.execute_dict_query(query, (symbol,))

            if len(results) < 2:
                return None

            latest = results[0]
            previous = results[1]

            # Skip if missing Donchian data
            if (safe_float(latest['donchian_high_20']) is None or
                    safe_float(latest['donchian_low_20']) is None or
                    safe_float(previous['donchian_high_20']) is None or
                    safe_float(previous['donchian_low_20']) is None):
                return None

            # Get fundamentals data for frontend display
            fundamentals = self.get_fundamentals(symbol)

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

            # Price position in channel (0-100%)
            price_position = ((current_price - donchian_low) / channel_width) * 100 if channel_width > 0 else 50

            breakout_type = None
            urgency = "none"

            # ACTUAL BREAKOUTS (price breaks through previous Donchian level)
            if (current_price > prev_donchian_high and prev_close <= prev_donchian_high):
                breakout_type = "bullish_breakout"
                urgency = "immediate"
            elif (current_price < prev_donchian_low and prev_close >= prev_donchian_low):
                breakout_type = "bearish_breakout"
                urgency = "immediate"

            # NEAR BREAKOUTS (within 3% of current Donchian levels)
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

            if breakout_type:
                # Calculate stop loss and target prices
                atr_value = safe_float(latest['atr_14'])
                if atr_value and atr_value > 0:
                    atr = atr_value
                else:
                    # Fallback: use 2% of current price as ATR estimate
                    atr = current_price * 0.02

                if 'bullish' in breakout_type:
                    stop_loss = round(current_price - (atr * 2), 2)
                    target_price = round(current_price + (atr * 3), 2)
                    reward_risk_ratio = 3.0
                else:
                    stop_loss = round(current_price + (atr * 2), 2)
                    target_price = round(current_price - (atr * 3), 2)
                    reward_risk_ratio = 3.0

                return {
                    # Core identification
                    'symbol': symbol,
                    'type': breakout_type,
                    'urgency': urgency,
                    'timestamp': datetime.now().isoformat(),
                    'screening_date': latest['date'].isoformat() if hasattr(latest['date'], 'isoformat') else str(
                        latest['date']),

                    # Price data (properly formatted)
                    'current_price': round(current_price, 2),
                    'open_price': round(safe_float(latest['open']), 2) if latest['open'] else None,
                    'high_price': round(safe_float(latest['high']), 2) if latest['high'] else None,
                    'low_price': round(safe_float(latest['low']), 2) if latest['low'] else None,
                    'price_change_pct': round(price_change_pct, 2),

                    # Donchian channel data (properly formatted)
                    'donchian_high': round(donchian_high, 2),
                    'donchian_low': round(donchian_low, 2),
                    'donchian_mid': round(donchian_mid, 2) if donchian_mid else None,
                    'channel_width': round(channel_width, 2),
                    'channel_width_pct': round(channel_width_pct, 2),
                    'price_position_in_channel': round(price_position, 1),

                    # Distance analysis (properly formatted)
                    'distance_to_high_pct': round(distance_to_high, 2),
                    'distance_to_low_pct': round(distance_to_low, 2),
                    'distance_to_breakout': round(distance_to_high if 'bullish' in breakout_type else distance_to_low,
                                                  2),

                    # Volume data (properly formatted)
                    'volume': int(safe_float(latest['volume'])) if latest['volume'] else 0,
                    'volume_formatted': f"{int(safe_float(latest['volume'])):,}" if latest['volume'] else "0",
                    'volume_ratio': round(safe_float(latest['volume_ratio']), 2) if latest['volume_ratio'] else 1.0,

                    # Technical indicators (properly formatted)
                    'rsi_14': format_for_json(latest['rsi_14']),
                    'atr_14': format_for_json(latest['atr_14']),
                    'sma_20': format_for_json(latest['sma_20']),
                    'sma_50': format_for_json(latest['sma_50']),

                    # Trading suggestions (properly formatted)
                    'stop_loss_price': stop_loss,
                    'target_price': target_price,
                    'reward_risk_ratio': reward_risk_ratio,
                    'position_size_suggestion': "2-3% of portfolio",

                    # Fundamental data (properly formatted)
                    'market_cap': format_for_json(fundamentals.get('market_cap', 0)),
                    'market_cap_formatted': self.format_market_cap(fundamentals.get('market_cap', 0)),
                    'sector': fundamentals.get('sector', 'Unknown'),
                    'industry': fundamentals.get('industry', 'Unknown'),
                    'pe_ratio': format_for_json(fundamentals.get('pe_ratio')),
                    'pb_ratio': format_for_json(fundamentals.get('pb_ratio')),
                    'beta': format_for_json(fundamentals.get('beta')),
                    'dividend_yield': format_for_json(fundamentals.get('dividend_yield')),
                    'quality_grade': fundamentals.get('quality_grade'),

                    # Quality scores (properly formatted)
                    'growth_score': format_for_json(fundamentals.get('growth_score')),
                    'profitability_score': format_for_json(fundamentals.get('profitability_score')),
                    'financial_health_score': format_for_json(fundamentals.get('financial_health_score')),
                    'valuation_score': format_for_json(fundamentals.get('valuation_score')),
                    'overall_quality_score': format_for_json(fundamentals.get('overall_quality_score')),

                    # Display helpers for frontend
                    'display_name': symbol,
                    'signal_strength': self.calculate_signal_strength(breakout_type, distance_to_high, distance_to_low),
                    'color_code': self.get_color_code(breakout_type),
                    'icon': self.get_icon(breakout_type),
                    'summary_text': self.get_summary_text(breakout_type, distance_to_high, distance_to_low,
                                                          current_price)
                }

            return None

        except Exception as e:
            logger.error(f"Error checking {symbol}: {e}")
            return None

    def screen_symbols(self, symbols: List[str]) -> List[Dict]:
        """Screen symbols for breakouts"""
        results = []

        logger.info(f"Screening {len(symbols)} symbols...")

        for i, symbol in enumerate(symbols, 1):
            try:
                breakout = self.check_donchian_breakout(symbol)
                if breakout:
                    results.append(breakout)
                    logger.info(f"Found {breakout['type']} for {symbol} at ${breakout['current_price']:.2f}")

                # Progress update
                if i % 100 == 0:
                    logger.info(f"Screened {i}/{len(symbols)}, found {len(results)} signals")

            except Exception as e:
                logger.warning(f"Error screening {symbol}: {e}")
                continue

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
            return round(data, 6)  # Limit precision
        elif hasattr(data, '__float__'):  # Decimal type
            try:
                float_val = float(data)
                if np.isnan(float_val) or np.isinf(float_val):
                    return None
                return round(float_val, 6)
            except (ValueError, TypeError):
                return None
        else:
            return data

    def calculate_average_signal_strength(self, results: List[Dict]) -> str:
        """Calculate average signal strength"""
        if not results:
            return "none"

        strength_values = {
            'very_strong': 4,
            'strong': 3,
            'medium': 2,
            'weak': 1
        }

        total_value = sum(strength_values.get(r.get('signal_strength', 'weak'), 1) for r in results)
        avg_value = total_value / len(results)

        if avg_value >= 3.5:
            return "very_strong"
        elif avg_value >= 2.5:
            return "strong"
        elif avg_value >= 1.5:
            return "medium"
        else:
            return "weak"

    def save_results(self, results: List[Dict]) -> bool:
        """Save comprehensive results to JSON file for frontend"""
        try:
            # Create output directory
            output_dir = "breakout_results"
            os.makedirs(output_dir, exist_ok=True)

            # Organize results by type
            bullish_breakouts = [r for r in results if r['type'] == 'bullish_breakout']
            bearish_breakouts = [r for r in results if r['type'] == 'bearish_breakout']
            near_bullish = [r for r in results if r['type'] == 'near_bullish']
            near_bearish = [r for r in results if r['type'] == 'near_bearish']

            # Sort by urgency and distance
            def sort_key(x):
                urgency_order = {'immediate': 0, 'very_high': 1, 'high': 2, 'medium': 3, 'low': 4}
                return (urgency_order.get(x['urgency'], 5), x['distance_to_breakout'])

            bullish_breakouts.sort(key=sort_key)
            bearish_breakouts.sort(key=sort_key)
            near_bullish.sort(key=sort_key)
            near_bearish.sort(key=sort_key)

            # Calculate summary statistics
            total_signals = len(results)
            avg_distance = np.mean([r['distance_to_breakout'] for r in results]) if results else 0

            # Sector breakdown
            sector_counts = {}
            for result in results:
                sector = result.get('sector', 'Unknown')
                sector_counts[sector] = sector_counts.get(sector, 0) + 1

            # Urgency breakdown
            urgency_counts = {}
            for result in results:
                urgency = result.get('urgency', 'none')
                urgency_counts[urgency] = urgency_counts.get(urgency, 0) + 1

            # Create comprehensive output
            output_data = {
                'metadata': {
                    'timestamp': datetime.now().isoformat(),
                    'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'screening_date': datetime.now().date().isoformat(),
                    'version': '2.0',
                    'data_source': 'donchian_screener_no_filters'
                },
                'summary': {
                    'total_signals': total_signals,
                    'bullish_breakouts': len(bullish_breakouts),
                    'bearish_breakouts': len(bearish_breakouts),
                    'near_bullish': len(near_bullish),
                    'near_bearish': len(near_bearish),
                    'immediate_signals': urgency_counts.get('immediate', 0),
                    'high_priority_signals': urgency_counts.get('very_high', 0) + urgency_counts.get('high', 0),
                    'average_distance_to_breakout': round(avg_distance, 2),
                    'sector_breakdown': sector_counts,
                    'urgency_breakdown': urgency_counts
                },
                'signals': {
                    'bullish_breakouts': bullish_breakouts,
                    'bearish_breakouts': bearish_breakouts,
                    'near_bullish': near_bullish,
                    'near_bearish': near_bearish
                },
                'top_signals': {
                    'most_urgent': sorted(results, key=sort_key)[:10],
                    'closest_to_breakout': sorted(results, key=lambda x: x['distance_to_breakout'])[:10],
                    'highest_volume': sorted(results, key=lambda x: x['volume'], reverse=True)[:10],
                    'by_sector': {sector: [r for r in results if r.get('sector') == sector][:3]
                                  for sector in list(sector_counts.keys())[:5]}
                },
                'market_overview': {
                    'dominant_direction': 'bullish' if (len(bullish_breakouts) + len(near_bullish)) > (
                            len(bearish_breakouts) + len(near_bearish)) else 'bearish',
                    'market_activity_level': 'high' if total_signals > 50 else 'medium' if total_signals > 20 else 'low',
                    'breakout_vs_near_ratio': round(
                        (len(bullish_breakouts) + len(bearish_breakouts)) / max(total_signals, 1), 2),
                    'average_signal_strength': self.calculate_average_signal_strength(results)
                }
            }

            # Clean the output data
            output_data = self.clean_data_for_json(output_data)

            # Save timestamped file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{output_dir}/donchian_breakouts_{timestamp}.json"
            with open(filename, 'w') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            # Save latest file for frontend
            latest_filename = f"{output_dir}/latest_breakouts.json"
            with open(latest_filename, 'w') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)

            # Save simplified version for quick access
            simple_data = self.clean_data_for_json({
                'timestamp': output_data['metadata']['timestamp'],
                'total_signals': total_signals,
                'signals': [
                    {
                        'symbol': r['symbol'],
                        'type': r['type'],
                        'price': r['current_price'],
                        'urgency': r['urgency'],
                        'distance': r['distance_to_breakout'],
                        'summary': r['summary_text']
                    }
                    for r in results
                ]
            })

            simple_filename = f"{output_dir}/simple_breakouts.json"
            with open(simple_filename, 'w') as f:
                json.dump(simple_data, f, indent=2, ensure_ascii=False)

            logger.info(f"Comprehensive results saved to {filename}")
            logger.info(f"Latest results saved to {latest_filename}")
            logger.info(f"Simple results saved to {simple_filename}")

            return True

        except Exception as e:
            logger.error(f"Error saving results: {e}")
            return False

    def run_screening(self, test_symbols: Optional[List[str]] = None):
        """Run the screening"""
        start_time = datetime.now()

        if test_symbols:
            symbols = test_symbols
            logger.info(f"Test mode with symbols: {symbols}")
        else:
            symbols = self.get_all_symbols()

        if not symbols:
            logger.error("No symbols to screen")
            return

        logger.info(f"Starting Donchian breakout screening at {start_time}")

        # Screen for breakouts
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

        logger.info(f"""
Screening completed in {duration}
Symbols screened: {len(symbols)}
Total signals found: {len(results)}

ACTUAL BREAKOUTS: {bullish_breakouts + bearish_breakouts}
  Bullish: {bullish_breakouts}
  Bearish: {bearish_breakouts}

NEAR BREAKOUTS: {near_bullish + near_bearish}
  Near Bullish: {near_bullish}
  Near Bearish: {near_bearish}
        """)

        # Show top results with enhanced details
        if results:
            logger.info("Top signals found:")
            for result in results[:10]:
                symbol = result['symbol']
                current_price = result['current_price']
                urgency = result['urgency']
                signal_type = result['type'].replace('_', ' ').title()
                distance = result['distance_to_breakout']
                volume_ratio = result.get('volume_ratio', 1.0)
                sector = result.get('sector', 'Unknown')
                quality_score = result.get('overall_quality_score')

                logger.info(f"  {symbol}: {signal_type} - {distance}% away at ${current_price:.2f}")
                logger.info(
                    f"    Urgency: {urgency}, Volume: {volume_ratio:.1f}x, Sector: {sector}, Quality: {quality_score}")
        else:
            logger.info("No breakout signals found")


def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description='Complete Fixed Donchian Screener')
    parser.add_argument('--test', nargs='+', help='Test with specific symbols')
    args = parser.parse_args()

    screener = CompleteDonchianScreener()

    if args.test:
        screener.run_screening(test_symbols=args.test)
    else:
        screener.run_screening()


if __name__ == "__main__":
    main()