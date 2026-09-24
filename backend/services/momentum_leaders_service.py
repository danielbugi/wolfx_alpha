# File: backend/services/momentum_leaders_service.py
"""
Momentum Leaders — "who's still moving after making an earlier list" (distinct from Momentum
Board, which ranks TODAY's own lists). Reuses mechanism/alerts/board.py's compute() verbatim:
pool = stocks that were in any of the channel's lists (breakout/near-breakout gainers/ATR/
volume) on one of the previous BOARD_SESSIONS stored sessions; ranked by TODAY's % change among
those that closed higher. See board.py's own docstring for the full definition and what it
deliberately does not claim.

Unlike the Telegram channel's rendering (a Pillow-drawn candlestick card), the dashboard reuses
the existing SymbolHoverLink interactive chart on every row instead of building a second,
static chart image here.
"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor
import pandas as pd
import logging

logger = logging.getLogger(__name__)

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from alerts.board import compute as board_compute, BOARD_SESSIONS, GROUPS  # noqa: E402
from alerts.market_stats import to_wide  # noqa: E402

_CACHE_TTL_SECONDS = 300
_leaders_cache: Dict[str, Any] = {"data": None, "ts": 0.0}

PRICE_LOOKBACK_DAYS = 90  # calendar days -- comfortably covers the 51 trading bars compute() needs


class MomentumLeadersService:
    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def get_leaders(self) -> Dict[str, Any]:
        now = time.time()
        if _leaders_cache["data"] is not None and (now - _leaders_cache["ts"]) < _CACHE_TTL_SECONDS:
            return _leaders_cache["data"]

        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT session_date FROM digest_runs ORDER BY session_date DESC LIMIT %s", (BOARD_SESSIONS + 1,))
        session_dates = [r["session_date"] for r in cursor.fetchall()]
        if not session_dates:
            cursor.close()
            conn.close()
            data = {"session_date": None, "board": None}
            _leaders_cache["data"], _leaders_cache["ts"] = data, now
            return data

        session = session_dates[0]
        prior_dates = session_dates[1:]

        if not prior_dates:
            cursor.close()
            conn.close()
            data = {"session_date": session.isoformat(), "board": None}
            _leaders_cache["data"], _leaders_cache["ts"] = data, now
            return data

        cursor.execute("""
            SELECT session_date, symbol, category
            FROM digest_stocks
            WHERE session_date = ANY(%s) AND category = ANY(%s) AND list_ranks IS NOT NULL
        """, (prior_dates, list(GROUPS)))
        listed_rows = cursor.fetchall()

        if not listed_rows:
            cursor.close()
            conn.close()
            data = {"session_date": session.isoformat(), "board": None}
            _leaders_cache["data"], _leaders_cache["ts"] = data, now
            return data

        pool_symbols = sorted({r["symbol"] for r in listed_rows})
        cursor.execute("""
            SELECT symbol, date, open, high, low, close, volume
            FROM stock_prices
            WHERE symbol = ANY(%s) AND date <= %s AND date >= %s::date - %s::int
            ORDER BY symbol, date
        """, (pool_symbols, session, session, PRICE_LOOKBACK_DAYS))
        price_rows = cursor.fetchall()
        cursor.close()
        conn.close()

        listed = pd.DataFrame(listed_rows)
        listed["session_date"] = pd.to_datetime(listed["session_date"])

        px = pd.DataFrame(price_rows)
        if px.empty:
            data = {"session_date": session.isoformat(), "board": None}
            _leaders_cache["data"], _leaders_cache["ts"] = data, now
            return data
        px["date"] = pd.to_datetime(px["date"])
        for c in ("open", "high", "low", "close", "volume"):
            px[c] = pd.to_numeric(px[c], errors="coerce")
        wide = to_wide(px)

        board = board_compute(listed, wide, session)
        data = {"session_date": session.isoformat(), "board": _jsonable(board) if board else None}
        _leaders_cache["data"], _leaders_cache["ts"] = data, now
        return data


def _jsonable(board: Dict[str, Any]) -> Dict[str, Any]:
    """FastAPI's default encoder already handles date/Timestamp values fine, but ohlc rows carry
    tuples-or-None -- normalize those to plain lists (or None) explicitly rather than relying on
    encoder tuple-handling, which is fine either way but this keeps the shape obvious."""
    out = dict(board)
    for key in ("top", "more"):
        out[key] = [
            {**row, "ohlc": [list(bar) if bar is not None else None for bar in row.get("ohlc", [])]}
            for row in out.get(key, [])
        ]
    return out
