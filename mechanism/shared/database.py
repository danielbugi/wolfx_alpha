# automation/shared/database.py
"""
Database connection management with pooling, retry logic, and monitoring
Provides centralized database access for all trading system components
"""

import asyncio
import asyncpg
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.pool import ThreadedConnectionPool
import logging
import time
from typing import Optional, Dict, Any, List, Tuple, AsyncContextManager, ContextManager
from contextlib import contextmanager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import threading
import pandas as pd

from .config import config
from .utils import retry_on_failure, timing_decorator, performance_monitor

logger = logging.getLogger(__name__)


@dataclass
class DatabaseMetrics:
    """Database performance metrics"""
    total_connections: int = 0
    active_connections: int = 0
    query_count: int = 0
    avg_query_time: float = 0.0
    error_count: int = 0
    last_error: Optional[str] = None
    uptime_start: datetime = None

    def __post_init__(self):
        if self.uptime_start is None:
            self.uptime_start = datetime.now()

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.uptime_start).total_seconds()


class DatabaseManager:
    """
    Centralized database manager with connection pooling and monitoring
    Supports both synchronous and asynchronous operations
    """

    def __init__(self):
        self.sync_pool: Optional[ThreadedConnectionPool] = None
        self.async_pool: Optional[asyncpg.Pool] = None
        self.metrics = DatabaseMetrics()
        self._lock = threading.Lock()
        self._initialized = False

    def initialize_sync_pool(self) -> bool:
        """Initialize synchronous connection pool"""
        try:
            logger.info("🔌 Initializing synchronous database connection pool...")

            self.sync_pool = ThreadedConnectionPool(
                minconn=config.db_pool_min_size,
                maxconn=config.db_pool_max_size,
                **config.db_config
            )

            # Test connection
            with self.get_sync_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT version();")
                version = cursor.fetchone()[0]
                logger.info(f"✅ Sync pool initialized - PostgreSQL: {version}")

            self.metrics.total_connections = config.db_pool_max_size
            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize sync pool: {e}")
            self.metrics.error_count += 1
            self.metrics.last_error = str(e)
            return False

    async def initialize_async_pool(self) -> bool:
        """Initialize asynchronous connection pool"""
        try:
            logger.info("⚡ Initializing asynchronous database connection pool...")

            self.async_pool = await asyncpg.create_pool(
                **config.db_config,
                min_size=config.db_pool_min_size,
                max_size=config.db_pool_max_size,
                command_timeout=config.db_timeout
            )

            # Test connection
            async with self.get_async_connection() as conn:
                version = await conn.fetchval("SELECT version();")
                logger.info(f"✅ Async pool initialized - PostgreSQL: {version}")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to initialize async pool: {e}")
            self.metrics.error_count += 1
            self.metrics.last_error = str(e)
            return False

    @contextmanager
    def get_sync_connection(self) -> ContextManager[psycopg2.extensions.connection]:
        """Get synchronous database connection from pool"""
        conn = None
        start_time = time.time()

        try:
            if not self.sync_pool:
                raise RuntimeError("Sync pool not initialized")

            conn = self.sync_pool.getconn()
            with self._lock:
                self.metrics.active_connections += 1

            yield conn

        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database connection error: {e}")
            self.metrics.error_count += 1
            self.metrics.last_error = str(e)
            raise

        finally:
            if conn:
                try:
                    self.sync_pool.putconn(conn)
                except Exception as e:
                    logger.error(f"Error returning connection to pool: {e}")

                with self._lock:
                    self.metrics.active_connections -= 1
                    self.metrics.query_count += 1

                    query_time = time.time() - start_time
                    self.metrics.avg_query_time = (
                            (self.metrics.avg_query_time * (self.metrics.query_count - 1) + query_time)
                            / self.metrics.query_count
                    )

    @asynccontextmanager
    async def get_async_connection(self) -> AsyncContextManager[asyncpg.Connection]:
        """Get asynchronous database connection from pool"""
        start_time = time.time()

        try:
            if not self.async_pool:
                raise RuntimeError("Async pool not initialized")

            async with self.async_pool.acquire() as conn:
                with self._lock:
                    self.metrics.active_connections += 1

                yield conn

        except Exception as e:
            logger.error(f"Async database connection error: {e}")
            self.metrics.error_count += 1
            self.metrics.last_error = str(e)
            raise

        finally:
            with self._lock:
                self.metrics.active_connections -= 1
                self.metrics.query_count += 1

                query_time = time.time() - start_time
                self.metrics.avg_query_time = (
                        (self.metrics.avg_query_time * (self.metrics.query_count - 1) + query_time)
                        / self.metrics.query_count
                )

    @retry_on_failure(max_retries=3, delay=1.0)
    def execute_query(self, query: str, params: Optional[Tuple] = None) -> List[Tuple]:
        """Execute a query and return results"""
        with self.get_sync_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    @retry_on_failure(max_retries=3, delay=1.0)
    def execute_dict_query(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        """Execute a query and return results as dictionaries"""
        with self.get_sync_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    @retry_on_failure(max_retries=3, delay=1.0)
    def execute_insert(self, query: str, params: Optional[Tuple] = None) -> bool:
        """Execute an insert/update/delete query"""
        with self.get_sync_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return True

    @retry_on_failure(max_retries=3, delay=1.0)
    def bulk_insert(self, query: str, data: List[Tuple]) -> int:
        """Execute bulk insert with execute_values"""
        if not data:
            return 0

        with self.get_sync_connection() as conn:
            cursor = conn.cursor()
            execute_values(cursor, query, data)
            conn.commit()
            return len(data)

    @retry_on_failure(max_retries=3, delay=1.0)
    async def execute_async_query(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        """Execute async query and return results"""
        async with self.get_async_connection() as conn:
            rows = await conn.fetch(query, *(params or ()))
            return [dict(row) for row in rows]

    @retry_on_failure(max_retries=3, delay=1.0)
    async def execute_async_insert(self, query: str, params: Optional[Tuple] = None) -> bool:
        """Execute async insert/update/delete"""
        async with self.get_async_connection() as conn:
            await conn.execute(query, *(params or ()))
            return True

    @retry_on_failure(max_retries=3, delay=1.0)
    async def bulk_insert_async(self, query: str, data: List[Tuple]) -> int:
        """Execute async bulk insert"""
        if not data:
            return 0

        async with self.get_async_connection() as conn:
            await conn.executemany(query, data)
            return len(data)

    def test_connection(self) -> Dict[str, Any]:
        """Test database connection and return status"""
        try:
            start_time = time.time()

            with self.get_sync_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT version(), current_database(), current_user;")
                version, database, user = cursor.fetchone()

                # Get basic stats
                cursor.execute("""
                    SELECT 
                        (SELECT COUNT(*) FROM stock_prices) as stock_prices,
                        (SELECT COUNT(*) FROM technical_indicators) as tech_indicators,
                        (SELECT COUNT(*) FROM daily_fundamentals) as fundamentals,
                        (SELECT COUNT(*) FROM breakouts) as breakouts
                """)
                stats = cursor.fetchone()

            connection_time = time.time() - start_time

            return {
                'connected': True,
                'version': version,
                'database': database,
                'user': user,
                'connection_time_ms': round(connection_time * 1000, 2),
                'stats': {
                    'stock_prices': stats[0],
                    'technical_indicators': stats[1],
                    'daily_fundamentals': stats[2],
                    'breakouts': stats[3]
                }
            }

        except Exception as e:
            return {
                'connected': False,
                'error': str(e),
                'connection_time_ms': None,
                'stats': None
            }

    def get_health_status(self) -> Dict[str, Any]:
        """Get comprehensive database health status"""
        try:
            with self.get_sync_connection() as conn:
                cursor = conn.cursor()

                # Database size and activity
                cursor.execute("""
                    SELECT 
                        pg_size_pretty(pg_database_size(current_database())) as db_size,
                        (SELECT COUNT(*) FROM pg_stat_activity WHERE datname = current_database()) as active_sessions
                """)
                db_size, active_sessions = cursor.fetchone()

                # Table sizes
                cursor.execute("""
                    SELECT 
                        schemaname,
                        tablename,
                        pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
                        pg_total_relation_size(schemaname||'.'||tablename) as size_bytes
                    FROM pg_tables 
                    WHERE schemaname = 'public' 
                    ORDER BY size_bytes DESC 
                    LIMIT 10
                """)
                table_sizes = cursor.fetchall()

                # Recent activity
                cursor.execute("""
                    SELECT 
                        MAX(date) as latest_price_date,
                        COUNT(DISTINCT symbol) as unique_symbols
                    FROM stock_prices
                """)
                latest_date, symbol_count = cursor.fetchone()

            return {
                'status': 'healthy',
                'database_size': db_size,
                'active_sessions': active_sessions,
                'latest_data_date': latest_date,
                'unique_symbols': symbol_count,
                'table_sizes': [
                    {'table': f"{row[0]}.{row[1]}", 'size': row[2], 'size_bytes': row[3]}
                    for row in table_sizes
                ],
                'metrics': {
                    'total_queries': self.metrics.query_count,
                    'avg_query_time_ms': round(self.metrics.avg_query_time * 1000, 2),
                    'error_count': self.metrics.error_count,
                    'uptime_seconds': round(self.metrics.uptime_seconds)
                }
            }

        except Exception as e:
            return {
                'status': 'error',
                'error': str(e),
                'metrics': {
                    'error_count': self.metrics.error_count,
                    'last_error': self.metrics.last_error
                }
            }

    def close_pools(self):
        """Close all connection pools"""
        try:
            if self.sync_pool:
                self.sync_pool.closeall()
                logger.info("✅ Synchronous pool closed")

        except Exception as e:
            logger.error(f"Error closing sync pool: {e}")

        # Note: Async pool closing should be done with await

    async def close_async_pool(self):
        """Close async connection pool"""
        try:
            if self.async_pool:
                await self.async_pool.close()
                logger.info("✅ Asynchronous pool closed")

        except Exception as e:
            logger.error(f"Error closing async pool: {e}")

    def insert_weekly_indicators(self, data: Dict[str, Any]) -> bool:
        """Insert weekly technical indicators - matches your existing insert patterns"""
        import logging
        logger = logging.getLogger(__name__)

        try:
            # DEBUG: Log what we're trying to insert
            logger.info(f"DEBUG INSERT: Data keys: {list(data.keys())}")
            logger.info(f"DEBUG INSERT: Symbol: {data.get('symbol')}")
            logger.info(f"DEBUG INSERT: Date: {data.get('week_ending_date')}")
            logger.info(f"DEBUG INSERT: RSI value: {data.get('rsi_14w')} (type: {type(data.get('rsi_14w'))})")
            logger.info(f"DEBUG INSERT: All data: {data}")

            query = """
                INSERT INTO weekly_technical_indicators 
                (symbol, week_ending_date, donchian_high_20w, donchian_low_20w, donchian_mid_20w,
                 weekly_open, weekly_high, weekly_low, weekly_close, weekly_volume,
                 sma_10w, sma_20w, rsi_14w, volume_ratio_weekly, price_position_weekly)
                VALUES (%(symbol)s, %(week_ending_date)s, %(donchian_high_20w)s, %(donchian_low_20w)s,
                        %(donchian_mid_20w)s, %(weekly_open)s, %(weekly_high)s, %(weekly_low)s,
                        %(weekly_close)s, %(weekly_volume)s, %(sma_10w)s, %(sma_20w)s,
                        %(rsi_14w)s, %(volume_ratio_weekly)s, %(price_position_weekly)s)
                ON CONFLICT (symbol, week_ending_date) DO UPDATE SET
                    donchian_high_20w = EXCLUDED.donchian_high_20w,
                    donchian_low_20w = EXCLUDED.donchian_low_20w,
                    donchian_mid_20w = EXCLUDED.donchian_mid_20w,
                    rsi_14w = EXCLUDED.rsi_14w,
                    volume_ratio_weekly = EXCLUDED.volume_ratio_weekly,
                    price_position_weekly = EXCLUDED.price_position_weekly,
                    updated_at = NOW()
            """

            # Use your existing connection pattern
            with self.get_sync_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, data)
                conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to insert weekly indicators: {e}")
            return False

    def get_daily_data_for_weekly_calc(self, symbol: str, weeks: int = 26) -> pd.DataFrame:
        """Get daily data for weekly calculations - matches your existing query patterns"""
        query = """
               SELECT sp.date, sp.open, sp.high, sp.low, sp.close, sp.volume,
                      ti.donchian_high_20, ti.donchian_low_20, ti.rsi_14
               FROM stock_prices sp
               LEFT JOIN technical_indicators ti ON sp.symbol = ti.symbol AND sp.date = ti.date
               WHERE sp.symbol = %s AND sp.date >= %s
               ORDER BY sp.date DESC
           """

        start_date = datetime.now().date() - timedelta(weeks=weeks)
        return pd.read_sql(query, self.get_connection(), params=[symbol, start_date])

    def insert_monthly_indicators(self, data: Dict[str, Any]) -> bool:
        """Insert monthly technical indicators"""
        try:
            query = """
                INSERT INTO monthly_technical_indicators 
                (symbol, month_ending_date, donchian_high_12m, donchian_low_12m, donchian_mid_12m,
                 monthly_open, monthly_high, monthly_low, monthly_close, monthly_volume,
                 trend_direction, trend_strength_6m)
                VALUES (%(symbol)s, %(month_ending_date)s, %(donchian_high_12m)s, %(donchian_low_12m)s,
                        %(donchian_mid_12m)s, %(monthly_open)s, %(monthly_high)s, %(monthly_low)s,
                        %(monthly_close)s, %(monthly_volume)s, %(trend_direction)s, %(trend_strength_6m)s)
                ON CONFLICT (symbol, month_ending_date) DO UPDATE SET
                    donchian_high_12m = EXCLUDED.donchian_high_12m,
                    trend_direction = EXCLUDED.trend_direction,
                    updated_at = NOW()
            """

            with self.get_sync_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, data)
                conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to insert monthly indicators: {e}")
            return False


# Utility functions for common database operations
class DatabaseUtils:
    """Utility functions for common database operations"""

    @staticmethod
    def get_symbols_with_data(db: DatabaseManager, limit: Optional[int] = None) -> List[str]:
        """Get symbols that have recent data"""
        query = """
            SELECT DISTINCT symbol 
            FROM stock_prices 
            WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            ORDER BY symbol
        """

        if limit:
            query += f" LIMIT {limit}"

        results = db.execute_query(query)
        return [row[0] for row in results]

    @staticmethod
    def get_last_update_date(db: DatabaseManager, symbol: str) -> Optional[str]:
        """Get the last update date for a symbol"""
        query = "SELECT MAX(date) FROM stock_prices WHERE symbol = %s"
        results = db.execute_query(query, (symbol,))

        if results and results[0][0]:
            return results[0][0].isoformat()
        return None

    @staticmethod
    def get_symbols_needing_update(db: DatabaseManager, days_threshold: int = 1) -> List[Dict[str, Any]]:
        """Get symbols that need updates based on last update date"""
        query = """
            SELECT 
                symbol,
                MAX(date) as last_date,
                CURRENT_DATE - MAX(date) as days_behind
            FROM stock_prices
            GROUP BY symbol
            HAVING CURRENT_DATE - MAX(date) >= %s
            ORDER BY days_behind DESC
        """

        return db.execute_dict_query(query, (days_threshold,))

    @staticmethod
    def get_data_quality_report(db: DatabaseManager) -> Dict[str, Any]:
        """Generate data quality report"""
        queries = {
            'symbol_coverage': """
                SELECT 
                    COUNT(DISTINCT p.symbol) as symbols_with_prices,
                    COUNT(DISTINCT t.symbol) as symbols_with_tech,
                    COUNT(DISTINCT f.symbol) as symbols_with_fundamentals
                FROM stock_prices p
                FULL OUTER JOIN technical_indicators t ON p.symbol = t.symbol
                FULL OUTER JOIN daily_fundamentals f ON p.symbol = f.symbol
            """,
            'date_coverage': """
                SELECT 
                    MIN(date) as earliest_date,
                    MAX(date) as latest_date,
                    COUNT(DISTINCT date) as unique_dates
                FROM stock_prices
            """,
            'data_completeness': """
                SELECT 
                    symbol,
                    COUNT(*) as total_records,
                    COUNT(volume) as records_with_volume,
                    ROUND(COUNT(volume)::numeric / COUNT(*) * 100, 2) as volume_completeness_pct
                FROM stock_prices
                GROUP BY symbol
                HAVING COUNT(*) > 100
                ORDER BY volume_completeness_pct ASC
                LIMIT 10
            """
        }

        report = {}
        for key, query in queries.items():
            try:
                results = db.execute_dict_query(query)
                report[key] = results
            except Exception as e:
                report[key] = {'error': str(e)}

        return report


# Global database manager instance
db = DatabaseManager()

# Auto-initialize sync pool on import
if not db._initialized:
    try:
        success = db.initialize_sync_pool()
        if success:
            db._initialized = True
            logger.info("✅ Database manager initialized with sync pool")
        else:
            logger.warning("⚠️ Database manager initialized but sync pool failed")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database manager: {e}")

# Database utilities instance
db_utils = DatabaseUtils()

if __name__ == "__main__":
    print("🗄️ Database Manager Test")
    print("=" * 50)

    # Test connection
    status = db.test_connection()
    if status['connected']:
        print("✅ Database connection: SUCCESS")
        print(f"   Database: {status['database']}")
        print(f"   User: {status['user']}")
        print(f"   Connection time: {status['connection_time_ms']}ms")
        print(f"   Stock prices: {status['stats']['stock_prices']:,}")
        print(f"   Technical indicators: {status['stats']['technical_indicators']:,}")
        print(f"   Fundamentals: {status['stats']['daily_fundamentals']:,}")
        print(f"   Breakouts: {status['stats']['breakouts']:,}")
    else:
        print("❌ Database connection: FAILED")
        print(f"   Error: {status['error']}")

    # Test health status
    health = db.get_health_status()
    if health['status'] == 'healthy':
        print(f"\n💚 Database health: HEALTHY")
        print(f"   Size: {health['database_size']}")
        print(f"   Active sessions: {health['active_sessions']}")
        print(f"   Latest data: {health['latest_data_date']}")
        print(f"   Unique symbols: {health['unique_symbols']}")
        print(f"   Total queries: {health['metrics']['total_queries']}")
        print(f"   Avg query time: {health['metrics']['avg_query_time_ms']}ms")
    else:
        print(f"❌ Database health: ERROR")
        print(f"   Error: {health.get('error')}")


# Add these methods to your existing database.py file
# Location: mechanism/shared/database.py

