# check_database_schema.py
# Check what tables and columns exist in your database
import psycopg2
import os
from dotenv import load_dotenv

print("🔍 Checking your database schema...")

# Load environment variables
load_dotenv()

# Get configuration using your variable names
config = {
    'host': os.getenv('DB_HOST') or os.getenv('POSTGRES_HOST', 'localhost'),
    'port': os.getenv('DB_PORT') or os.getenv('POSTGRES_PORT', '5432'),
    'database': os.getenv('DB_NAME') or os.getenv('POSTGRES_DB', 'trading_db'),
    'user': os.getenv('DB_USER') or os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD') or os.getenv('POSTGRES_PASSWORD', '')
}

try:
    conn = psycopg2.connect(**config)
    cursor = conn.cursor()

    print(f"✅ Connected to database: {config['database']}")

    # Get all tables
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        ORDER BY table_name;
    """)

    tables = [row[0] for row in cursor.fetchall()]

    print(f"\n📋 Found {len(tables)} tables in your database:")
    for table in tables:
        print(f"  📊 {table}")

    # Check each table's record count and columns
    for table in tables:
        try:
            # Get record count
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]

            # Get column info
            cursor.execute(f"""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = '{table}' 
                ORDER BY ordinal_position;
            """)
            columns = cursor.fetchall()

            print(f"\n📊 Table: {table}")
            print(f"   Records: {count:,}")
            print(f"   Columns ({len(columns)}):")
            for col_name, col_type in columns:
                print(f"     - {col_name} ({col_type})")

        except Exception as e:
            print(f"   ❌ Error checking {table}: {e}")

    # Check for expected trading data tables
    expected_tables = [
        'stock_prices',
        'technical_indicators',
        'stock_fundamentals',
        'quarterly_fundamentals',
        'donchian_breakouts',
        'historical_breakouts'
    ]

    print(f"\n🎯 Expected tables status:")
    for expected_table in expected_tables:
        if expected_table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {expected_table}")
            count = cursor.fetchone()[0]
            print(f"  ✅ {expected_table}: {count:,} records")
        else:
            print(f"  ❌ {expected_table}: Missing")

    # Sample some data from stock_prices to understand structure
    print(f"\n📈 Sample from stock_prices table:")
    cursor.execute("SELECT * FROM stock_prices LIMIT 3")
    sample_data = cursor.fetchall()

    # Get column names
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'stock_prices' 
        ORDER BY ordinal_position;
    """)
    column_names = [row[0] for row in cursor.fetchall()]

    print(f"   Columns: {', '.join(column_names)}")
    for i, row in enumerate(sample_data, 1):
        print(f"   Row {i}: {row}")

    conn.close()

    print(f"\n🎯 SUMMARY:")
    print(f"✅ Your database has the core tables: stock_prices, technical_indicators")
    print(f"❌ Missing tables needed for full automation:")

    missing_tables = []
    for expected_table in expected_tables:
        if expected_table not in tables:
            missing_tables.append(expected_table)

    for missing_table in missing_tables:
        print(f"   - {missing_table}")

    if missing_tables:
        print(f"\n📝 Next steps:")
        print(f"1. We can create the missing tables")
        print(f"2. Or update scripts to work with your existing structure")
        print(f"3. Your core data (prices + indicators) is perfect!")

except Exception as e:
    print(f"❌ Error: {e}")