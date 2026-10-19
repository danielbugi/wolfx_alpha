"""The price-history response carries the recorded discontinuities inside its window, so a performance chart never reads
a return across an unadjusted split (additive field: older clients ignore it)."""
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.stock import get_stock_service, stock_router  # noqa: E402


class _Svc:
    def __init__(self):
        self.asked = []

    def get_price_history(self, symbol, days):
        return [{"date": "2026-05-01", "close": 10.0}, {"date": "2026-10-16", "close": 12.0}]

    def get_price_discontinuities(self, symbol, from_date):
        self.asked.append((symbol, from_date))
        return [{"date": "2026-06-02", "kind": "split", "ratio": 0.5}]


def test_price_history_includes_discontinuities_from_the_first_returned_bar():
    svc = _Svc()
    app = FastAPI()
    app.include_router(stock_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "collaborator")
    app.dependency_overrides[get_stock_service] = lambda: svc
    body = TestClient(app).get("/api/stock/abc/price-history?days=300").json()
    assert body["bars"][0]["date"] == "2026-05-01" and body["discontinuities"] == [{"date": "2026-06-02", "kind": "split", "ratio": 0.5}]
    assert svc.asked == [("ABC", "2026-05-01")]
