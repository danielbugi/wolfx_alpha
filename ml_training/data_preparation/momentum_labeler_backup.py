# ml_training/data_preparation/momentum_labeler.py

import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import sys
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')

# Import the working ML config
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'config'))
try:
    from ml_config import ml_config

    print(f"SUCCESS: Using ML configuration")
    print(f"Database: {ml_config.DB_USER}@{ml_config.DB_HOST}:{ml_config.DB_PORT}/{ml_config.DB_NAME}")
except ImportError as e:
    print(f"ERROR: Could not import ml_config: {e}")
    sys.exit(1)


class MomentumLabeler:
    """
    Enhanced momentum scoring for breakouts based on sustained price action
    """

    def __init__(self):
        # Use the working ML config
        self.db_config = ml_config.db_config

        # Test connection
        success, message = ml_config.test_db_connection()
        if not success:
            print(f"ERROR: Database connection failed: {message}")
            raise ConnectionError(f"Cannot connect to database: {message}")
        else:
            print(f"SUCCESS: Database connection verified")

    def get_db_connection(self):
        """Create PostgreSQL connection"""
        try:
            conn = psycopg2.connect(**self.db_config)
            return conn
        except Exception as e:
            print(f"ERROR: Database connection failed: {e}")
            return None

    def load_breakout_data(self, limit=None):
        """Load breakout data for momentum analysis"""
        print("Loading breakout data from PostgreSQL...")

        conn = self.get_db_connection()
        if not conn:
            return None

        query = """
            SELECT 
                symbol, date, breakout_type, entry_price, success,
                volume_ratio, atr_pct, rsi_value, price_change_pct,
                max_gain_10d, max_loss_10d, days_to_peak
            FROM breakouts
            WHERE success IS NOT NULL
            ORDER BY date DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        df = pd.read_sql(query, conn)
        conn.close()

        print(f"SUCCESS: Loaded {len(df)} breakouts for momentum analysis")
        return df

    def get_price_history_for_breakout(self, symbol, breakout_date, days_forward=25):
        """Get price history following a breakout"""
        conn = self.get_db_connection()
        if not conn:
            return None

        query = """
            SELECT date, open, high, low, close, volume
            FROM stock_prices
            WHERE symbol = %s 
                AND date >= %s 
                AND date <= %s + INTERVAL '%s days'
            ORDER BY date
        """

        try:
            df = pd.read_sql(query, conn, params=[symbol, breakout_date, breakout_date, days_forward])
            conn.close()
            return df
        except Exception as e:
            print(f"WARNING: Error getting price history for {symbol}: {e}")
            conn.close()
            return None

    def calculate_momentum_score(self, price_data, entry_price, breakout_type):
        """Calculate momentum score (0-100) based on sustained price action"""
        if price_data is None or len(price_data) < 5:
            return 0

        prices = price_data['close'].values
        volumes = price_data['volume'].values
        highs = price_data['high'].values
        lows = price_data['low'].values

        if len(prices) == 0:
            return 0

        momentum_score = 0
        is_bullish = (breakout_type == 'bullish')

        # 1. TREND PERSISTENCE (30 points max)
        if is_bullish:
            trend_days = sum(1 for p in prices if p >= entry_price)
        else:
            trend_days = sum(1 for p in prices if p <= entry_price)

        trend_persistence = min(30, (trend_days / len(prices)) * 30)
        momentum_score += trend_persistence

        # 2. NEW HIGHS/LOWS CREATION (25 points max)
        if is_bullish:
            new_extremes = sum(1 for h in highs if h > entry_price)
            progressive_highs = 0
            max_so_far = entry_price
            for high in highs:
                if high > max_so_far:
                    progressive_highs += 1
                    max_so_far = high
            new_extremes += progressive_highs
        else:
            new_extremes = sum(1 for l in lows if l < entry_price)
            progressive_lows = 0
            min_so_far = entry_price
            for low in lows:
                if low < min_so_far:
                    progressive_lows += 1
                    min_so_far = low
            new_extremes += progressive_lows

        extremes_score = min(25, (new_extremes / len(prices)) * 25)
        momentum_score += extremes_score

        # 3. DAILY CONSISTENCY (20 points max)
        daily_returns = np.diff(prices) / prices[:-1]

        if is_bullish:
            positive_days = sum(1 for r in daily_returns if r > 0)
            consistency = positive_days / len(daily_returns) if len(daily_returns) > 0 else 0
        else:
            negative_days = sum(1 for r in daily_returns if r < 0)
            consistency = negative_days / len(daily_returns) if len(daily_returns) > 0 else 0

        consistency_score = consistency * 20
        momentum_score += consistency_score

        # 4. VOLUME CONFIRMATION (15 points max)
        avg_volume = np.mean(volumes) if len(volumes) > 0 else 1
        first_day_volume = volumes[0] if len(volumes) > 0 else avg_volume

        volume_strength = min(2.0, first_day_volume / avg_volume) if avg_volume > 0 else 1.0
        volume_score = (volume_strength - 1.0) * 15
        momentum_score += volume_score

        # 5. SPEED OF MOVE (10 points max)
        if len(prices) > 1:
            total_move = abs(prices[-1] - entry_price) / entry_price
            speed_score = min(10, total_move * 100)
            momentum_score += speed_score

        return round(min(100, momentum_score))

    def calculate_momentum_category(self, momentum_score):
        """Categorize momentum score"""
        if momentum_score >= 80:
            return "exceptional"
        elif momentum_score >= 65:
            return "strong"
        elif momentum_score >= 50:
            return "moderate"
        elif momentum_score >= 35:
            return "weak"
        else:
            return "failed"

    def process_breakouts_batch(self, breakouts_df, batch_size=100):
        """Process breakouts in batches to calculate momentum scores"""
        print(f"Processing {len(breakouts_df)} breakouts in batches of {batch_size}...")

        results = []

        for i in range(0, len(breakouts_df), batch_size):
            batch = breakouts_df.iloc[i:i + batch_size]
            print(f"   Processing batch {i // batch_size + 1}/{(len(breakouts_df) - 1) // batch_size + 1}")

            for _, breakout in batch.iterrows():
                try:
                    price_data = self.get_price_history_for_breakout(
                        breakout['symbol'],
                        breakout['date'],
                        days_forward=ml_config.MOMENTUM_DAYS_FORWARD
                    )

                    if price_data is not None and len(price_data) >= 5:
                        momentum_score = self.calculate_momentum_score(
                            price_data,
                            breakout['entry_price'],
                            breakout['breakout_type']
                        )

                        momentum_category = self.calculate_momentum_category(momentum_score)

                        final_price = price_data['close'].iloc[-1] if len(price_data) > 0 else breakout['entry_price']
                        total_return = (final_price - breakout['entry_price']) / breakout['entry_price'] * 100

                        results.append({
                            'symbol': breakout['symbol'],
                            'date': breakout['date'],
                            'breakout_type': breakout['breakout_type'],
                            'entry_price': breakout['entry_price'],
                            'original_success': breakout['success'],
                            'momentum_score': momentum_score,
                            'momentum_category': momentum_category,
                            'total_return_pct': round(total_return, 2),
                            'analysis_days': len(price_data)
                        })
                    else:
                        results.append({
                            'symbol': breakout['symbol'],
                            'date': breakout['date'],
                            'breakout_type': breakout['breakout_type'],
                            'entry_price': breakout['entry_price'],
                            'original_success': breakout['success'],
                            'momentum_score': 0,
                            'momentum_category': 'insufficient_data',
                            'total_return_pct': 0,
                            'analysis_days': 0
                        })

                except Exception as e:
                    print(f"WARNING: Error processing {breakout['symbol']} on {breakout['date']}: {e}")
                    results.append({
                        'symbol': breakout['symbol'],
                        'date': breakout['date'],
                        'breakout_type': breakout['breakout_type'],
                        'entry_price': breakout['entry_price'],
                        'original_success': breakout['success'],
                        'momentum_score': 0,
                        'momentum_category': 'error',
                        'total_return_pct': 0,
                        'analysis_days': 0
                    })

        return pd.DataFrame(results)

    def save_momentum_scores(self, momentum_df):
        """Save momentum scores to PostgreSQL"""
        print("Saving momentum scores to database...")

        conn = self.get_db_connection()
        if not conn:
            return False

        cursor = conn.cursor()

        create_table_query = """
            CREATE TABLE IF NOT EXISTS momentum_scores (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(10),
                date DATE,
                breakout_type VARCHAR(10),
                entry_price NUMERIC(10,2),
                original_success BOOLEAN,
                momentum_score INTEGER,
                momentum_category VARCHAR(20),
                total_return_pct NUMERIC(8,2),
                analysis_days INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, date)
            );
        """

        cursor.execute(create_table_query)

        insert_query = """
            INSERT INTO momentum_scores 
            (symbol, date, breakout_type, entry_price, original_success, 
             momentum_score, momentum_category, total_return_pct, analysis_days)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, date) DO UPDATE SET
                momentum_score = EXCLUDED.momentum_score,
                momentum_category = EXCLUDED.momentum_category,
                total_return_pct = EXCLUDED.total_return_pct,
                analysis_days = EXCLUDED.analysis_days
        """

        try:
            for _, row in momentum_df.iterrows():
                cursor.execute(insert_query, (
                    row['symbol'], row['date'], row['breakout_type'], row['entry_price'],
                    row['original_success'], row['momentum_score'], row['momentum_category'],
                    row['total_return_pct'], row['analysis_days']
                ))

            conn.commit()
            print(f"SUCCESS: Saved {len(momentum_df)} momentum scores to database")
            return True

        except Exception as e:
            print(f"ERROR: Error saving momentum scores: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            conn.close()

    def generate_momentum_report(self, momentum_df):
        """Generate momentum analysis report"""
        print("\nMOMENTUM SCORING REPORT")
        print("=" * 50)

        print(f"Total breakouts analyzed: {len(momentum_df)}")
        print(f"Average momentum score: {momentum_df['momentum_score'].mean():.1f}")

        print(f"\nMomentum Categories:")
        category_counts = momentum_df['momentum_category'].value_counts()
        for category, count in category_counts.items():
            pct = (count / len(momentum_df)) * 100
            print(f"   {category}: {count} ({pct:.1f}%)")

        bullish_df = momentum_df[momentum_df['breakout_type'] == 'bullish']
        bearish_df = momentum_df[momentum_df['breakout_type'] == 'bearish']

        print(f"\nBreakout Type Analysis:")
        print(f"   Bullish: {len(bullish_df)} (avg score: {bullish_df['momentum_score'].mean():.1f})")
        print(f"   Bearish: {len(bearish_df)} (avg score: {bearish_df['momentum_score'].mean():.1f})")

        successful = momentum_df[momentum_df['original_success'] == True]
        failed = momentum_df[momentum_df['original_success'] == False]

        print(f"\nOriginal Success vs Momentum Score:")
        print(f"   Originally successful: avg momentum {successful['momentum_score'].mean():.1f}")
        print(f"   Originally failed: avg momentum {failed['momentum_score'].mean():.1f}")

        print(f"\nTop 10 Momentum Scores:")
        top_performers = momentum_df.nlargest(10, 'momentum_score')
        for _, row in top_performers.iterrows():
            print(f"   {row['symbol']} ({row['date']}): Score {row['momentum_score']} - {row['momentum_category']}")

        return momentum_df


def main():
    """Main function"""
    print("MOMENTUM LABELING SYSTEM")
    print("=" * 50)

    labeler = MomentumLabeler()
    breakouts_df = labeler.load_breakout_data(limit=1000)

    if breakouts_df is None or len(breakouts_df) == 0:
        print("ERROR: No breakout data found")
        return

    momentum_df = labeler.process_breakouts_batch(breakouts_df)
    success = labeler.save_momentum_scores(momentum_df)

    if success:
        labeler.generate_momentum_report(momentum_df)
        print(f"\nSUCCESS! Momentum scores saved to 'momentum_scores' table")
        print(f"Ready for feature engineering phase")
    else:
        print(f"ERROR: Failed to save momentum scores")


if __name__ == "__main__":
    main()