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
    'SI=F': 'Silver',
    'HG=F': 'Copper',
    'PL=F': 'Platinum',
    'CL=F': 'Crude Oil (WTI)',
    'NG=F': 'Natural Gas',
    'DX-Y.NYB': 'US Dollar Index',
    'BTC-USD': 'Bitcoin',
}

# Display order on the dashboard -- indices first, then the volatility/rates regime gauges, then commodities
# (metals, then energy; the dashboard shows these in their own Commodities card), then currency/crypto.
# A symbol with no rows yet (e.g. just added to the updater) is simply skipped until its history lands.
DISPLAY_ORDER = ['^GSPC', '^IXIC', '^RUT', '^DJI', '^VIX', '^TNX', 'GC=F', 'SI=F', 'HG=F', 'PL=F', 'CL=F', 'NG=F',
                 'DX-Y.NYB', 'BTC-USD']


def _vix_regime(value: float) -> str:
    if value < 15:
        return 'calm'
    if value < 20:
        return 'normal'
    if value < 30:
        return 'elevated'
    return 'fear'


# ---- Markets pages (services/instruments.py registry) ---------------------------------------------------------------

_instruments_cache: Dict[str, Any] = {"data": None, "ts": 0.0}


def _close_on_or_before(rows: List[Any], target) -> Any:
    """Last close dated on or before `target` (rows are (date, close) ascending); None if history doesn't reach back."""
    found = None
    for d, c in rows:
        if d > target:
            break
        found = c
    return found


def instrument_performance(rows: List[Any]) -> Dict[str, Any]:
    """Changes by CALENDAR offset (1W = vs the last close on/before 7 days ago, etc.), so 7-day crypto and 5-day futures
    compare like for like. A window the history doesn't reach is None, never a shorter period passed off as it."""
    from datetime import timedelta
    if not rows:
        return {"1d": None, "1w": None, "1m": None, "3m": None, "ytd": None, "1y": None}
    last_date, last = rows[-1]

    def change(base):
        return round((last / base - 1) * 100, 4) if base not in (None, 0) and last is not None else None

    prev = rows[-2][1] if len(rows) > 1 else None
    year_start = last_date.replace(month=1, day=1)
    return {
        "1d": change(prev),
        "1w": change(_close_on_or_before(rows, last_date - timedelta(days=7))),
        "1m": change(_close_on_or_before(rows, last_date - timedelta(days=30))),
        "3m": change(_close_on_or_before(rows, last_date - timedelta(days=91))),
        "ytd": change(_close_on_or_before(rows, year_start - timedelta(days=1))),
        "1y": change(_close_on_or_before(rows, last_date - timedelta(days=365))),
    }


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

    def get_instruments(self) -> Dict[str, Any]:
        from services.instruments import GROUPS, INSTRUMENTS
        now = time.time()
        if _instruments_cache["data"] is not None and (now - _instruments_cache["ts"]) < _CACHE_TTL_SECONDS:
            return _instruments_cache["data"]
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT symbol, date, close FROM market_index_prices "
                "WHERE symbol = ANY(%s) AND date >= CURRENT_DATE - 400 AND close IS NOT NULL ORDER BY symbol, date",
                ([i.symbol for i in INSTRUMENTS],),
            )
            by_symbol: Dict[str, List[Any]] = {}
            for symbol, d, c in cursor.fetchall():
                by_symbol.setdefault(symbol, []).append((d, float(c)))
            cursor.close()
        finally:
            conn.close()

        items = []
        for inst in INSTRUMENTS:
            rows = by_symbol.get(inst.symbol, [])
            items.append({
                "symbol": inst.symbol,
                "name": inst.name,
                "group": inst.group,
                "unit": inst.unit,
                "has_data": bool(rows),
                "latest_date": str(rows[-1][0]) if rows else None,
                "close": rows[-1][1] if rows else None,
                "change_pct": instrument_performance(rows),
                "sparkline": [c for _, c in rows[-30:]],
            })
        data = {"groups": GROUPS, "instruments": items}
        _instruments_cache["data"], _instruments_cache["ts"] = data, now
        return data

    def get_instrument_history(self, symbol: str, days: int) -> List[Dict[str, Any]]:
        """Daily OHLCV for one instrument, oldest first, in the stock page's bar shape (Donchian computed client-side)."""
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT date, open, high, low, close, volume FROM market_index_prices "
                "WHERE symbol = %s AND date >= CURRENT_DATE - %s AND close IS NOT NULL ORDER BY date",
                (symbol, days),
            )
            rows = cursor.fetchall()
            cursor.close()
        finally:
            conn.close()
        f = lambda v: float(v) if v is not None else None  # noqa: E731
        return [
            {"date": str(d), "open": f(o), "high": f(h), "low": f(lo), "close": float(c),
             "volume": int(v) if v is not None else None, "donchian_high_20": None, "donchian_low_20": None}
            for d, o, h, lo, c, v in rows
        ]
