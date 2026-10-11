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


# ---- reason codes ------------------------------------------------------------------------------------------------

def test_missing_provider_keys_are_named_in_the_reason():
    def no_keys(_):
        raise RuntimeError("Alpaca keys are not configured")
    body = _client(_service(FakeStore(), fetch=no_keys)).get("/api/news/AAPL").json()
    assert body["status"] == "unavailable" and body["reason"] == "provider_keys_missing"


def test_ok_responses_have_no_reason():
    assert _client(_service(FakeStore())).get("/api/news/AAPL").json()["reason"] is None


# ---- the store works before and after migration 33 (news_items.image_url) -----------------------------------------

import services.news_service as news_mod  # noqa: E402
from services.news_service import BackendNewsStore  # noqa: E402


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        if "information_schema.columns" in sql:
            self.rows = [{"?column?": 1}] if self.conn.has_column else []
        elif sql.startswith("SELECT published_at"):
            if "image_url" in sql and "NULL AS image_url" not in sql and not self.conn.has_column:
                raise RuntimeError("column image_url does not exist")
            self.rows = [{"published_at": NOW, "headline": "H", "source": None, "url": "https://x/1", "image_url": None}]
        else:
            if "image_url" in sql and not self.conn.has_column:
                raise RuntimeError("column image_url does not exist")
            self.rows = []

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, has_column):
        self.has_column, self.sql = has_column, []

    def cursor(self, cursor_factory=None):
        return _Cursor(self)

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=False)
def fresh_column_cache(monkeypatch):
    monkeypatch.setattr(news_mod, "_image_column", {"present": False, "checked_at": 0.0})


@pytest.mark.parametrize("has_column", [False, True])
def test_store_reads_and_writes_with_or_without_the_image_column(fresh_column_cache, has_column):
    conn = _Conn(has_column)
    store = BackendNewsStore(lambda: conn)
    rows = store.news_recent("AAPL", 5)
    assert rows[0]["image_url"] is None
    store.save_news("AAPL", [{**_item(1, image="https://img.test/t.jpg")}], True, NOW)
    inserts = [q for q in conn.sql if q.startswith("INSERT INTO news_items")]
    assert inserts and all(("image_url" in q) == has_column for q in inserts)


def test_column_added_later_is_picked_up_after_the_recheck_window(fresh_column_cache, monkeypatch):
    conn = _Conn(False)
    store = BackendNewsStore(lambda: conn)
    assert store.has_image_column() is False
    conn.has_column = True
    assert store.has_image_column() is False  # still inside the recheck window
    monkeypatch.setattr(news_mod, "_IMAGE_COLUMN_RECHECK_S", 0)
    assert store.has_image_column() is True
