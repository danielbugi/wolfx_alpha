"""Market Intelligence router: auth on every route, observed-only by default, explicit opt-in for reconstructed rows, graceful
`available: false` when the storage is not provisioned. DB-backed cases run against a throwaway schema with migrations 24/25 and skip
when Postgres is unreachable (CI's real-Postgres job treats a skip as a failure)."""
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
sys.path.append(os.path.join(ROOT, "mechanism"))
sys.path.append(os.path.join(ROOT, "mechanism", "market_intelligence", "tests"))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.market_intelligence import get_market_intelligence_service, market_intelligence_router  # noqa: E402
from services.market_intelligence_service import MarketIntelligenceService  # noqa: E402

ROUTES = ["/api/market-intelligence/definitions", "/api/market-intelligence/snapshot", "/api/market-intelligence/history"]
MIGRATIONS = ["add_market_snapshot_tables.sql", "add_market_event_tables.sql"]


def _client(service=None, authenticated=True):
    app = FastAPI()
    app.include_router(market_intelligence_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_market_intelligence_service] = lambda: service
    return TestClient(app)


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_requires_authentication(route):
    r = _client(service=object(), authenticated=False).get(route)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_authenticated"


def test_definitions_need_no_database_and_call_the_regime_a_heuristic():
    body = _client(MarketIntelligenceService(lambda: None)).get("/api/market-intelligence/definitions").json()
    assert body["risk_regime"]["is_heuristic"] is True and body["relative_strength"]["min_sector_members"] == 5


@pytest.mark.parametrize("route", [
    "/api/market-intelligence/snapshot?session=not-a-date",
    "/api/market-intelligence/snapshot?include_reconstructed=maybe",
    "/api/market-intelligence/history?limit=0",
    "/api/market-intelligence/history?limit=251",
])
def test_invalid_input_is_rejected_before_any_query(route):
    assert _client(MarketIntelligenceService(lambda: None)).get(route).status_code == 422


def test_there_is_no_write_route():
    paths = {(m, r.path) for r in market_intelligence_router.routes for m in r.methods}
    assert {m for m, _ in paths} == {"GET"}


# ---- real Postgres ----------------------------------------------------------------------------------------------------
def _connect_args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


@contextmanager
def _schema(with_migrations=True):
    import psycopg2
    try:
        args = _connect_args()
        admin = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "mi_api_" + uuid.uuid4().hex[:10]
    cur = admin.cursor()
    cur.execute(f'CREATE SCHEMA "{schema}"')
    cur.execute(f'SET search_path TO "{schema}"')
    if with_migrations:
        for name in MIGRATIONS:
            with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
                cur.execute(fh.read())
    admin.commit()

    class _Conn:
        """Hands out connections on the throwaway schema and survives close() like the pooled wrapper."""
        def __call__(self):
            c = psycopg2.connect(options=f"-c search_path={schema}", **args)
            return c

    try:
        yield _Conn()
    finally:
        admin.rollback()
        admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.commit()
        admin.close()


def _seed(get_conn, provenance):
    import mi_samples as S
    from market_intelligence import store
    conn = get_conn()
    basis = "recomputed from stored prices" if provenance == "reconstructed" else None
    store.write_session(conn.cursor(), S.regime(), S.relative_strength(provenance), provenance, "api-test", "test@abc", reconstruction_basis=basis)
    conn.commit()
    conn.close()
    return S.T.isoformat()


def test_snapshot_is_observed_only_by_default_and_opt_in_is_explicit():
    with _schema() as get_conn:
        session = _seed(get_conn, "reconstructed")
        c = _client(MarketIntelligenceService(get_conn))
        body = c.get("/api/market-intelligence/snapshot").json()
        assert body["available"] is False and body["reason"] == "no_snapshot"          # a reconstructed row is invisible by default
        body = c.get("/api/market-intelligence/snapshot?include_reconstructed=true").json()
        assert body["available"] and body["provenance"] == "reconstructed" and body["observed"] is False
        assert body["session_date"] == session and "not a record of what was known" in body["provenance_note"]
        _seed(get_conn, "observed")
        body = c.get("/api/market-intelligence/snapshot").json()
        assert body["provenance"] == "observed" and body["sector_map"]["pit_safe"] is True and len(body["sectors"]) == 3
        by_date = c.get(f"/api/market-intelligence/snapshot?session={session}").json()
        assert by_date["provenance"] == "observed"
        assert c.get("/api/market-intelligence/snapshot?session=2000-01-03").json()["reason"] == "no_snapshot"


def test_history_lists_provenance_and_hides_reconstructed_by_default():
    with _schema() as get_conn:
        _seed(get_conn, "observed")
        _seed(get_conn, "reconstructed")
        c = _client(MarketIntelligenceService(get_conn))
        assert [s["provenance"] for s in c.get("/api/market-intelligence/history").json()["sessions"]] == ["observed"]
        both = c.get("/api/market-intelligence/history?include_reconstructed=true").json()["sessions"]
        assert sorted(s["provenance"] for s in both) == ["observed", "reconstructed"]


def test_unprovisioned_database_answers_unavailable_not_a_500():
    with _schema(with_migrations=False) as get_conn:
        c = _client(MarketIntelligenceService(get_conn))
        for route in ("/api/market-intelligence/snapshot", "/api/market-intelligence/history"):
            r = c.get(route)
            assert r.status_code == 200 and r.json()["available"] is False and r.json()["reason"] == "not_provisioned"
