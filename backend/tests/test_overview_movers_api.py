"""The Overview's movers endpoints serve DiscoveryService.movers (one Screener v2 evaluation for one session) in the
dashboard's existing StockItem shape, plus the session and coverage that explain them. No database: the service is faked."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main  # noqa: E402
from auth.dependencies import CurrentUser, require_authenticated_user  # noqa: E402
from services import discovery_service as D  # noqa: E402

ROW = {"symbol": "UP", "sector": "Technology", "close": 12.3456789, "change_1d": 20.004, "volume": 1_234_567.0,
       "volume_ratio": 1.234, "dollar_vol_20": 1.2e7, "market_cap": 1.5e9}
PAYLOAD = {"session": "2026-10-23", "previous_session": "2026-10-22", "universe": 3060,
           "coverage": {"change_1d": 3055, "volume_ratio": 3054, "dollar_vol_20": 3060},
           "top_gainers": [ROW], "top_losers": [dict(ROW, symbol="DOWN", change_1d=-9.5, sector=None)],
           "unusual_volume": [dict(ROW, symbol="VOL", volume_ratio=3.0, market_cap=None, volume=None)],
           "top_gainers_total": 1200, "top_losers_total": 1700, "unusual_volume_total": 71}


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def movers(self, limit=5):
        calls.append(limit)
        return PAYLOAD
    monkeypatch.setattr(D.DiscoveryService, "movers", movers)
    main.cache.clear()
    yield calls
    main.cache.clear()


def _client():
    main.app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    return TestClient(main.app)


def test_rows_keep_the_stockitem_shape_and_missing_values_stay_null(fake):
    m = main._screener_movers(5)
    assert m["top_gainers"] == [{"symbol": "UP", "current_price": 12.3457, "price_change_pct": 20.0, "volume": 1234567,
                                 "volume_ratio": 1.23, "sector": "Technology", "market_cap": 1500000000}]
    assert m["top_losers"][0]["sector"] == "Unknown"
    assert m["unusual_volume"][0]["market_cap"] is None and m["unusual_volume"][0]["volume"] is None   # never 0
    assert m["meta"] == {"session": "2026-10-23", "previous_session": "2026-10-22", "universe": 3060,
                         "coverage": PAYLOAD["coverage"], "totals": {"top_gainers": 1200, "top_losers": 1700, "unusual_volume": 71},
                         "floor": {"min_close": 1.0, "min_dollar_vol_20": 1_000_000.0}, "min_volume_ratio": 2.0}


def test_main_page_data_carries_the_movers_session_from_one_engine_call(fake, monkeypatch):
    class Cur:
        def execute(self, *a):
            pass

        def fetchone(self):
            return {"total_symbols": 3054, "avg_volume_ratio": 0.99, "latest_date": __import__("datetime").date(2026, 10, 23)}

        def close(self):
            pass

    class Conn:
        def cursor(self, **k):
            return Cur()

        def close(self):
            pass
    monkeypatch.setattr(main, "get_database_connection", lambda: Conn())
    data = main._build_main_page_data()
    assert fake == [5]                                             # one engine evaluation for all three lists
    assert [r["symbol"] for r in data["top_gainers"]] == ["UP"]
    assert [r["symbol"] for r in data["unusual_volume"]] == ["VOL"]
    assert data["movers"]["session"] == "2026-10-23"
    assert set(data) == {"timestamp", "market_summary", "top_gainers", "top_losers", "unusual_volume", "movers"}


@pytest.mark.parametrize("path,first", [("/api/dashboard/top-gainers", "UP"), ("/api/dashboard/top-losers", "DOWN"),
                                        ("/api/dashboard/unusual-volume", "VOL")])
def test_single_list_endpoints_serve_the_same_engine_lists(fake, path, first):
    r = _client().get(path, params={"limit": 5})
    assert r.status_code == 200 and r.json()[0]["symbol"] == first
    assert _client().get(path, params={"limit": 4}).status_code == 422          # bounds unchanged


def test_the_endpoints_stay_authenticated():
    main.app.dependency_overrides.pop(require_authenticated_user, None)
    for path in ("/api/dashboard/main-page-data", "/api/dashboard/top-gainers"):
        assert TestClient(main.app).get(path).status_code == 401
