"""Quant Lab data-quality API: authenticated, GET-only, a read-only transaction on every request, explicit `available: false` instead of errors, a short shared cache, and nothing about the
database in an error response. No database needed: the payload builder is injected; the SQL itself is exercised against a real schema by mechanism/inflection/tests."""
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)
sys.path.append(os.path.join(ROOT, "mechanism"))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.data_quality import data_quality_router, get_data_quality_service  # noqa: E402
from services import data_quality_service as svc  # noqa: E402
from services.data_quality_service import DataQualityService  # noqa: E402

ROUTE = "/api/lab/data-quality"
GOOD = {"schema": "data_quality_v1", "available": True, "coverage_ratio": 0.5}


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

    def cursor(self, **kwargs):
        return FakeCursor(self.log)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1


def _client(service=None, authenticated=True):
    app = FastAPI()
    app.include_router(data_quality_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_data_quality_service] = lambda: service
    return TestClient(app)


def test_requires_authentication():
    r = _client(DataQualityService(lambda: FakeConn(), lambda db: GOOD), authenticated=False).get(ROUTE)
    assert r.status_code in (401, 403)


def test_get_only_and_read_only_transaction_and_connection_released():
    conn = FakeConn()
    c = _client(DataQualityService(lambda: conn, lambda db: GOOD))
    assert c.get(ROUTE).json() == GOOD
    assert c.post(ROUTE).status_code == 405
    assert conn.log[0] == "SET TRANSACTION READ ONLY" and conn.rolled_back == 1 and conn.closed == 1


def test_unavailable_database_and_builder_errors_are_explicit_and_leak_nothing():
    down = _client(DataQualityService(lambda: None, lambda db: GOOD)).get(ROUTE).json()
    assert down["available"] is False and "database" in down["reason"]

    def boom(db):
        raise RuntimeError("relation sec_quarter does not exist; password=hunter2")
    bad = _client(DataQualityService(lambda: FakeConn(), boom)).get(ROUTE).json()
    assert bad["available"] is False and "hunter2" not in str(bad) and "sec_quarter" not in str(bad)


def test_short_cache_is_shared_between_requests():
    calls, t = [], [0.0]

    def builder(db):
        calls.append(1)
        return GOOD
    s = DataQualityService(lambda: FakeConn(), builder, clock=lambda: t[0], cache_seconds=600)
    c = _client(s)
    c.get(ROUTE)
    c.get(ROUTE)
    assert len(calls) == 1
    t[0] = 601.0
    c.get(ROUTE)
    assert len(calls) == 2


def test_the_point_in_time_family_is_confirmation_eligible_only_with_enough_coverage():
    class Db:
        def __init__(self, with_revenue, events):
            self.with_revenue, self.events = with_revenue, events

        def execute_dict_query(self, sql, params=()):
            if "events_in_test_period" in sql:
                return [{"events_in_test_period": 400, "complete": 400 if self.events else 0, "companies": 200, "weeks": 60}]
            if "with_provenance" in sql:
                return [{"features": 10, "with_provenance": 10}]
            return [{"method_version": "inflection_v2_sec_pit", "features": 10, "symbols": 3, "newest_available": "2026-10-30", "events": self.events}]

    import sys as _s
    cov = {"universe": {"with_cik": 100, "with_revenue_quarters": 0}}
    fake_cov = type(_s)("sec_coverage")
    fake_cov.coverage = lambda db: {**cov, "universe": {"with_cik": 100, "with_revenue_quarters": db.with_revenue}}
    fake_rec = type(_s)("reconcile_db")
    fake_rec.run = lambda a, b, n, db: {"events_in_both": 0}
    import data_updaters
    import inflection
    old = (getattr(data_updaters, "sec_coverage", None), getattr(inflection, "reconcile_db", None), _s.modules.get("data_updaters.sec_coverage"), _s.modules.get("inflection.reconcile_db"))
    data_updaters.sec_coverage, inflection.reconcile_db = fake_cov, fake_rec
    _s.modules["data_updaters.sec_coverage"], _s.modules["inflection.reconcile_db"] = fake_cov, fake_rec
    try:
        low = svc.build_payload(Db(20, 5))
        pit = next(n for n in low["namespaces"] if n["method_version"] == "inflection_v2_sec_pit")
        assert pit["role_now"] == "exploratory" and "20.0%" in pit["role_reason"] and pit["role_reason"].startswith("INSUFFICIENT_DATA")
        high = svc.build_payload(Db(85, 5))
        assert next(n for n in high["namespaces"] if n["method_version"] == "inflection_v2_sec_pit")["role_now"] == "confirmation_eligible"
        none = svc.build_payload(Db(85, 0))
        assert next(n for n in none["namespaces"] if n["method_version"] == "inflection_v2_sec_pit")["role_now"] == "exploratory"
        # the non-point-in-time families are never eligible, whatever the coverage
        assert all(n["role_now"] == "exploratory" for n in high["namespaces"] if not n["point_in_time"])
    finally:
        data_updaters.sec_coverage, inflection.reconcile_db = old[0], old[1]
        for k, v in (("data_updaters.sec_coverage", old[2]), ("inflection.reconcile_db", old[3])):
            if v is None:
                _s.modules.pop(k, None)
            else:
                _s.modules[k] = v


def test_coverage_alone_never_qualifies_and_the_reason_names_every_failing_gate():
    ok = {"features": 50, "features_with_provenance": 50, "events_in_test_period": 300, "complete_window_events": 200, "companies": 120, "public_weeks": 50}
    assert all(g["ok"] for g in svc.confirmation_gates(0.9, ok))
    for bad, gate in (({"complete_window_events": 149}, "complete_forward_windows"), ({"companies": 99}, "independent_companies"),
                      ({"public_weeks": 39}, "independent_public_weeks"), ({"features_with_provenance": 49}, "point_in_time_features"), ({"features": 0, "features_with_provenance": 0}, "point_in_time_features")):
        failing = [g["gate"] for g in svc.confirmation_gates(0.9, {**ok, **bad}) if not g["ok"]]
        assert failing == [gate]
    assert [g["gate"] for g in svc.confirmation_gates(0.5, ok) if not g["ok"]] == ["coverage"]
    assert next(g for g in svc.confirmation_gates(0.9, ok) if g["gate"] == "untouched_holdout")["ok"]
