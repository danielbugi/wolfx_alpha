# File: backend/debug_database.py
"""
Debug script to identify database connection and query issues
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()


def test_database_connection():
    """Test basic database connection"""
    print("🔍 Testing Database Connection...")
    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'trading_production'),
            user=os.getenv('DB_USER', 'trading_user'),
            password=os.getenv('DB_PASSWORD')
        )
        print("✅ Database connection successful!")
        return conn
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return None


def check_table_structure(conn):
    """Check if required tables exist and their structure"""
    print("\n🔍 Checking Table Structure...")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Check if tables exist
    tables_to_check = ['stock_prices', 'technical_indicators', 'daily_fundamentals']

    for table in tables_to_check:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"✅ Table '{table}': {count:,} records")

            # Show recent data
            cursor.execute(f"SELECT MAX(date) as latest_date FROM {table}")
            latest = cursor.fetchone()
            print(f"   📅 Latest date: {latest['latest_date']}")

        except Exception as e:
            print(f"❌ Table '{table}' error: {e}")

    cursor.close()


def check_data_availability(conn):
    """Check if we have recent data for calculations"""
    print("\n🔍 Checking Data Availability...")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Check latest stock prices with volume
        cursor.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN volume IS NOT NULL AND volume > 0 THEN 1 END) as with_volume,
                MAX(date) as latest_date,
                MIN(date) as earliest_date
            FROM stock_prices
        """)
        result = cursor.fetchone()
        print(f"📊 Stock Prices:")
        print(f"   Total records: {result['total_records']:,}")
        print(f"   With volume: {result['with_volume']:,}")
        print(f"   Date range: {result['earliest_date']} to {result['latest_date']}")

        # Check technical indicators
        cursor.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN volume_ratio IS NOT NULL THEN 1 END) as with_volume_ratio,
                MAX(date) as latest_date
            FROM technical_indicators
        """)
        result = cursor.fetchone()
        print(f"📊 Technical Indicators:")
        print(f"   Total records: {result['total_records']:,}")
        print(f"   With volume_ratio: {result['with_volume_ratio']:,}")
        print(f"   Latest date: {result['latest_date']}")

        # Check daily fundamentals
        cursor.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN sector IS NOT NULL THEN 1 END) as with_sector,
                MAX(date) as latest_date
            FROM daily_fundamentals
        """)
        result = cursor.fetchone()
        print(f"📊 Daily Fundamentals:")
        print(f"   Total records: {result['total_records']:,}")
        print(f"   With sector: {result['with_sector']:,}")
        print(f"   Latest date: {result['latest_date']}")

    except Exception as e:
        print(f"❌ Data availability check failed: {e}")

    cursor.close()


def test_specific_queries(conn):
    """Test the specific queries used in our endpoints"""
    print("\n🔍 Testing Specific Queries...")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Test top gainers query (simplified)
    print("\n📈 Testing Top Gainers Query...")
    try:
        cursor.execute("""
            WITH latest_prices AS (
                SELECT DISTINCT ON (symbol) 
                    symbol, date, close, volume
                FROM stock_prices 
                WHERE date >= CURRENT_DATE - INTERVAL '5 days'
                ORDER BY symbol, date DESC
                LIMIT 10
            )
            SELECT symbol, close, volume, date
            FROM latest_prices
            ORDER BY close DESC
        """)

        results = cursor.fetchall()
        print(f"✅ Query executed successfully, got {len(results)} results")

        if results:
            print("📋 Sample results:")
            for i, row in enumerate(results[:3]):
                print(f"   {i + 1}. {row['symbol']}: ${row['close']} on {row['date']}")

    except Exception as e:
        print(f"❌ Top gainers query failed: {e}")
        import traceback
        traceback.print_exc()

    # Test price change calculation
    print("\n📊 Testing Price Change Calculation...")
    try:
        cursor.execute("""
            SELECT 
                symbol,
                date,
                close,
                LAG(close) OVER (PARTITION BY symbol ORDER BY date) as prev_close
            FROM stock_prices 
            WHERE symbol = 'AAPL'
            ORDER BY date DESC
            LIMIT 5
        """)

        results = cursor.fetchall()
        print(f"✅ Price change query executed successfully")

        if results:
            print("📋 AAPL price history:")
            for row in results:
                if row['prev_close']:
                    change_pct = ((row['close'] - row['prev_close']) / row['prev_close'] * 100)
                    print(f"   {row['date']}: ${row['close']} (change: {change_pct:.2f}%)")
                else:
                    print(f"   {row['date']}: ${row['close']} (no prev data)")

    except Exception as e:
        print(f"❌ Price change query failed: {e}")
        import traceback
        traceback.print_exc()

    cursor.close()


def main():
    """Main debug function"""
    print("🚀 Database Debug Script")
    print("=" * 50)

    # Test connection
    conn = test_database_connection()
    if not conn:
        return

    try:
        # Run all checks
        check_table_structure(conn)
        check_data_availability(conn)
        test_specific_queries(conn)

    finally:
        conn.close()
        print("\n✅ Debug complete!")


if __name__ == "__main__":
    main()