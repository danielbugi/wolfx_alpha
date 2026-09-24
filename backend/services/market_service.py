# File: backend/services/market_service.py
"""
Market Service - Advanced market data processing and analysis
Handles complex calculations and business logic for market data
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import logging
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MarketFilter:
    """Data class for market filtering criteria"""
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_volume: Optional[int] = None
    min_market_cap: Optional[int] = None
    max_market_cap: Optional[int] = None
    sectors: Optional[List[str]] = None
    min_price_change: Optional[float] = None
    max_price_change: Optional[float] = None
    min_volume_ratio: Optional[float] = None
    min_rsi: Optional[float] = None
    max_rsi: Optional[float] = None
    quality_grade: Optional[List[str]] = None


def _num(value, cast):
    """SQL NULL (e.g. MIN/MAX over zero rows) stays None instead of raising or becoming 0."""
    return None if value is None else cast(value)


class MarketService:
    """Advanced market data service with sophisticated analytics"""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def get_market_overview(self) -> Dict[str, Any]:
        """Get comprehensive market overview statistics"""
        try:
            conn = self.get_db_connection()
            if not conn:
                raise Exception("Database connection failed")

            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Market breadth analysis
            # NOTE: technical_indicators/daily_fundamentals are joined via
            # LATERAL against the already-deduped one-row-per-symbol
            # latest_prices CTE, not against the raw 5-day stock_prices window
            # -- the old version ran a correlated MAX(date) subquery per row
            # of that window (~5x more rows than symbols), for both tables, in
            # both queries below. This is the same per-symbol-lookup anti-
            # pattern already fixed for the dashboard's top-gainers/losers/
            # unusual-volume queries in main.py (see CLAUDE.md's 2026-09-19
            # changelog) -- just never applied here.
            cursor.execute("""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (sp.symbol)
                        sp.symbol,
                        sp.close,
                        sp.volume,
                        LAG(sp.close) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_close
                    FROM stock_prices sp
                    WHERE sp.date >= CURRENT_DATE - INTERVAL '5 days'
                    ORDER BY sp.symbol, sp.date DESC
                ),
                latest_data AS (
                    SELECT
                        lp.symbol,
                        lp.close,
                        lp.volume,
                        lp.prev_close,
                        ti.volume_ratio,
                        ti.rsi_14,
                        df.sector,
                        df.market_cap,
                        df.quality_grade
                    FROM latest_prices lp
                    LEFT JOIN LATERAL (
                        SELECT volume_ratio, rsi_14
                        FROM technical_indicators
                        WHERE symbol = lp.symbol
                        ORDER BY date DESC
                        LIMIT 1
                    ) ti ON true
                    LEFT JOIN LATERAL (
                        SELECT sector, market_cap, quality_grade
                        FROM daily_fundamentals
                        WHERE symbol = lp.symbol
                        ORDER BY date DESC
                        LIMIT 1
                    ) df ON true
                )
                SELECT
                    COUNT(*) as total_stocks,
                    COUNT(CASE WHEN prev_close IS NOT NULL AND ((close - prev_close) / prev_close * 100) > 0 THEN 1 END) as gainers,
                    COUNT(CASE WHEN prev_close IS NOT NULL AND ((close - prev_close) / prev_close * 100) < 0 THEN 1 END) as losers,
                    COUNT(CASE WHEN volume_ratio >= 2.0 THEN 1 END) as unusual_volume_count,
                    COUNT(CASE WHEN rsi_14 > 70 THEN 1 END) as overbought_count,
                    COUNT(CASE WHEN rsi_14 < 30 THEN 1 END) as oversold_count,
                    AVG(volume_ratio) as avg_volume_ratio,
                    AVG(rsi_14) as avg_rsi,
                    SUM(market_cap) as total_market_cap
                FROM latest_data
                WHERE close >= 1.0  -- Filter penny stocks
            """)

            market_stats = cursor.fetchone()

            # Sector analysis
            cursor.execute("""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (sp.symbol)
                        sp.symbol,
                        sp.close,
                        LAG(sp.close) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_close
                    FROM stock_prices sp
                    WHERE sp.date >= CURRENT_DATE - INTERVAL '5 days'
                    ORDER BY sp.symbol, sp.date DESC
                ),
                latest_data AS (
                    SELECT
                        lp.symbol,
                        lp.close,
                        lp.prev_close,
                        df.sector
                    FROM latest_prices lp
                    LEFT JOIN LATERAL (
                        SELECT sector
                        FROM daily_fundamentals
                        WHERE symbol = lp.symbol
                        ORDER BY date DESC
                        LIMIT 1
                    ) df ON true
                )
                SELECT
                    COALESCE(sector, 'Unknown') as sector,
                    COUNT(*) as stock_count,
                    AVG(CASE 
                        WHEN prev_close > 0 
                        THEN ((close - prev_close) / prev_close * 100)
                        ELSE 0 
                    END) as avg_performance
                FROM latest_data
                WHERE close >= 1.0 AND prev_close IS NOT NULL
                GROUP BY sector
                ORDER BY avg_performance DESC
            """)

            sector_performance = cursor.fetchall()

            cursor.close()
            conn.close()

            return {
                "market_breadth": {
                    "total_stocks": market_stats['total_stocks'],
                    "gainers": market_stats['gainers'],
                    "losers": market_stats['losers'],
                    "advance_decline_ratio": round(market_stats['gainers'] / max(market_stats['losers'], 1), 2),
                    "unusual_volume_count": market_stats['unusual_volume_count'],
                    "overbought_count": market_stats['overbought_count'],
                    "oversold_count": market_stats['oversold_count']
                },
                "market_indicators": {
                    "avg_volume_ratio": round(float(market_stats['avg_volume_ratio']), 2) if market_stats[
                        'avg_volume_ratio'] else 1.0,
                    "avg_rsi": round(float(market_stats['avg_rsi']), 2) if market_stats['avg_rsi'] else 50.0,
                    "total_market_cap": int(market_stats['total_market_cap']) if market_stats['total_market_cap'] else 0
                },
                "sector_performance": [
                    {
                        "sector": sector['sector'],
                        "stock_count": sector['stock_count'],
                        "avg_performance": round(float(sector['avg_performance']), 2) if sector[
                            'avg_performance'] else 0
                    }
                    for sector in sector_performance
                ]
            }

        except Exception as e:
            logger.error(f"Error getting market overview: {e}")
            raise

    def get_filtered_stocks(self, filter_criteria: MarketFilter, limit: int = 100) -> List[Dict[str, Any]]:
        """Get stocks matching sophisticated filter criteria - FIXED VERSION"""
        try:
            conn = self.get_db_connection()
            if not conn:
                raise Exception("Database connection failed")

            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Build dynamic WHERE clause for the CTE
            cte_where_conditions = ["sp.date >= CURRENT_DATE - INTERVAL '5 days'"]
            cte_params = []

            # Build dynamic WHERE clause for the final query (using CTE column names)
            final_where_conditions = ["current_price >= 1.0", "prev_close IS NOT NULL"]  # Always filter penny stocks
            final_params = []

            if filter_criteria.min_price:
                final_where_conditions.append("current_price >= %s")
                final_params.append(filter_criteria.min_price)

            if filter_criteria.max_price:
                final_where_conditions.append("current_price <= %s")
                final_params.append(filter_criteria.max_price)

            if filter_criteria.min_volume:
                final_where_conditions.append("volume >= %s")
                final_params.append(filter_criteria.min_volume)

            if filter_criteria.min_market_cap:
                final_where_conditions.append("market_cap >= %s")
                final_params.append(filter_criteria.min_market_cap)

            if filter_criteria.max_market_cap:
                final_where_conditions.append("market_cap <= %s")
                final_params.append(filter_criteria.max_market_cap)

            if filter_criteria.sectors:
                placeholders = ','.join(['%s'] * len(filter_criteria.sectors))
                final_where_conditions.append(f"sector IN ({placeholders})")
                final_params.extend(filter_criteria.sectors)

            if filter_criteria.min_volume_ratio:
                final_where_conditions.append("volume_ratio >= %s")
                final_params.append(filter_criteria.min_volume_ratio)

            if filter_criteria.min_rsi:
                final_where_conditions.append("rsi_14 >= %s")
                final_params.append(filter_criteria.min_rsi)

            if filter_criteria.max_rsi:
                final_where_conditions.append("rsi_14 <= %s")
                final_params.append(filter_criteria.max_rsi)

            if filter_criteria.quality_grade:
                placeholders = ','.join(['%s'] * len(filter_criteria.quality_grade))
                final_where_conditions.append(f"quality_grade IN ({placeholders})")
                final_params.extend(filter_criteria.quality_grade)

            cte_where_clause = " AND ".join(cte_where_conditions)
            final_where_clause = " AND ".join(final_where_conditions)

            # Combine parameters
            all_params = cte_params + final_params

            # Build the main query with fixed column references
            # Same LATERAL-per-symbol pattern as get_market_overview() above --
            # ti/df are looked up against the deduped one-row-per-symbol
            # latest_prices CTE instead of the raw multi-day stock_prices
            # window, avoiding a correlated MAX(date) subquery per pre-dedup row.
            query = f"""
                WITH latest_prices AS (
                    SELECT DISTINCT ON (sp.symbol)
                        sp.symbol,
                        sp.date,
                        sp.close as current_price,
                        sp.volume,
                        LAG(sp.close) OVER (PARTITION BY sp.symbol ORDER BY sp.date) as prev_close
                    FROM stock_prices sp
                    WHERE {cte_where_clause}
                    ORDER BY sp.symbol, sp.date DESC
                ),
                latest_data AS (
                    SELECT
                        lp.symbol,
                        lp.date,
                        lp.current_price,
                        lp.volume,
                        lp.prev_close,
                        ti.volume_ratio,
                        ti.rsi_14,
                        ti.donchian_high_20,
                        ti.donchian_low_20,
                        df.sector,
                        df.market_cap,
                        df.pe_ratio,
                        df.quality_grade,
                        df.overall_quality_score
                    FROM latest_prices lp
                    LEFT JOIN LATERAL (
                        SELECT volume_ratio, rsi_14, donchian_high_20, donchian_low_20
                        FROM technical_indicators
                        WHERE symbol = lp.symbol
                        ORDER BY date DESC
                        LIMIT 1
                    ) ti ON true
                    LEFT JOIN LATERAL (
                        SELECT sector, market_cap, pe_ratio, quality_grade, overall_quality_score
                        FROM daily_fundamentals
                        WHERE symbol = lp.symbol
                        ORDER BY date DESC
                        LIMIT 1
                    ) df ON true
                )
                SELECT
                    symbol,
                    current_price,
                    volume,
                    CASE 
                        WHEN prev_close > 0 
                        THEN ((current_price - prev_close) / prev_close * 100)
                        ELSE 0 
                    END as price_change_pct,
                    COALESCE(volume_ratio, 1.0) as volume_ratio,
                    rsi_14,
                    COALESCE(sector, 'Unknown') as sector,
                    market_cap,
                    pe_ratio,
                    quality_grade,
                    overall_quality_score,
                    donchian_high_20,
                    donchian_low_20,
                    -- Calculate technical score. NULLIF guards a real, hit-in-production
                    -- case: a symbol whose 20-day high/low channel is perfectly flat
                    -- (donchian_high_20 = donchian_low_20) previously crashed this whole
                    -- query with a SQL division-by-zero the moment such a row appeared
                    -- in the top-N result set -- not a rare edge case, since a flat
                    -- channel is exactly what "before a breakout" looks like.
                    CASE
                        WHEN donchian_high_20 > 0 AND donchian_low_20 > 0 AND donchian_high_20 <> donchian_low_20
                        THEN ((current_price - donchian_low_20) / NULLIF(donchian_high_20 - donchian_low_20, 0) * 100)
                        ELSE 50
                    END as donchian_position
                FROM latest_data
                WHERE {final_where_clause}
            """

            # Add price change filters if specified (these need to be in the final query since they use calculated fields)
            if filter_criteria.min_price_change:
                query += " AND ((current_price - prev_close) / prev_close * 100) >= %s"
                all_params.append(filter_criteria.min_price_change)

            if filter_criteria.max_price_change:
                query += " AND ((current_price - prev_close) / prev_close * 100) <= %s"
                all_params.append(filter_criteria.max_price_change)

            # Add sorting and limit
            query += " ORDER BY price_change_pct DESC LIMIT %s"
            all_params.append(limit)

            cursor.execute(query, all_params)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            # Format results
            formatted_results = []
            for row in results:
                formatted_results.append({
                    "symbol": row['symbol'],
                    "current_price": float(row['current_price']),
                    "price_change_pct": round(float(row['price_change_pct']), 2),
                    "volume": int(row['volume']) if row['volume'] else 0,
                    "volume_ratio": round(float(row['volume_ratio']), 2) if row['volume_ratio'] else 1.0,
                    "rsi_14": round(float(row['rsi_14']), 2) if row['rsi_14'] else None,
                    "sector": row['sector'] or "Unknown",
                    "market_cap": int(row['market_cap']) if row['market_cap'] else None,
                    "pe_ratio": round(float(row['pe_ratio']), 2) if row['pe_ratio'] else None,
                    "quality_grade": row['quality_grade'],
                    "quality_score": round(float(row['overall_quality_score']), 1) if row[
                        'overall_quality_score'] else None,
                    "donchian_position": round(float(row['donchian_position']), 1) if row[
                        'donchian_position'] else 50.0,
                    "technical_strength": self._calculate_technical_strength(
                        row['rsi_14'],
                        row['volume_ratio'],
                        row['donchian_position']
                    )
                })

            return formatted_results

        except Exception as e:
            logger.error(f"Error filtering stocks: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            raise

    def get_available_filters(self) -> Dict[str, Any]:
        """Get available filter options (sectors, price ranges, etc.)"""
        try:
            conn = self.get_db_connection()
            if not conn:
                raise Exception("Database connection failed")

            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Get sectors
            cursor.execute("""
                SELECT DISTINCT sector, COUNT(*) as stock_count
                FROM daily_fundamentals 
                WHERE sector IS NOT NULL 
                AND date = (SELECT MAX(date) FROM daily_fundamentals)
                GROUP BY sector
                ORDER BY stock_count DESC
            """)
            sectors = cursor.fetchall()

            # Get price ranges
            cursor.execute("""
                SELECT 
                    MIN(close) as min_price,
                    MAX(close) as max_price,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY close) as price_25th,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY close) as price_median,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY close) as price_75th
                FROM stock_prices 
                WHERE date = (SELECT MAX(date) FROM stock_prices)
                AND close >= 1.0
            """)
            price_stats = cursor.fetchone()

            # Get market cap ranges
            cursor.execute("""
                SELECT 
                    MIN(market_cap) as min_market_cap,
                    MAX(market_cap) as max_market_cap,
                    PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY market_cap) as mc_25th,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY market_cap) as mc_median,
                    PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY market_cap) as mc_75th
                FROM daily_fundamentals 
                WHERE date = (SELECT MAX(date) FROM daily_fundamentals)
                AND market_cap IS NOT NULL
            """)
            market_cap_stats = cursor.fetchone()

            cursor.close()
            conn.close()

            return {
                "sectors": [
                    {
                        "name": sector['sector'],
                        "stock_count": sector['stock_count']
                    }
                    for sector in sectors
                ],
                # Aggregates over an empty table come back as NULL: report "unknown" (None), never
                # crash on float(None) and never invent a 0 that looks like a real price/market cap.
                "price_ranges": {
                    "min": _num(price_stats['min_price'], float),
                    "max": _num(price_stats['max_price'], float),
                    "percentiles": {
                        "25th": _num(price_stats['price_25th'], float),
                        "median": _num(price_stats['price_median'], float),
                        "75th": _num(price_stats['price_75th'], float)
                    }
                },
                "market_cap_ranges": {
                    "min": _num(market_cap_stats['min_market_cap'], int),
                    "max": _num(market_cap_stats['max_market_cap'], int),
                    "percentiles": {
                        "25th": _num(market_cap_stats['mc_25th'], int),
                        "median": _num(market_cap_stats['mc_median'], int),
                        "75th": _num(market_cap_stats['mc_75th'], int)
                    }
                },
                "quality_grades": ["A", "B", "C", "D", "F"],
                "technical_ranges": {
                    "rsi": {"min": 0, "max": 100, "oversold": 30, "overbought": 70},
                    "volume_ratio": {"min": 0.1, "max": 10.0, "normal": 1.0, "unusual": 2.0}
                }
            }

        except Exception as e:
            logger.error(f"Error getting filter options: {e}")
            raise

    def _calculate_technical_strength(self, rsi: Optional[float], volume_ratio: Optional[float],
                                      donchian_position: Optional[float]) -> str:
        """Calculate overall technical strength rating"""
        score = 0
        factors = 0

        if rsi is not None:
            if rsi > 70:
                score += 1  # Overbought but strong
            elif rsi > 50:
                score += 2  # Bullish momentum
            elif rsi > 30:
                score += 1  # Neutral
            factors += 1

        if volume_ratio is not None:
            if volume_ratio > 2.0:
                score += 2  # High volume interest
            elif volume_ratio > 1.5:
                score += 1  # Above average volume
            factors += 1

        if donchian_position is not None:
            if donchian_position > 80:
                score += 2  # Near breakout high
            elif donchian_position > 60:
                score += 1  # Upper range
            elif donchian_position < 20:
                score -= 1  # Lower range
            factors += 1

        if factors == 0:
            return "Unknown"

        avg_score = score / factors

        if avg_score >= 1.5:
            return "Strong"
        elif avg_score >= 1.0:
            return "Moderate"
        elif avg_score >= 0.5:
            return "Neutral"
        else:
            return "Weak"