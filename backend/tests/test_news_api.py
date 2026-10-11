"""Stock news API: auth required, symbol validated before any work, cached headlines served in the shared format, and
every failure mode (provider down, database problem) reported as 'unavailable' rather than an error. No network or
database: the provider and the store are fakes."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.news import get_stock_news_service, news_router  # noqa: E402
from services.news_service import StockNewsService  # noqa: E402

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self):
        self.state, self.items = {}, {}

    def news_state(self, symbol):
        return self.state.get(symbol)

    def news_recent(self, symbol, limit):
        return sorted(self.items.get(symbol, []), key=lambda i: i["published_at"], reverse=True)[:limit]

    def save_news(self, symbol, items, ok, now):
        self.items.setdefault(symbol, []).extend(items)
        self.state[symbol] = {"fetched_at": now, "ok": ok}


class BrokenStore(FakeStore):
    def news_state(self, symbol):
        raise RuntimeError("relation news_fetched does not exist")


def _item(n, image=None):
    return {"published_at": NOW - timedelta(hours=n), "headline": f"Headline {n}", "source": "benzinga",
            "url": f"https://example.com/{n}", "url_hash": f"h{n}", "image_url": image}


def _client(service=None, authenticated=True):
    app = FastAPI()
    app.include_router(news_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_stock_news_service] = lambda: service
    return TestClient(app)


def _service(store, fetch=lambda s: [_item(1), _item(3), _item(2)]):
    svc = StockNewsService(store, fetch=fetch)
    svc._news._now = lambda: NOW
    return svc


def test_requires_authentication():
    r = _client(_service(FakeStore()), authenticated=False).get("/api/news/AAPL")
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/api/news/AA%20PL", "/api/news/TOOLONGSYMBOL1", "/api/news/AAPL?limit=0", "/api/news/AAPL?limit=21"])
def test_bad_input_is_rejected_before_any_work(path):
    assert _client(_service(FakeStore())).get(path).status_code == 422


def test_returns_newest_first_with_only_public_fields():
    body = _client(_service(FakeStore())).get("/api/news/aapl?limit=2").json()
    assert body["symbol"] == "AAPL" and body["status"] == "ok" and body["stale"] is False
    assert [i["headline"] for i in body["items"]] == ["Headline 1", "Headline 2"]
    assert set(body["items"][0]) == {"published_at", "headline", "source", "url", "image_url"}  # no url_hash / article text
    assert body["items"][0]["published_at"].startswith("2026-10-11T11:00")


def test_second_request_uses_the_cache_not_the_provider():
    calls = []
    svc = _service(FakeStore(), fetch=lambda s: calls.append(s) or [_item(1)])
    client = _client(svc)
    client.get("/api/news/AAPL")
    client.get("/api/news/AAPL")
    assert calls == ["AAPL"]


def test_no_headlines_is_empty_not_an_error():
    body = _client(_service(FakeStore(), fetch=lambda s: [])).get("/api/news/AAPL").json()
    assert body["status"] == "empty" and body["items"] == []


def test_provider_failure_is_unavailable():
    def boom(_):
        raise RuntimeError("Alpaca keys are not configured")
    body = _client(_service(FakeStore(), fetch=boom)).get("/api/news/AAPL").json()
    assert body["status"] == "unavailable" and body["items"] == []


def test_database_problem_is_unavailable_not_a_500():
    r = _client(_service(BrokenStore())).get("/api/news/AAPL")
    assert r.status_code == 200 and r.json()["status"] == "unavailable"


def test_thumbnail_is_passed_through_and_missing_is_null():
    svc = _service(FakeStore(), fetch=lambda s: [_item(1, image="https://img.test/t.jpg"), _item(2)])
    items = _client(svc).get("/api/news/AAPL").json()["items"]
    assert items[0]["image_url"] == "https://img.test/t.jpg" and items[1]["image_url"] is None
