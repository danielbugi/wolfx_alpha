# migrate_to_postgresql.py
# Script to migrate data from SQLite to PostgreSQL

import sqlite3
import asyncpg
import pandas as pd
import asyncio
from datetime import datetime
import logging
from typing import Dict, List, Tuple
import sys
import os

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class SQLiteToPostgreSQLMigrator:
    """
    Migrate trading data from SQLite to PostgreSQL
    """

    def __init__(self, sqlite_path: str, postgres_config: Dict[str, str]):
        self.sqlite_path = sqlite_path
        self.postgres_config = postgres_config
        self.pg_pool = None

    async def initialize_connections(self):
        """Initialize database connections"""
        try:
            # Test SQLite connection
            if not os.path.exists(self.sqlite_path):
                raise FileNotFoundError(f"SQLite database not found: {self.sqlite_path}")

            # Test PostgreSQL connection
            self.pg_pool = await asyncpg.create_pool(**self.postgres_config)

            logger.info("✅ Database connections established")

        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            raise

    async def analyze_sqlite_data(self) -> Dict[str, int]:
        """Analyze SQLite data to understand what we're migrating"""
        logger.info("🔍 Analyzing SQLite database...")

        sqlite_conn = sqlite3.connect(self.sqlite_path)

        analysis = {}

        # Get table info
        tables_query = "SELECT name FROM sqlite_master WHERE type='table'"
        tables = pd.read_sql(tables_query, sqlite_conn)['name'].tolist()

        for table in tables:
            try:
                count_query = f"SELECT COUNT(*) as count FROM {table}"
                count = pd.read_sql(count_query, sqlite_conn)['count'].iloc[0]
                analysis[table] = count
                logger.info(f"  📊 {table}: {count:,} records")

            except Exception as e:
                logger.warning(f"  ⚠️  Could not analyze table {table}: {e}")
                analysis[table] = 0

        sqlite_conn.close()

        total_records = sum(analysis.values())
        logger.info(f"📈 Total records to migrate: {total_records:,}")

        return analysis

    async def migrate_table(self, table_name: str, batch_size: int = 1000) -> bool:
        """Migrate a single table from SQLite to PostgreSQL"""
        logger.info(f"🚀 Starting migration of table: {table_name}")

        try:
            # Read data from SQLite
            sqlite_conn = sqlite3.connect(self.sqlite_path)

            # Get total records for progress tracking
            total_records = pd.read_sql(f"SELECT COUNT(*) as count FROM {table_name}", sqlite_conn)['count'].iloc[0]

            if total_records == 0:
                logger.info(f"⏭️  {table_name}: No data to migrate")
                sqlite_conn.close()
                return True

            logger.info(f"📊 {table_name}: Migrating {total_records:,} records...")

            # Read data in batches
            offset = 0
            migrated = 0

            async with self.pg_pool.acquire() as pg_conn:
                while offset < total_records:
                    # Read batch from SQLite
                    query = f"SELECT * FROM {table_name} LIMIT {batch_size} OFFSET {offset}"
                    batch_df = pd.read_sql(query, sqlite_conn)

                    if batch_df.empty:
                        break

                    # Transform data for PostgreSQL
                    batch_df = self.transform_data_for_postgres(batch_df, table_name)

                    # Insert batch into PostgreSQL
                    success = await self.insert_batch_to_postgres(pg_conn, table_name, batch_df)

                    if success:
                        migrated += len(batch_df)
                        progress = (migrated / total_records) * 100
                        logger.info(f"  📈 {table_name}: {migrated:,}/{total_records:,} ({progress:.1f}%)")
                    else:
                        logger.error(f"❌ Failed to insert batch for {table_name}")
                        sqlite_conn.close()
                        return False

                    offset += batch_size

            sqlite_conn.close()
            logger.info(f"✅ {table_name}: Migration completed - {migrated:,} records")
            return True

        except Exception as e:
            logger.error(f"❌ Migration failed for {table_name}: {e}")
            return False

    def transform_data_for_postgres(self, df: pd.DataFrame, table_name: str) -> pd.DataFrame:
        """Transform SQLite data to be compatible with PostgreSQL"""

        # Handle NaN values
        df = df.where(pd.notnull(df), None)

        # Table-specific transformations
        if table_name == 'stock_prices':
            # Ensure decimal precision
            for col in ['open', 'high', 'low', 'close', 'adj_close']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # Ensure volume is integer
            if 'volume' in df.columns:
                df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype('Int64')

        elif table_name == 'technical_indicators':
            # Handle technical indicator precision
            decimal_cols = ['sma_10', 'sma_20', 'sma_50', 'macd', 'macd_signal', 'macd_histogram',
                            'bollinger_upper', 'bollinger_lower', 'donchian_high_20', 'donchian_low_20',
                            'donchian_mid_20', 'atr_14']

            for col in decimal_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # Handle percentage columns
            percentage_cols = ['rsi_14', 'price_position', 'channel_width_pct']
            for col in percentage_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

        elif table_name == 'daily_fundamentals':
            # Handle fundamental data
            if 'market_cap' in df.columns:
                df['market_cap'] = pd.to_numeric(df['market_cap'], errors='coerce')

            # Handle scores
            score_cols = ['growth_score', 'profitability_score', 'financial_health_score',
                          'valuation_score', 'overall_quality_score']
            for col in score_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

        elif table_name == 'breakouts':
            # Handle breakout data
            if 'success' in df.columns:
                df['success'] = df['success'].astype('bool')

        # Convert date columns
        date_columns = ['date', 'quarter', 'created_at', 'updated_at']
        for col in date_columns:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')

        return df

    async def insert_batch_to_postgres(self, conn, table_name: str, df: pd.DataFrame) -> bool:
        """Insert a batch of data into PostgreSQL"""
        try:
            # Convert DataFrame to list of tuples
            data = [tuple(row) for row in df.values]
            columns = list(df.columns)

            # Create INSERT query with conflict handling
            placeholders = ', '.join(['$' + str(i) for i in range(1, len(columns) + 1)])

            # Handle different conflict resolution strategies by table
            if table_name in ['stock_prices', 'technical_indicators', 'daily_fundamentals']:
                # Use ON CONFLICT DO UPDATE for data tables
                conflict_cols = self.get_conflict_columns(table_name)
                update_cols = [col for col in columns if col not in conflict_cols and col not in ['id', 'created_at']]

                if update_cols:
                    update_clause = ', '.join([f"{col} = EXCLUDED.{col}" for col in update_cols])
                    query = f"""
                        INSERT INTO {table_name} ({', '.join(columns)})
                        VALUES ({placeholders})
                        ON CONFLICT ({', '.join(conflict_cols)}) 
                        DO UPDATE SET {update_clause}, updated_at = NOW()
                    """
                else:
                    query = f"""
                        INSERT INTO {table_name} ({', '.join(columns)})
                        VALUES ({placeholders})
                        ON CONFLICT ({', '.join(conflict_cols)}) DO NOTHING
                    """
            else:
                # Simple insert for other tables
                query = f"""
                    INSERT INTO {table_name} ({', '.join(columns)})
                    VALUES ({placeholders})
                    ON CONFLICT DO NOTHING
                """

            # Execute batch insert
            await conn.executemany(query, data)
            return True

        except Exception as e:
            logger.error(f"❌ Batch insert failed for {table_name}: {e}")
            logger.error(f"Sample data: {df.head(2).to_dict()}")
            return False

    def get_conflict_columns(self, table_name: str) -> List[str]:
        """Get the columns that define uniqueness for conflict resolution"""
        conflict_mapping = {
            'stock_prices': ['symbol', 'date'],
            'technical_indicators': ['symbol', 'date'],
            'daily_fundamentals': ['symbol', 'date'],
            'quarterly_fundamentals': ['symbol', 'quarter'],
            'breakouts': ['symbol', 'date', 'breakout_type'],
            'companies': ['symbol']
        }
        return conflict_mapping.get(table_name, ['id'])

    async def verify_migration(self) -> Dict[str, Dict[str, int]]:
        """Verify that migration was successful by comparing record counts"""
        logger.info("🔍 Verifying migration...")

        # Get SQLite counts
        sqlite_conn = sqlite3.connect(self.sqlite_path)
        sqlite_tables = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", sqlite_conn)['name'].tolist()

        sqlite_counts = {}
        for table in sqlite_tables:
            try:
                count = pd.read_sql(f"SELECT COUNT(*) as count FROM {table}", sqlite_conn)['count'].iloc[0]
                sqlite_counts[table] = count
            except:
                sqlite_counts[table] = 0

        sqlite_conn.close()

        # Get PostgreSQL counts
        async with self.pg_pool.acquire() as conn:
            pg_counts = {}
            for table in sqlite_tables:
                try:
                    count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                    pg_counts[table] = count
                except:
                    pg_counts[table] = 0

        # Compare and report
        verification = {}
        total_sqlite = 0
        total_postgres = 0

        logger.info("📊 Migration Verification Results:")
        logger.info("-" * 60)

        for table in sqlite_tables:
            sqlite_count = sqlite_counts.get(table, 0)
            postgres_count = pg_counts.get(table, 0)

            total_sqlite += sqlite_count
            total_postgres += postgres_count

            status = "✅" if sqlite_count == postgres_count else "⚠️"
            logger.info(f"{status} {table:<25}: SQLite: {sqlite_count:>6,} | PostgreSQL: {postgres_count:>6,}")

            verification[table] = {
                'sqlite_count': sqlite_count,
                'postgres_count': postgres_count,
                'match': sqlite_count == postgres_count
            }

        logger.info("-" * 60)
        logger.info(f"📈 TOTAL: SQLite: {total_sqlite:,} | PostgreSQL: {total_postgres:,}")

        if total_sqlite == total_postgres:
            logger.info("🎉 Migration verification PASSED! All data migrated successfully.")
        else:
            logger.warning("⚠️  Migration verification found discrepancies. Please review.")

        return verification

    async def run_full_migration(self) -> bool:
        """Run the complete migration process"""
        logger.info("🚀 Starting SQLite to PostgreSQL migration...")

        try:
            # Initialize connections
            await self.initialize_connections()

            # Analyze source data
            analysis = await self.analyze_sqlite_data()

            if sum(analysis.values()) == 0:
                logger.warning("⚠️  No data found in SQLite database")
                return False

            # Define migration order (handle dependencies)
            migration_order = [
                'companies',
                'stock_prices',
                'technical_indicators',
                'daily_fundamentals',
                'quarterly_fundamentals',
                'breakouts',
                'ml_models',
                'data_updates'
            ]

            # Migrate each table
            failed_tables = []

            for table in migration_order:
                if table in analysis and analysis[table] > 0:
                    success = await self.migrate_table(table)
                    if not success:
                        failed_tables.append(table)
                else:
                    logger.info(f"⏭️  Skipping {table}: No data or table doesn't exist")

            # Verify migration
            verification = await self.verify_migration()

            # Summary
            if failed_tables:
                logger.error(f"❌ Migration completed with errors. Failed tables: {failed_tables}")
                return False
            else:
                logger.info("✅ Migration completed successfully!")
                return True

        except Exception as e:
            logger.error(f"❌ Migration failed: {e}")
            return False

        finally:
            if self.pg_pool:
                await self.pg_pool.close()


# =============================================================================
# MIGRATION RUNNER
# =============================================================================

async def main():
    """Main migration function"""

    print("🐘 SQLite to PostgreSQL Migration Tool")
    print("=" * 50)

    # Configuration
    sqlite_path = input("Enter SQLite database path (default: mechanism/data/trading_data.db): ").strip()
    if not sqlite_path:
        sqlite_path = "mechanism/data/trading_data.db"

    postgres_config = {
        'host': input("PostgreSQL host (default: localhost): ").strip() or 'localhost',
        'port': int(input("PostgreSQL port (default: 5432): ").strip() or '5432'),
        'database': input("Database name (default: trading_production): ").strip() or 'trading_production',
        'user': input("Username (default: trading_user): ").strip() or 'trading_user',
        'password': input("Password: ").strip()
    }

    if not postgres_config['password']:
        print("❌ Password is required")
        return

    # Confirm migration
    print(f"\n📋 Migration Plan:")
    print(f"  From: {sqlite_path}")
    print(
        f"  To:   postgresql://{postgres_config['user']}@{postgres_config['host']}:{postgres_config['port']}/{postgres_config['database']}")

    confirm = input("\nProceed with migration? (yes/no): ").strip().lower()
    if confirm not in ['yes', 'y']:
        print("Migration cancelled")
        return

    # Run migration
    migrator = SQLiteToPostgreSQLMigrator(sqlite_path, postgres_config)
    success = await migrator.run_full_migration()

    if success:
        print("\n🎉 Migration completed successfully!")
        print("Next steps:")
        print("1. Test PostgreSQL connection")
        print("2. Update your Python code to use PostgreSQL")
        print("3. Update environment variables")
    else:
        print("\n❌ Migration failed. Check migration.log for details.")


# =============================================================================
# TESTING FUNCTIONS
# =============================================================================

def test_postgresql_connection():
    """Test PostgreSQL connection"""
    import asyncio
    import asyncpg

    async def test_conn():
        try:
            # Test connection parameters
            postgres_config = {
                'host': 'localhost',
                'port': 5432,
                'database': 'trading_production',
                'user': 'trading_user',
                'password': input("Enter password for testing: ")
            }

            conn = await asyncpg.connect(**postgres_config)

            # Test basic query
            result = await conn.fetchval("SELECT COUNT(*) FROM stock_prices")
            print(f"✅ PostgreSQL connection successful!")
            print(f"📊 Stock prices table has {result:,} records")

            await conn.close()

        except Exception as e:
            print(f"❌ PostgreSQL connection failed: {e}")

    asyncio.run(test_conn())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        test_postgresql_connection()
    else:
        asyncio.run(main())