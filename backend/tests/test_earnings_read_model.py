"""The one earnings read model (services/earnings_read_model.py).

Pure part: statuses (reported / upcoming with confidence confirmed|unverified / awaiting_results), `next`, past limits,
explicit empty entries, and the symbol-narrowed CTEs being the approved CTEs plus nothing but the two symbol filters.

Real-Postgres part (self-skips without a database; CI's db-bootstrap-integration job runs it), the confidence scenarios:
confirmed; a date missing from a COMPLETE newer check inside the period it answered (superseded) and before it
(kept, unverified); a FAILED check (writes nothing: the previous complete answer stands, its age visible); a PARTIAL
check (unverified); a date MOVED by the vendor; NO known upcoming date; META-style; parity with the board's and the
Screener's form of the rule (date AND confidence, on a fully migrated database); narrowed == unnarrowed; one statement.

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


def _row(sym, d, fetched, est=1.0, act=None, surp=None, confirmed=True, complete=True):
    latest = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
    return (sym, d, est, act, surp, fetched, latest, confirmed, complete)


# ------------------------------------------------------------------------------------------------------------- pure
def test_statuses_next_and_provenance():
    f_old, f_new = datetime(2026, 10, 7, tzinfo=UTC), datetime(2026, 10, 21, tzinfo=UTC)
    rows = [_row("X", date(2026, 4, 29), f_new, act=7.31, surp=5.0, complete=False),
            _row("X", date(2026, 7, 29), f_new, act=6.18, complete=False),
            _row("X", date(2026, 10, 20), f_new, complete=False),              # past, no EPS stored yet
            _row("X", date(2026, 10, 28), f_old, confirmed=False, complete=False),
            _row("X", date(2027, 1, 27), f_new, confirmed=False, complete=False)]
    out = RM.assemble(rows, ["X"], TODAY, past_limit=12)["X"]
    assert [(d["date"], d["status"], d["confidence"]) for d in out["earnings_dates"]] == [
        ("2026-04-29", "reported", None), ("2026-07-29", "reported", None), ("2026-10-20", "awaiting_results", None),
        ("2026-10-28", "upcoming", "unverified"), ("2027-01-27", "upcoming", "unverified")]
    assert out["next"] == {"date": "2026-10-28", "confidence": "unverified", "listed_at": f_old.isoformat()}
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


T1, T2 = _ts(2026, 10, 7), _ts(2026, 10, 21)                     # an older and the newest check
FIXTURE = [
    # CONF: the newest check is complete and lists the date
    ("CONF", date(2026, 7, 28), 1.0, 1.1, 5.0, T2), ("CONF", date(2026, 11, 5), 1.2, None, None, _ts(2026, 10, 21, s=1)),
    # DROP: listed by T1; the COMPLETE T2 check answered from 2026-07-20 on and no longer lists it -> superseded
    ("DROP", date(2026, 11, 4), 1.0, None, None, T1),
    ("DROP", date(2026, 7, 20), 0.9, 1.0, 2.0, T2), ("DROP", date(2027, 2, 10), 1.1, None, None, _ts(2026, 10, 21, s=1)),
    # OUTSIDE: listed by T1; the complete T2 check answered only from 2027-02-10 on -> kept, unverified (not ruled out)
    ("OUTSIDE", date(2026, 11, 4), 1.0, None, None, T1),
    ("OUTSIDE", date(2027, 2, 10), 1.1, None, None, T2), ("OUTSIDE", date(2027, 5, 10), 1.2, None, None, _ts(2026, 10, 21, s=1)),
    # FAIL: one complete check at T1; a later check failed and wrote nothing -> the T1 answer stands (its age is visible)
    ("FAIL", date(2026, 7, 30), 0.5, 0.6, 3.0, T1), ("FAIL", date(2026, 11, 6), 0.7, None, None, _ts(2026, 10, 7, s=1)),
    # PART: complete at T1; the T2 check wrote ONE row of three (partial) -> nothing superseded, nothing confirmed
    ("PART", date(2026, 7, 31), 0.5, 0.6, 3.0, T1), ("PART", date(2026, 11, 9), 0.7, None, None, _ts(2026, 10, 7, s=1)),
    ("PART", date(2026, 11, 12), 0.7, None, None, T2),
    # META-style partial: the newest check returned only past reports
    ("META", date(2026, 10, 28), 6.5, None, None, T1),
    ("META", date(2026, 7, 29), 5.9, 6.18, 4.7, T2), ("META", date(2026, 4, 29), 6.7, 7.31, 9.1, _ts(2026, 10, 21, s=1)),
    ("META", date(2026, 1, 28), 8.2, 8.88, 8.3, _ts(2026, 10, 21, s=2)),
    # MOVED (AAPL-style): 10-29 by T1, moved to 11-02 by the complete T2 check
    ("AAPL", date(2026, 10, 29), 1.98, None, None, _ts(2026, 10, 6)),
    ("AAPL", date(2026, 11, 2), 1.98, None, None, _ts(2026, 10, 20)),
    ("AAPL", date(2026, 7, 30), 1.89, 2.02, 6.74, _ts(2026, 10, 20, s=1)),
    ("AAPL", date(2026, 4, 30), 1.94, 2.01, 3.46, _ts(2026, 10, 20, s=2)),
    # NONE: only reported history; the newest check lists no upcoming date
    ("NONE", date(2026, 7, 15), 1.0, 1.0, 0.0, T2), ("NONE", date(2026, 4, 15), 1.0, 1.1, 10.0, _ts(2026, 10, 21, s=1)),
]


@pytest.fixture(scope="module")
def db():
    with _schema() as (admin, connect):
        cur = admin.cursor()
        for r in FIXTURE:
            cur.execute("INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at) VALUES (%s,%s,%s,%s,%s,%s)", r)
        admin.commit()
        yield admin, connect


def _model(db, *syms):
    return RM.EarningsReadModel(db[1]).for_symbols(list(syms), today=TODAY)


def test_confirmed_date(db):
    m = _model(db, "CONF")["CONF"]
    assert m["next"]["date"] == "2026-11-05" and m["next"]["confidence"] == "confirmed" and m["schedule_complete"] is True


def test_date_missing_from_a_complete_newer_check(db):
    m = _model(db, "DROP", "OUTSIDE")
    # inside the period the complete check answered for: superseded (never shown as upcoming); the newer date is confirmed
    assert m["DROP"]["next"] == {"date": "2027-02-10", "confidence": "confirmed", "listed_at": m["DROP"]["next"]["listed_at"]}
    assert "2026-11-04" not in [d["date"] for d in m["DROP"]["earnings_dates"]]
    # before that period (the check did not answer for it): kept, labelled unverified -- never silently dropped or trusted
    assert m["OUTSIDE"]["next"]["date"] == "2026-11-04" and m["OUTSIDE"]["next"]["confidence"] == "unverified"
    assert m["OUTSIDE"]["next"]["listed_at"].startswith("2026-10-07")


def test_failed_check_is_not_evidence_and_its_age_shows(db):
    m = _model(db, "FAIL")["FAIL"]
    assert m["next"]["date"] == "2026-11-06" and m["next"]["confidence"] == "confirmed"
    assert m["latest_fetch_at"].startswith("2026-10-07")                 # the last check that stored anything


def test_partial_check_confirms_nothing_and_supersedes_nothing(db):
    m = _model(db, "PART", "META")
    assert [(d["date"], d["confidence"]) for d in m["PART"]["earnings_dates"] if d["status"] == "upcoming"] == [
        ("2026-11-09", "unverified"), ("2026-11-12", "unverified")]
    assert m["PART"]["schedule_complete"] is False
    assert m["META"]["next"]["date"] == "2026-10-28" and m["META"]["next"]["confidence"] == "unverified"
    assert m["META"]["next"]["listed_at"].startswith("2026-10-07") and m["META"]["latest_fetch_at"].startswith("2026-10-21")


def test_date_moved_by_the_vendor(db):
    a = _model(db, "AAPL")["AAPL"]
    assert a["next"]["date"] == "2026-11-02" and a["next"]["confidence"] == "confirmed"
    assert "2026-10-29" not in [d["date"] for d in a["earnings_dates"]]


def test_no_known_upcoming_date(db):
    m = _model(db, "NONE", "NEWCO")
    assert m["NONE"]["next"] is None and [d["status"] for d in m["NONE"]["earnings_dates"]] == ["reported", "reported"]
    assert m["NEWCO"]["next"] is None and m["NEWCO"]["earnings_dates"] == [] and m["NEWCO"]["latest_fetch_at"] is None


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
    out = RM.EarningsReadModel(lambda: C(connect())).for_symbols(["META", "AAPL", "CONF", "DROP", "NEWCO"], today=TODAY)
    assert out["NEWCO"]["next"] is None and out["NEWCO"]["earnings_dates"] == []
    assert len(executed) == 2                                       # SET TRANSACTION READ ONLY + the one read


def test_parity_with_the_board_and_screener_forms_of_the_rule(db):
    admin, connect = db
    cur = admin.cursor()
    cur.execute(f"SET search_path TO {connect().info.dsn_parameters.get('options', '').split('=')[-1]}")
    syms = sorted({r[0] for r in FIXTURE})
    out = RM.EarningsReadModel(connect).for_symbols(syms, today=TODAY)
    # the board: MIN(report_date) over current rows on/after the anchor (unnarrowed CTEs)
    cur.execute("WITH " + EC.EARNINGS_CURRENT_CTES + " SELECT symbol, MIN(report_date) FROM earnings_current "
                "WHERE is_current AND report_date >= %s GROUP BY symbol", (TODAY,))
    board = {s: d.isoformat() for s, d in cur.fetchall()}
    assert {s: out[s]["next"]["date"] for s in out if out[s]["next"]} == board
    # the Python reference of the rule
    ref_rows = [{"symbol": s, "report_date": d, "eps_actual": a, "fetched_at": f} for s, d, _, a, _, f in FIXTURE]
    for s in syms:
        ref = EC.next_current_report(ref_rows, s, TODAY)
        assert (out[s]["next"]["date"] if out[s]["next"] else None) == (ref.isoformat() if ref else None), s
    # confidence = the Python reference: in the latest fetch AND that fetch complete
    st = EC.classify(ref_rows)
    for s in syms:
        if out[s]["next"]:
            k = (s, date.fromisoformat(out[s]["next"]["date"]))
            assert out[s]["next"]["confidence"] == ("confirmed" if st[k].in_latest_fetch and st[k].latest_fetch_complete else "unverified"), s
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


# ------------------------------------------------------------------- cross-surface parity (fully migrated database)
def test_read_model_screener_and_board_show_the_same_date_and_confidence(monkeypatch):
    """Stock page / Watchlist / Earnings page (read model), Screener rows (DiscoveryService) and the board (Earnings
    page board rows): the same next date and the same confidence for a confirmed, an outside-window and a partial case.
    Far-future dates, so the board's CURRENT_DATE anchor and the Screener's session anchor select the same date."""
    sys.path.insert(0, os.path.dirname(__file__))
    import test_overview_movers as M
    from services import discovery_service as D
    from services import momentum_board_service as MB
    rows = [("C99", date(2099, 3, 2), 1.0, None, None, T2), ("C99", date(2099, 6, 1), 1.0, None, None, _ts(2026, 10, 21, s=1)),
            ("O99", date(2099, 3, 3), 1.0, None, None, T1),
            ("O99", date(2099, 9, 1), 1.0, None, None, T2), ("O99", date(2099, 12, 1), 1.0, None, None, _ts(2026, 10, 21, s=1)),
            ("P99", date(2099, 3, 4), 1.0, None, None, T1), ("P99", date(2099, 6, 4), 1.0, None, None, _ts(2026, 10, 7, s=1)),
            ("P99", date(2099, 7, 4), 1.0, None, None, T2)]
    syms = ["C99", "O99", "P99"]
    expect = {"C99": ("2099-03-02", "confirmed"), "O99": ("2099-03-03", "unverified"), "P99": ("2099-03-04", "unverified")}
    with M._fresh_db() as connect:
        conn = connect()
        cur = conn.cursor()
        for d in M._sessions():
            for s in syms:
                cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,10,10,10,10,10,1000000)", (s, d))
        for r in rows:
            cur.execute("INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at) VALUES (%s,%s,%s,%s,%s,%s)", r)
        today = date.today()
        cur.execute("INSERT INTO digest_runs (session_date, universe_n, up_n, down_n, counts, min_dv, top_n) VALUES (%s, 3, 1, 1, '{}'::jsonb, 0, 10)", (today,))
        for s in syms:
            cur.execute("INSERT INTO digest_stocks (session_date, symbol, category, close, ret1_pct, dv20, list_ranks) "
                        "VALUES (%s, %s, 'breakout', 10, 1.0, 5e7, '{\"gainers\": 1}'::jsonb)", (today, s))
        conn.commit()
        conn.close()

        model = RM.EarningsReadModel(connect).for_symbols(syms, today=TODAY)
        screener = {r["symbol"]: r for r in D.DiscoveryService(connect).search(D.Query(symbols=tuple(syms)))["rows"]}
        MB._board_cache.update({"data": None, "ts": 0.0})
        board = {r["symbol"]: r for r in MB.MomentumBoardService(connect).get_board(limit=10)["results"]}
        MB._board_cache.update({"data": None, "ts": 0.0})
        for s in syms:
            assert (model[s]["next"]["date"], model[s]["next"]["confidence"]) == expect[s], s
            assert (screener[s]["next_earnings_date"], screener[s]["next_earnings_confidence"]) == expect[s], s
            assert (board[s]["next_earnings_date"], board[s]["next_earnings_confidence"]) == expect[s], s
            assert screener[s]["earnings_checked_at"][:10] == model[s]["latest_fetch_at"][:10]
