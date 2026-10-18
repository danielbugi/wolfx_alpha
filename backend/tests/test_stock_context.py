"""Stock Context (S5): the Screener's own search for one symbol (exact filter inside the SQL), the stock-only MA200 with
its strict 200-session rule, the bounded session-calendar cache, and the route's validation/auth."""
import os
import sys
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.stock import get_discovery_service, stock_router  # noqa: E402
from services import discovery_service as D  # noqa: E402


# ------------------------------------------------------------------------------------------- shared engine: exact symbol
def test_exact_symbol_filter_is_part_of_the_sql_where_before_pagination():
    q = D.normalise(D.parse_params([("symbol", "aapl"), ("symbol", "msft"), ("page_size", "1")]))
    where, params = D.build_where(q)
    assert where.endswith("symbol = ANY(%s)") and params[-1] == ["AAPL", "MSFT"]
    sql = D.page_sql(where, D.order_clause("symbol", "asc"))
    assert sql.index("filtered AS (SELECT * FROM base WHERE") < sql.index("LIMIT %(limit)s")   # filtered first, then paged
    for bad in ([("symbol", "A;B")], [("symbol", "X" * 11)], [("symbol", "AAA")] * 51):
        with pytest.raises(D.InvalidRequest):
            D.normalise(D.parse_params(bad))


def test_ma20_and_ma50_distances_are_shared_metrics_from_session_aligned_indicators():
    assert D.METRICS["dist_sma20"].family == D.DERIVED and D.METRICS["dist_sma50"].family == D.DERIVED
    sql = D.page_sql("TRUE", D.order_clause("dist_sma50", "desc"))
    assert "sma_20::float8 AS sma_20" in sql and "t.date = %(s)s" in sql
    assert "CASE WHEN sma_50 > 0 THEN (close / sma_50 - 1) * 100 END AS dist_sma50" in sql
    assert "dist_sma200" not in D.METRICS                       # the 200-day average is stock-only, not a Screener metric


# --------------------------------------------------------------------------------------------- stock context: the search
class _FakeService:
    def __init__(self, rows, cursor):
        self.rows = rows
        self.cursor = cursor
        self.queries = []

    def search(self, qr, export=False):
        self.queries.append(qr)
        rows = [r for r in self.rows if r["symbol"] in qr.symbols]
        return {"session": "2026-10-16", "previous_session": "2026-10-15", "dollar_volume_window": {"from": "2026-09-21", "to": "2026-10-16", "sessions": 20},
                "fundamentals_basis": D.fundamentals_basis(date(2026, 10, 16), qr.fundamentals_cutoff),
                "fundamentals_cutoff_utc": "x", "universe": 3, "coverage": {"rs": 3}, "recent_sessions": ["2026-10-16"], "rows": rows}

    def _run(self, fn):
        return fn(self.cursor)


class _Cur:
    """Answers the four queries stock_context/ma200_for make, in order."""

    def __init__(self, sessions, n_closes, avg, disc=0, newest=date(2026, 10, 16)):
        self.sessions, self.n_closes, self.avg, self.disc, self.newest = sessions, n_closes, avg, disc, newest
        self.last = None
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        self.last = sql

    def fetchone(self):
        if "MAX(date)" in self.last:
            return (self.newest,)
        if "price_discontinuities" in self.last:
            return (self.disc,)
        return (self.n_closes, self.avg)

    def fetchall(self):
        return [(d, 3000) for d in self.sessions]


def _sessions(n, end=date(2026, 10, 16)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


ROW = {"symbol": "AAA", "close": 110.0, "rs_20": 91.0, "rs_60": None, "market_cap": None, "fundamentals_after_close": True}


def test_context_returns_the_screeners_row_unchanged_and_only_accepts_session_and_cutoff():
    D._session_cache.clear()
    svc = _FakeService([ROW], _Cur(_sessions(250), 200, 100.0))
    ctx = D.stock_context(svc, "aaa", [("session", "2026-10-16"), ("fundamentals_cutoff", "session_close")])
    assert ctx["row"] is ROW and ctx["in_universe"] is True                     # identical object: no second calculation
    q = svc.queries[-1]
    assert q.symbols == ("AAA",) and q.session == date(2026, 10, 16) and q.fundamentals_cutoff == "session_close" and q.page_size == 1
    assert ctx["fundamentals_basis"]["cutoff_mode"] == "session_close"
    with pytest.raises(D.InvalidRequest, match="only session and fundamentals_cutoff"):
        D.stock_context(svc, "AAA", [("sort", "rs_20")])


def test_unknown_symbol_or_no_bar_on_the_session_is_unavailable_never_substituted():
    D._session_cache.clear()
    svc = _FakeService([ROW], _Cur(_sessions(250), 200, 100.0))
    ctx = D.stock_context(svc, "ZZZZ", [])
    assert ctx["in_universe"] is False and ctx["row"] is None and ctx["stock_only"]["dist_sma200"] is None
    assert ctx["unavailable_reason"] == "no bar for ZZZZ on the 2026-10-16 session"


# ------------------------------------------------------------------------------------------------------------ MA200 rule
def test_ma200_ok_only_with_200_valid_closes_and_no_discontinuity():
    D._session_cache.clear()
    m = D.ma200_for(_Cur(_sessions(260), 200, 100.0), "AAA", date(2026, 10, 16), 110.0, date(2026, 10, 16))
    assert m["state"] == "ok" and m["sma200"] == 100.0 and abs(m["value"] - 10.0) < 1e-9
    assert m["window_to"] == "2026-10-16" and m["window_from"] == _sessions(200)[0].isoformat()


@pytest.mark.parametrize("sessions,n,disc,reason", [
    (150, 150, 0, "only 150 market sessions stored in the lookback window"),
    (260, 182, 0, "182 of 200 sessions with a valid close"),
    (260, 200, 1, "a price discontinuity is recorded inside the window"),
])
def test_ma200_is_unavailable_with_an_explicit_reason_and_never_extends_the_lookback(sessions, n, disc, reason):
    D._session_cache.clear()
    cur = _Cur(_sessions(sessions), n, 100.0, disc)
    m = D.ma200_for(cur, "AAA", date(2026, 10, 16), 110.0, date(2026, 10, 16))
    assert m["state"] == "unavailable" and m["value"] is None and m["reason"] == reason
    if sessions >= 200:
        # the close query asks for exactly the last 200 sessions, never more
        assert any("date = ANY(%s)" in s for s in cur.executed)


def test_session_calendar_cache_is_bounded_and_keyed_by_session_and_newest_load():
    D._session_cache.clear()
    cur = _Cur(_sessions(260), 200, 100.0)
    D.real_sessions_up_to(cur, date(2026, 10, 16), date(2026, 10, 16))
    D.real_sessions_up_to(cur, date(2026, 10, 16), date(2026, 10, 16))
    assert sum("GROUP BY date" in s for s in cur.executed) == 1                    # cached within the TTL
    D.real_sessions_up_to(cur, date(2026, 10, 16), date(2026, 10, 19))            # a newer load changes the identity
    assert sum("GROUP BY date" in s for s in cur.executed) == 2
    assert D.SESSION_CACHE_TTL_S <= 900
    for i in range(70):
        D.real_sessions_up_to(cur, date(2026, 1, 1) + timedelta(days=i), date(2026, 10, 16))
    assert len(D._session_cache) <= 65


def test_ma200_definition_states_scope_coverage_and_adjustment():
    t = D.MA200_DEFINITION
    assert "200 market sessions" in t and "every one of those 200 sessions" in t and "not in the Screener" in t and "adjusted closes" in t


# ------------------------------------------------------------------------------------------------------------- the route
def _client(authenticated=True):
    app = FastAPI()
    app.include_router(stock_router)
    D._session_cache.clear()
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[get_discovery_service] = lambda: _FakeService([ROW], _Cur(_sessions(260), 200, 100.0))
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "collaborator")
    return TestClient(app)


def test_context_route_validates_symbol_and_parameters_and_keeps_auth():
    c = _client()
    r = c.get("/api/stock/AAA/context")
    assert r.status_code == 200 and r.json()["row"]["rs_20"] == 91.0 and r.json()["stock_only"]["dist_sma200"]["state"] == "ok"
    assert c.get("/api/stock/A;B/context").status_code in (404, 422)
    assert c.get("/api/stock/" + "X" * 11 + "/context").status_code == 422
    assert c.get("/api/stock/AAA/context?sort=rs_20").status_code == 422
    assert c.get("/api/stock/AAA/context?fundamentals_cutoff=later").status_code == 422
    assert c.get("/api/stock/ZZZZ/context").json()["in_universe"] is False
    assert c.post("/api/stock/AAA/context").status_code == 405
    assert _client(False).get("/api/stock/AAA/context").status_code == 401
