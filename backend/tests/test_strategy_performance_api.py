"""Generic strategy performance API: auth on every route, input validation before any query, the `{value, n, state}` contract, unavailable
dimensions answering not_available (never zero, never a query), strategy-neutrality (no Donchian-specific path or code), and real-Postgres
behaviour in a throwaway schema (skips only when Postgres is unreachable; CI's real-Postgres job treats a skip as a failure)."""
import os
import re
import sys
import uuid
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers import strategy_intelligence as si_router  # noqa: E402
from routers.strategy_performance import get_strategy_performance_service, strategy_performance_router  # noqa: E402
from services.strategy_performance_service import ALL_DIMENSIONS, StrategyPerformanceService  # noqa: E402

AVAILABLE = ["direction", "exit", "quality_grade", "sector", "signal_month", "holding_bars", "model_scored"]
UNAVAILABLE = ["market_regime", "volatility_regime", "relative_strength", "earnings_proximity", "catalyst", "ml_score_bucket"]
BASE = "/api/strategies/any_strategy/v1/performance"
ROUTES = ["/api/strategies/performance/contract", BASE, BASE + "/outcomes", BASE + "/breakdowns", BASE + "/breakdowns/direction"]


def _app(service=None, authenticated=True, with_intelligence=False):
    app = FastAPI()
    if with_intelligence:
        app.include_router(si_router.strategy_intelligence_router)
    app.include_router(strategy_performance_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    if service is not None:
        app.dependency_overrides[get_strategy_performance_service] = lambda: service
        if with_intelligence:
            app.dependency_overrides[si_router.get_strategy_intelligence_service] = lambda: service
    return TestClient(app)


# ----------------------------------------------------------------------------------------------- no database
@pytest.mark.parametrize("route", ROUTES)
def test_every_route_requires_authentication(route):
    r = _app(service=object(), authenticated=False).get(route)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_authenticated"


def test_every_route_is_a_get_and_the_surface_is_read_only():
    methods = {m for r in strategy_performance_router.routes for m in r.methods}
    assert methods == {"GET"}


def test_no_endpoint_or_code_is_specific_to_one_strategy():
    paths = [r.path for r in strategy_performance_router.routes]
    assert paths and not any("donchian" in p.lower() or "breakout" in p.lower() for p in paths)
    for rel in ("backend/routers/strategy_performance.py", "backend/services/strategy_performance_service.py"):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            assert not re.search(r"donchian|breakout", fh.read(), re.I), rel


def test_the_contract_is_static_and_lists_what_is_unavailable_and_why():
    body = _app(service=StrategyPerformanceService(lambda: None)).get("/api/strategies/performance/contract").json()
    assert sorted(body["dimensions"]) == sorted(AVAILABLE)
    assert sorted(body["unavailable_dimensions"]) == sorted(UNAVAILABLE)
    for k, v in body["unavailable_dimensions"].items():
        assert v["available"] is False and v["state"] == "not_available" and v["requires"], k
    assert body["min_sample_size"] == 5 and body["metric_shape"]["state"] == ["ok", "preliminary", "no_data", "not_available"]
    assert "max_favourable_excursion" in body["unavailable_metrics"]
    assert list(ALL_DIMENSIONS) == body["dimension_order"]


@pytest.mark.parametrize("route", [
    "/api/strategies/Bad-Key/v1/performance",
    "/api/strategies/ok/v1;DROP/performance",
    "/api/strategies/ok/v1/performance/breakdowns/Direction",
    "/api/strategies/ok/v1/performance/breakdowns/a;b",
])
def test_invalid_path_input_is_rejected_before_any_query(route):
    assert _app(service=StrategyPerformanceService(lambda: None)).get(route).status_code == 422


class _FakeCursor:
    def __init__(self, log):
        self.log = log
        self.rows = []
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        if "FROM strategies WHERE strategy_key" in sql:
            self.rows = [{"id": 7, "strategy_key": params[0], "strategy_version": params[1], "description": None, "created_at": None}]
        else:
            raise AssertionError("no further query is allowed here: " + sql[:60])

    def fetchall(self):
        return self.rows


class _FakeConn:
    def __init__(self):
        self.log = []

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self.log)

    def close(self):
        pass


def test_an_unknown_dimension_is_422_and_runs_no_query():
    conn = _FakeConn()
    r = _app(service=StrategyPerformanceService(lambda: conn)).get(BASE + "/breakdowns/symbol")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_dimension"
    assert r.json()["detail"]["dimensions"] == list(ALL_DIMENSIONS) and conn.log == []


@pytest.mark.parametrize("dim", UNAVAILABLE)
def test_an_unavailable_dimension_answers_not_available_after_only_the_strategy_lookup(dim):
    conn = _FakeConn()
    r = _app(service=StrategyPerformanceService(lambda: conn)).get(f"{BASE}/breakdowns/{dim}")
    body = r.json()
    assert r.status_code == 200 and body["state"] == "not_available" and body["groups"] == [] and body["requires"]
    assert len(conn.log) == 1 and "FROM strategies" in conn.log[0]


# --------------------------------------------------------------------------------------------- real Postgres
MIGRATIONS = ["create_trading_schema.sql", "add_market_data_tables.sql", "add_ml_dataset_tables.sql", "add_signal_ledger_tables.sql",
              "add_strategy_identity_release_a.sql", "add_signal_ledger_eval_flags.sql"]


def _args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")), dbname=os.getenv("DB_NAME", "trading_production"),
                user=os.getenv("DB_USER", "trading_user"), password=os.getenv("DB_PASSWORD", ""), connect_timeout=3)


@pytest.fixture(scope="module")
def pg():
    import psycopg2
    try:
        admin = psycopg2.connect(**_args())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "perf_api_" + uuid.uuid4().hex[:10]
    try:
        cur = admin.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        for name in MIGRATIONS:
            with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
                cur.execute(fh.read())
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise

    def connect():
        return psycopg2.connect(options=f"-c search_path={schema}", **_args())

    def run(sql, params=None):
        c = connect()
        try:
            with c.cursor() as cu:
                cu.execute(sql, params)
                out = cu.fetchall() if cu.description else []
            c.commit()
            return out
        finally:
            c.close()

    yield connect, run
    admin.rollback()
    admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    admin.commit()
    admin.close()


class Seed:
    def __init__(self, run, key, version="v1"):
        self.run, self.key, self.version = run, key, version
        self.sid = run("INSERT INTO strategies (strategy_key, strategy_version, description) VALUES (%s, %s, 't') RETURNING id", (key, version))[0][0]
        self.n = 0

    def signal(self, status="open", r=None, bars=None, direction=1, grade="A", sector="Technology", model=None, mae=None,
               d=date(2099, 7, 1), evaluation_flag=None):
        self.n += 1
        resolved = d + timedelta(days=bars or 1) if status != "open" else None
        self.run("""INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price, target1_price, target2_price,
                    target3_price, sector, quality_grade, status, outcome_r, mae_r, resolved_date, bars_held, last_evaluated_date,
                    strategy_id, strategy_version, model_version, evaluation_flag)
                    VALUES (%s,%s,%s,100,2,96,104,108,112,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (f"{self.key[:6].upper()}{self.n}", d, direction, sector, grade, status, r, mae, resolved, bars, d, self.sid, self.version,
                  model, evaluation_flag))


@pytest.fixture(scope="module")
def world(pg):
    connect, run = pg
    a = Seed(run, "alpha_trend")
    for i in range(6):                                                           # 6 resolved winners (target1, +1R), bullish, grade A
        a.signal("target1", 1.0, 3, 1, "A", "Technology", "m_v1", -0.2, date(2099, 7, 1))
    for i in range(3):                                                           # 3 stopped (-1R), bearish, grade B, other sector
        a.signal("stopped", -1.0, 2, -1, "B", "Energy", None, -1.0, date(2099, 8, 3))
    a.signal("expired", 0.4, 18, 1, None, None, "unknown", -0.5, date(2099, 8, 4))   # legacy 'unknown' model, NULL grade/sector
    a.signal("open", None, None, 1, "A", "Technology", None, None, date(2099, 8, 5))
    a.signal("open", None, None, 1, "A", "Technology", None, None, date(2099, 8, 6), evaluation_flag="split_suspect")  # held
    b = Seed(run, "beta_reversion")                                              # a second strategy with one signal: proves no cross-mixing
    b.signal("stopped", -1.0, 2, -1, "C", "Utilities", None, -1.0)
    empty = Seed(run, "gamma_empty")
    return {"a": a, "b": b, "empty": empty, "service": StrategyPerformanceService(connect)}


@pytest.fixture
def client(world):
    return _app(service=world["service"], with_intelligence=True)


def _groups(body):
    return {g["bucket"]: g for g in body["groups"]}


def test_overview_follows_the_canonical_definitions_and_declares_what_is_missing(client):
    body = client.get("/api/strategies/alpha_trend/v1/performance").json()
    assert body["strategy"]["key"] == "alpha_trend" and body["min_sample_size"] == 5
    t, p = body["tracking"], body["performance"]
    assert (t["total_signals"], t["open"], t["held"], t["resolved"]) == (12, 2, 1, 10)
    assert p["resolved_n"] == 10 and p["winners"] == 6 and p["win_rate"] == {"value": 0.6, "n": 10, "state": "ok"}
    assert p["average_mfe_r"]["state"] == "not_available" and p["average_mfe_r"]["value"] is None
    assert body["exit_rules"]["state"] == "not_available"                       # no rules declared for this strategy: never inherited from another
    assert body["unavailable_dimensions"].keys() == set(UNAVAILABLE) and "max_favourable_excursion" in body["unavailable_metrics"]
    assert set(body["directions"]) == {"bullish", "bearish"}


def test_the_declared_exit_rules_are_served_only_for_the_strategy_they_belong_to(world):
    from strategy_analytics import performance as P
    assert P.exit_rules({"strategy_key": "donchian_breakout", "strategy_version": "v1"})["state"] == "declared"
    assert P.exit_rules({"strategy_key": "alpha_trend", "strategy_version": "v1"})["state"] == "not_available"


def test_outcome_stats_carry_counts_shares_and_the_milestone_semantics(client):
    o = client.get("/api/strategies/alpha_trend/v1/performance/outcomes").json()["outcomes"]
    assert o["resolved_n"] == 10
    assert o["terminal"]["stopped"]["count"] == 3 and o["terminal"]["target1"]["count"] == 6
    assert o["terminal"]["stopped"]["share"] == {"value": 0.3, "n": 10, "state": "ok"}
    assert o["terminal"]["expired"]["positive_count"] == 1 and "post-exit" in o["milestone_semantics"]


def test_breakdown_by_direction_and_exit_type(client):
    d = _groups(client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/direction").json())
    assert set(d) == {"bullish", "bearish"}
    assert d["bullish"]["resolved"] == 7 and d["bullish"]["winners"] == 6 and d["bearish"]["resolved"] == 3 and d["bearish"]["winners"] == 0
    assert d["bearish"]["win_rate"] == {"value": 0.0, "n": 3, "state": "preliminary"}              # a real 0 % with n=3, flagged preliminary
    assert d["bearish"]["profit_factor"] == {"value": 0.0, "n": 3, "state": "preliminary"}         # losers only: a real 0.0
    assert d["bullish"]["profit_factor"]["state"] == "not_available" and d["bullish"]["profit_factor"]["value"] is None   # no loser: undefined, not infinite
    e = _groups(client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/exit").json())
    assert {k: v["signals"] for k, v in e.items()} == {"target1": 6, "stopped": 3, "expired": 1, "open": 2}
    assert e["open"]["resolved"] == 0 and e["open"]["win_rate"] == {"value": None, "n": 0, "state": "no_data"}


def test_breakdown_by_grade_sector_month_holding_and_scored(client):
    g = _groups(client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/quality_grade").json())
    assert g["A"]["resolved"] == 6 and g["B"]["resolved"] == 3 and g["ungraded"]["resolved"] == 1
    assert g["A"]["average_r"] == {"value": 1.0, "n": 6, "state": "ok"}
    s = _groups(client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/sector").json())
    assert set(s) == {"Technology", "Energy", "unclassified"} and s["Energy"]["sum_r"]["value"] == -3.0
    m = _groups(client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/signal_month").json())
    assert set(m) == {"2099-07", "2099-08"} and m["2099-07"]["resolved"] == 6
    h = _groups(client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/holding_bars").json())
    assert h["01-05"]["resolved"] == 9 and h["16+"]["resolved"] == 1 and h["unresolved"]["resolved"] == 0
    sc = _groups(client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/model_scored").json())
    assert sc["scored"]["resolved"] == 6 and sc["unscored"]["resolved"] == 4         # legacy 'unknown' and NULL are unscored, never a model


def test_every_group_shows_its_sample_size_and_every_metric_has_the_value_n_state_shape(client):
    body = client.get("/api/strategies/alpha_trend/v1/performance/breakdowns").json()
    assert set(body["dimensions"]) == set(AVAILABLE) | set(UNAVAILABLE)
    for dim, out in body["dimensions"].items():
        for grp in out["groups"]:
            assert {"signals", "resolved"} <= set(grp)
            for m in ("win_rate", "average_r", "median_r", "sum_r", "profit_factor", "average_holding_bars", "average_mae_r"):
                assert set(grp[m]) >= {"value", "n", "state"}, (dim, m)
                if grp[m]["state"] in ("no_data", "not_available"):
                    assert grp[m]["value"] is None
                if grp[m]["state"] == "preliminary":
                    assert 0 < grp[m]["n"] < 5


def test_unavailable_dimensions_are_listed_with_what_they_require_and_never_with_groups(client):
    dims = client.get("/api/strategies/alpha_trend/v1/performance/breakdowns").json()["dimensions"]
    for d in UNAVAILABLE:
        assert dims[d]["state"] == "not_available" and dims[d]["groups"] == [] and dims[d]["requires"]


def test_an_empty_strategy_reports_no_data_not_zero(client):
    o = client.get("/api/strategies/gamma_empty/v1/performance").json()
    assert o["tracking"]["tracking_status"] == "no_signals_yet" and o["performance"]["win_rate"] == {"value": None, "n": 0, "state": "no_data"}
    d = client.get("/api/strategies/gamma_empty/v1/performance/breakdowns/direction").json()
    assert d["state"] == "no_data" and d["groups"] == []


def test_strategies_never_mix(client):
    d = _groups(client.get("/api/strategies/beta_reversion/v1/performance/breakdowns/direction").json())
    assert set(d) == {"bearish"} and d["bearish"]["signals"] == 1
    assert client.get("/api/strategies/beta_reversion/v1/performance").json()["tracking"]["total_signals"] == 1


def test_unknown_strategy_or_version_is_404_on_every_route(client):
    for route in ("/performance", "/performance/outcomes", "/performance/breakdowns", "/performance/breakdowns/direction"):
        r = client.get("/api/strategies/no_such_strategy/v1" + route)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "strategy_not_found", route
    assert client.get("/api/strategies/alpha_trend/v9/performance").status_code == 404


def test_the_existing_strategies_api_is_unchanged(client):
    listed = client.get("/api/strategies").json()["strategies"]
    assert {s["key"] for s in listed} >= {"alpha_trend", "beta_reversion", "gamma_empty"}
    s = client.get("/api/strategies/alpha_trend/v1/summary").json()
    assert s["performance"]["win_rate"] == {"value": 0.6, "n": 10, "state": "ok"} and "capabilities" in s
    assert client.get("/api/strategies/definitions").status_code == 200


def test_the_performance_view_and_the_existing_summary_agree(client):
    p = client.get("/api/strategies/alpha_trend/v1/performance").json()
    s = client.get("/api/strategies/alpha_trend/v1/summary").json()
    assert p["performance"] == s["performance"] and p["tracking"] == s["tracking"]
    total_by_dir = sum(g["resolved"] for g in client.get("/api/strategies/alpha_trend/v1/performance/breakdowns/direction").json()["groups"])
    assert total_by_dir == s["performance"]["resolved_n"]


def test_get_requests_do_not_change_the_ledger(world, pg, client):
    _, run = pg
    before = run("SELECT count(*), coalesce(sum(outcome_r),0), md5(string_agg(id::text || status, ',' ORDER BY id)) FROM signal_ledger")
    for route in ROUTES[1:] + ["/api/strategies/alpha_trend/v1/performance/breakdowns/sector"]:
        client.get(route.replace("any_strategy", "alpha_trend"))
    assert run("SELECT count(*), coalesce(sum(outcome_r),0), md5(string_agg(id::text || status, ',' ORDER BY id)) FROM signal_ledger") == before
