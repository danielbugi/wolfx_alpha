# File: backend/services/market_calendar_service.py
"""
US market sessions for the dashboard's market clock (sidebar): which days are trading days and when each closes,
including holidays and early closes, from the same official calendar the pipeline uses
(mechanism/shared/market_calendar.py -> Alpaca's market-calendar endpoint).

The module is loaded from its file directly: importing it as `shared.market_calendar` would run the `shared` package's
__init__, which opens the mechanism's database pool. Calendar data changes rarely, so it is kept in memory for a few
hours. The response says where the calendar came from; on the module's weekday fallback, holidays are NOT known and
the response says so (`holidays_known: false`) rather than presenting a guess as fact.
"""
import importlib.util
import logging
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger(__name__)

_CALENDAR_FILE = Path(__file__).resolve().parents[2] / "mechanism" / "shared" / "market_calendar.py"
_spec = importlib.util.spec_from_file_location("first_light_market_calendar", _CALENDAR_FILE)
market_calendar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(market_calendar)

CACHE_TTL_S = 6 * 3600
PAST_DAYS, AHEAD_DAYS = 7, 21
REGULAR_OPEN = "09:30"  # NYSE/Nasdaq regular session open (holidays and early closes affect the close, not the open)

_cache: Dict[str, Any] = {"data": None, "ts": 0.0}
_lock = threading.Lock()


def _load(today: date, fetch: Callable[[date, date], Dict[str, str]]) -> Tuple[Dict[str, str], str]:
    """{iso_date: "HH:MM" close} and its source. Alpaca directly first (no cache-file write needed in the backend
    container), then the module's own chain (on-disk cache -> weekday fallback)."""
    try:
        return fetch(today - timedelta(days=PAST_DAYS), today + timedelta(days=AHEAD_DAYS)), "alpaca"
    except Exception as e:  # noqa: BLE001 - network / credentials: fall back like the pipeline does
        log.warning("market calendar fetch failed: %s", type(e).__name__)
    sessions, source = market_calendar.get_sessions(today)
    return {d.isoformat(): close.strftime("%H:%M") for d, close in sessions.items()}, source


def get_market_calendar(now: Optional[float] = None, today: Optional[date] = None,
                        fetch: Optional[Callable[[date, date], Dict[str, str]]] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    with _lock:
        if _cache["data"] is not None and now - _cache["ts"] < CACHE_TTL_S and today is None:
            return _cache["data"]
        ny_today = today or datetime.now(market_calendar.NY).date()
        raw, source = _load(ny_today, fetch or market_calendar._fetch_alpaca)
        lo, hi = ny_today - timedelta(days=PAST_DAYS), ny_today + timedelta(days=AHEAD_DAYS)
        sessions = sorted(
            ({"date": d, "close": c} for d, c in raw.items() if lo <= date.fromisoformat(d) <= hi),
            key=lambda s: s["date"],
        )
        data = {
            "timezone": "America/New_York",
            "regular_open": REGULAR_OPEN,
            "source": source,
            "holidays_known": source != "weekday-fallback",
            "sessions": sessions,
        }
        if today is None:
            _cache["data"], _cache["ts"] = data, now
        return data
