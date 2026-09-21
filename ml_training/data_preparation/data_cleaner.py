# ml_training/data_preparation/data_cleaner.py

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
except ImportError as e:
    print(f"ERROR: Could not import ml_config: {e}")
    sys.exit(1)


class PostgreSQLDataCleaner:
    """
    Data quality checker and cleaner for PostgreSQL trading database
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

    def check_database_health(self):
        """Check overall database health and table status"""
        print("CHECKING DATABASE HEALTH")
        print("=" * 40)

        conn = self.get_db_connection()
        if not conn:
            return False

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            tables_to_check = [
                'stock_prices',
                'technical_indicators',
                'daily_fundamentals',
                'breakouts',
                'ml_training_data'
            ]

            table_status = {}

            for table in tables_to_check:
                try:
                    cursor.execute(f"SELECT COUNT(*) as count FROM {table}")
                    result = cursor.fetchone()
                    table_status[table] = result['count']
                    print(f"SUCCESS: {table}: {result['count']:,} records")
                except Exception as e:
                    table_status[table] = 0
                    print(f"ERROR: {table}: Table not found or error ({e})")

            # Check for momentum_scores table
            try:
                cursor.execute("SELECT COUNT(*) as count FROM momentum_scores")
                result = cursor.fetchone()
                table_status['momentum_scores'] = result['count']
                print(f"SUCCESS: momentum_scores: {result['count']:,} records")
            except:
                table_status['momentum_scores'] = 0
                print(f"WARNING: momentum_scores: Not found (run momentum_labeler.py)")

            conn.close()

            # Overall assessment
            print(f"\nOVERALL HEALTH:")
            if table_status['stock_prices'] > 0 and table_status['technical_indicators'] > 0:
                print(f"SUCCESS: Core data available for ML training")
                return True
            else:
                print(f"ERROR: Missing core data - run data updates first")
                return False

        except Exception as e:
            print(f"ERROR: Error checking database health: {e}")
            conn.close()
            return False

    def check_data_freshness(self):
        """Check if data is recent enough for trading"""
        print(f"\nCHECKING DATA FRESHNESS")
        print("=" * 40)

        conn = self.get_db_connection()
        if not conn:
            return

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            for table in ['stock_prices', 'technical_indicators', 'daily_fundamentals']:
                try:
                    cursor.execute(f"SELECT MAX(date) as latest_date FROM {table}")
                    result = cursor.fetchone()
                    latest_date = result['latest_date']

                    if latest_date:
                        days_ago = (datetime.now().date() - latest_date).days
                        if days_ago <= 3:
                            print(f"SUCCESS: {table}: {latest_date} ({days_ago} days ago)")
                        else:
                            print(f"WARNING: {table}: {latest_date} ({days_ago} days ago) - may need update")
                    else:
                        print(f"ERROR: {table}: No data found")

                except Exception as e:
                    print(f"ERROR: {table}: Error checking date ({e})")

            conn.close()

        except Exception as e:
            print(f"ERROR: Error checking data freshness: {e}")
            conn.close()

    def check_breakout_data_quality(self):
        """Check quality of breakout data for ML training"""
        print(f"\nCHECKING BREAKOUT DATA QUALITY")
        print("=" * 40)

        conn = self.get_db_connection()
        if not conn:
            return

        try:
            breakouts_query = """
                SELECT 
                    COUNT(*) as total_breakouts,
                    COUNT(CASE WHEN success IS NOT NULL THEN 1 END) as labeled_breakouts,
                    COUNT(CASE WHEN success = true THEN 1 END) as successful_breakouts,
                    COUNT(CASE WHEN breakout_type = 'bullish' THEN 1 END) as bullish_count,
                    COUNT(CASE WHEN breakout_type = 'bearish' THEN 1 END) as bearish_count,
                    MIN(date) as earliest_date,
                    MAX(date) as latest_date
                FROM breakouts
            """

            breakouts_df = pd.read_sql(breakouts_query, conn)
            stats = breakouts_df.iloc[0]

            print(f"Breakout Statistics:")
            print(f"   Total breakouts: {stats['total_breakouts']:,}")
            print(f"   Labeled breakouts: {stats['labeled_breakouts']:,}")
            print(
                f"   Success rate: {(stats['successful_breakouts'] / stats['labeled_breakouts'] * 100) if stats['labeled_breakouts'] > 0 else 0:.1f}%")
            print(f"   Bullish: {stats['bullish_count']:,}")
            print(f"   Bearish: {stats['bearish_count']:,}")
            print(f"   Date range: {stats['earliest_date']} to {stats['latest_date']}")

            if stats['labeled_breakouts'] >= 1000:
                print(f"SUCCESS: Sufficient training data available")
            elif stats['labeled_breakouts'] >= 100:
                print(f"WARNING: Limited training data - consider generating more")
            else:
                print(f"ERROR: Insufficient training data - need more breakouts")

            conn.close()

        except Exception as e:
            print(f"ERROR: Error checking breakout data: {e}")
            conn.close()

    def clean_and_validate_breakouts(self):
        """Clean and validate breakout data for ML training"""
        print(f"\nCLEANING BREAKOUT DATA")
        print("=" * 40)

        conn = self.get_db_connection()
        if not conn:
            return False

        cursor = conn.cursor()

        try:
            cleanup_queries = [
                "DELETE FROM breakouts WHERE entry_price IS NULL OR entry_price <= 0",
                "DELETE FROM breakouts WHERE atr_pct > 50 OR atr_pct < 0",
                "DELETE FROM breakouts WHERE volume_ratio > 100 OR volume_ratio < 0",
                "DELETE FROM breakouts WHERE rsi_value > 100 OR rsi_value < 0"
            ]

            total_deleted = 0
            for query in cleanup_queries:
                cursor.execute(query)
                deleted = cursor.rowcount
                total_deleted += deleted
                print(f"   Cleaned {deleted} invalid records")

            conn.commit()

            if total_deleted > 0:
                print(f"SUCCESS: Cleaned {total_deleted} invalid breakout records")
            else:
                print(f"SUCCESS: No invalid records found")

            conn.close()
            return True

        except Exception as e:
            print(f"ERROR: Error cleaning breakout data: {e}")
            conn.rollback()
            conn.close()
            return False

    def generate_ml_readiness_report(self):
        """Generate comprehensive ML readiness report"""
        print(f"\nML TRAINING READINESS REPORT")
        print("=" * 50)

        db_healthy = self.check_database_health()
        self.check_data_freshness()
        self.check_breakout_data_quality()

        print(f"\nOVERALL READINESS ASSESSMENT")
        print("=" * 40)

        if db_healthy:
            print(f"SUCCESS: READY FOR ML TRAINING")
            print(f"Next steps:")
            print(f"   1. Run: python ml_training/data_preparation/momentum_labeler.py")
            print(f"   2. Run: python ml_training/data_preparation/feature_builder.py")
            print(f"   3. Train models with enhanced dataset")
        else:
            print(f"ERROR: NOT READY FOR ML TRAINING")
            print(f"Required fixes:")
            print(f"   - Fix database connection and data issues")


def main():
    """Main function"""
    print("POSTGRESQL DATA CLEANER & VALIDATOR")
    print("=" * 50)

    cleaner = PostgreSQLDataCleaner()

    print("Cleaning invalid breakout records...")
    cleaner.clean_and_validate_breakouts()

    cleaner.generate_ml_readiness_report()


if __name__ == "__main__":
    main()