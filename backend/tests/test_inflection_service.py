"""Inflection read API: the service against a real PostgreSQL with migration 40 (skipped without one) and the router's graceful answer when the tables are not installed yet.
Rows live under symbols ZZQ* and are removed before and after."""
import hashlib
import os
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from services.inflection_service import InflectionService, LABELS  # noqa: E402

TODAY = date(2026, 10, 31)
SYMS = ["ZZQ1", "ZZQ2", "ZZQ3"]


def _conn_factory():
    try:
        import psycopg2
        host, port, name, user, pw = (os.environ.get(k) for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"))
        if not all((host, name, user)):
            pytest.skip("no database configured")
        def make():
            return psycopg2.connect(host=host, port=port or 5432, dbname=name, user=user, password=pw)
        c = make()
        cur = c.cursor()
        cur.execute("SELECT 1 FROM fundamental_inflection_event LIMIT 1")
        cur.execute("SELECT 1 FROM companies LIMIT 1")
        c.rollback()
        c.close()
        return make
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 40 not reachable: {e}")


def _exec(make, sql, params=()):
    c = make()
    try:
        cur = c.cursor()
        cur.execute(sql, params)
        c.commit()
    finally:
        c.close()


@pytest.fixture
def svc():
    make = _conn_factory()
    for t in ("fundamental_inflection_event", "fundamental_feature", "daily_fundamentals", "companies"):
        _exec(make, f"DELETE FROM {t} WHERE symbol LIKE 'ZZQ%%'")
    from psycopg2.extras import Json
    def ev(sym, kind, strength, sev, avail, cap, mk=None):
        _exec(make, """INSERT INTO fundamental_inflection_event (symbol, quarter_end, event_type, method_version, fiscal_year, fiscal_quarter, strength, severity, score, metrics, explanation, components,
                       context, available_at, availability_basis, source, data_quality, event_hash) VALUES (%s,'2026-06-30',%s,'inflection_v1',2026,2,%s,%s,%s,%s,%s,%s,%s,%s,'earnings_calendar','quarterly_fundamentals','ok',%s)""",
              (sym, kind, strength, sev, strength, Json(mk or {"op_margin": 0.2, "op_margin_py": 0.1, "op_margin_yoy_bps": 1000.0}), Json(["line one"]), Json([]),
               Json({"reaction": {"ret_2d": 0.04}, "excess_vs_sector_since_publication": 0.02, "valuation_latest": {"market_cap": cap, "ps_ratio": 3.0}, "valuation_at_publication": {"ps_ratio": 2.8}}), avail, hashlib.sha256((sym + kind).encode()).hexdigest()))
    ev("ZZQ1", "margin_expansion", 90, "high", TODAY - timedelta(days=3), 5e9)
    ev("ZZQ1", "fcf_turnaround", 60, "medium", TODAY - timedelta(days=3), 5e9, {"free_cash_flow": 5e6, "free_cash_flow_py": -2e6, "fcf_margin": 0.05})
    ev("ZZQ2", "margin_expansion", 70, "medium", TODAY - timedelta(days=40), 2e8)
    ev("ZZQ3", "revenue_acceleration", 55, "medium", TODAY - timedelta(days=10), 9e10, {"rev_yoy": 0.3, "rev_yoy_prev": 0.1, "revenue": 1e9, "revenue_py": 7.7e8})
    for s, sec in (("ZZQ1", "Technology"), ("ZZQ2", "Energy"), ("ZZQ3", "Technology")):
        _exec(make, "INSERT INTO daily_fundamentals (symbol, date, sector) VALUES (%s, '2026-10-30', %s)", (s, sec))
        _exec(make, "INSERT INTO companies (symbol, company_name) VALUES (%s, %s)", (s, f"{s} Inc."))
    for i, (q, rev) in enumerate((("2026-03-31", 100e6), ("2026-06-30", 120e6))):
        _exec(make, """INSERT INTO fundamental_feature (symbol, quarter_end, method_version, available_at, availability_basis, metrics, score, components, data_quality, source)
                       VALUES ('ZZQ1', %s, 'inflection_v1', %s, 'earnings_calendar', %s, %s, %s, %s, 'quarterly_fundamentals')""",
              (q, TODAY - timedelta(days=60 - 30 * i), Json({"revenue": rev, "op_margin": 0.1 + 0.05 * i, "rev_yoy": 0.1 * (i + 1), "net_income": 5e6 * (i + 1)}), 40 + 30 * i, Json([]), "ok" if i else "partial"))
    yield InflectionService(make)
    for t in ("fundamental_inflection_event", "fundamental_feature", "daily_fundamentals", "companies"):
        _exec(make, f"DELETE FROM {t} WHERE symbol LIKE 'ZZQ%%'")


def zz(r):
    return [e for e in r["events"] if e["symbol"].startswith("ZZQ")]


def test_events_come_back_shaped_for_the_ui_with_provenance_and_freshness(svc):
    r = svc.list_events(q="ZZQ", today=TODAY)
    e = next(x for x in zz(r) if x["symbol"] == "ZZQ1" and x["event_type"] == "margin_expansion")
    assert r["method_version"] == "inflection_v1" and "Research signals" in r["notice"]
    assert (e["company_name"], e["sector"], e["label"], e["freshness_days"], e["availability_basis"], e["source"]) == ("ZZQ1 Inc.", "Technology", LABELS["margin_expansion"], 3, "earnings_calendar", "quarterly_fundamentals")
    assert e["reaction_2d"] == pytest.approx(0.04) and e["market_cap"] == 5e9 and e["valuation"]["ps_ratio"] == 3.0 and e["valuation"]["ps_ratio_at_publication"] == 2.8
    assert e["key_metrics"][0] == {"label": "Operating margin", "current": 0.2, "previous": 0.1, "unit": "pct"} and e["explanation"] == ["line one"]


def test_filters_sort_and_paging(svc):
    assert {e["symbol"] for e in zz(svc.list_events(q="ZZQ", event_types=["margin_expansion"], today=TODAY))} == {"ZZQ1", "ZZQ2"}
    assert {e["symbol"] for e in zz(svc.list_events(q="ZZQ", sector="Energy", today=TODAY))} == {"ZZQ2"}
    assert {e["symbol"] for e in zz(svc.list_events(q="ZZQ", severity="high", today=TODAY))} == {"ZZQ1"}
    assert {e["event_type"] for e in zz(svc.list_events(q="ZZQ", min_strength=65, today=TODAY))} == {"margin_expansion"}
    assert {e["symbol"] for e in zz(svc.list_events(q="ZZQ", since=TODAY - timedelta(days=15), today=TODAY))} == {"ZZQ1", "ZZQ3"}
    assert {e["symbol"] for e in zz(svc.list_events(q="ZZQ", min_market_cap=1e9, max_market_cap=2e10, today=TODAY))} == {"ZZQ1"}
    by_strength = [e["strength"] for e in zz(svc.list_events(q="ZZQ", sort="strength", today=TODAY))]
    assert by_strength == sorted(by_strength, reverse=True)
    page = svc.list_events(q="ZZQ", limit=2, offset=0, today=TODAY)
    rest = svc.list_events(q="ZZQ", limit=2, offset=2, today=TODAY)
    assert page["total"] == rest["total"] == 4 and page["count"] == 2 and rest["count"] == 2 and not ({e["id"] for e in page["events"]} & {e["id"] for e in rest["events"]})


def test_the_stock_view_has_events_and_a_quarterly_trend_with_quality_flags(svc):
    s = svc.stock("zzq1", today=TODAY)
    assert s["available"] and len(s["events"]) == 2 and [t["quarter_end"] for t in s["trend"]] == ["2026-03-31", "2026-06-30"]
    assert s["latest_score"] == 70.0 and s["trend"][0]["data_quality"] == "partial" and s["trend"][1]["revenue"] == 120e6 and s["data_status"]["partial_quarters"] == 1
    none = svc.stock("ZZQ9", today=TODAY)
    assert none["available"] is False and none["events"] == [] and none["trend"] == []


def test_the_summary_counts_by_type_and_severity_within_the_window(svc):
    s = svc.summary(days=30, today=TODAY)
    me = next(t for t in s["by_type"] if t["event_type"] == "margin_expansion")
    assert s["available"] and me["high"] >= 1 and me["total"] >= 1 and s["newest_event"] is not None


def test_the_router_answers_calmly_when_the_tables_are_not_installed_yet():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from auth.dependencies import CurrentUser, require_authenticated_user
    from routers import inflection as R

    class Missing(Exception):
        pass

    class Broken:
        def list_events(self, **k): raise Missing('relation "fundamental_inflection_event" does not exist')
        def summary(self, **k): raise Missing('relation "fundamental_feature" does not exist')
        def stock(self, s): raise Missing('relation "fundamental_feature" does not exist')

    app = FastAPI()
    app.include_router(R.inflection_router)
    app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "o@x.y", "owner")
    app.dependency_overrides[R.get_inflection_service] = lambda: Broken()
    c = TestClient(app)
    for path in ("/api/inflection/events", "/api/inflection/summary", "/api/inflection/stock/AAPL"):
        r = c.get(path)
        assert r.status_code == 200 and r.json()["available"] is False and "not installed" in r.json()["reason"]
    assert c.get("/api/inflection/events?event_type=nope").status_code == 422
    assert c.get("/api/inflection/stock/BAD!").status_code == 422
    app.dependency_overrides.pop(require_authenticated_user)
    assert TestClient(app).get("/api/inflection/events").status_code in (401, 403)
