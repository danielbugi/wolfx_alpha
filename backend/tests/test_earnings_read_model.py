"""The one earnings read model (services/earnings_read_model.py).

Pure part: statuses (reported / upcoming, re-listed or not by the latest check / awaiting_results), `next`, past limits, explicit
empty entries, and the symbol-narrowed CTEs being the approved CTEs plus nothing but the two symbol filters.

Real-Postgres part (self-skips without a database; CI's db-bootstrap-integration job runs it): META-style (an upcoming
date listed by an OLDER fetch; the latest fetch returned no upcoming date, so it is kept but marked not re-listed), AAPL-style
(a moved date: the obsolete ghost row is never shown), a symbol without rows, parity with the board's and the
Screener's form of the same rule, narrowed == unnarrowed, and one statement for many symbols.

Routes: same /api/stock/{symbol}/earnings row shape plus provenance; the batch endpoint's bounds; no Yahoo anywhere."""
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from services import earnings_current as EC  # noqa: E402
from services import earnings_read_model as RM  # noqa: E402

TODAY = date(2026, 10, 25)
UTC = timezone.utc


def _row(sym, d, fetched, est=1.0, act=None, surp=None, in_latest=True, complete=True):
    latest = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
    return (sym, d, est, act, surp, fetched, latest, in_latest, complete)


# ------------------------------------------------------------------------------------------------------------- pure
def test_statuses_next_and_provenance():
    f_old, f_new = datetime(2026, 10, 7, tzinfo=UTC), datetime(2026, 10, 21, tzinfo=UTC)
    rows = [_row("X", date(2026, 4, 29), f_new, act=7.31, surp=5.0, complete=False),
            _row("X", date(2026, 7, 29), f_new, act=6.18, complete=False),
            _row("X", date(2026, 10, 20), f_new, complete=False),              # past, no EPS stored yet
            _row("X", date(2026, 10, 28), f_old, in_latest=False, complete=False),
            _row("X", date(2027, 1, 27), f_new, complete=False)]
    out = RM.assemble(rows, ["X"], TODAY, past_limit=12)["X"]
    assert [(d["date"], d["status"], d["listed_by_latest_check"]) for d in out["earnings_dates"]] == [
        ("2026-04-29", "reported", None), ("2026-07-29", "reported", None), ("2026-10-20", "awaiting_results", None),
        ("2026-10-28", "upcoming", False), ("2027-01-27", "upcoming", True)]
    assert out["next"] == {"date": "2026-10-28", "listed_by_latest_check": False, "listed_at": f_old.isoformat()}
    assert (out["source"], out["rule"], out["today"]) == ("earnings_calendar", "current_row_v1", "2026-10-25")
    assert out["schedule_complete"] is False and out["latest_fetch_at"].startswith("2026-10-21")
    assert "time" not in out["earnings_dates"][0] and "bmo" not in str(out).lower()       # no invented report time


def test_past_limit_keeps_the_most_recent_reports_and_every_upcoming_date():
    f = datetime(2026, 10, 21, tzinfo=UTC)
    rows = [_row("X", date(2024 + i // 4, 1 + 3 * (i % 4), 15), f, act=1.0) for i in range(10)] + [_row("X", date(2026, 11, 2), f)]
    out = RM.assemble(rows, ["X"], TODAY, past_limit=3)["X"]
    assert [d["status"] for d in out["earnings_dates"]] == ["reported"] * 3 + ["upcoming"]
    assert [d["date"] for d in out["earnings_dates"]] == ["2025-10-15", "2026-01-15", "2026-04-15", "2026-11-02"]


def test_a_symbol_without_rows_is_an_explicit_unknown_never_a_guess():
    out = RM.assemble([], ["NEWCO"], TODAY, past_limit=12)["NEWCO"]
    assert out == {"symbol": "NEWCO", "source": "earnings_calendar", "rule": "current_row_v1", "today": "2026-10-25",
                   "latest_fetch_at": None, "schedule_complete": None, "next": None, "earnings_dates": []}


def test_the_narrowed_ctes_are_the_approved_rule_plus_only_the_symbol_filters():
    undo = (RM.FILTERED_CTES
            .replace("FROM earnings_calendar WHERE symbol = ANY(%(symbols)s) GROUP BY symbol", "FROM earnings_calendar GROUP BY symbol")
            .replace("FROM (SELECT * FROM earnings_calendar WHERE symbol = ANY(%(symbols)s)) e JOIN", "FROM earnings_calendar e JOIN"))
    assert undo == EC.EARNINGS_CURRENT_CTES


def test_bounds():
    m = RM.EarningsReadModel(lambda: None)
    assert m.for_symbols([]) == {}
    with pytest.raises(ValueError):
        m.for_symbols([f"S{i}" for i in range(51)])
    with pytest.raises(ValueError):
        m.for_symbols(["A"], past_limit=41)


def test_no_product_route_reads_yahoo():
    for rel in ("backend/routers/stock.py", "backend/routers/earnings.py", "backend/services/earnings_read_model.py"):
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert "yfinance" not in src and "earnings_service" not in src, rel
    assert not os.path.exists(os.path.join(ROOT, "backend", "services", "earnings_service.py"))


# ---------------------------------------------------------------------------------------------------- real Postgres
MIGRATIONS = ["create_trading_schema.sql", "add_earnings_calendar_table.sql"]


@contextmanager
def _schema():
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    try:
        args = dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                    user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
        admin = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "earn_rm_" + uuid.uuid4().hex[:10]
    cur = admin.cursor()
    cur.execute(f'CREATE SCHEMA "{schema}"')
    cur.execute(f'SET search_path TO "{schema}"')
    for name in MIGRATIONS:
        with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
            cur.execute(fh.read())
    admin.commit()
    try:
        yield admin, (lambda: psycopg2.connect(options=f"-c search_path={schema}", **args))
    finally:
        admin.rollback()
        admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.commit()
        admin.close()


def _ts(y, m, d, h=23, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


FIXTURE = [
    # META-style: 10-28 listed by the 10-07 fetch; the newest fetch (10-21) wrote only past reports -> incomplete answer
    ("META", date(2026, 10, 28), 6.5, None, None, _ts(2026, 10, 7)),
    ("META", date(2026, 7, 29), 5.9, 6.18, 4.7, _ts(2026, 10, 21)),
    ("META", date(2026, 4, 29), 6.7, 7.31, 9.1, _ts(2026, 10, 21, s=1)),
    ("META", date(2026, 1, 28), 8.2, 8.88, 8.3, _ts(2026, 10, 21, s=2)),
    # AAPL-style: the report moved from 10-29 (old fetch) to 11-02 (newest, complete fetch) -> the ghost is never shown
    ("AAPL", date(2026, 10, 29), 1.98, None, None, _ts(2026, 10, 6)),
    ("AAPL", date(2026, 11, 2), 1.98, None, None, _ts(2026, 10, 20)),
    ("AAPL", date(2026, 7, 30), 1.89, 2.02, 6.74, _ts(2026, 10, 20, s=1)),
    ("AAPL", date(2026, 4, 30), 1.94, 2.01, 3.46, _ts(2026, 10, 20, s=2)),
]


@pytest.fixture(scope="module")
def db():
    with _schema() as (admin, connect):
        cur = admin.cursor()
        for r in FIXTURE:
            cur.execute("INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at) VALUES (%s,%s,%s,%s,%s,%s)", r)
        admin.commit()
        yield admin, connect


def test_meta_style_stale_row_is_kept_but_marked_not_relisted(db):
    _, connect = db
    m = RM.EarningsReadModel(connect).for_symbols(["META"], today=TODAY)["META"]
    assert m["next"] == {"date": "2026-10-28", "listed_by_latest_check": False, "listed_at": m["next"]["listed_at"]}
    assert m["next"]["listed_at"].startswith("2026-10-07") and m["latest_fetch_at"].startswith("2026-10-21")
    assert m["schedule_complete"] is False
    assert [d["status"] for d in m["earnings_dates"]] == ["reported", "reported", "reported", "upcoming"]


def test_aapl_style_moved_date_shows_only_the_current_date(db):
    _, connect = db
    a = RM.EarningsReadModel(connect).for_symbols(["aapl"], today=TODAY)["AAPL"]
    assert a["next"]["date"] == "2026-11-02" and a["next"]["listed_by_latest_check"] is True
    assert "2026-10-29" not in [d["date"] for d in a["earnings_dates"]]
    assert a["schedule_complete"] is True


def test_unknown_symbol_and_one_statement_for_many_symbols(db):
    _, connect = db
    executed = []

    class C:
        def __init__(self, conn):
            self._c = conn

        def cursor(self):
            cur = self._c.cursor()

            class K:
                def execute(self, *a):
                    executed.append(a[0])
                    return cur.execute(*a)

                def __getattr__(self, n):
                    return getattr(cur, n)
            return K()

        def rollback(self):
            self._c.rollback()

        def close(self):
            self._c.close()
    out = RM.EarningsReadModel(lambda: C(connect())).for_symbols(["META", "AAPL", "NEWCO"], today=TODAY)
    assert out["NEWCO"]["next"] is None and out["NEWCO"]["earnings_dates"] == []
    assert len(executed) == 2                                       # SET TRANSACTION READ ONLY + the one read


def test_parity_with_the_board_and_screener_forms_of_the_rule(db):
    admin, connect = db
    cur = admin.cursor()
    cur.execute(f"SET search_path TO {connect().info.dsn_parameters.get('options', '').split('=')[-1]}")
    out = RM.EarningsReadModel(connect).for_symbols(["META", "AAPL"], today=TODAY)
    # the board: MIN(report_date) over current rows on/after the anchor (unnarrowed CTEs)
    cur.execute("WITH " + EC.EARNINGS_CURRENT_CTES + " SELECT symbol, MIN(report_date) FROM earnings_current "
                "WHERE is_current AND report_date >= %s GROUP BY symbol", (TODAY,))
    board = {s: d.isoformat() for s, d in cur.fetchall()}
    assert {s: out[s]["next"]["date"] for s in out} == board
    # the Python reference of the rule
    ref_rows = [{"symbol": s, "report_date": d, "eps_actual": a, "fetched_at": f} for s, d, _, a, _, f in FIXTURE]
    for s in ("META", "AAPL"):
        assert out[s]["next"]["date"] == EC.next_current_report(ref_rows, s, TODAY).isoformat()
    # narrowed == unnarrowed for every current row of the requested symbols
    cur.execute("WITH " + EC.EARNINGS_CURRENT_CTES + " SELECT symbol, report_date FROM earnings_current WHERE is_current ORDER BY 1, 2")
    full = [(s, d.isoformat()) for s, d in cur.fetchall()]
    assert full == [(s, d["date"]) for s in sorted(out) for d in out[s]["earnings_dates"]]
    admin.rollback()


# ------------------------------------------------------------------------------------------------------------ routes
class _Fake:
    def __init__(self):
        self.calls = []

    def for_symbols(self, symbols, past_limit=12, today=None):
        self.calls.append((list(symbols), past_limit))
        return RM.assemble([], [s.upper() for s in symbols], TODAY, past_limit)


def _app(fake, auth=True):
    from routers.earnings import earnings_router, get_earnings_read_model as get_batch
    from routers.stock import get_earnings_read_model as get_single, stock_router
    app = FastAPI()
    app.include_router(stock_router)
    app.include_router(earnings_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    if auth:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    app.dependency_overrides[get_single] = lambda: fake
    app.dependency_overrides[get_batch] = lambda: fake
    return TestClient(app)


def test_stock_earnings_route_keeps_its_shape_and_adds_provenance():
    fake = _Fake()
    r = _app(fake).get("/api/stock/meta/earnings", params={"limit": 16})
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "META" and body["earnings_dates"] == [] and body["source"] == "earnings_calendar"
    assert {"next", "latest_fetch_at", "schedule_complete", "today", "rule"} <= set(body)
    assert fake.calls == [(["meta"], 16)]


def test_batch_route_is_one_call_bounded_and_validated():
    fake = _Fake()
    c = _app(fake)
    r = c.get("/api/earnings", params=[("symbol", "AAPL"), ("symbol", "META")])
    assert r.status_code == 200 and set(r.json()["symbols"]) == {"AAPL", "META"} and len(fake.calls) == 1
    assert c.get("/api/earnings", params=[("symbol", f"S{i}") for i in range(51)]).status_code == 422
    assert c.get("/api/earnings", params=[("symbol", "BAD SYM")]).status_code == 422
    assert c.get("/api/earnings").status_code == 422


def test_routes_stay_authenticated():
    c = _app(_Fake(), auth=False)
    assert c.get("/api/earnings", params={"symbol": "AAPL"}).status_code == 401
    assert c.get("/api/stock/AAPL/earnings").status_code == 401
