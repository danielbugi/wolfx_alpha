"""Market calendar for the sidebar clock: official calendar first, honest about the weekday fallback (holidays unknown),
cached in memory, and served behind auth. No network: the provider and the fallback are faked."""
import os
import sys
from datetime import date, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import services.market_calendar_service as mcs  # noqa: E402
from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.market import market_router  # noqa: E402

TODAY = date(2026, 11, 25)  # Wednesday before Thanksgiving


def _alpaca(start, end):
    # Thanksgiving (11-26) is absent; Black Friday (11-27) closes early.
    return {"2026-11-24": "16:00", "2026-11-25": "16:00", "2026-11-27": "13:00", "2026-11-30": "16:00", "2025-01-01": "16:00"}


def test_official_calendar_keeps_holidays_and_early_closes():
    data = mcs.get_market_calendar(today=TODAY, fetch=_alpaca)
    days = {s["date"]: s["close"] for s in data["sessions"]}
    assert data["source"] == "alpaca" and data["holidays_known"] is True and data["regular_open"] == "09:30"
    assert "2026-11-26" not in days and days["2026-11-27"] == "13:00"
    assert "2025-01-01" not in days  # outside the window


def test_weekday_fallback_says_holidays_are_unknown(monkeypatch):
    def boom(start, end):
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET not set")
    fallback = {date(2026, 11, 26): datetime(2026, 11, 26, 16, 0, tzinfo=mcs.market_calendar.NY)}
    monkeypatch.setattr(mcs.market_calendar, "get_sessions", lambda today: (fallback, "weekday-fallback"))
    data = mcs.get_market_calendar(today=TODAY, fetch=boom)
    assert data["source"] == "weekday-fallback" and data["holidays_known"] is False
    assert data["sessions"] == [{"date": "2026-11-26", "close": "16:00"}]


def test_live_requests_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(mcs, "_cache", {"data": None, "ts": 0.0})
    monkeypatch.setattr(mcs.market_calendar, "_fetch_alpaca", lambda s, e: calls.append(1) or {"2026-11-25": "16:00"})
    mcs.get_market_calendar(now=1000.0)
    mcs.get_market_calendar(now=1000.0 + 60)
    assert len(calls) == 1


def _client(authenticated=True):
    app = FastAPI()
    app.include_router(market_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    return TestClient(app)


def test_calendar_route_requires_auth_and_returns_sessions(monkeypatch):
    assert _client(authenticated=False).get("/api/market/calendar").status_code == 401
    monkeypatch.setattr(mcs, "_cache", {"data": None, "ts": 0.0})
    monkeypatch.setattr(mcs.market_calendar, "_fetch_alpaca", lambda s, e: {datetime.now(mcs.market_calendar.NY).date().isoformat(): "16:00"})
    body = _client().get("/api/market/calendar").json()
    assert body["holidays_known"] is True and len(body["sessions"]) == 1
