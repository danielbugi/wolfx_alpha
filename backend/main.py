# File: backend/main.py
from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extensions import connection as _PGConnection
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional
import json
from datetime import datetime, timedelta
import logging
from utils import find_latest_ml_enhanced_file, load_ml_enhanced_data, load_ml_enhanced_data_cached
from contextlib import asynccontextmanager
from services.performance_service import tracker as performance_tracker

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables for caching
cache = {}
cache_timestamps = {}
CACHE_DURATION = 900  # 15 minutes
# Dashboard routes below run as plain `def` handlers (see the
# async-def-blocking-the-event-loop fix in this file's history), which
# Starlette executes in its worker threadpool -- meaning concurrent requests
# can now genuinely touch this dict from different OS threads at once.
_cache_lock = threading.Lock()


# ============================================================================
# DATABASE CONNECTION POOLING
# ============================================================================
# Added 2026-09-19 after /api/performance/ showed every request paying a bare
# psycopg2.connect() (~40-50ms just to open a socket + auth) and slower
# endpoints (system-health, ml-stats) opening 5-9+ of these sequentially —
# see SYSTEM_HEALTH_REPORT.md and CLAUDE.md §3.3/§9 for the measurements
# that motivated this. mechanism/shared/database.py already had a real
# ThreadedConnectionPool; the backend never reused it, so this adds one
# directly here instead of restructuring the mechanism/ import path.
#
# _PooledConnection makes this a drop-in change: every router/service in
# this codebase already does `conn.close()` in a `finally` block expecting a
# real close. Overriding close() to return the connection to the pool means
# none of those call sites need to change.
db_pool: Optional[ThreadedConnectionPool] = None


class _PooledConnection(_PGConnection):
    def close(self):
        # Reentrancy guard -- REQUIRED, not defensive fluff. psycopg2's
        # ThreadedConnectionPool._putconn() (see pool.py) itself calls
        # conn.close() to discard a connection instead of pooling it,
        # whenever more than `minconn` idle connections are already parked,
        # or the connection's transaction status came back UNKNOWN. Without
        # this guard, that inner conn.close() re-enters this same override,
        # which calls db_pool.putconn(self) a second time on the same
        # thread -- but ThreadedConnectionPool.putconn() serializes on a
        # plain (non-reentrant) threading.Lock, so the second acquire blocks
        # forever behind the first call's still-held lock. That's not just
        # this one connection stuck: every other getconn()/putconn() in the
        # process needs the same lock, so one such return permanently wedges
        # the entire pool -- every future request hangs, including totally
        # unrelated ones. This was latent as long as the app rarely had more
        # than `minconn` (2) connections checked out at once; it reproduces
        # reliably once real concurrency is introduced (e.g. the
        # ThreadPoolExecutor-based parallel fetches added for main-page-data/
        # system-health/ml-stats), which is exactly why it surfaced during
        # that fix's own testing rather than before.
        if getattr(self, '_pool_is_closing_me', False):
            super().close()
            return
        if db_pool is not None and not self.closed:
            try:
                self.rollback()  # never hand back a connection mid-transaction
            except Exception:
                pass
            self._pool_is_closing_me = True
            try:
                db_pool.putconn(self)
                return
            except Exception:
                pass  # pool rejected it (e.g. shutting down) — fall through to a real close
            finally:
                self._pool_is_closing_me = False
        if not self.closed:
            super().close()


def _init_db_pool():
    global db_pool
    try:
        # Default bumped from 2 -> 8: several endpoints now fetch their
        # sub-components concurrently (main-page-data/system-health/ml-stats
        # each check out 4-5 connections at once, see this file's and
        # services/*.py's ThreadPoolExecutor usage). Below minconn idle
        # connections are kept warm and reused; above it, psycopg2's pool
        # closes and later reopens a real TCP+auth connection on every
        # request -- harmless now that the close()/putconn() reentrancy
        # deadlock above is fixed, but still needless churn under normal
        # concurrency.
        db_pool = ThreadedConnectionPool(
            int(os.getenv('DB_POOL_MIN', 8)),
            int(os.getenv('DB_POOL_MAX', 30)),
            connection_factory=_PooledConnection,
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 5432)),
            database=os.getenv('DB_NAME', 'trading_production'),
            user=os.getenv('DB_USER', 'trading_user'),
            password=os.getenv('DB_PASSWORD'),
        )
        logger.info(
            f"✅ Database connection pool initialized (min={os.getenv('DB_POOL_MIN', 8)}, "
            f"max={os.getenv('DB_POOL_MAX', 30)})"
        )
    except Exception as e:
        logger.error(f"❌ Failed to initialize database connection pool: {e}")
        db_pool = None


_init_db_pool()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    logger.info("🚀 Trading System Backend Starting...")

    # Test database connection on startup
    try:
        conn = get_database_connection()
        if conn:
            conn.close()
            logger.info("✅ Database connection verified")
        else:
            logger.error("❌ Database connection failed")
    except Exception as e:
        logger.error(f"❌ Database connection error: {e}")

    # Test ML file loading
    try:
        ml_data = load_ml_enhanced_data()
        if ml_data:
            logger.info("✅ ML enhanced data loaded successfully")
        else:
            logger.error("❌ ML enhanced data loading failed")
    except Exception as e:
        logger.error(f"❌ ML data loading error: {e}")

    # Keep the dashboard's main-page-data cache perpetually warm (see
    # _main_page_prewarm_loop) so a user request never pays the cold-build cost.
    prewarm_thread = threading.Thread(target=_main_page_prewarm_loop, daemon=True)
    prewarm_thread.start()

    yield

    _main_page_prewarm_stop.set()
    if db_pool is not None:
        db_pool.closeall()
    logger.info("🔄 Trading System Backend Shutting Down...")


# Initialize FastAPI app
app = FastAPI(
    title="Trading System API",
    description="Advanced stock screening and ML-enhanced trading signals API",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:8080", "http://localhost:3001"],  # Common frontend ports
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def track_request_performance(request: Request, call_next):
    """Times every request and records it against the path TEMPLATE (e.g.
    /api/stock/{symbol}, not /api/stock/AAPL) so per-endpoint stats don't
    fragment by symbol. See services/performance_service.py and
    routers/performance.py (/api/performance/*) for where this surfaces."""
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000

    route = request.scope.get("route")
    path_template = route.path if route is not None else request.url.path
    performance_tracker.record_request(request.method, path_template, response.status_code, duration_ms)

    return response


def get_database_connection():
    """Get a connection from the pool. Callers use it exactly as before
    (including their own `conn.close()`) — see _PooledConnection above for
    why that now returns it to the pool instead of closing the socket."""
    global db_pool
    if db_pool is None:
        _init_db_pool()
    if db_pool is None:
        logger.error("Database connection pool unavailable")
        return None
    try:
        return db_pool.getconn()
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None


def get_cached_data(cache_key: str, cache_duration: int = CACHE_DURATION):
    """Get cached data if still valid"""
    with _cache_lock:
        if cache_key in cache and cache_key in cache_timestamps:
            time_diff = datetime.now() - cache_timestamps[cache_key]
            if time_diff.total_seconds() < cache_duration:
                logger.info(f"📦 Serving cached data for: {cache_key}")
                return cache[cache_key]
    return None


def set_cached_data(cache_key: str, data: Any):
    """Set cached data with timestamp"""
    with _cache_lock:
        cache[cache_key] = data
        cache_timestamps[cache_key] = datetime.now()
    logger.info(f"💾 Cached data for: {cache_key}")


# ============================================================================
# INCLUDE ROUTERS
# ============================================================================

# Import and include screener router
try:
    from routers.screener import screener_router

    app.include_router(screener_router)
    logger.info("✅ Screener router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Screener router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including screener router: {e}")

# Import and include stock detail router
try:
    from routers.stock import stock_router

    app.include_router(stock_router)
    logger.info("✅ Stock router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Stock router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including stock router: {e}")

# Import and include alpha finder router
try:
    from routers.alpha import alpha_router

    app.include_router(alpha_router)
    logger.info("✅ Alpha router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Alpha router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including alpha router: {e}")

# Import and include trading strategy router
try:
    from routers.strategy import strategy_router

    app.include_router(strategy_router)
    logger.info("✅ Strategy router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Strategy router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including strategy router: {e}")

# Import and include deep value / turnaround alerts router
try:
    from routers.deep_value import deep_value_router

    app.include_router(deep_value_router)
    logger.info("✅ Deep value router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Deep value router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including deep value router: {e}")

# Import and include system health router (production-readiness checks --
# distinct from the basic /api/health liveness check above)
try:
    from routers.system_health import system_health_router

    app.include_router(system_health_router)
    logger.info("✅ System health router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  System health router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including system health router: {e}")

# Import and include ML stats router (model accuracy, feature importance,
# training data composition, prediction track record, retraining cadence)
try:
    from routers.ml_stats import ml_stats_router

    app.include_router(ml_stats_router)
    logger.info("✅ ML stats router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  ML stats router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including ML stats router: {e}")

# Import and include performance router (endpoint/DB/frontend-route latency
# tracking and scoring — see services/performance_service.py)
try:
    from routers.performance import performance_router

    app.include_router(performance_router)
    logger.info("✅ Performance router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Performance router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including performance router: {e}")

# Import and include market data router (real index/commodity/macro data —
# see services/market_data_service.py, distinct from /api/screener's
# stock-universe-only "market overview")
try:
    from routers.market import market_router

    app.include_router(market_router)
    logger.info("✅ Market data router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Market data router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including market data router: {e}")


# ============================================================================
# HEALTH AND STATUS ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "message": "Trading System API",
        "version": "1.0.0",
        "status": "operational",
        "features": ["dashboard", "screener", "ml_insights"],
        "endpoints": [
            "/api/health",
            "/api/system-health/",
            "/api/ml-stats/",
            "/api/dashboard/main-page-data",
            "/api/dashboard/top-gainers",
            "/api/dashboard/top-losers",
            "/api/dashboard/unusual-volume",
            "/api/dashboard/top-ai-picks",
            "/api/screener/search",
            "/api/screener/filters",
            "/api/screener/market-overview",
            "/api/screener/presets"
        ]
    }


@app.get("/api/health")
async def health_check():
    """Comprehensive health check"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "components": {}
    }

    # Check database connection
    try:
        conn = get_database_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM stock_prices")
            price_count = cursor.fetchone()[0]
            cursor.close()
            conn.close()

            health_status["components"]["database"] = {
                "status": "healthy",
                "stock_prices_count": price_count
            }
        else:
            health_status["components"]["database"] = {
                "status": "unhealthy",
                "error": "Connection failed"
            }
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["components"]["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"

    # Check ML data availability
    try:
        ml_data = load_ml_enhanced_data()
        if ml_data and 'signals' in ml_data:
            total_signals = (
                    len(ml_data['signals'].get('bullish_breakouts', [])) +
                    len(ml_data['signals'].get('bearish_breakouts', [])) +
                    len(ml_data['signals'].get('near_bullish', [])) +
                    len(ml_data['signals'].get('near_bearish', []))
            )
            health_status["components"]["ml_data"] = {
                "status": "healthy",
                "total_signals": total_signals,
                "top_ai_picks": len(ml_data.get('ai_insights', {}).get('top_ai_picks', []))
            }
        else:
            health_status["components"]["ml_data"] = {
                "status": "unhealthy",
                "error": "No signals found"
            }
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["components"]["ml_data"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"

    # Check screener service
    try:
        from services.market_service import MarketService
        market_service = MarketService(get_database_connection)
        filter_options = market_service.get_available_filters()

        health_status["components"]["screener"] = {
            "status": "healthy",
            "available_sectors": len(filter_options.get('sectors', [])),
            "filter_options_loaded": True
        }
    except Exception as e:
        health_status["components"]["screener"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        health_status["status"] = "degraded"

    return health_status


# ============================================================================
# DASHBOARD ENDPOINTS (EXISTING)
# ============================================================================

def _build_main_page_data():
    """Fetch every dashboard component and populate the cache. Used both by
    the route handler (on a cache miss) and by the background pre-warm loop
    below (so a user request essentially never pays this cost -- see
    _main_page_prewarm_loop). The four sub-fetches are independent blocking
    DB calls; running them concurrently instead of sequentially means a
    cold-cache build takes as long as the slowest one, not the sum of all
    four."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        f_gainers = executor.submit(get_top_gainers_internal, 5)
        f_losers = executor.submit(get_top_losers_internal, 5)
        f_volume = executor.submit(get_unusual_volume_internal, 5)
        f_picks = executor.submit(get_top_ai_picks_internal, 5)

        top_gainers = f_gainers.result()
        top_losers = f_losers.result()
        unusual_volume = f_volume.result()
        top_ai_picks = f_picks.result()

    # Market summary - use latest available data
    conn = get_database_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get market summary stats from technical indicators (most recent)
    cursor.execute("""
        SELECT
            COUNT(DISTINCT symbol) as total_symbols,
            ROUND(AVG(volume_ratio), 2) as avg_volume_ratio,
            MAX(date) as latest_date
        FROM technical_indicators
        WHERE date = (SELECT MAX(date) FROM technical_indicators)
        AND volume_ratio IS NOT NULL
    """)
    summary_stats = cursor.fetchone()

    cursor.close()
    conn.close()

    main_page_data = {
        "timestamp": datetime.now().isoformat(),
        "market_summary": {
            "total_symbols": summary_stats['total_symbols'] if summary_stats else 0,
            "avg_volume_ratio": float(summary_stats['avg_volume_ratio']) if summary_stats and summary_stats[
                'avg_volume_ratio'] else 1.0,
            "active_signals": len(top_ai_picks) if top_ai_picks else 0,
            "last_updated": summary_stats['latest_date'].strftime("%Y-%m-%d") if summary_stats and summary_stats[
                'latest_date'] else "Unknown"
        },
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "unusual_volume": unusual_volume,
        "top_ai_picks": top_ai_picks
    }

    set_cached_data("main_page_data", main_page_data)
    return main_page_data


_main_page_prewarm_stop = threading.Event()


def _main_page_prewarm_loop():
    """Proactively rebuilds the main-page-data cache before its 15-minute TTL
    expires, so a real user request only ever hits the cache -- previously
    every ~15 minutes some request paid the full cold-build cost (and, since
    the sub-fetches ran on the event loop, briefly starved every other
    concurrent request too). Runs once immediately (warms on startup), then
    on a loop until app shutdown."""
    while True:
        try:
            _build_main_page_data()
        except Exception as e:
            logger.error(f"Background main-page-data refresh failed: {e}")
        if _main_page_prewarm_stop.wait(max(CACHE_DURATION - 120, 60)):
            break


@app.get("/api/dashboard/main-page-data")
def get_main_page_data():
    """Get all main page data in a single request"""
    cached_data = get_cached_data("main_page_data", 900)  # 15 minute cache
    if cached_data:
        return cached_data

    try:
        return _build_main_page_data()
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Error getting main page data: {e}")
        logger.error(f"Full traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Error retrieving main page data: {str(e)}")


# ============================================================================
# INDIVIDUAL DASHBOARD COMPONENT ENDPOINTS
# ============================================================================

@app.get("/api/dashboard/top-gainers")
def get_top_gainers(limit: int = Query(10, ge=5, le=50)):
    """Get top gaining stocks"""
    return get_top_gainers_internal(limit)


def get_top_gainers_internal(limit: int = 10):
    """Internal function to get top gainers - FIXED VERSION"""
    cache_key = f"top_gainers_{limit}"
    cached_data = get_cached_data(cache_key)

    if cached_data:
        return cached_data

    try:
        conn = get_database_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # SIMPLIFIED query that handles missing join data gracefully
        cursor.execute("""
            WITH latest_data AS (
                SELECT DISTINCT ON (sp.symbol) 
                    sp.symbol,
                    sp.date,
                    sp.close,
                    sp.volume,
                    sp.open
                FROM stock_prices sp
                WHERE sp.date >= CURRENT_DATE - INTERVAL '7 days'
                ORDER BY sp.symbol, sp.date DESC
            ),
            previous_data AS (
                -- LATERAL per-symbol lookup (uses idx_stock_prices_symbol_date) instead of
                -- an INNER JOIN across the whole table -- the old version merge-joined all
                -- 6.4M rows of stock_prices against latest_data before filtering, which
                -- alone cost ~7.5s of this query's ~7.8s total (see EXPLAIN ANALYZE in
                -- SYSTEM_HEALTH_REPORT.md's 2026-09-19 performance investigation). This
                -- form matches the technical_indicators/daily_fundamentals joins below,
                -- which were already fast because they do the same per-symbol lookup.
                SELECT
                    ld.symbol,
                    prev.close AS prev_close
                FROM latest_data ld
                LEFT JOIN LATERAL (
                    SELECT sp.close
                    FROM stock_prices sp
                    WHERE sp.symbol = ld.symbol AND sp.date < ld.date
                    ORDER BY sp.date DESC
                    LIMIT 1
                ) prev ON true
            ),
            price_changes AS (
                SELECT
                    ld.symbol,
                    ld.close as current_price,
                    ld.volume,
                    pd.prev_close,
                    CASE
                        WHEN pd.prev_close > 0
                        THEN ((ld.close - pd.prev_close) / pd.prev_close * 100)
                        ELSE 0
                    END as price_change_pct
                FROM latest_data ld
                LEFT JOIN previous_data pd ON ld.symbol = pd.symbol
                WHERE ld.close >= 1.0  -- Filter penny stocks
                AND ld.volume >= 100000  -- Minimum volume
                AND pd.prev_close IS NOT NULL
            )
            SELECT
                pc.symbol,
                pc.current_price,
                pc.price_change_pct,
                pc.volume,
                COALESCE(ti.volume_ratio, 1.0) as volume_ratio,
                COALESCE(df.sector, 'Unknown') as sector,
                df.market_cap
            FROM price_changes pc
            LEFT JOIN technical_indicators ti ON pc.symbol = ti.symbol
                AND ti.date = (SELECT MAX(date) FROM technical_indicators WHERE symbol = pc.symbol)
            LEFT JOIN daily_fundamentals df ON pc.symbol = df.symbol
                AND df.date = (SELECT MAX(date) FROM daily_fundamentals WHERE symbol = pc.symbol)
            WHERE pc.price_change_pct > 0
            ORDER BY pc.price_change_pct DESC
            LIMIT %s
        """, (limit,))

        gainers = cursor.fetchall()
        cursor.close()
        conn.close()

        # Format results
        formatted_gainers = []
        for gainer in gainers:
            formatted_gainers.append({
                "symbol": gainer['symbol'],
                "current_price": float(gainer['current_price']),
                "price_change_pct": round(float(gainer['price_change_pct']), 2),
                "volume": int(gainer['volume']) if gainer['volume'] else 0,
                "volume_ratio": round(float(gainer['volume_ratio']), 2) if gainer['volume_ratio'] else 1.0,
                "sector": gainer['sector'] or "Unknown",
                "market_cap": int(gainer['market_cap']) if gainer['market_cap'] else None
            })

        set_cached_data(cache_key, formatted_gainers)
        return formatted_gainers

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Error getting top gainers: {e}")
        logger.error(f"Full traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Error retrieving top gainers: {str(e)}")


@app.get("/api/dashboard/top-losers")
def get_top_losers(limit: int = Query(10, ge=5, le=50)):
    """Get top losing stocks"""
    return get_top_losers_internal(limit)


def get_top_losers_internal(limit: int = 10):
    """Internal function to get top losers - FIXED VERSION"""
    cache_key = f"top_losers_{limit}"
    cached_data = get_cached_data(cache_key)

    if cached_data:
        return cached_data

    try:
        conn = get_database_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Same query as gainers but with price_change_pct < 0 and ORDER BY ASC
        cursor.execute("""
            WITH latest_data AS (
                SELECT DISTINCT ON (sp.symbol) 
                    sp.symbol,
                    sp.date,
                    sp.close,
                    sp.volume,
                    sp.open
                FROM stock_prices sp
                WHERE sp.date >= CURRENT_DATE - INTERVAL '7 days'
                ORDER BY sp.symbol, sp.date DESC
            ),
            previous_data AS (
                -- LATERAL per-symbol lookup, not a full-table INNER JOIN -- see the
                -- matching comment in get_top_gainers_internal for why.
                SELECT
                    ld.symbol,
                    prev.close AS prev_close
                FROM latest_data ld
                LEFT JOIN LATERAL (
                    SELECT sp.close
                    FROM stock_prices sp
                    WHERE sp.symbol = ld.symbol AND sp.date < ld.date
                    ORDER BY sp.date DESC
                    LIMIT 1
                ) prev ON true
            ),
            price_changes AS (
                SELECT
                    ld.symbol,
                    ld.close as current_price,
                    ld.volume,
                    pd.prev_close,
                    CASE
                        WHEN pd.prev_close > 0
                        THEN ((ld.close - pd.prev_close) / pd.prev_close * 100)
                        ELSE 0
                    END as price_change_pct
                FROM latest_data ld
                LEFT JOIN previous_data pd ON ld.symbol = pd.symbol
                WHERE ld.close >= 1.0  -- Filter penny stocks
                AND ld.volume >= 100000  -- Minimum volume
                AND pd.prev_close IS NOT NULL
            )
            SELECT
                pc.symbol,
                pc.current_price,
                pc.price_change_pct,
                pc.volume,
                COALESCE(ti.volume_ratio, 1.0) as volume_ratio,
                COALESCE(df.sector, 'Unknown') as sector,
                df.market_cap
            FROM price_changes pc
            LEFT JOIN technical_indicators ti ON pc.symbol = ti.symbol
                AND ti.date = (SELECT MAX(date) FROM technical_indicators WHERE symbol = pc.symbol)
            LEFT JOIN daily_fundamentals df ON pc.symbol = df.symbol
                AND df.date = (SELECT MAX(date) FROM daily_fundamentals WHERE symbol = pc.symbol)
            WHERE pc.price_change_pct < 0
            ORDER BY pc.price_change_pct ASC
            LIMIT %s
        """, (limit,))

        losers = cursor.fetchall()
        cursor.close()
        conn.close()

        # Format results
        formatted_losers = []
        for loser in losers:
            formatted_losers.append({
                "symbol": loser['symbol'],
                "current_price": float(loser['current_price']),
                "price_change_pct": round(float(loser['price_change_pct']), 2),
                "volume": int(loser['volume']) if loser['volume'] else 0,
                "volume_ratio": round(float(loser['volume_ratio']), 2) if loser['volume_ratio'] else 1.0,
                "sector": loser['sector'] or "Unknown",
                "market_cap": int(loser['market_cap']) if loser['market_cap'] else None
            })

        set_cached_data(cache_key, formatted_losers)
        return formatted_losers

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Error getting top losers: {e}")
        logger.error(f"Full traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Error retrieving top losers: {str(e)}")


@app.get("/api/dashboard/unusual-volume")
def get_unusual_volume(limit: int = Query(10, ge=5, le=50)):
    """Get stocks with unusual volume activity"""
    return get_unusual_volume_internal(limit)


def get_unusual_volume_internal(limit: int = 10):
    """Internal function to get unusual volume stocks - FIXED VERSION"""
    cache_key = f"unusual_volume_{limit}"
    cached_data = get_cached_data(cache_key)

    if cached_data:
        return cached_data

    try:
        conn = get_database_connection()
        if not conn:
            raise HTTPException(status_code=500, detail="Database connection failed")

        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Focus on technical_indicators table which has volume_ratio
        cursor.execute("""
            WITH latest_tech AS (
                SELECT DISTINCT ON (ti.symbol)
                    ti.symbol,
                    ti.date,
                    ti.volume_ratio
                FROM technical_indicators ti
                WHERE ti.date >= CURRENT_DATE - INTERVAL '7 days'
                AND ti.volume_ratio >= 2.0  -- At least 2x normal volume
                ORDER BY ti.symbol, ti.date DESC
            ),
            latest_prices AS (
                SELECT DISTINCT ON (sp.symbol)
                    sp.symbol,
                    sp.date,
                    sp.close as current_price,
                    sp.volume
                FROM stock_prices sp
                WHERE sp.date >= CURRENT_DATE - INTERVAL '7 days'
                ORDER BY sp.symbol, sp.date DESC
            ),
            previous_prices AS (
                -- LATERAL per-symbol lookup, not an INNER JOIN + correlated MAX(date)
                -- subquery over the whole table -- see the matching comment in
                -- get_top_gainers_internal for why this was the dashboard's main
                -- slow-query source.
                SELECT
                    lp.symbol,
                    prev.close AS prev_close
                FROM latest_prices lp
                LEFT JOIN LATERAL (
                    SELECT sp.close
                    FROM stock_prices sp
                    WHERE sp.symbol = lp.symbol AND sp.date < lp.date
                    ORDER BY sp.date DESC
                    LIMIT 1
                ) prev ON true
            )
            SELECT 
                lt.symbol,
                lp.current_price,
                lp.volume,
                lt.volume_ratio,
                CASE 
                    WHEN pp.prev_close > 0 
                    THEN ((lp.current_price - pp.prev_close) / pp.prev_close * 100)
                    ELSE 0 
                END as price_change_pct,
                COALESCE(df.sector, 'Unknown') as sector,
                df.market_cap
            FROM latest_tech lt
            INNER JOIN latest_prices lp ON lt.symbol = lp.symbol
            LEFT JOIN previous_prices pp ON lt.symbol = pp.symbol
            LEFT JOIN daily_fundamentals df ON lt.symbol = df.symbol 
                AND df.date = (SELECT MAX(date) FROM daily_fundamentals WHERE symbol = lt.symbol)
            WHERE lp.current_price >= 1.0    -- Filter penny stocks
            AND lp.volume >= 500000         -- High absolute volume
            ORDER BY lt.volume_ratio DESC
            LIMIT %s
        """, (limit,))

        unusual_volume_stocks = cursor.fetchall()
        cursor.close()
        conn.close()

        # Format results
        formatted_stocks = []
        for stock in unusual_volume_stocks:
            formatted_stocks.append({
                "symbol": stock['symbol'],
                "current_price": float(stock['current_price']),
                "volume": int(stock['volume']) if stock['volume'] else 0,
                "volume_ratio": round(float(stock['volume_ratio']), 2),
                "price_change_pct": round(float(stock['price_change_pct']), 2) if stock['price_change_pct'] else 0,
                "sector": stock['sector'] or "Unknown",
                "market_cap": int(stock['market_cap']) if stock['market_cap'] else None
            })

        set_cached_data(cache_key, formatted_stocks)
        return formatted_stocks

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Error getting unusual volume: {e}")
        logger.error(f"Full traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Error retrieving unusual volume: {str(e)}")


@app.get("/api/dashboard/top-ai-picks")
def get_top_ai_picks(limit: int = Query(10, ge=5, le=20)):
    """Get top AI-selected stock picks"""
    return get_top_ai_picks_internal(limit)


def get_top_ai_picks_internal(limit: int = 10):
    """Internal function to get top AI picks - FIXED VERSION"""
    cache_key = f"top_ai_picks_{limit}"
    cached_data = get_cached_data(cache_key, 3600)  # 1 hour cache for ML data

    if cached_data:
        return cached_data

    try:
        # Load ML enhanced data (shared cache -- see utils.load_ml_enhanced_data_cached)
        ml_data = load_ml_enhanced_data_cached()
        if not ml_data or 'ai_insights' not in ml_data:
            return []

        # Get top AI picks from ML data
        ai_picks = ml_data['ai_insights'].get('top_ai_picks', [])

        # Limit results and format
        formatted_picks = []
        for i, pick in enumerate(ai_picks[:limit]):
            formatted_pick = {
                "rank": i + 1,
                "symbol": pick.get('symbol'),
                "current_price": pick.get('current_price'),
                "breakout_type": pick.get('signal_type', '').replace('_', ' ').title(),
                "ml_score": pick.get('ml_momentum_probability'),  # null = no validated score, never 0
                "confidence": pick.get('ml_confidence'),
                "urgency": pick.get('urgency', 'medium'),
                "sector": pick.get('sector', 'Unknown'),
                "volume_ratio": pick.get('volume_ratio', 1.0),
                "price_change_pct": pick.get('price_change_pct', 0),
                "reasoning": pick.get('summary_text', 'High probability breakout signal')
            }
            formatted_picks.append(formatted_pick)

        set_cached_data(cache_key, formatted_picks)
        return formatted_picks

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Error getting top AI picks: {e}")
        logger.error(f"Full traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Error retrieving top AI picks: {str(e)}")


# ============================================================================
# MAIN APPLICATION
# ============================================================================

if __name__ == "__main__":
    # Development server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )