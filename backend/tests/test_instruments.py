"""Markets pages: the backend's instrument registry matches the pipeline's updater list, changes are measured over
calendar windows (never a shorter period passed off as the requested one), and the routes are protected and validate
the symbol."""
import ast
import os
import sys
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.market import get_market_data_service, market_router  # noqa: E402
from services.instruments import GROUPS, INSTRUMENTS, find  # noqa: E402
from services.market_data_service import instrument_performance  # noqa: E402

UPDATER = os.path.join(BACKEND, "..", "mechanism", "data_updaters", "market_index_updater.py")


def _updater_symbols():
    tree = ast.parse(open(UPDATER, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "MARKET_INDEX_SYMBOLS":
            return {elt.elts[0].value for elt in node.value.elts}
    raise AssertionError("MARKET_INDEX_SYMBOLS not found in market_index_updater.py")


def test_everything_the_pipeline_fetches_is_on_the_markets_pages():
    # The registry may list instruments the pipeline doesn't fetch yet (new data sources are on hold until the new
    # database design lands); those have no rows, so the pages hide them. The other way round is a bug: fetched data
    # that no page shows.
    unlisted = _updater_symbols() - {i.symbol for i in INSTRUMENTS}
    assert not unlisted, f"add to services/instruments.py: {sorted(unlisted)}"


def test_registry_is_well_formed():
    keys = {g["key"] for g in GROUPS}
    assert all(i.group in keys for i in INSTRUMENTS)
    assert len({i.symbol for i in INSTRUMENTS}) == len(INSTRUMENTS)
    assert find("btc-usd").name == "Bitcoin"
    assert find("nope") is None


def test_performance_uses_calendar_windows_and_never_a_shorter_period():
    rows = [(date(2026, 1, 2), 100.0), (date(2026, 9, 1), 110.0), (date(2026, 10, 2), 120.0), (date(2026, 10, 9), 132.0)]
    p = instrument_performance(rows)
    assert p["1d"] == 10.0  # vs the previous close
    assert p["1w"] == 10.0  # vs the close on/before 10-02
    assert p["1m"] == 20.0  # vs the close on/before 09-09 -> 09-01
    assert p["ytd"] is None  # no close on/before 2025-12-31 in the history
    assert p["1y"] is None  # history doesn't reach a year back
    assert instrument_performance([])["1d"] is None


class _FakeService:
    def get_instruments(self):
        return {"groups": GROUPS, "instruments": []}

    def get_instrument_history(self, symbol, days):
        return [{"date": "2026-10-09", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None,
                 "donchian_high_20": None, "donchian_low_20": None}]


def _client(authenticated=True):
    app = FastAPI()
    app.include_router(market_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[get_market_data_service] = lambda: _FakeService()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    return TestClient(app)


def test_routes_require_auth():
    assert _client(False).get("/api/market/instruments").status_code == 401
    assert _client(False).get("/api/market/instruments/GC=F/history").status_code == 401


def test_instruments_route_lists_groups():
    body = _client().get("/api/market/instruments").json()
    assert [g["key"] for g in body["groups"]][0] == "indices"


def test_history_for_known_symbol_and_404_for_unknown():
    client = _client()
    body = client.get("/api/market/instruments/GC=F/history").json()
    assert body["name"] == "Gold" and body["unit"] == "/oz" and len(body["bars"]) == 1
    assert client.get("/api/market/instruments/NOPE/history").status_code == 404
    assert client.get("/api/market/instruments/GC=F/history?days=1").status_code == 422
