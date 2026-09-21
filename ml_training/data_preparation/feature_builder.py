# ml_training/data_preparation/feature_builder.py
# DEPRECATED 2026-09-20 -- do not run to (re)build training data. This module reads `breakouts` /
# `technical_indicators`, which sit on an older price basis than `stock_prices` (~42% of 2023-2025
# entry prices disagree with current closes by >2%, biasing labels; see CLAUDE.md "ML audit 2026-09-20").
# Use ml_training/data_preparation/build_dataset.py (stock_prices only, shared ml_training/features code).
# Kept because ml_training/tests/test_price_features.py checks the extracted label against it.

import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import sys
import json  # Add proper json import
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')

# Import the working ML config
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'config'))
try:
    from ml_config import ml_config

    print(f"SUCCESS: Using ML configuration")
except ImportError as e:
    print(f"ERROR: Could not import ml_config: {e}")
    sys.exit(1)


class SwingTradingFeatureBuilder:
    """
    Build comprehensive features for swing trading momentum breakout prediction
    """

    def __init__(self):
        # Use the working ML config
        self.db_config = ml_config.db_config

        # Test connection
        success, message = ml_config.test_db_connection()
        if not success:
            print(f"ERROR: Database connection failed: {message}")
            raise ConnectionError(f"Cannot connect to database: {message}")

    def get_db_connection(self):
        """Create PostgreSQL connection"""
        try:
            conn = psycopg2.connect(**self.db_config)
            return conn
        except Exception as e:
            print(f"ERROR: Database connection failed: {e}")
            return None

    def load_momentum_breakouts(self, limit=None):
        """Load breakouts with momentum scores"""
        print("Loading breakouts with momentum scores...")

        conn = self.get_db_connection()
        if not conn:
            return None

        query = """
            SELECT 
                symbol, date, breakout_type, entry_price, original_success,
                momentum_score, momentum_category, total_return_pct, analysis_days
            FROM momentum_scores
            WHERE momentum_score > 0
            ORDER BY date DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        df = pd.read_sql(query, conn)
        conn.close()

        print(f"SUCCESS: Loaded {len(df)} breakouts with momentum scores")
        return df

    def get_technical_features(self, symbol, date, lookback_days=20, conn=None):
        """
        Get technical indicator features.

        `conn`: an already-open connection to reuse (see build_training_
        dataset -- opening a fresh psycopg2 connection per query, times 4
        queries times tens of thousands of breakouts, was the actual
        bottleneck the first time this ran at Russell-3000 scale: ~265,000
        individual connections, several hours, for a job meant to run
        DAILY on a VPS). Falls back to opening/closing its own connection
        when called standalone (e.g. from a notebook or a one-off script),
        so nothing outside build_training_dataset needs to change.
        """
        owns_conn = conn is None
        if owns_conn:
            conn = self.get_db_connection()
        if not conn:
            return {}

        query = """
            SELECT date, sma_10, sma_20, sma_50, rsi_14, macd, macd_signal, macd_histogram,
                   bollinger_upper, bollinger_lower, atr_14, donchian_high_20, donchian_low_20,
                   donchian_mid_20, volume_sma_10, volume_ratio, price_position, channel_width_pct
            FROM technical_indicators
            WHERE symbol = %s AND date <= %s AND date >= %s - INTERVAL '%s days'
            ORDER BY date DESC LIMIT %s
        """

        try:
            tech_df = pd.read_sql(query, conn, params=[symbol, date, date, lookback_days, lookback_days + 5])
            if owns_conn:
                conn.close()

            if len(tech_df) == 0:
                return {}

            latest = tech_df.iloc[0]
            features = {}

            # Basic technical features
            features['rsi_14'] = latest['rsi_14'] or 50
            features['macd'] = latest['macd'] or 0
            features['volume_ratio'] = latest['volume_ratio'] or 1
            features['price_position'] = latest['price_position'] or 50
            features['channel_width_pct'] = latest['channel_width_pct'] or 5
            features['atr_14'] = latest['atr_14'] if latest['atr_14'] is not None else None

            # Moving averages
            features['sma_10'] = latest['sma_10'] or 0
            features['sma_20'] = latest['sma_20'] or 0
            features['sma_50'] = latest['sma_50'] or 0

            # SMA relationships
            if features['sma_20'] > 0:
                features['sma_10_vs_20'] = (features['sma_10'] - features['sma_20']) / features['sma_20'] * 100
            else:
                features['sma_10_vs_20'] = 0

            # Bollinger position
            if latest['bollinger_upper'] and latest['bollinger_lower']:
                bb_range = latest['bollinger_upper'] - latest['bollinger_lower']
                if bb_range > 0:
                    features['bollinger_position'] = (latest['price_position'] - latest[
                        'bollinger_lower']) / bb_range * 100
                else:
                    features['bollinger_position'] = 50
            else:
                features['bollinger_position'] = 50

            # Donchian position
            features['donchian_position'] = latest['price_position'] or 50

            return features

        except Exception as e:
            print(f"WARNING: Error getting technical features for {symbol}: {e}")
            if owns_conn:
                conn.close()
            return {}

    def get_fundamental_features(self, symbol, date, conn=None):
        """Get fundamental features. `conn`: see get_technical_features."""
        owns_conn = conn is None
        if owns_conn:
            conn = self.get_db_connection()
        if not conn:
            return {}

        query = """
            SELECT market_cap, pe_ratio, pb_ratio, ps_ratio, peg_ratio, beta, dividend_yield,
                   sector, industry, growth_score, profitability_score, financial_health_score,
                   valuation_score, overall_quality_score, quality_grade
            FROM daily_fundamentals
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 1
        """

        try:
            fund_df = pd.read_sql(query, conn, params=[symbol, date])
            if owns_conn:
                conn.close()

            if len(fund_df) == 0:
                return {}

            fund = fund_df.iloc[0]
            features = {}

            # Quality scores (normalized to 0-1)
            features['growth_score'] = (fund['growth_score'] or 5) / 10
            features['profitability_score'] = (fund['profitability_score'] or 5) / 10
            features['financial_health_score'] = (fund['financial_health_score'] or 5) / 10
            features['overall_quality_score'] = (fund['overall_quality_score'] or 5) / 10

            # Grade encoding
            grade_mapping = {'A': 1.0, 'B': 0.75, 'C': 0.5, 'D': 0.25, 'F': 0.0}
            features['quality_grade_numeric'] = grade_mapping.get(fund['quality_grade'], 0.5)

            # Valuation ratios (capped)
            features['pe_ratio'] = min(100, max(0, fund['pe_ratio'] or 15))
            features['pb_ratio'] = min(20, max(0, fund['pb_ratio'] or 2))
            features['beta'] = fund['beta'] or 1.0

            # Market cap (log scale)
            market_cap = fund['market_cap'] or 1000000000
            features['log_market_cap'] = np.log10(max(1000000, market_cap))

            # Sector
            features['sector'] = fund['sector'] or 'Unknown'

            return features

        except Exception as e:
            print(f"WARNING: Error getting fundamental features for {symbol}: {e}")
            if owns_conn:
                conn.close()
            return {}

    def get_earnings_trajectory_features(self, symbol, date, conn=None):
        """
        Earnings-trajectory features from quarterly_fundamentals.

        Previously unused by the ML pipeline entirely -- build_features_for_
        breakout() only pulled point-in-time ratios from daily_fundamentals
        (PE/PB/market cap as of the breakout date), never the quarter-over-
        quarter revenue/earnings series quarterly_fundamentals_updater.py
        actually builds. That series is exactly what's needed to let the
        model learn "growth stock turning profitable after years of
        losing" as a pattern, rather than only seeing a static snapshot.

        `conn`: see get_technical_features.
        """
        owns_conn = conn is None
        if owns_conn:
            conn = self.get_db_connection()
        if not conn:
            return {}

        query = """
            SELECT quarter, net_income, revenue_growth_yoy, eps_growth_yoy,
                   net_margin, piotroski_f_score
            FROM quarterly_fundamentals
            WHERE symbol = %s AND quarter <= %s
            ORDER BY quarter DESC
            LIMIT 8
        """

        features = {
            'quarters_of_earnings_data': 0,
            'latest_net_income_positive': 0,
            'consecutive_profitable_quarters': 0,
            'earnings_turnaround': 0,
            'earnings_growth_yoy': 0.0,
            'eps_growth_yoy': 0.0,
            'net_margin_trend': 0.0,
            'piotroski_f_score': None,
        }

        try:
            q_df = pd.read_sql(query, conn, params=[symbol, date])
            if owns_conn:
                conn.close()

            if len(q_df) == 0:
                return features

            features['quarters_of_earnings_data'] = len(q_df)

            latest = q_df.iloc[0]
            latest_positive = (latest['net_income'] or 0) > 0
            features['latest_net_income_positive'] = 1 if latest_positive else 0
            features['earnings_growth_yoy'] = float(latest['revenue_growth_yoy']) if pd.notna(latest['revenue_growth_yoy']) else 0.0
            features['eps_growth_yoy'] = float(latest['eps_growth_yoy']) if pd.notna(latest['eps_growth_yoy']) else 0.0
            if pd.notna(latest['piotroski_f_score']):
                features['piotroski_f_score'] = float(latest['piotroski_f_score'])

            # Consecutive profitable quarters counting back from the latest.
            consecutive = 0
            for _, row in q_df.iterrows():
                if (row['net_income'] or 0) > 0:
                    consecutive += 1
                else:
                    break
            features['consecutive_profitable_quarters'] = consecutive

            # The turnaround signal itself: profitable right now, but not
            # always -- at least one losing quarter somewhere in the
            # trailing ~2 years. Distinguishes "just flipped to
            # profitable" from "has always been profitable" (a steady
            # blue-chip breakout, which is a different pattern this
            # project is specifically trying to separate out).
            had_losing_quarter = (q_df['net_income'].fillna(0) <= 0).any()
            features['earnings_turnaround'] = 1 if (latest_positive and had_losing_quarter) else 0

            # Margin trend: latest net_margin vs. 4 quarters back (YoY
            # margin expansion/contraction), complementing the revenue/EPS
            # growth rates with a profitability-quality trend.
            if len(q_df) >= 5 and pd.notna(q_df.iloc[0]['net_margin']) and pd.notna(q_df.iloc[4]['net_margin']):
                features['net_margin_trend'] = float(q_df.iloc[0]['net_margin'] - q_df.iloc[4]['net_margin'])

            return features

        except Exception as e:
            print(f"WARNING: Error getting earnings trajectory features for {symbol}: {e}")
            if owns_conn:
                conn.close()
            return features

    def get_breakout_quality_features(self, symbol, date, entry_price, breakout_type, conn=None):
        """
        Breakout-mechanism-specific features -- how "clean" this particular
        Donchian breakout is, not just generic momentum/fundamental context.

        1. channel_squeeze_percentile: ranks today's channel_width_pct
           against its own trailing 120-day history. Classical breakout
           trading logic: a breakout out of a narrow (squeezed) channel
           tends to run further than one out of an already-wide channel,
           but channel_width_pct alone doesn't capture that -- a stock's
           "normal" width varies a lot by name, so it needs to be ranked
           against its own history, not read as a raw number.
        2. breakout_distance_atr: how far past the channel edge price
           closed, normalized by ATR -- separates a decisive breakout from
           a marginal one on a scale comparable across stocks of very
           different volatility (a $2 move means something different for a
           $20 stock than a $200 one; ATR-normalizing fixes that).

        `conn`: see get_technical_features.
        """
        owns_conn = conn is None
        if owns_conn:
            conn = self.get_db_connection()
        if not conn:
            return {}

        query = """
            SELECT date, channel_width_pct, donchian_high_20, donchian_low_20, atr_14
            FROM technical_indicators
            WHERE symbol = %s AND date <= %s
            ORDER BY date DESC LIMIT 120
        """

        try:
            df = pd.read_sql(query, conn, params=[symbol, date])
            if owns_conn:
                conn.close()

            if len(df) == 0:
                return {}

            latest = df.iloc[0]
            features = {}

            widths = df['channel_width_pct'].dropna()
            if len(widths) >= 20 and pd.notna(latest['channel_width_pct']):
                features['channel_squeeze_percentile'] = float((widths <= latest['channel_width_pct']).mean() * 100)
            else:
                features['channel_squeeze_percentile'] = 50.0  # not enough history -- neutral

            atr = latest['atr_14']
            if pd.notna(atr) and atr > 0:
                if breakout_type == 'bullish' and pd.notna(latest['donchian_high_20']):
                    features['breakout_distance_atr'] = float((entry_price - latest['donchian_high_20']) / atr)
                elif breakout_type == 'bearish' and pd.notna(latest['donchian_low_20']):
                    features['breakout_distance_atr'] = float((latest['donchian_low_20'] - entry_price) / atr)
                else:
                    features['breakout_distance_atr'] = 0.0
            else:
                features['breakout_distance_atr'] = 0.0

            return features

        except Exception as e:
            print(f"WARNING: Error getting breakout quality features for {symbol}: {e}")
            if owns_conn:
                conn.close()
            return {}

    def build_features_for_breakout(self, symbol, date, breakout_type, entry_price, conn=None):
        """Build complete feature set for a breakout. `conn`: see get_technical_features."""
        features = {}

        # Basic features
        features['is_bullish'] = 1 if breakout_type == 'bullish' else 0
        features['entry_price'] = entry_price

        # Get technical, fundamental, earnings-trajectory, and breakout-
        # quality features
        tech_features = self.get_technical_features(symbol, date, conn=conn)
        fund_features = self.get_fundamental_features(symbol, date, conn=conn)
        earnings_features = self.get_earnings_trajectory_features(symbol, date, conn=conn)
        quality_features = self.get_breakout_quality_features(symbol, date, entry_price, breakout_type, conn=conn)

        # Combine features
        features.update(tech_features)
        features.update(fund_features)
        features.update(earnings_features)
        features.update(quality_features)

        # Add interaction features
        if 'overall_quality_score' in features and 'volume_ratio' in features:
            features['quality_volume'] = features['overall_quality_score'] * features['volume_ratio']

        if 'overall_quality_score' in features and 'rsi_14' in features:
            features['quality_rsi'] = features['overall_quality_score'] * (features['rsi_14'] / 100)

        # Turnaround breakout with strong volume confirmation is the
        # specific pattern this project's stated strategy is chasing (see
        # CLAUDE.md) -- an explicit interaction term for it, rather than
        # relying on the model to discover the combination on its own from
        # two separately-weak individual features.
        if 'earnings_turnaround' in features and 'volume_ratio' in features:
            features['turnaround_volume'] = features['earnings_turnaround'] * features['volume_ratio']

        # Sector encoding
        if 'sector' in features:
            major_sectors = ['Technology', 'Healthcare', 'Financial Services', 'Consumer Cyclical', 'Industrials']
            for sector in major_sectors:
                features[f'sector_{sector.lower().replace(" ", "_")}'] = 1 if features['sector'] == sector else 0
            del features['sector']

        return features

    def build_training_dataset(self, momentum_df, save_to_db=True):
        """
        Build complete ML training dataset.

        Opens ONE connection and reuses it for all four feature queries x
        every breakout, instead of the 4-connections-per-breakout pattern
        the individual get_*_features() methods use standalone. That
        pattern is what made the first Russell-3000-scale run of this
        (66,191 breakouts -> ~265,000 individual psycopg2.connect() calls)
        impractical for something meant to run daily on a VPS. autocommit
        is set so this read-only loop never holds an open transaction
        across tens of thousands of queries.
        """
        print(f"Building features for {len(momentum_df)} breakouts...")

        conn = self.get_db_connection()
        if conn:
            conn.autocommit = True

        training_data = []

        try:
            for i, (_, breakout) in enumerate(momentum_df.iterrows()):
                if i % 500 == 0:
                    print(f"   Processing {i + 1}/{len(momentum_df)}")

                try:
                    # A connection can drop over a run this long (VPS
                    # network blip, DB restart) -- reconnect once rather
                    # than losing the rest of the batch.
                    if conn is None or conn.closed:
                        conn = self.get_db_connection()
                        if conn:
                            conn.autocommit = True

                    features = self.build_features_for_breakout(
                        breakout['symbol'],
                        breakout['date'],
                        breakout['breakout_type'],
                        breakout['entry_price'],
                        conn=conn,
                    )

                    # Add targets
                    features['target_momentum_score'] = breakout['momentum_score']
                    features['target_momentum_category'] = breakout['momentum_category']
                    features['target_binary'] = 1 if breakout['momentum_score'] >= 65 else 0
                    features['target_return_pct'] = breakout['total_return_pct']

                    # Add metadata
                    features['symbol'] = breakout['symbol']
                    features['date'] = breakout['date']
                    features['breakout_type'] = breakout['breakout_type']

                    training_data.append(features)

                except Exception as e:
                    print(f"WARNING: Error building features for {breakout['symbol']}: {e}")
                    continue
        finally:
            if conn and not conn.closed:
                conn.close()

        training_df = pd.DataFrame(training_data)

        print(f"SUCCESS: Built features for {len(training_df)} breakouts")
        feature_count = len(
            [col for col in training_df.columns if not col.startswith(('target_', 'symbol', 'date', 'breakout_type'))])
        print(f"Feature count: {feature_count}")

        if save_to_db:
            self.save_training_dataset(training_df)

        return training_df

    def save_training_dataset(self, training_df):
        """Save training dataset to PostgreSQL with proper JSON handling"""
        print("Saving training dataset to database...")

        conn = self.get_db_connection()
        if not conn:
            return False

        cursor = conn.cursor()

        # Create table
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

        cursor.execute(create_table_query)

        # Insert data
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

        try:
            for _, row in training_df.iterrows():
                # Extract features (exclude metadata and targets)
                feature_cols = [col for col in training_df.columns
                                if not col.startswith(('target_', 'symbol', 'date', 'breakout_type'))]
                features_dict = row[feature_cols].to_dict()

                # Convert numpy types to Python types for JSON
                for key, value in features_dict.items():
                    if pd.isna(value):
                        features_dict[key] = None
                    elif isinstance(value, (np.integer, np.floating)):
                        features_dict[key] = float(value)
                    elif isinstance(value, np.bool_):
                        features_dict[key] = bool(value)

                # Use standard json.dumps instead of pd.io.json.dumps
                features_json = json.dumps(features_dict)

                cursor.execute(insert_query, (
                    row['symbol'],
                    row['date'],
                    row['breakout_type'],
                    features_json,  # Properly serialized JSON
                    row['target_momentum_score'],
                    row['target_momentum_category'],
                    row['target_binary'],
                    row['target_return_pct']
                ))

            conn.commit()
            print(f"SUCCESS: Saved {len(training_df)} training records to database")
            return True

        except Exception as e:
            print(f"ERROR: Error saving training dataset: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()


def main():
    """Main function"""
    import argparse
    parser = argparse.ArgumentParser(description='Feature Engineering Pipeline')
    parser.add_argument('--limit', type=int, default=None,
                         help='Limit number of momentum-labeled breakouts to build features for '
                              '(default: no limit -- the previous hardcoded 500 silently under-used '
                              'the dataset once the universe grew past a handful of symbols)')
    args = parser.parse_args()

    print("FEATURE ENGINEERING PIPELINE")
    print("=" * 50)

    builder = SwingTradingFeatureBuilder()
    momentum_df = builder.load_momentum_breakouts(limit=args.limit)

    if momentum_df is None or len(momentum_df) == 0:
        print("ERROR: No momentum-labeled breakouts found")
        return

    training_df = builder.build_training_dataset(momentum_df, save_to_db=True)

    if len(training_df) > 0:
        print(f"\nSUCCESS! Training dataset ready: {len(training_df)} samples")
        print(f"Target distribution:")
        print(training_df['target_momentum_category'].value_counts())
        print(f"Ready for model training!")


if __name__ == "__main__":
    main()