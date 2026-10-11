"""Quant Lab reports API: authentication on every route, read-only, provenance and caveats enforced, no path traversal, the committed reports all valid."""
import json
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.lab_reports import get_lab_reports_service, lab_reports_router  # noqa: E402
from services.lab_reports_service import DEFAULT_DIR, LabReportsService, validate  # noqa: E402


def _client(service=None, authenticated=True):
    app = FastAPI()
    app.include_router(lab_reports_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_lab_reports_service] = lambda: service
    return TestClient(app)


def _doc(**kw):
    d = {"schema": "lab_report_v1", "id": "demo", "kind": "ml", "title": "T", "headline": "H", "generated_at": "2026-10-11", "code_ref": "abc", "status": "research_only",
         "use": "x", "caveats": ["c"], "provenance": {"source": "s"}, "data": {"k": 1}}
    d.update(kw)
    return d


def _dir(tmp_path, docs):
    for d in docs:
        (tmp_path / f"{d['id']}.json").write_text(json.dumps(d), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps({"reports": [{"id": d["id"], "file": f"{d['id']}.json"} for d in docs]}), encoding="utf-8")
    return str(tmp_path)


@pytest.mark.parametrize("route", ["/api/lab/reports", "/api/lab/reports/demo"])
def test_every_route_requires_authentication(route):
    r = _client(service=LabReportsService("."), authenticated=False).get(route)
    assert r.status_code == 401


def test_there_is_no_write_route():
    c = _client(LabReportsService("."))
    for method in ("post", "put", "patch", "delete"):
        assert getattr(c, method)("/api/lab/reports").status_code in (404, 405)
        assert getattr(c, method)("/api/lab/reports/demo").status_code in (404, 405)


def test_a_valid_report_is_listed_and_served_with_the_research_only_notice(tmp_path):
    c = _client(LabReportsService(_dir(tmp_path, [_doc()])))
    body = c.get("/api/lab/reports").json()
    assert body["available"] is True and "not a trading signal" in body["notice"].lower() and body["reports"][0]["id"] == "demo"
    got = c.get("/api/lab/reports/demo").json()
    assert got["status"] == "research_only" and got["caveats"] and got["provenance"]["source"]


@pytest.mark.parametrize("mutation", [{"status": "live"}, {"caveats": []}, {"provenance": {}}, {"kind": "alpha"}])
def test_a_report_without_provenance_or_caveats_or_research_status_is_never_served(tmp_path, mutation):
    svc = LabReportsService(_dir(tmp_path, [_doc(**mutation)]))
    c = _client(svc)
    body = c.get("/api/lab/reports").json()
    assert body["reports"] == [] and body["available"] is False and body["invalid"][0]["id"] == "demo"
    assert c.get("/api/lab/reports/demo").status_code == 404


def test_a_missing_field_makes_the_report_invalid(tmp_path):
    d = _doc()
    d.pop("code_ref")
    assert "missing code_ref" in validate(d)
    assert _client(LabReportsService(_dir(tmp_path, [d]))).get("/api/lab/reports/demo").status_code == 404


@pytest.mark.parametrize("rid", ["..%2Findex", "..", "a.b", "UPPER", "x" * 80, "demo%00"])
def test_a_report_id_cannot_reach_another_file(tmp_path, rid):
    c = _client(LabReportsService(_dir(tmp_path, [_doc()])))
    assert c.get(f"/api/lab/reports/{rid}").status_code in (404, 422)


def test_an_index_entry_cannot_point_at_another_file(tmp_path):
    d = _doc()
    (tmp_path / "demo.json").write_text(json.dumps(d), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps({"reports": [{"id": "demo", "file": "../../etc/passwd"}]}), encoding="utf-8")
    assert _client(LabReportsService(str(tmp_path))).get("/api/lab/reports").json()["reports"] == []


def test_an_empty_or_missing_directory_is_unavailable_not_an_error(tmp_path):
    body = _client(LabReportsService(str(tmp_path / "none"))).get("/api/lab/reports").json()
    assert body["available"] is False and body["reports"] == []


def test_every_committed_report_is_valid_and_carries_its_own_caveats():
    svc = LabReportsService(DEFAULT_DIR)
    body = svc.list_reports()
    assert body["invalid"] == [] and len(body["reports"]) >= 6
    ids = {r["id"] for r in body["reports"]}
    assert {"strategy_baseline", "ml_evaluation", "exit_policies", "catalyst_earnings", "catalyst_radar_recent", "ranker_stability"} <= ids
    for r in body["reports"]:
        doc = svc.get_report(r["id"])
        assert doc["caveats"] and doc["code_ref"] and doc["provenance"]["source"]
