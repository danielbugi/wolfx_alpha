"""Sector pages: medians and breadth over members that have a value (never a filled-in zero), calendar windows, and
routes that are protected, order-safe next to /sectors/history, and 404 on an unknown sector."""
import os
import sys
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.market import get_market_data_service, get_sector_service, market_router  # noqa: E402
from services.sector_service import pct, slugify, summarize, window_starts  # noqa: E402


def test_summarize_ignores_missing_values():
    assert summarize([1.0, -2.0, None, 3.0]) == {"median": 1.0, "up_share": 66.7, "n": 3}
    assert summarize([None, None]) == {"median": None, "up_share": None, "n": 0}


def test_pct_needs_both_closes():
    assert pct(110.0, 100.0) == 10.0
    assert pct(None, 100.0) is None
    assert pct(110.0, None) is None
    assert pct(110.0, 0.0) is None


def test_windows_are_calendar_offsets():
    w = window_starts(date(2026, 9, 23))
    assert w["1w"] == date(2026, 9, 16)
    assert w["ytd"] == date(2025, 12, 31)
    assert w["1y"] == date(2025, 9, 23)


def test_slug():
    assert slugify("Financial Services") == "financial-services"
    assert slugify("Technology") == "technology"


class _FakeSectors:
    def get_sectors(self):
        return {"as_of": "2026-09-23", "sectors": [{"sector": "Technology", "slug": "technology"}]}

    def get_sector(self, slug):
        return {"sector": "Technology", "slug": slug} if slug == "technology" else None


class _FakeMarketData:
    def get_sector_history(self, days):
        return {"days": days, "sectors": []}


def _client(authenticated=True):
    app = FastAPI()
    app.include_router(market_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[get_sector_service] = lambda: _FakeSectors()
    app.dependency_overrides[get_market_data_service] = lambda: _FakeMarketData()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    return TestClient(app)


def test_sector_routes():
    c = _client()
    assert c.get("/api/market/sectors").json()["sectors"][0]["slug"] == "technology"
    assert c.get("/api/market/sectors/technology").json()["sector"] == "Technology"
    assert c.get("/api/market/sectors/nope").status_code == 404
    # the older history route still wins over the slug route
    assert c.get("/api/market/sectors/history").json() == {"days": 90, "sectors": []}
    assert _client(False).get("/api/market/sectors").status_code == 401
