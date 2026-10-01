"""Release B research endpoints under /api/strategies/{key}/{version}/research/*: auth on every route, input validation
before any query, router <-> service wiring (stub service), the unavailable contract against a real database, and the
status-code contract of the detail routes. The read model itself is covered against throwaway schemas in
mechanism/research/tests/test_research_analytics.py.

The real-service tests tolerate both database states: locally migration 22 is not applied (availability
not_available); in CI's freshly migrated schema it is (a new strategy has no capture run: no_data). Neither state may
ever produce a zero count or an item."""
import os
import sys
import uuid
from datetime import date

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from tests.test_strategy_intelligence_api import _app, _connect  # noqa: E402

from services.strategy_intelligence_service import (  # noqa: E402
    ResearchUnavailable, StrategyIntelligenceService, StrategyNotFound,
)

BASE = "/api/strategies/donchian_breakout/v1/research"
ROUTES = [f"{BASE}/summary", f"{BASE}/capture-runs", f"{BASE}/candidates", f"{BASE}/candidates/1",
          f"{BASE}/snapshots/1"]


@pytest.mark.parametrize("route", ROUTES)
def test_every_research_route_requires_authentication(route):
    r = _app(service=object(), authenticated=False).get(route)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_authenticated"


@pytest.mark.parametrize("route", [
    "/api/strategies/Bad-Key/v1/research/summary",
    f"{BASE}/capture-runs?limit=0", f"{BASE}/capture-runs?limit=121",
    f"{BASE}/candidates?sort=best", f"{BASE}/candidates?limit=201", f"{BASE}/candidates?limit=0",
    f"{BASE}/candidates?offset=-1", f"{BASE}/candidates?direction=long", f"{BASE}/candidates?guard=maybe",
    f"{BASE}/candidates?selected=perhaps", f"{BASE}/candidates?symbol=A;DROP", f"{BASE}/candidates?grade=Z9",
    f"{BASE}/candidates?candidate_class=Bad%20Class", f"{BASE}/candidates?session_date=yesterday",
    f"{BASE}/candidates/0", f"{BASE}/snapshots/0", f"{BASE}/candidates/abc",
])
def test_invalid_input_is_rejected_before_any_query(route):
    assert _app(service=StrategyIntelligenceService(lambda: None)).get(route).status_code == 422


# =================================================================== router <-> service wiring
class Stub:
    def __init__(self, **behaviour):
        self.calls = []
        self.behaviour = behaviour

    def _call(self, name, *args, **kw):
        self.calls.append((name, args, kw))
        result = self.behaviour.get(name, {"ok": name})
        if isinstance(result, Exception):
            raise result
        return result

    def research_summary(self, *a, **k):
        return self._call("summary", *a, **k)

    def research_capture_runs(self, *a, **k):
        return self._call("capture_runs", *a, **k)

    def research_candidates(self, *a, **k):
        return self._call("candidates", *a, **k)

    def research_candidate(self, *a, **k):
        return self._call("candidate", *a, **k)

    def research_snapshot(self, *a, **k):
        return self._call("snapshot", *a, **k)


def test_candidate_filters_are_forwarded_to_the_service_verbatim():
    stub = Stub()
    url = (f"{BASE}/candidates?session_date=2099-01-15&direction=bearish&guard=rejected&selected=false&symbol=aapl"
           "&candidate_class=near_bearish&grade=b&sort=combined_desc&limit=25&offset=50")
    assert _app(service=stub).get(url).status_code == 200
    name, args, kw = stub.calls[0]
    assert name == "candidates" and args == ("donchian_breakout", "v1")
    assert kw == dict(session_date=date(2099, 1, 15), direction="bearish", guard="rejected", selected=False,
                      symbol="aapl", candidate_class="near_bearish", grade="b", sort="combined_desc", limit=25,
                      offset=50)


def test_candidate_defaults_are_server_side_paging_with_no_session_pinned():
    stub = Stub()
    _app(service=stub).get(f"{BASE}/candidates")
    assert stub.calls[0][2] == dict(session_date=None, direction=None, guard=None, selected=None, symbol=None,
                                    candidate_class=None, grade=None, sort="rank", limit=50, offset=0)


def test_strategy_identity_comes_from_the_path_not_the_router():
    stub = Stub()
    _app(service=stub).get("/api/strategies/mean_reversion/v2/research/summary")
    assert stub.calls[0] == ("summary", ("mean_reversion", "v2"), {})


def test_service_value_error_is_a_422_invalid_query():
    r = _app(service=Stub(candidates=ValueError("unknown sort"))).get(f"{BASE}/candidates")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_query"


@pytest.mark.parametrize("route", ROUTES)
def test_unknown_strategy_is_404_on_every_route(route):
    stub = Stub(summary=StrategyNotFound("x"), capture_runs=StrategyNotFound("x"), candidates=StrategyNotFound("x"),
                candidate=StrategyNotFound("x"), snapshot=StrategyNotFound("x"))
    r = _app(service=stub).get(route)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "strategy_not_found"


def test_detail_routes_distinguish_unavailable_from_not_found():
    avail = {"state": "not_available", "release": "B", "reason": "not installed"}
    unavailable = Stub(candidate=ResearchUnavailable(avail), snapshot=ResearchUnavailable(avail))
    for route in (f"{BASE}/candidates/5", f"{BASE}/snapshots/5"):
        r = _app(service=unavailable).get(route)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "research_not_available"
    missing = Stub(candidate=None, snapshot=None)
    assert _app(service=missing).get(f"{BASE}/candidates/5").json()["detail"]["code"] == "candidate_not_found"
    assert _app(service=missing).get(f"{BASE}/snapshots/5").json()["detail"]["code"] == "snapshot_not_found"


def test_a_found_detail_is_returned_as_is():
    payload = {"id": 5, "lineage": {"observation_id": 5, "snapshot_id": 9, "signal": None}}
    assert _app(service=Stub(candidate=payload)).get(f"{BASE}/candidates/5").json() == payload


# =================================================================== real service, real database
@pytest.fixture
def db():
    try:
        c = _connect()
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SELECT evaluation_flag FROM signal_ledger LIMIT 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable or signal_ledger lacks migration 21: {type(e).__name__}")
    key = "zz_rs_" + uuid.uuid4().hex[:8]
    with c.cursor() as cur:
        cur.execute("INSERT INTO strategies (strategy_key, strategy_version) VALUES (%s, 'v1') RETURNING id", (key,))
        sid = cur.fetchone()[0]
    yield {"key": key}
    with c.cursor() as cur:
        cur.execute("DELETE FROM strategies WHERE id = %s", (sid,))
    c.close()


@pytest.fixture
def client(db):
    return _app(service=StrategyIntelligenceService(_connect))


def test_a_strategy_without_captures_is_never_reported_as_zero(client, db):
    root = f"/api/strategies/{db['key']}/v1/research"
    summary = client.get(f"{root}/summary").json()
    assert summary["availability"]["state"] in ("not_available", "no_data")
    assert all(s["value"] is None for s in summary["funnel"]["stages"])
    assert all(c["value"] is None and c["state"] != "ok" for c in summary["cards"].values())
    assert all(r["value"] is None for r in summary["rates"].values())
    assert summary["forward_outcomes"]["state"] == "not_available"
    runs = client.get(f"{root}/capture-runs").json()
    assert runs["history"] == [] and runs["latest"] is None
    assert runs["overall"]["status"] in ("not_available", "not_active")
    cands = client.get(f"{root}/candidates?direction=bullish&guard=passed&symbol=aa&limit=10").json()
    assert cands["items"] == [] and cands["total"] is None and cands["has_more"] is False
    assert cands["availability"]["state"] in ("not_available", "no_data")


def test_detail_routes_answer_404_with_a_code_never_a_500(client, db):
    root = f"/api/strategies/{db['key']}/v1/research"
    for route, codes in ((f"{root}/candidates/999999999", {"research_not_available", "candidate_not_found"}),
                         (f"{root}/snapshots/999999999", {"research_not_available", "snapshot_not_found"})):
        r = client.get(route)
        assert r.status_code == 404 and r.json()["detail"]["code"] in codes


def test_unknown_strategy_is_404_against_the_real_service(client):
    r = client.get("/api/strategies/no_such_strategy/v1/research/summary")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "strategy_not_found"
    assert client.get("/api/strategies/no_such_strategy/v1/research/candidates").status_code == 404


def test_existing_summary_still_answers_unchanged(client, db):
    body = client.get(f"/api/strategies/{db['key']}/v1/summary").json()
    assert body["tracking"]["total_signals"] == 0 and "capabilities" in body
