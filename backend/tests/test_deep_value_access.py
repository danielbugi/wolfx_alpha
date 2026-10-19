"""Deep Value is internal research (owner decision 2026-10-19): the API answers only the owner."""
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.deep_value import deep_value_router, get_deep_value_service  # noqa: E402


class _Svc:
    def scan(self, **kwargs):
        return {"turnaround_alerts": [], "deep_value_watch": [], "excluded_balance_sheet": 0}

    def __getattr__(self, name):
        return lambda *a, **k: {"turnaround_alerts": [], "deep_value_watch": [], "excluded_balance_sheet": 0}


def _client(role):
    app = FastAPI()
    app.include_router(deep_value_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[get_deep_value_service] = lambda: _Svc()
    if role:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "u@example.com", role)
    return TestClient(app)


def test_deep_value_scan_is_owner_only():
    assert _client(None).get("/api/deep-value/scan").status_code == 401
    assert _client("collaborator").get("/api/deep-value/scan").status_code == 403
    assert _client("owner").get("/api/deep-value/scan").status_code != 403
