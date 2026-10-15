"""Forward tracker dashboard API: authenticated, GET-only, read-only transaction on every request, explicit `available: false` instead of errors, a short shared cache, and nothing about the
database in an error response. No database needed: the aggregation is injected; its correctness against the stored rows is covered by mechanism/forward_collection/tests/test_paper_dashboard_db.py."""
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
sys.path.append(os.path.join(ROOT, "mechanism"))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.forward_tracker import forward_tracker_router, get_forward_tracker_service  # noqa: E402
from services.forward_tracker_service import ForwardTrackerService  # noqa: E402

ROUTE = "/api/lab/forward-tracker"
GOOD = {"schema": "forward_tracker_dashboard_v1", "source": "forward", "available": True, "sessions_processed": 3}


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, *a):
        self.log.append(sql)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.log, self.rolled_back, self.closed = [], 0, 0

    def cursor(self):
        return FakeCursor(self.log)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1


def _client(service=None, authenticated=True):
    app = FastAPI()
    app.include_router(forward_tracker_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_forward_tracker_service] = lambda: service
    return TestClient(app)


def test_the_route_requires_authentication():
    r = _client(service=object(), authenticated=False).get(ROUTE)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_authenticated"


def test_only_get_exists():
    c = _client(ForwardTrackerService(lambda: FakeConn(), builder=lambda cur: GOOD))
    for method in ("post", "put", "patch", "delete"):
        assert getattr(c, method)(ROUTE).status_code in (404, 405)


def test_a_request_runs_in_a_read_only_transaction_rolls_back_and_returns_the_connection():
    conn = FakeConn()
    body = _client(ForwardTrackerService(lambda: conn, builder=lambda cur: GOOD)).get(ROUTE).json()
    assert body == GOOD and body["source"] == "forward"
    assert conn.log[0] == "SET TRANSACTION READ ONLY" and conn.rolled_back == 1 and conn.closed == 1


def test_an_unavailable_database_is_an_explicit_unavailable_answer_not_an_error():
    r = _client(ForwardTrackerService(lambda: None, builder=lambda cur: GOOD)).get(ROUTE)
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["source"] == "forward" and "unavailable" in r.json()["reason"]


def test_a_failure_inside_the_aggregation_never_leaks_internals_and_still_returns_the_connection():
    conn = FakeConn()

    def boom(cur):
        raise RuntimeError('relation "paper_event" does not exist at host db-secret-host')
    r = _client(ForwardTrackerService(lambda: conn, builder=boom)).get(ROUTE)
    body = r.json()
    assert r.status_code == 200 and body["available"] is False and "could not be computed" in body["reason"]
    assert "paper_event" not in r.text and "db-secret-host" not in r.text and conn.closed == 1


def test_the_answer_is_cached_briefly_and_recomputed_after_the_window():
    now = {"t": 0.0}
    calls = {"n": 0}

    def builder(cur):
        calls["n"] += 1
        return dict(GOOD, sessions_processed=calls["n"])
    svc = ForwardTrackerService(lambda: FakeConn(), builder=builder, clock=lambda: now["t"], cache_seconds=120)
    c = _client(svc)
    assert c.get(ROUTE).json()["sessions_processed"] == 1
    now["t"] = 119
    assert c.get(ROUTE).json()["sessions_processed"] == 1 and calls["n"] == 1
    now["t"] = 121
    assert c.get(ROUTE).json()["sessions_processed"] == 2 and calls["n"] == 2


def test_failures_are_not_cached():
    state = {"fail": True}

    def builder(cur):
        if state["fail"]:
            raise RuntimeError("x")
        return GOOD
    svc = ForwardTrackerService(lambda: FakeConn(), builder=builder, clock=lambda: 0.0, cache_seconds=120)
    c = _client(svc)
    assert c.get(ROUTE).json()["available"] is False
    state["fail"] = False
    assert c.get(ROUTE).json() == GOOD


def test_the_service_source_has_no_write_statements():
    import re
    from pathlib import Path
    code = re.sub(r'""".*?"""', "", Path(sys.modules["services.forward_tracker_service"].__file__).read_text(encoding="utf-8"), flags=re.S)
    for forbidden in ("INSERT", "UPDATE ", "DELETE", "TRUNCATE", "DROP ", "ALTER ", "commit("):
        assert forbidden not in code, forbidden
