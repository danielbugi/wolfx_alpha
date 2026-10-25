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
from pathlib import Path
from dotenv import load_dotenv
from typing import List, Dict, Any, Optional
import json
from datetime import datetime, timedelta
import logging
from utils import find_latest_ml_enhanced_file, load_ml_enhanced_data, load_ml_enhanced_data_cached
from contextlib import asynccontextmanager
from services.performance_service import tracker as performance_tracker
from auth.dependencies import require_authenticated_user

# Load environment variables. Explicit path, not a bare load_dotenv(): python-dotenv's default upward
# search stops at the FIRST `.env` it finds starting from this file's directory, and a stale, untracked
# `backend/.env` (DB config only, missing everything added since) used to shadow the real repo-root `.env`
# whenever the backend was started with `cd backend && uvicorn main:app` — exactly the command this
# project's own docs tell you to run. That silently disabled TELEGRAM_CONTROL_TOKEN and would have done the
# same to the newer auth config (JWT_SECRET, SMTP_*). Found and fixed 2026-09-22; see PLATFORM_ARCHITECTURE.md.
#
# override=True, not the library default (False): without it, a variable already present in the process's
# inherited environment — even an empty string, e.g. left over from an earlier terminal/parent-shell session
# in the same window lineage — silently wins over whatever `.env` says, forever, no matter how many times
# `.env` is edited and the server restarted from that same shell. This is exactly what happened 2026-09-22
# while wiring up SMTP for the 2FA email: SMTP_HOST was correct on disk, verified working in isolation, and
# still came back empty inside the running server — traced to this. `.env` is meant to be the single source
# of truth for this app's config (CLAUDE.md §6); it should always win.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

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
# Set by _shutdown_db_pool() before closeall(): from then on close() must really close (see the guard in close()).
_pool_shutting_down = False


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
        # Same deadlock, shutdown path: ThreadedConnectionPool.closeall() holds the pool lock while it calls
        # conn.close() on every connection; handing the connection back via putconn() here would wait on that
        # same lock forever -- which is what hung every `uvicorn --reload` at "Waiting for application shutdown."
        if _pool_shutting_down:
            if not self.closed:
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


def _shutdown_db_pool():
    """Close every pooled connection. Sets the shutdown flag first so _PooledConnection.close() really closes
    instead of re-entering the pool lock that closeall() is holding."""
    global _pool_shutting_down
    _pool_shutting_down = True
    if db_pool is not None and not db_pool.closed:
        db_pool.closeall()


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
    _shutdown_db_pool()
    logger.info("🔄 Trading System Backend Shutting Down...")


# Initialize FastAPI app
app = FastAPI(
    title="Trading System API",
    description="Advanced stock screening and ML-enhanced trading signals API",
    version="1.0.0",
    lifespan=lifespan
)

# Reads the comma-separated ALLOWED_ORIGINS env var (already present in .env and
# docker/.env.example, previously unused -- see CURRENT_ARCHITECTURE.md §7). Falls back to the
# existing hardcoded localhost list only when the var is entirely unset, so today's local dev
# behavior is unchanged for anyone whose .env predates this. Fails closed on a literal "*": this
# API uses allow_credentials=True, and a wildcard origin combined with credentials is rejected by
# browsers anyway and is a real security footgun if it were ever silently accepted.
def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS")
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        if "*" in origins:
            raise RuntimeError(
                "ALLOWED_ORIGINS must not contain '*' -- this API uses allow_credentials=True; "
                "a wildcard origin with credentials is invalid per the CORS spec and a real risk."
            )
        return origins
    return ["http://localhost:3000", "http://localhost:5173", "http://localhost:8080", "http://localhost:3001"]


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(),
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

# Import and include the auth router (login/2FA/refresh/logout/me + Owner-only
# user & session management — see routers/auth.py). Included first and
# unprotected at the router level: its own routes decide per-endpoint whether
# a caller must already be signed in (e.g. /me) or not (e.g. /login).
try:
    from routers.auth import auth_router

    app.include_router(auth_router)
    logger.info("✅ Auth router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Auth router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including auth router: {e}")

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

# Import and include the Quant Lab reports router (read-only research artifacts; no database)
try:
    from routers.lab_reports import lab_reports_router

    app.include_router(lab_reports_router)
    logger.info("✅ Lab reports router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Lab reports router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including lab reports router: {e}")

# Import and include the forward paper tracker dashboard router (READ-ONLY aggregation of the stored paper_* rows; answers available:false when the storage is absent)
try:
    from routers.forward_tracker import forward_tracker_router

    app.include_router(forward_tracker_router)
    logger.info("✅ Forward tracker router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Forward tracker router not available: {e}")
except Exception as e:
    logger.error(f"❌ Failed to include forward tracker router: {e}")

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

# Import and include the Momentum Board router (dashboard's find-big-winners panel,
# replacing the old ML/alignment-based Alpha Finder — see
# services/momentum_board_service.py)
try:
    from routers.momentum_board import momentum_board_router

    app.include_router(momentum_board_router)
    logger.info("✅ Momentum board router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Momentum board router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including momentum board router: {e}")

# Import and include the Momentum Leaders router (who's still moving after making an
# earlier Momentum Board list — see services/momentum_leaders_service.py)
try:
    from routers.momentum_leaders import momentum_leaders_router

    app.include_router(momentum_leaders_router)
    logger.info("✅ Momentum leaders router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Momentum leaders router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including momentum leaders router: {e}")

# Import and include the Telegram Control Center router (what was posted to the
# channels + edit/delete behind TELEGRAM_CONTROL_TOKEN — see
# routers/telegram_control.py and mechanism/alerts/channel_control.py)
try:
    from routers.telegram_control import telegram_control_router

    app.include_router(telegram_control_router)
    logger.info("✅ Telegram control router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Telegram control router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including Telegram control router: {e}")

# Import and include the Bot Access Control router (who may use the private
# assistant — approve/decline requests, revoke, invite links — Owner-only;
# see routers/bot_access.py and mechanism/alerts/access.py)
try:
    from routers.bot_access import bot_access_router

    app.include_router(bot_access_router)
    logger.info("✅ Bot access router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Bot access router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including bot access router: {e}")

# Import and include the Track Record router (the live outcome ledger's
# authenticated summary and signal list — see mechanism/add_signal_ledger_tables.sql
# and services/track_record_service.py)
try:
    from routers.track_record import track_record_router

    app.include_router(track_record_router)
    logger.info("✅ Track record router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Track record router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including track record router: {e}")

# Import and include the Strategy Intelligence router (internal per-strategy summary, data health and
# signal explorer over signal_ledger -- computed by mechanism/strategy_analytics)
try:
    from routers.strategy_intelligence import strategy_intelligence_router

    app.include_router(strategy_intelligence_router)
    logger.info("✅ Strategy intelligence router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Strategy intelligence router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including strategy intelligence router: {e}")

# Generic strategy performance API (read-only: summary, outcomes, conditional breakdowns for ANY strategy/version,
# computed by mechanism/strategy_analytics; unavailable dimensions answer not_available)
try:
    from routers.strategy_performance import strategy_performance_router

    app.include_router(strategy_performance_router)
    logger.info("✅ Strategy performance router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Strategy performance router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including strategy performance router: {e}")


# Import and include the Market Intelligence router (internal, read-only: session market regime and sector / relative-strength
# snapshots from mechanism/market_intelligence; answers `available: false` until migrations 24/25 are applied)
try:
    from routers.market_intelligence import market_intelligence_router

    app.include_router(market_intelligence_router)
    logger.info("✅ Market intelligence router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  Market intelligence router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including market intelligence router: {e}")


# Import and include the stock news router (headlines for the stock page, through the same cached news service the
# Telegram assistant uses -- mechanism/alerts/news_service.py)
try:
    from routers.news import news_router

    app.include_router(news_router)
    logger.info("✅ News router included successfully")
except ImportError as e:
    logger.warning(f"⚠️  News router not available: {e}")
except Exception as e:
    logger.error(f"❌ Error including news router: {e}")


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
    _main_page_prewarm_loop). The three movers lists come from ONE Smart Screener v2
    evaluation for one explicit session (DiscoveryService.movers, 2026-10-25): the same
    filters and ordering as the Screener views the Overview links to, and never a
    symbol whose newest bar is older than the session (the old per-list queries took
    each symbol's latest row within 7 days, and unusual volume its latest ratio >= 2,
    so a past day's move could be shown as today's).

    No longer fetches "top AI picks" here (removed 2026-09-23, see CLAUDE.md's
    dashboard reframe entries): that endpoint read alignment-only scores from
    a screener era when ML was expected to contribute, mislabeled "AI" with no
    model currently served (see the ML audit) -- and nothing on the dashboard
    has rendered its list since AIPicksWidget.tsx was deleted. The Active
    Signals summary card now gets its number from Momentum Board's
    `starred_total` instead (frontend-side), not from this endpoint."""
    movers = _screener_movers(5)

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
            # null (not a fabricated 1.0 "normal volume") when the table has no rows yet --
            # same fix as the top-gainers/top-losers volume_ratio fabricated-default fix.
            "avg_volume_ratio": float(summary_stats['avg_volume_ratio']) if summary_stats and summary_stats[
                'avg_volume_ratio'] is not None else None,
            "last_updated": summary_stats['latest_date'].strftime("%Y-%m-%d") if summary_stats and summary_stats[
                'latest_date'] else "Unknown"
        },
        "top_gainers": movers["top_gainers"],
        "top_losers": movers["top_losers"],
        "unusual_volume": movers["unusual_volume"],
        "movers": movers["meta"],
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


@app.get("/api/dashboard/main-page-data", dependencies=[Depends(require_authenticated_user)])
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

def _mover_item(r: Dict[str, Any]) -> Dict[str, Any]:
    """A Screener v2 row in the dashboard's StockItem shape. Missing values stay null (never 0 or 1.0)."""
    return {
        "symbol": r["symbol"],
        "current_price": round(float(r["close"]), 4),
        "price_change_pct": round(float(r["change_1d"]), 2) if r["change_1d"] is not None else None,
        "volume": int(r["volume"]) if r["volume"] is not None else None,
        "volume_ratio": round(float(r["volume_ratio"]), 2) if r["volume_ratio"] is not None else None,
        "sector": r["sector"] or "Unknown",
        "market_cap": int(r["market_cap"]) if r["market_cap"] is not None else None,
    }


def _screener_movers(limit: int) -> Dict[str, Any]:
    """Top gainers / losers / unusual volume for the newest real session, from one Screener v2 evaluation."""
    from services.discovery_service import DiscoveryService, MOVERS_FLOOR
    m = DiscoveryService(get_database_connection).movers(limit)
    out = {k: [_mover_item(r) for r in m[k]] for k in ("top_gainers", "top_losers", "unusual_volume")}
    out["meta"] = {
        "session": m["session"],
        "previous_session": m["previous_session"],
        "universe": m["universe"],
        "coverage": m["coverage"],
        "totals": {k: m[f"{k}_total"] for k in ("top_gainers", "top_losers", "unusual_volume")},
        "floor": {"min_close": MOVERS_FLOOR["close"][0], "min_dollar_vol_20": MOVERS_FLOOR["dollar_vol_20"][0]},
        "min_volume_ratio": 2.0,
    }
    return out


def _movers_list(key: str, limit: int) -> List[Dict[str, Any]]:
    cache_key = f"movers_{key}_{limit}"
    cached = get_cached_data(cache_key)
    if cached:
        return cached
    try:
        items = _screener_movers(limit)[key]
    except Exception as e:
        logger.error(f"Error getting {key}: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving {key.replace('_', ' ')}")
    set_cached_data(cache_key, items)
    return items


@app.get("/api/dashboard/top-gainers", dependencies=[Depends(require_authenticated_user)])
def get_top_gainers(limit: int = Query(10, ge=5, le=50)):
    """Top gaining stocks for the newest real session (Screener v2 rules: $1+ close, $1M+/day dollar volume)."""
    return _movers_list("top_gainers", limit)


@app.get("/api/dashboard/top-losers", dependencies=[Depends(require_authenticated_user)])
def get_top_losers(limit: int = Query(10, ge=5, le=50)):
    """Top losing stocks for the newest real session (same rules as the gainers)."""
    return _movers_list("top_losers", limit)


@app.get("/api/dashboard/unusual-volume", dependencies=[Depends(require_authenticated_user)])
def get_unusual_volume(limit: int = Query(10, ge=5, le=50)):
    """Stocks trading at 2x+ their usual volume ON the newest real session (same price and liquidity floor)."""
    return _movers_list("unusual_volume", limit)


# /api/dashboard/top-ai-picks removed 2026-09-23 (see CLAUDE.md's dashboard reframe entries):
# it read alignment-only scores mislabeled "AI" with no ML model currently served, and nothing
# has rendered its list since AIPicksWidget.tsx was deleted the day before.

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