# File: backend/services/stock_service.py
"""
Stock Service - Per-symbol data assembly (price history, technicals, fundamentals,
quarterly financials, breakout history) for the stock detail API
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class StockService:
    """Fetches and assembles all DB-backed data for a single symbol"""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def symbol_exists(self, symbol: str) -> bool:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM daily_fundamentals WHERE symbol = %s "
                "UNION SELECT 1 FROM stock_prices WHERE symbol = %s LIMIT 1",
                (symbol, symbol)
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def get_price_history(self, symbol: str, days: int = 180) -> List[Dict[str, Any]]:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT sp.date, sp.open, sp.high, sp.low, sp.close, sp.volume,
                       ti.donchian_high_20, ti.donchian_low_20
                FROM stock_prices sp
                LEFT JOIN technical_indicators ti
                    ON ti.symbol = sp.symbol AND ti.date = sp.date
                WHERE sp.symbol = %s
                ORDER BY sp.date DESC
                LIMIT %s
            """, (symbol, days))
            rows = cursor.fetchall()

            return [
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "open": float(row["open"]) if row["open"] is not None else None,
                    "high": float(row["high"]) if row["high"] is not None else None,
                    "low": float(row["low"]) if row["low"] is not None else None,
                    "close": float(row["close"]),
                    "volume": int(row["volume"]) if row["volume"] is not None else None,
                    "donchian_high_20": float(row["donchian_high_20"]) if row["donchian_high_20"] is not None else None,
                    "donchian_low_20": float(row["donchian_low_20"]) if row["donchian_low_20"] is not None else None,
                }
                for row in reversed(rows)
            ]
        finally:
            conn.close()

    def get_technical_snapshot(self, symbol: str) -> Dict[str, Any]:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("""
                SELECT sp.close AS current_price, sp.volume, sp.date,
                       LAG(sp.close) OVER (ORDER BY sp.date) AS prev_close
                FROM stock_prices sp
                WHERE sp.symbol = %s
                ORDER BY sp.date DESC
                LIMIT 2
            """, (symbol,))
            price_rows = cursor.fetchall()
            latest_price = price_rows[0] if price_rows else None

            cursor.execute("""
                SELECT * FROM technical_indicators
                WHERE symbol = %s ORDER BY date DESC LIMIT 1
            """, (symbol,))
            daily = cursor.fetchone()

            cursor.execute("""
                SELECT * FROM weekly_technical_indicators
                WHERE symbol = %s ORDER BY week_ending_date DESC LIMIT 1
            """, (symbol,))
            weekly = cursor.fetchone()

            cursor.execute("""
                SELECT * FROM monthly_technical_indicators
                WHERE symbol = %s ORDER BY month_ending_date DESC LIMIT 1
            """, (symbol,))
            monthly = cursor.fetchone()

            current_price = float(latest_price["current_price"]) if latest_price else None
            prev_close = float(latest_price["prev_close"]) if latest_price and latest_price["prev_close"] else None
            price_change_pct = (
                round((current_price - prev_close) / prev_close * 100, 2)
                if current_price is not None and prev_close else 0.0
            )

            return {
                "current_price": current_price,
                "price_change_pct": price_change_pct,
                "volume": int(latest_price["volume"]) if latest_price and latest_price["volume"] else None,
                "as_of_date": latest_price["date"].strftime("%Y-%m-%d") if latest_price else None,
                "daily": _row_to_dict(daily),
                "weekly": _row_to_dict(weekly),
                "monthly": _row_to_dict(monthly),
            }
        finally:
            conn.close()

    def get_fundamentals_snapshot(self, symbol: str) -> Dict[str, Any]:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT * FROM daily_fundamentals
                WHERE symbol = %s ORDER BY date DESC LIMIT 1
            """, (symbol,))
            row = cursor.fetchone()
            return _row_to_dict(row) or {}
        finally:
            conn.close()

    def get_quarterly_financials(self, symbol: str, quarters: int = 8) -> List[Dict[str, Any]]:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT quarter, fiscal_year, fiscal_quarter, revenue, net_income, eps,
                       gross_margin, operating_margin, net_margin,
                       revenue_growth_yoy, eps_growth_yoy, roe, roa,
                       debt_to_equity, current_ratio, free_cash_flow
                FROM quarterly_fundamentals
                WHERE symbol = %s
                ORDER BY quarter DESC
                LIMIT %s
            """, (symbol, quarters))
            rows = cursor.fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def get_breakout_history(self, symbol: str) -> Dict[str, Any]:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT date, breakout_type, entry_price, success,
                       max_gain_10d, max_loss_10d, days_to_peak
                FROM breakouts
                WHERE symbol = %s
                ORDER BY date DESC
            """, (symbol,))
            rows = cursor.fetchall()

            entries = [
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "breakout_type": row["breakout_type"],
                    "entry_price": float(row["entry_price"]) if row["entry_price"] is not None else None,
                    "success": row["success"],
                    "max_gain_10d": float(row["max_gain_10d"]) if row["max_gain_10d"] is not None else None,
                    "max_loss_10d": float(row["max_loss_10d"]) if row["max_loss_10d"] is not None else None,
                    "days_to_peak": row["days_to_peak"],
                }
                for row in rows
            ]

            decided = [e for e in entries if e["success"] is not None]
            wins = [e for e in decided if e["success"]]
            gains = [e["max_gain_10d"] for e in entries if e["max_gain_10d"] is not None]
            losses = [e["max_loss_10d"] for e in entries if e["max_loss_10d"] is not None]

            return {
                "total_breakouts": len(entries),
                "win_rate": round(len(wins) / len(decided) * 100, 1) if decided else None,
                "avg_gain_10d": round(sum(gains) / len(gains), 2) if gains else None,
                "avg_loss_10d": round(sum(losses) / len(losses), 2) if losses else None,
                "entries": entries,
            }
        finally:
            conn.close()

    def search_symbols(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                WITH latest_fundamentals AS (
                    SELECT DISTINCT ON (symbol) symbol, sector
                    FROM daily_fundamentals
                    ORDER BY symbol, date DESC
                ),
                latest_prices AS (
                    SELECT DISTINCT ON (symbol) symbol, close
                    FROM stock_prices
                    ORDER BY symbol, date DESC
                )
                SELECT lf.symbol, lf.sector, lp.close AS current_price
                FROM latest_fundamentals lf
                LEFT JOIN latest_prices lp ON lp.symbol = lf.symbol
                WHERE lf.symbol ILIKE %s
                ORDER BY lf.symbol
                LIMIT %s
            """, (f"{query.upper()}%", limit))
            rows = cursor.fetchall()
            return [
                {
                    "symbol": row["symbol"],
                    "sector": row["sector"],
                    "current_price": float(row["current_price"]) if row["current_price"] is not None else None,
                }
                for row in rows
            ]
        finally:
            conn.close()


def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    """Convert a RealDictRow to a plain, JSON-safe dict (Decimal/date -> float/str)"""
    if row is None:
        return None
    result = {}
    for key, value in dict(row).items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif hasattr(value, "__float__") and not isinstance(value, (int, float, bool)):
            result[key] = float(value)
        else:
            result[key] = value
    return result
