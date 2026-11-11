"""Opportunity Intelligence v0 API: owner-only, read-only, filters; the service wraps mechanism/opportunity without recomputing anything."""
import os
import sys
from datetime import date, datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.opportunity import get_opportunity_service, opportunity_router  # noqa: E402
from services import opportunity_service as OS  # noqa: E402


def _brief(sym, converging):
    return {"symbol": sym, "convergence": {"converging": converging}}


REPORT = {"version": "brief_v0", "session": "2026-11-10", "knowledge_cutoff": "2026-11-11T12:00:00+00:00", "counts": {}, "pit_exclusions": {},
          "briefs": [_brief("AAA", True), _brief("BBB", False), _brief("CCC", True)]}


class _Svc(OS.OpportunityService):
    def __init__(self):
        self.calls = []

    def report(self, session, days, now=None):
        self.calls.append((session, days))
        return dict(REPORT)


def _client(role, svc=None):
    app = FastAPI()
    app.include_router(opportunity_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[get_opportunity_service] = lambda: svc or _Svc()
    if role:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "u@example.com", role)
    return TestClient(app)


def test_owner_only():
    for path in ("/api/opportunity/briefs", "/api/opportunity/briefs/AAA"):
        assert _client(None).get(path).status_code == 401
        assert _client("collaborator").get(path).status_code == 403
        assert _client("owner").get(path).status_code == 200


def test_filters_and_explicit_session():
    svc = _Svc()
    r = _client("owner", svc).get("/api/opportunity/briefs", params={"session": "2026-11-10", "days": 7, "converging_only": "true", "limit": 1}).json()
    assert [b["symbol"] for b in r["briefs"]] == ["AAA"] and r["returned"] == 1 and svc.calls == [(date(2026, 11, 10), 7)]


def test_company_brief_and_404():
    c = _client("owner")
    assert [b["symbol"] for b in c.get("/api/opportunity/briefs/bbb").json()["briefs"]] == ["BBB"]
    assert c.get("/api/opportunity/briefs/ZZZ").status_code == 404


def test_no_database_is_503_not_a_crash():
    svc = OS.OpportunityService(lambda: None)
    assert _client("owner", svc).get("/api/opportunity/briefs").status_code == 503


def test_provisional_until_the_knowledge_cutoff_passes(monkeypatch):
    monkeypatch.setattr(OS.B, "run", lambda connect, session, days: dict(REPORT, briefs=[]))
    svc = OS.OpportunityService(lambda: object())
    assert svc.report(date(2026, 11, 10), 14, now=datetime(2026, 11, 11, 11, 59, tzinfo=timezone.utc))["provisional"] is True
    assert svc.report(date(2026, 11, 10), 14, now=datetime(2026, 11, 11, 12, 1, tzinfo=timezone.utc))["provisional"] is False


def test_the_router_and_service_compute_nothing_themselves():
    root = os.path.join(os.path.dirname(__file__), "..")
    for rel in ("routers/opportunity.py", "services/opportunity_service.py"):
        src = open(os.path.join(root, rel), encoding="utf-8").read()
        for forbidden in ("SELECT ", "INSERT ", "surprise_pct", "rev_yoy"):
            assert forbidden not in src, (rel, forbidden)
