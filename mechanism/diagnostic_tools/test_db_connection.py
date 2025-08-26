# test_db_connection.py
# Quick database connection tester with robust .env detection
import psycopg2
import os
from dotenv import load_dotenv

print("🔍 Testing database connection...")

# Try multiple locations for .env file
possible_env_paths = [
    '.env',  # Current directory (root when running from root)
    '../.env',  # Parent directory (if running from automation/)
    os.path.join(os.path.dirname(__file__), '../..', '.env'),  # Relative to script location
]

env_found = False
for env_path in possible_env_paths:
    abs_path = os.path.abspath(env_path)
    print(f"📁 Checking for .env at: {abs_path}")

    if os.path.exists(abs_path):
        print(f"✅ Found .env file!")
        load_dotenv(abs_path)
        env_found = True

        # Show what's in the file (without exposing password)
        try:
            with open(abs_path, 'r') as f:
                lines = f.readlines()
                print(f"📄 .env file contents ({len(lines)} lines):")
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if 'PASSWORD' in line:
                            key, value = line.split('=', 1)
                            print(f"   {key}={'*' * len(value) if value else '(EMPTY!)'}")
                        else:
                            print(f"   {line}")
        except Exception as e:
            print(f"⚠️ Could not read .env file: {e}")

        break
    else:
        print(f"❌ Not found")

if not env_found:
    print("\n🚨 NO .env FILE FOUND!")
    print("📝 Please create a .env file in your project root with:")
    print("""
DB_HOST=localhost
DB_PORT=5432
DB_NAME=trading_db
DB_USER=postgres
DB_PASSWORD=your_actual_password
""")
    exit(1)

# Get configuration
config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'trading_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', '')
}

print(f"\n📋 Configuration loaded:")
print(f"  Host: {config['host']}")
print(f"  Port: {config['port']}")
print(f"  Database: {config['database']}")
print(f"  User: {config['user']}")
print(f"  Password: {'*' * len(config['password']) if config['password'] else '(EMPTY!)'}")

if not config['password']:
    print("\n🚨 PASSWORD IS STILL EMPTY!")
    print("💡 Your .env file exists but DB_PASSWORD is not set or is empty")
    print("📝 Edit your .env file and add:")
    print("   DB_PASSWORD=your_actual_password")
    exit(1)

# Test connection
try:
    print("\n🔌 Attempting connection...")
    conn = psycopg2.connect(**config)
    cursor = conn.cursor()

    # Test query
    cursor.execute("SELECT version();")
    version = cursor.fetchone()[0]
    print(f"✅ Connection successful!")
    print(f"📊 PostgreSQL version: {version}")

    # Test our trading database
    cursor.execute("SELECT COUNT(*) FROM stock_prices;")
    count = cursor.fetchone()[0]
    print(f"💾 Stock prices in database: {count:,}")

    conn.close()
    print("\n🎉 Database is working perfectly!")
    print("✅ You can now run: python mechanism/master_automation_runner.py health")

except psycopg2.OperationalError as e:
    print(f"❌ Connection failed: {e}")

    if "no password supplied" in str(e):
        print("\n💡 FIX: Add your PostgreSQL password to .env file")

    elif "password authentication failed" in str(e):
        print("\n💡 FIX: Wrong password. Update .env file with correct password")

    elif "database" in str(e) and "does not exist" in str(e):
        print("\n💡 FIX: Wrong database name. Check DB_NAME in .env file")

    elif "could not connect" in str(e):
        print("\n💡 FIX: PostgreSQL server not running or wrong host/port")

except Exception as e:
    print(f"❌ Unexpected error: {e}")