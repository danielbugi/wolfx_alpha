# File: backend/services/deep_value_service.py
"""
Deep Value Service - finds stocks trading near their 12-month low at a cheap
valuation (without being a financial-health trap), and flags the subset that
just flipped from a quarterly net loss to a net profit -- the "real alpha"
turnaround pattern.
"""

import time
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)

# The underlying data (daily_fundamentals, monthly_technical_indicators,
# stock_prices, quarterly_fundamentals) only changes once per day via the
# automation pipeline, but this service is re-instantiated per request (see
# routers/deep_value.py's get_deep_value_service Depends). Caching the raw
# SQL fetch here -- module-level, so it survives across those per-request
# instances -- means the (previously uncached, full-table-scan) DB round
# trip only actually happens once per TTL instead of on every request;
# scan()'s cheap in-Python filtering by near_low_pct/valuation/health score
# still runs fresh every call so query-param changes are still honored.
_RAW_CACHE_TTL_SECONDS = 900  # matches main.py's dashboard cache convention
_raw_cache: Dict[str, Any] = {"rows": None, "turnaround_map": None, "ts": 0.0}


class DeepValueService:
    """DB-backed deep-value / earnings-turnaround scanner"""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def _get_turnaround_map(self) -> Dict[str, Dict[str, Any]]:
        """
        For every symbol, look at its two most recent quarters and flag a
        turnaround when the latest is profitable and the one before was a
        loss. Done in Python (ordered by the query) rather than SQL pivoting
        -- simplest way to compare "this row vs. the next row per symbol".
        """
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT symbol, quarter, net_income
                FROM quarterly_fundamentals
                WHERE net_income IS NOT NULL
                ORDER BY symbol, quarter DESC
            """)
            rows = cursor.fetchall()

            by_symbol: Dict[str, list] = {}
            for row in rows:
                by_symbol.setdefault(row['symbol'], []).append(row)

            turnaround_map = {}
            for symbol, quarters in by_symbol.items():
                if len(quarters) < 2:
                    continue
                latest, previous = quarters[0], quarters[1]
                is_turnaround = latest['net_income'] > 0 and previous['net_income'] < 0
                turnaround_map[symbol] = {
                    'is_turnaround': is_turnaround,
                    'latest_quarter': latest['quarter'].strftime('%Y-%m-%d'),
                    'latest_net_income': float(latest['net_income']),
                    'previous_quarter': previous['quarter'].strftime('%Y-%m-%d'),
                    'previous_net_income': float(previous['net_income']),
                }
            return turnaround_map
        finally:
            conn.close()

    def _fetch_rows(self) -> List[Dict[str, Any]]:
        """
        Raw candidate rows: daily_fundamentals (narrowed to the last 40 days --
        it refreshes daily, so anything older is stale anyway) drives the
        query, with monthly_technical_indicators and stock_prices looked up
        per-symbol via LATERAL instead of each running its own DISTINCT ON
        over the FULL table (millions of stock_prices rows). Same per-symbol-
        lookup fix already applied to the dashboard queries in main.py and to
        market_service.py -- this was the last remaining full-table-scan spot.
        """
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                WITH latest_fundamentals AS (
                    SELECT DISTINCT ON (symbol)
                        symbol, sector, market_cap, pe_ratio,
                        valuation_score, financial_health_score,
                        overall_quality_score, quality_grade
                    FROM daily_fundamentals
                    WHERE date >= CURRENT_DATE - INTERVAL '40 days'
                    AND valuation_score IS NOT NULL
                    AND financial_health_score IS NOT NULL
                    ORDER BY symbol, date DESC
                )
                SELECT
                    lf.symbol, lf.sector, lf.market_cap, lf.pe_ratio,
                    lf.valuation_score, lf.financial_health_score,
                    lf.overall_quality_score, lf.quality_grade,
                    lm.donchian_low_12m, lp.current_price
                FROM latest_fundamentals lf
                LEFT JOIN LATERAL (
                    SELECT donchian_low_12m
                    FROM monthly_technical_indicators
                    WHERE symbol = lf.symbol
                    ORDER BY month_ending_date DESC
                    LIMIT 1
                ) lm ON true
                LEFT JOIN LATERAL (
                    SELECT close AS current_price
                    FROM stock_prices
                    WHERE symbol = lf.symbol
                    ORDER BY date DESC
                    LIMIT 1
                ) lp ON true
                WHERE lm.donchian_low_12m IS NOT NULL
                AND lm.donchian_low_12m > 0
                AND lp.current_price IS NOT NULL
            """)
            return cursor.fetchall()
        finally:
            conn.close()

    def _get_raw_data(self) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        now = time.time()
        if _raw_cache["rows"] is not None and (now - _raw_cache["ts"]) < _RAW_CACHE_TTL_SECONDS:
            return _raw_cache["rows"], _raw_cache["turnaround_map"]

        rows = self._fetch_rows()
        turnaround_map = self._get_turnaround_map()
        _raw_cache["rows"] = rows
        _raw_cache["turnaround_map"] = turnaround_map
        _raw_cache["ts"] = now
        return rows, turnaround_map

    def scan(
        self,
        near_low_pct: float = 15.0,
        min_valuation_score: float = 12.0,
        min_financial_health_score: float = 10.0,
    ) -> Dict[str, List[Dict[str, Any]]]:
        rows, turnaround_map = self._get_raw_data()

        deep_value_watch = []
        turnaround_alerts = []

        for row in rows:
            price = float(row['current_price'])
            low = float(row['donchian_low_12m'])
            distance_from_low_pct = (price - low) / low * 100

            valuation_score = float(row['valuation_score'])
            financial_health_score = float(row['financial_health_score'])

            if not (
                distance_from_low_pct <= near_low_pct
                and valuation_score >= min_valuation_score
                and financial_health_score >= min_financial_health_score
            ):
                continue

            turnaround = turnaround_map.get(row['symbol'])

            entry = {
                'symbol': row['symbol'],
                'sector': row['sector'] or 'Unknown',
                'current_price': price,
                'distance_from_low_pct': round(distance_from_low_pct, 2),
                'donchian_low_12m': round(low, 2),
                'valuation_score': valuation_score,
                'financial_health_score': financial_health_score,
                'overall_quality_score': float(row['overall_quality_score']) if row['overall_quality_score'] is not None else None,
                'quality_grade': row['quality_grade'],
                'pe_ratio': float(row['pe_ratio']) if row['pe_ratio'] is not None else None,
                'market_cap': float(row['market_cap']) if row['market_cap'] is not None else None,
                'is_turnaround': bool(turnaround and turnaround['is_turnaround']),
            }

            if turnaround and turnaround['is_turnaround']:
                entry.update({
                    'latest_quarter': turnaround['latest_quarter'],
                    'latest_net_income': turnaround['latest_net_income'],
                    'previous_quarter': turnaround['previous_quarter'],
                    'previous_net_income': turnaround['previous_net_income'],
                })
                turnaround_alerts.append(entry)
            else:
                deep_value_watch.append(entry)

        deep_value_watch.sort(key=lambda e: e['distance_from_low_pct'])
        turnaround_alerts.sort(key=lambda e: e['distance_from_low_pct'])

        return {
            'turnaround_alerts': turnaround_alerts,
            'deep_value_watch': deep_value_watch,
        }
