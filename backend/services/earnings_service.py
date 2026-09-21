# File: backend/services/earnings_service.py
"""
On-demand earnings-date lookups (for chart markers), fetched from yfinance.

Deliberately isolated from the DB-backed StockService / get_database_connection
path: this is a per-symbol, per-view lookup (not part of the daily batch
pipeline in mechanism/), so it owns its own small in-memory TTL cache instead
of touching Postgres or main.py's dashboard cache.

No other data source in this codebase can supply this today: quarterly_fundamentals
only stores fiscal quarter END dates (not report/announcement dates), and Tiingo's
/statements endpoint doesn't expose a report-date field either -- and is Dow-30-only
on the current plan regardless (see mechanism/shared/tiingo_client.py).
"""

import logging
import math
import threading
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_SUCCESS_TTL_SECONDS = 60 * 60       # earnings dates change rarely once known
_FAILURE_TTL_SECONDS = 10 * 60       # don't let a transient Yahoo hiccup stay "stuck" for an hour

_cache_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}  # symbol -> {"data": [...], "ts": datetime, "ok": bool}


def _clean_float(value: Any) -> Any:
    """NaN/None -> None, everything else -> plain float (JSON can't encode NaN)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _fetch_from_yfinance(symbol: str, limit: int) -> List[Dict[str, Any]]:
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed -- cannot fetch earnings dates")
        return []

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.get_earnings_dates(limit=limit)
        if df is None or df.empty:
            return []

        rows = []
        for idx, row in df.iterrows():
            rows.append({
                "date": idx.strftime("%Y-%m-%d"),
                "eps_estimate": _clean_float(row.get("EPS Estimate")),
                "eps_actual": _clean_float(row.get("Reported EPS")),
                "surprise_pct": _clean_float(row.get("Surprise(%)")),
            })
        rows.sort(key=lambda r: r["date"])
        return rows
    except Exception as e:
        logger.warning(f"Earnings lookup failed for {symbol}: {e}")
        return []


def get_earnings_dates(symbol: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Cached earnings-date lookup. Always returns a list (never raises) --
    missing/failed earnings data must never break the chart it feeds."""
    now = datetime.now()

    with _cache_lock:
        cached = _cache.get(symbol)
        if cached:
            ttl = _SUCCESS_TTL_SECONDS if cached["ok"] else _FAILURE_TTL_SECONDS
            if (now - cached["ts"]).total_seconds() < ttl:
                return cached["data"]

    data = _fetch_from_yfinance(symbol, limit)

    with _cache_lock:
        _cache[symbol] = {"data": data, "ts": now, "ok": bool(data)}

    return data
