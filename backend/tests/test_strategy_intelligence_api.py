"""Strategy Intelligence router: auth on every route (no DB needed), then status codes and response
contracts against real Postgres (skips when unreachable -- CI's db-bootstrap-integration job runs it
against a freshly migrated schema)."""
import os
import sys
import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.strategy_intelligence import (  # noqa: E402
    get_strategy_intelligence_service, strategy_intelligence_router,
)
from services.strategy_intelligence_service import StrategyIntelligenceService  # noqa: E402

ROUTES = ["/api/strategies", "/api/strategies/definitions",
          "/api/strategies/donchian_breakout/v1/summary", "/api/strategies/donchian_breakout/v1/data-health",
          "/api/strategies/donchian_breakout/v1/signals", "/api/strategies/donchian_breakout/v1/signals/1"]


def _app(service=None, authenticated=True):
    app = FastAPI()
    app.include_router(strategy_intelligence_router)
    app.dependency_overrides[get_auth_store] = lambda: object()  # never reached without a valid token
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_strategy_intelligence_service] = lambda: service
    return TestClient(app)


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_requires_authentication(route):
    r = _app(service=object(), authenticated=False).get(route)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_authenticated"


def test_definitions_are_served_without_a_database():
    body = _app(service=StrategyIntelligenceService(lambda: None)).get("/api/strategies/definitions").json()
    assert body["min_sample_size"] == 5 and "win_rate" in body["definitions"]
    assert "stopped" in body["statuses"] and body["evaluation_flags"] == ["split_suspect"]


@pytest.mark.parametrize("route", [
    "/api/strategies/Bad-Key/v1/summary",                              # key pattern
    "/api/strategies/donchian_breakout/v1/signals?sort=best",
    "/api/strategies/donchian_breakout/v1/signals?limit=201",
    "/api/strategies/donchian_breakout/v1/signals?offset=-1",
    "/api/strategies/donchian_breakout/v1/signals?direction=long",
    "/api/strategies/donchian_breakout/v1/signals?symbol=A;DROP",
    "/api/strategies/donchian_breakout/v1/signals/0",
])
def test_invalid_input_is_rejected_before_any_query(route):
    r = _app(service=StrategyIntelligenceService(lambda: None)).get(route)
    assert r.status_code == 422


# =================================================================== real Postgres
def _connect():
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, "..", ".env"))
    return psycopg2.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
                            dbname=os.getenv("DB_NAME", "trading_production"),
                            user=os.getenv("DB_USER", "trading_user"), password=os.getenv("DB_PASSWORD", ""),
                            connect_timeout=3)


@pytest.fixture
def db():
    try:
        c = _connect()
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SELECT evaluation_flag FROM signal_ledger LIMIT 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable or signal_ledger lacks migration 21: {type(e).__name__}")
    key = "zz_api_" + uuid.uuid4().hex[:8]
    sym = "ZA" + uuid.uuid4().hex[:6].upper()
    with c.cursor() as cur:
        cur.execute("INSERT INTO strategies (strategy_key, strategy_version) VALUES (%s, 'v1') RETURNING id", (key,))
        sid = cur.fetchone()[0]
        for i, (status, r, bars) in enumerate([("open", None, None), ("target1", 1.0, 3), ("stopped", -1.0, 2)]):
            cur.execute("""
                INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price, target1_price,
                    target2_price, target3_price, sector, quality_grade, status, outcome_r, resolved_date, bars_held,
                    last_evaluated_date, strategy_id, strategy_version)
                VALUES (%s, %s, 1, 100, 2, 96, 104, 108, 112, 'Technology', 'A', %s, %s, %s, %s, %s, %s, 'v1')""",
                (f"{sym[:7]}{i}", date(2099, 7, 1), status, r, date(2099, 7, 5) if bars else None, bars,
                 date(2099, 7, 1), sid))
    yield {"key": key, "sid": sid}
    with c.cursor() as cur:
        cur.execute("DELETE FROM signal_ledger WHERE strategy_id = %s", (sid,))
        cur.execute("DELETE FROM strategies WHERE id = %s", (sid,))
    c.close()


@pytest.fixture
def client(db):
    return _app(service=StrategyIntelligenceService(_connect))


def test_list_and_summary(client, db):
    listed = client.get("/api/strategies").json()["strategies"]
    assert any(s["key"] == db["key"] and s["tracking"]["total_signals"] == 3 for s in listed)
    body = client.get(f"/api/strategies/{db['key']}/v1/summary").json()
    assert body["tracking"]["total_signals"] == 3 and body["tracking"]["resolved"] == 2
    assert body["performance"]["win_rate"] == {"value": 0.5, "n": 2, "state": "preliminary"}
    assert body["capabilities"]["feature_snapshots"]["available"] is False


def test_unknown_strategy_and_signal_are_404(client, db):
    r = client.get("/api/strategies/no_such_strategy/v1/summary")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "strategy_not_found"
    assert client.get(f"/api/strategies/{db['key']}/v2/signals").status_code == 404
    r = client.get(f"/api/strategies/{db['key']}/v1/signals/999999999")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "signal_not_found"


def test_signals_pagination_contract_and_detail(client, db):
    page = client.get(f"/api/strategies/{db['key']}/v1/signals?limit=2&lifecycle=resolved&sort=r_desc").json()
    assert page["total"] == 2 and page["limit"] == 2 and page["offset"] == 0 and page["has_more"] is False
    assert [i["status"] for i in page["items"]] == ["target1", "stopped"]
    first = page["items"][0]
    detail = client.get(f"/api/strategies/{db['key']}/v1/signals/{first['id']}").json()
    assert detail["lifecycle"]["outcome_r"] == 1.0 and detail["identity"]["direction"] == "bullish"
    assert detail["provenance"]["feature_snapshot_id"] is None


def test_data_health_contract(client, db):
    h = client.get(f"/api/strategies/{db['key']}/v1/data-health").json()
    assert h["ledger"]["total_rows"] == 3 and h["ledger"]["normally_open"] == 1
    assert set(h["invariants"]) >= {"duplicate_event_identities", "multiple_open_positions"}
    assert h["evaluation"]["evaluator_runs"]["state"] == "not_available"
