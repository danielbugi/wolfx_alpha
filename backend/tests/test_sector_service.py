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


# ---------------------------------------------------------------- S3: provenance and date anchoring
from services import sector_service as SS  # noqa: E402


def _svc_with(stocks, as_of="2026-10-16"):
    svc = SS.SectorService(lambda: None)
    svc._load = lambda: {"as_of": as_of, "stocks": stocks}
    svc._equal_weight_indices = lambda days, sector=None: {"sectors": {}, "spx": []}
    return svc


def _stock(symbol, sector, industry="Software", close=10.0, change=1.0, above=True):
    ch = {w: change for w in SS.WINDOWS}
    return {"symbol": symbol, "sector": sector, "industry": industry, "close": close, "change_pct": ch, "market_cap": 1_000_000,
            "pe_ratio": None, "quality_grade": None, "rsi_14": None, "volume_ratio": None, "above_sma50": above}


def test_every_response_states_membership_is_current_not_point_in_time():
    svc = _svc_with([_stock("AAA", "Technology"), _stock("BBB", "Technology", close=None, change=None, above=None)])
    for payload in (svc.get_sectors(), svc.get_sector("technology")):
        m = payload["membership"]
        assert m["basis"] == "current" and m["point_in_time"] is False and "not a record" in m["note"]
    s = svc.get_sectors()["sectors"][0]
    assert (s["stock_count"], s["priced_count"]) == (2, 1)            # a member without a close is counted as unpriced
    assert s["change"]["1m"] == {"median": 1.0, "up_share": 100.0, "n": 1}  # never filled with a zero
    assert s["above_sma50_pct"] == 100.0


def test_sector_dates_are_anchored_to_the_newest_price_session_not_the_wall_clock():
    src = open(SS.__file__, encoding="utf-8").read()
    assert "CURRENT_DATE" not in src and "NOW()" not in src and "date.today" not in src and "datetime.now" not in src


def test_sector_routes_are_read_only_validate_the_slug_and_keep_auth():
    c = _client()
    assert c.get("/api/market/sectors/Technology").status_code == 422          # slugs are lower-case
    assert c.get("/api/market/sectors/tech;drop").status_code == 422
    assert c.get("/api/market/sectors/" + "a" * 61).status_code == 422
    assert c.get("/api/market/sectors/zz-unknown").status_code == 404
    for method in ("post", "put", "patch", "delete"):
        assert getattr(c, method)("/api/market/sectors").status_code == 405
        assert getattr(c, method)("/api/market/sectors/technology").status_code == 405
    anon = _client(False)
    assert anon.get("/api/market/sectors/technology").status_code == 401


def test_sector_service_only_reads():
    import re
    src = open(SS.__file__, encoding="utf-8").read()
    sql = " ".join(re.findall(r'"""([\s\S]*?)"""|"([^"]*)"', src) and [a or b for a, b in re.findall(r'"""([\s\S]*?)"""|"([^"]*)"', src)])
    assert not re.search(r"(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|DROP|ALTER|GRANT)", sql)
    assert "commit()" not in src


def test_no_price_data_gives_an_empty_answer_not_an_error():
    svc = SS.SectorService(lambda: None)
    svc._load = lambda: {"as_of": None, "stocks": []}
    svc._equal_weight_indices = lambda days, sector=None: {"sectors": {}, "spx": []}
    out = svc.get_sectors()
    assert out["as_of"] is None and out["sectors"] == [] and out["membership"]["point_in_time"] is False
    assert svc.get_sector("technology") is None
