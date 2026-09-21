# File: backend/services/market_data_service.py
"""
Market Data Service — real index/commodity/macro data (S&P 500, Nasdaq,
Russell 2000, Dow, VIX, 10Y yield, Gold, Crude, DXY, BTC) and persisted
sector-performance history.

Deliberately a separate file/class from services/market_service.py:
MarketService's "market overview" is breadth stats computed over the
internal stock universe (advance/decline, RSI overbought/oversold, sector
treemap) -- useful, but not actual index-level data. This service is the
real macro layer, backed by market_index_prices and sector_performance_daily
(mechanism/data_updaters/market_index_updater.py and
sector_performance_snapshot.py — see CLAUDE.md's 2026-09-19 dashboard
data-representation entry).
"""

import time
from typing import Dict, Any, List
from psycopg2.extras import RealDictCursor
import logging

logger = logging.getLogger(__name__)

# Same reasoning as deep_value_service.py's module-level cache: the
# underlying tables only change once/day via the automation pipeline, so a
# per-request DB round trip is pure waste. TTL kept short anyway so a
# same-day pipeline re-run (or a manual updater run) shows up without
# needing a backend restart.
_CACHE_TTL_SECONDS = 300
_indices_cache: Dict[str, Any] = {"data": None, "ts": 0.0}
_sector_history_cache: Dict[str, Any] = {}  # keyed by `days`

DISPLAY_NAMES = {
    '^GSPC': 'S&P 500',
    '^IXIC': 'Nasdaq Composite',
    '^RUT': 'Russell 2000',
    '^DJI': 'Dow Jones',
    '^VIX': 'VIX',
    '^TNX': '10-Year Yield',
    'GC=F': 'Gold',
    'CL=F': 'Crude Oil (WTI)',
    'DX-Y.NYB': 'US Dollar Index',
    'BTC-USD': 'Bitcoin',
}

# Display order on the dashboard's macro strip -- indices first, then the
# volatility/rates regime gauges, then commodities/currency/crypto.
DISPLAY_ORDER = ['^GSPC', '^IXIC', '^RUT', '^DJI', '^VIX', '^TNX', 'GC=F', 'CL=F', 'DX-Y.NYB', 'BTC-USD']


def _vix_regime(value: float) -> str:
    if value < 15:
        return 'calm'
    if value < 20:
        return 'normal'
    if value < 30:
        return 'elevated'
    return 'fear'


class MarketDataService:
    """DB-backed reader for market_index_prices and sector_performance_daily."""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def get_market_indices(self, sparkline_days: int = 30) -> Dict[str, Any]:
        now = time.time()
        if _indices_cache["data"] is not None and (now - _indices_cache["ts"]) < _CACHE_TTL_SECONDS:
            return _indices_cache["data"]

        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        # One row per symbol for latest close + prev close (for $/% change),
        # plus a LATERAL-joined sparkline series -- same per-symbol-lookup
        # pattern as market_service.py's fixed anti-pattern, not a table scan.
        cursor.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (symbol)
                    symbol, date, close,
                    LAG(close) OVER (PARTITION BY symbol ORDER BY date) as prev_close
                FROM market_index_prices
                ORDER BY symbol, date DESC
            )
            SELECT
                l.symbol,
                l.date as latest_date,
                l.close as latest_close,
                l.prev_close,
                COALESCE(
                    ARRAY_AGG(sp.close ORDER BY sp.date ASC) FILTER (WHERE sp.close IS NOT NULL),
                    ARRAY[]::DECIMAL[]
                ) as sparkline
            FROM latest l
            LEFT JOIN LATERAL (
                SELECT date, close
                FROM market_index_prices
                WHERE symbol = l.symbol
                ORDER BY date DESC
                LIMIT %s
            ) sp ON true
            GROUP BY l.symbol, l.date, l.close, l.prev_close
        """, (sparkline_days,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        by_symbol = {row['symbol']: row for row in rows}

        results: List[Dict[str, Any]] = []
        for symbol in DISPLAY_ORDER:
            row = by_symbol.get(symbol)
            if not row:
                continue
            close = float(row['latest_close']) if row['latest_close'] is not None else None
            prev_close = float(row['prev_close']) if row['prev_close'] is not None else None
            change = None
            change_pct = None
            if close is not None and prev_close:
                change = round(close - prev_close, 4)
                change_pct = round((close - prev_close) / prev_close * 100, 4)

            entry = {
                'symbol': symbol,
                'display_name': DISPLAY_NAMES.get(symbol, symbol),
                'latest_date': str(row['latest_date']) if row['latest_date'] else None,
                'close': close,
                'change': change,
                'change_pct': change_pct,
                'sparkline': [float(v) for v in (row['sparkline'] or [])],
            }
            if symbol == '^VIX' and close is not None:
                entry['regime'] = _vix_regime(close)
            results.append(entry)

        data = {'indices': results}
        _indices_cache["data"] = data
        _indices_cache["ts"] = now
        return data

    def get_sector_history(self, days: int = 90) -> Dict[str, Any]:
        now = time.time()
        cached = _sector_history_cache.get(days)
        if cached is not None and (now - cached["ts"]) < _CACHE_TTL_SECONDS:
            return cached["data"]

        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT date, sector, stock_count, avg_performance
            FROM sector_performance_daily
            WHERE date >= CURRENT_DATE - (%s || ' days')::INTERVAL
            ORDER BY date ASC, sector ASC
        """, (days,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        by_sector: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_sector.setdefault(row['sector'], []).append({
                'date': str(row['date']),
                'stock_count': row['stock_count'],
                'avg_performance': float(row['avg_performance']) if row['avg_performance'] is not None else 0.0,
            })

        data = {
            'days': days,
            'sectors': [
                {'sector': sector, 'history': history}
                for sector, history in sorted(by_sector.items())
            ],
        }
        _sector_history_cache[days] = {"data": data, "ts": now}
        return data
