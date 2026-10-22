"""GET /api/strategies/{key}/{version}/contracts: authenticated, static (no database), with optional assignment."""
import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.strategy_intelligence import get_strategy_intelligence_service, strategy_intelligence_router  # noqa: E402
from services.strategy_intelligence_service import StrategyIntelligenceService  # noqa: E402


def _app(service=None, authenticated=True):
    app = FastAPI()
    app.include_router(strategy_intelligence_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_strategy_intelligence_service] = lambda: service
    return TestClient(app)


URL = "/api/strategies/donchian_breakout/v1/contracts"


def test_requires_authentication():
    r = _app(service=object(), authenticated=False).get(URL)
    assert r.status_code == 401


def test_serves_both_contracts_with_hashes_without_a_database():
    body = _app(service=StrategyIntelligenceService(lambda: None)).get(URL).json()
    assert body["state"] == "declared" and [c["contract_version"] for c in body["contracts"]] == ["c1", "c2"]
    assert all(len(c["contract_hash"]) == 64 for c in body["contracts"])


def test_assignment_for_a_session_carries_certainty_and_evidence():
    body = _app(service=StrategyIntelligenceService(lambda: None)).get(URL + "?session=2026-10-07").json()
    a = body["assignment"]
    assert (a["contract_version"], a["certainty"]) == ("c2", "medium_high") and a["evidence"]


def test_unknown_version_is_404_and_bad_session_is_422():
    c = _app(service=StrategyIntelligenceService(lambda: None))
    assert c.get("/api/strategies/donchian_breakout/v9/contracts").status_code == 404
    assert c.get(URL + "?session=yesterday").status_code == 422
