"""Research Missions API: owner only, read endpoints, draft creation with validation and a capped budget, the budget endpoint, no way to run a
mission over HTTP, and nothing about the database in an error. No database needed: the connection is a fake that answers by SQL."""
import json
import os
import sys
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
sys.path.append(os.path.join(ROOT, "mechanism"))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.research_missions import get_research_missions_service, research_missions_router  # noqa: E402
from services.research_missions_service import ResearchMissionsService  # noqa: E402

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def execute(self, sql, params=()):
        self.conn.log.append((sql, params))
        rows = self.conn.answer(sql, params)
        self.description = [("x",)] if rows is not None else None
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, answer):
        self.answer, self.log, self.commits, self.rollbacks = answer, [], 0, 0

    def cursor(self, **kw):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def answer(sql, params):
    if "FROM research_mission" in sql and "ORDER BY updated_at" in sql:
        return [{"id": 1, "kind": "thematic", "title": "HBM", "domain": "ai_semiconductors", "status": "completed", "spent_usd": 0.31, "budget_usd": 2, "created_at": NOW, "updated_at": NOW}]
    if "INSERT INTO research_mission" in sql:
        return [{"id": 7, "status": "draft"}]
    if "FROM research_llm_usage WHERE created_at" in sql:
        return [{"month": "2026-11", "spent": 1.25, "reserved": 0.5}]
    if "SELECT * FROM research_mission WHERE id" in sql:
        return [] if params[0] == 404 else [{"id": params[0], "kind": "thematic", "title": "HBM", "question": "q", "domain": "ai_semiconductors", "params": {}, "status": "completed",
                                             "budget_usd": 2, "spent_usd": 0.31, "error": None, "created_by": "o@x", "created_at": NOW, "updated_at": NOW}]
    if "FROM research_llm_usage WHERE mission_id" in sql:
        return [{"calls": 3, "refused": 0, "cost_usd": 0.31}]
    if "FROM research_claim c" in sql:
        return [{"label": "C1", "kind": "fact", "text": "t", "confidence": "high", "status": "verified", "verifier_note": "ok", "evidence": ["E1"]}]
    if "FROM research_report" in sql:
        return [{"version": 1, "markdown": "# HBM", "created_at": NOW}]
    return []


def client(role="owner", conn=None):
    conn = conn or FakeConn(answer)
    app = FastAPI()
    app.include_router(research_missions_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "owner@example.com", role)
    app.dependency_overrides[get_research_missions_service] = lambda: ResearchMissionsService(lambda: conn)
    return TestClient(app), conn


def test_every_route_is_owner_only():
    c, _ = client(role="viewer")
    for method, path in (("get", "/api/research/missions"), ("get", "/api/research/missions/1"), ("get", "/api/research/budget"), ("post", "/api/research/missions")):
        assert getattr(c, method)(path, **({"json": {}} if method == "post" else {})).status_code == 403, path


def test_list_detail_and_budget():
    c, conn = client()
    r = c.get("/api/research/missions").json()
    assert r["missions"][0]["title"] == "HBM" and any(d["key"] == "ai_semiconductors" and d["thematic"] for d in r["domains"])
    d = c.get("/api/research/missions/1").json()
    assert d["report_markdown"] == "# HBM" and d["claims"][0]["evidence"] == ["E1"] and d["usage"]["calls"] == 3
    assert c.get("/api/research/missions/404").status_code == 404
    b = c.get("/api/research/budget").json()
    assert b["cap_usd"] == 150.0 and b["remaining_usd"] == 148.25 and b["paid_calls_enabled"] in (True, False)
    assert conn.commits == 0                                                         # reads never commit


def test_a_draft_is_created_with_a_capped_budget_and_nothing_runs():
    c, conn = client()
    r = c.post("/api/research/missions", json={"kind": "thematic", "title": "HBM and packaging", "question": "Who is exposed to HBM and why?", "domain": "ai_semiconductors",
                                                "params": {"theme": "HBM"}, "budget_usd": 999})
    assert r.status_code == 201 and r.json() == {"id": 7, "status": "draft", "budget_usd": 5.0}
    sql, params = next((s, p) for s, p in conn.log if "INSERT INTO research_mission" in s)
    assert json.loads(params[4]) == {"theme": "HBM"} and params[6] == "owner@example.com" and conn.commits == 1
    assert not any("research_llm_usage" in s and "INSERT" in s for s, _ in conn.log)    # creating a draft never reserves or calls anything


def test_invalid_drafts_are_rejected():
    c, _ = client()
    base = {"title": "Something", "question": "A question long enough", "params": {}}
    assert c.post("/api/research/missions", json={**base, "kind": "thematic", "domain": "nope"}).status_code == 422
    assert c.post("/api/research/missions", json={**base, "kind": "thematic", "domain": "general"}).status_code == 422
    assert c.post("/api/research/missions", json={**base, "kind": "company", "domain": "general"}).status_code == 422
    assert c.post("/api/research/missions", json={**base, "kind": "run", "domain": "general"}).status_code == 422


def test_there_is_no_route_that_runs_a_mission():
    paths = {getattr(r, "path", "") for r in research_missions_router.routes}
    assert not any("run" in p or "execute" in p for p in paths)
    methods = {m for r in research_missions_router.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "POST"}


def test_errors_leak_nothing_and_a_missing_migration_is_explicit():
    def missing(sql, params):
        raise RuntimeError('relation "research_mission" does not exist')

    def boom(sql, params):
        raise RuntimeError("connection to 10.0.0.5 failed: password=hunter2")
    c, _ = client(conn=FakeConn(missing))
    r = c.get("/api/research/missions")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "not_installed"
    c, _ = client(conn=FakeConn(boom))
    r = c.get("/api/research/missions")
    assert r.status_code == 500 and "hunter2" not in r.text and "10.0.0.5" not in r.text
