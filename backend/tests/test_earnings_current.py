"""Current-vs-superseded earnings dates (services/earnings_current.py) and the readers that use the rule.

Pure part: the Python reference rule on the situations that occur in production (reschedule to a later or an earlier
date, a pipeline retry, a failed fetch, a partial fetch, aged-out history, reported rows, new listings). Static part: the
Momentum Board and Discovery SQL read `earnings_current` and filter `is_current`; the SQL window equals the Python one.
Real-Postgres part (self-skips without a database; CI's db-bootstrap-integration job runs it): the SQL CTE gives exactly
the reference result on the same fixtures, and the REAL board and discovery queries return the current date."""
import os
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
sys.path.insert(0, BACKEND)

from services import earnings_current as EC  # noqa: E402

UTC = timezone.utc
RUN1 = datetime(2026, 10, 6, 20, 8, 10, tzinfo=UTC)       # an older updater run
RUN2 = datetime(2026, 10, 20, 19, 51, 59, tzinfo=UTC)     # the symbol's latest run (14 days later)


def row(sym, d, fetched, actual=None, est=1.0, surprise=None):
    return {"symbol": sym, "report_date": d, "eps_estimate": est, "eps_actual": actual, "surprise_pct": surprise, "fetched_at": fetched}


def aapl_rows():
    """The real AAPL shape on 2026-10-21: the vendor moved the report 10-29 -> 11-02; the 10-29 row was never removed."""
    return [
        row("AAPL", date(2026, 1, 29), RUN2, actual=2.84),
        row("AAPL", date(2026, 4, 30), RUN2 + timedelta(milliseconds=3), actual=2.01),
        row("AAPL", date(2026, 7, 30), RUN2 + timedelta(milliseconds=5), actual=2.02),
        row("AAPL", date(2026, 10, 29), RUN1),                                        # obsolete, earlier than the new date
        row("AAPL", date(2026, 11, 2), RUN2 + timedelta(milliseconds=8)),
    ]


def state(rows):
    return EC.classify(rows)


# ------------------------------------------------------------------------------------------------------------ the rule
def test_reschedule_to_a_later_date_hides_the_obsolete_earlier_date():
    st = state(aapl_rows())
    assert st[("AAPL", date(2026, 10, 29))].is_current is False
    assert st[("AAPL", date(2026, 11, 2))].is_current is True
    assert EC.next_current_report(aapl_rows(), "AAPL", date(2026, 10, 21)) == date(2026, 11, 2)


def test_reschedule_to_an_earlier_date_hides_the_obsolete_later_date():
    rows = [row("ABT", date(2026, 7, 16), RUN2, actual=1.31), row("ABT", date(2026, 10, 23), RUN2),
            row("ABT", date(2026, 10, 28), RUN1)]                                       # moved 10-28 -> 10-23
    st = state(rows)
    assert (st[("ABT", date(2026, 10, 23))].is_current, st[("ABT", date(2026, 10, 28))].is_current) == (True, False)


def test_a_latest_fetch_whose_only_dates_are_before_its_fetch_day_is_not_complete():
    """The vendor answered, but with nothing on or after the fetch day: no evidence about what is next."""
    rows = [row("ABT", date(2026, 7, 16), RUN2, actual=1.31), row("ABT", date(2026, 10, 14), RUN2),
            row("ABT", date(2026, 10, 21), RUN1)]
    st = state(rows)
    assert st[("ABT", date(2026, 10, 21))].latest_fetch_complete is False and st[("ABT", date(2026, 10, 21))].is_current is True


def test_reported_rows_are_history_and_never_hidden_even_when_absent_from_the_latest_fetch():
    rows = aapl_rows() + [row("AAPL", date(2025, 10, 30), RUN1, actual=1.85)]        # aged out of the vendor's window
    assert state(rows)[("AAPL", date(2025, 10, 30))].is_current is True


def test_a_reported_row_inside_the_answered_window_but_missing_from_the_latest_fetch_stays_current():
    """A partial write (or a vendor hiccup) dropped an already-reported quarter: it is history, never hidden."""
    rows = [row("PART", date(2026, 4, 10), RUN2, actual=0.9), row("PART", date(2026, 7, 10), RUN1, actual=1.1),
            row("PART", date(2026, 10, 30), RUN2)]
    st = state(rows)
    assert st[("PART", date(2026, 7, 10))].latest_fetch_complete is True and st[("PART", date(2026, 7, 10))].is_current is True


def test_rows_older_than_the_window_the_latest_fetch_answered_for_stay_current():
    rows = aapl_rows() + [row("AAPL", date(2025, 7, 31), RUN1)]                       # no actual, but before 2026-01-29
    assert state(rows)[("AAPL", date(2025, 7, 31))].is_current is True


def test_a_past_date_that_never_reported_and_is_no_longer_listed_is_hidden():
    """The 'reports today' failure: a stale past date with no EPS, inside the answered window."""
    rows = aapl_rows() + [row("AAPL", date(2026, 7, 28), RUN1)]
    assert state(rows)[("AAPL", date(2026, 7, 28))].is_current is False


def test_a_failed_fetch_writes_nothing_so_the_previous_complete_answer_still_decides():
    rows = [row("NB", date(2026, 7, 1), RUN1, actual=0.1), row("NB", date(2026, 9, 28), RUN1 - timedelta(days=14)),
            row("NB", date(2026, 10, 30), RUN1 + timedelta(seconds=1))]
    before = state(rows)
    # a later run that failed for NB writes no row -> classification is unchanged (nothing is inferred from absence of a run)
    assert state(list(rows)) == before
    assert before[("NB", date(2026, 9, 28))].is_current is False and before[("NB", date(2026, 10, 30))].is_current is True


def test_an_incomplete_latest_fetch_without_any_upcoming_date_is_not_evidence_of_a_cancellation():
    rows = [row("LEVI", date(2026, 7, 9), RUN2, actual=0.2), row("LEVI", date(2026, 4, 9), RUN2, actual=0.3),
            row("LEVI", date(2026, 10, 30), RUN1)]                                     # the latest run lost the upcoming row
    st = state(rows)
    assert st[("LEVI", date(2026, 10, 30))].is_current is True
    assert st[("LEVI", date(2026, 10, 30))].latest_fetch_complete is False


def test_a_latest_fetch_that_wrote_a_single_row_out_of_many_is_not_complete():
    rows = [row("AIR", date(2026, 3, 1), RUN1, actual=1.0), row("AIR", date(2026, 6, 1), RUN1, actual=1.0),
            row("AIR", date(2026, 10, 25), RUN1), row("AIR", date(2026, 10, 28), RUN2)]
    st = state(rows)
    assert st[("AIR", date(2026, 10, 25))].is_current is True                         # one-row answer: nothing superseded
    assert st[("AIR", date(2026, 10, 28))].latest_fetch_complete is False


def test_a_new_listing_with_a_single_upcoming_row_is_complete_and_current():
    st = state([row("NEWCO", date(2026, 11, 12), RUN2)])
    assert st[("NEWCO", date(2026, 11, 12))].latest_fetch_complete is True and st[("NEWCO", date(2026, 11, 12))].is_current


def test_a_same_day_pipeline_retry_is_the_latest_fetch_and_rows_of_one_run_are_grouped():
    retry = RUN2 + timedelta(hours=2)
    rows = [row("TBN", date(2026, 7, 2), retry, actual=0.5), row("TBN", date(2026, 9, 25), RUN2),
            row("TBN", date(2026, 10, 29), retry + timedelta(seconds=3))]              # rows of one run seconds apart
    st = state(rows)
    assert st[("TBN", date(2026, 7, 2))].in_latest_fetch and st[("TBN", date(2026, 10, 29))].in_latest_fetch
    assert st[("TBN", date(2026, 9, 25))].is_current is False


def test_symbols_are_independent_and_freshness_is_the_newest_fetch():
    rows = aapl_rows() + [row("NB", date(2026, 10, 30), RUN1)]
    st = state(rows)
    assert st[("NB", date(2026, 10, 30))].is_current and st[("NB", date(2026, 10, 30))].latest_fetch_at == RUN1
    assert st[("AAPL", date(2026, 11, 2))].latest_fetch_at == RUN2 + timedelta(milliseconds=8)


def test_naive_fetch_times_are_refused():
    with pytest.raises(ValueError):
        EC.classify([row("X", date(2026, 1, 1), datetime(2026, 1, 1))])


# ---------------------------------------------------------------------------------------------------------- static SQL
def test_the_sql_window_is_the_python_window_and_the_ctes_are_read_only():
    m = re.search(r"INTERVAL '(\d+) minutes'", EC.EARNINGS_CURRENT_CTES)
    assert m and timedelta(minutes=int(m.group(1))) == EC.FETCH_RUN_WINDOW
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b", EC.EARNINGS_CURRENT_CTES, re.I)
    assert "America/New_York" in EC.EARNINGS_CURRENT_CTES and str(EC.FETCH_DAY_TZ) == "America/New_York"


def test_board_and_discovery_read_only_current_rows():
    import inspect
    from services import discovery_service as D
    from services import momentum_board_service as MB
    board_sql = inspect.getsource(MB.MomentumBoardService._load_latest_session)
    for sql in (board_sql, D.BASE_SQL):
        assert "earnings_current" in sql and "is_current" in sql
        assert not re.search(r"FROM\s+earnings_calendar\b", sql.replace(EC.EARNINGS_CURRENT_CTES, "")), \
            "a reader bypasses the current-row rule"


# --------------------------------------------------------------------------------------------------------- real Postgres
MIGRATIONS = ["create_trading_schema.sql", "add_digest_tables.sql", "add_earnings_calendar_table.sql",
              "add_market_snapshot_tables.sql", "add_stock_relative_strength_table.sql", "add_ml_dataset_tables.sql"]


def _connect_args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


@contextmanager
def _schema():
    import psycopg2
    try:
        args = _connect_args()
        admin = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "earn_cur_" + uuid.uuid4().hex[:10]
    cur = admin.cursor()
    cur.execute(f'CREATE SCHEMA "{schema}"')
    cur.execute(f'SET search_path TO "{schema}"')
    for name in MIGRATIONS:
        with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
            cur.execute(fh.read())
    admin.commit()

    def conn():
        return psycopg2.connect(options=f"-c search_path={schema}", **args)
    try:
        yield admin, conn
    finally:
        admin.rollback()
        admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.commit()
        admin.close()


ALL_FIXTURES = (aapl_rows()
                + [row("AAPL", date(2025, 10, 30), RUN1, actual=1.85), row("AAPL", date(2025, 7, 31), RUN1),
                   row("AAPL", date(2026, 7, 28), RUN1)]
                + [row("ABT", date(2026, 7, 16), RUN2, actual=1.31), row("ABT", date(2026, 10, 23), RUN2), row("ABT", date(2026, 10, 28), RUN1)]
                + [row("LEVI", date(2026, 7, 9), RUN2, actual=0.2), row("LEVI", date(2026, 4, 9), RUN2, actual=0.3), row("LEVI", date(2026, 10, 30), RUN1)]
                + [row("AIR", date(2026, 3, 1), RUN1, actual=1.0), row("AIR", date(2026, 6, 1), RUN1, actual=1.0),
                   row("AIR", date(2026, 10, 25), RUN1), row("AIR", date(2026, 10, 28), RUN2)]
                + [row("NEWCO", date(2026, 11, 12), RUN2)]
                + [row("PART", date(2026, 4, 10), RUN2, actual=0.9), row("PART", date(2026, 7, 10), RUN1, actual=1.1),
                   row("PART", date(2026, 10, 30), RUN2)]
                + [row("TBN", date(2026, 7, 2), RUN2 + timedelta(hours=2), actual=0.5), row("TBN", date(2026, 9, 25), RUN2),
                   row("TBN", date(2026, 10, 29), RUN2 + timedelta(hours=2, seconds=3))])


def _insert(cur, rows):
    for r in rows:
        cur.execute("INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)", (r["symbol"], r["report_date"], r["eps_estimate"], r["eps_actual"], r["surprise_pct"], r["fetched_at"]))


def test_sql_rule_matches_the_python_reference_on_every_fixture():
    with _schema() as (admin, conn):
        cur = admin.cursor()
        _insert(cur, ALL_FIXTURES)
        admin.commit()
        cur.execute("WITH " + EC.EARNINGS_CURRENT_CTES + " SELECT symbol, report_date, in_latest_fetch, latest_fetch_complete, is_current, "
                    "latest_fetch_at FROM earnings_current")
        got = {(s, d): (a, b, c, t) for s, d, a, b, c, t in cur.fetchall()}
        ref = EC.classify(ALL_FIXTURES)
        assert set(got) == set(ref)
        for k, v in ref.items():
            assert got[k][:3] == (v.in_latest_fetch, v.latest_fetch_complete, v.is_current), k
            assert got[k][3] == v.latest_fetch_at, k


def test_real_board_query_shows_the_current_date_and_freshness(monkeypatch):
    from services import momentum_board_service as MB
    with _schema() as (admin, conn):
        cur = admin.cursor()
        _insert(cur, aapl_rows() + [row("NB", date(2099, 1, 15), RUN1), row("NB", date(2099, 1, 20), RUN1)])
        today = date.today()
        cur.execute("INSERT INTO digest_runs (session_date, universe_n, up_n, down_n, counts, min_dv, top_n) "
                    "VALUES (%s, 2, 1, 1, '{}'::jsonb, 0, 10)", (today,))
        for sym in ("AAPL", "NB"):
            cur.execute("INSERT INTO digest_stocks (session_date, symbol, category, close, ret1_pct, dv20, list_ranks) "
                        "VALUES (%s, %s, 'breakout', 100, 1.0, 5e7, '{\"gainers\": 1}'::jsonb)", (today, sym))
        admin.commit()
        monkeypatch.setattr(MB, "_today", lambda: date(2026, 10, 21))
        MB._board_cache.update({"data": None, "ts": 0.0})
        rows = {r["symbol"]: r for r in MB.MomentumBoardService(conn).get_board(limit=10)["results"]}
        MB._board_cache.update({"data": None, "ts": 0.0})
        if today <= date(2026, 11, 2):
            assert rows["AAPL"]["next_earnings_date"] == "2026-11-02"                  # never the obsolete 10-29
        assert rows["AAPL"]["earnings_schedule_complete"] is True
        assert rows["AAPL"]["earnings_fetched_at"].startswith("2026-10-20")
        assert rows["NB"]["next_earnings_date"] == "2099-01-15"                       # both rows from one run: both current
        assert rows["NB"]["earnings_schedule_complete"] is True


def test_real_discovery_query_uses_current_next_and_last_dates():
    from services import discovery_service as D
    with _schema() as (admin, conn):
        cur = admin.cursor()
        _insert(cur, aapl_rows() + [row("AAPL", date(2026, 7, 28), RUN1)])
        s = date(2026, 10, 20)
        for d in (date(2026, 10, 16), s):
            cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES ('AAPL', %s, 100, 101, 99, 100, 1000000)", (d,))
        admin.commit()
        out = D.DiscoveryService(conn).search(D.normalise(D.Query(session=s)))
        rec = next(r for r in out["rows"] if r["symbol"] == "AAPL")
        assert rec["next_earnings_date"] == "2026-11-02"                               # not the obsolete 10-29
        assert rec["last_earnings_date"] == "2026-07-30"                               # not the never-reported 07-28
        assert rec["days_to_earnings"] == (date(2026, 11, 2) - s).days


# ------------------------------------------------------------------------------- query shape (2026-10-22 regression)
# A per-row LATERAL lookup into earnings_current rescans the materialised CTE once per stock: on production volume
# (~3,000 symbols x ~65k calendar rows) every Screener request took ~30 s. The readers must aggregate once and join.
def test_no_reader_uses_a_per_row_lateral_into_earnings_current():
    import inspect
    from services import discovery_service as D
    from services import momentum_board_service as MB
    for sql in (inspect.getsource(MB.MomentumBoardService._load_latest_session), D.BASE_SQL):
        for m in re.finditer(r"LATERAL\s*\((.*?)\)\s*\w+\s+ON", sql, re.S):
            assert "earnings_current" not in m.group(1), "per-row LATERAL into earnings_current"


class _RecCursor:
    def __init__(self, cur, log):
        self._cur, self._log = cur, log

    def execute(self, sql, params=None):
        self._log.append(self._cur.mogrify(sql, params).decode())
        return self._cur.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _RecConn:
    def __init__(self, conn, log):
        self._conn, self._log = conn, log

    def cursor(self, *a, **k):
        return _RecCursor(self._conn.cursor(*a, **k), self._log)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _cte_loops(plan):
    out = []
    def walk(n):
        if n.get("CTE Name", "").startswith("earnings"):
            out.append((n["CTE Name"], n.get("Actual Loops")))
        for c in n.get("Plans", []):
            walk(c)
    walk(plan[0]["Plan"])
    return out


def _bulk_fixture(cur, n_symbols=1000):
    rows = []
    for i in range(n_symbols):
        sym = f"S{i:04d}"
        rows += [row(sym, date(2025, m, 15), RUN2, actual=1.0) for m in (1, 4, 7, 10)]
        rows += [row(sym, date(2026, m, 15), RUN2, actual=1.0) for m in (1, 4, 7)]
        rows += [row(sym, date(2026, 10, 28), RUN1), row(sym, date(2026, 11, 3), RUN2)]
    _insert(cur, rows)
    return [f"S{i:04d}" for i in range(n_symbols)]


def test_real_queries_scan_each_earnings_cte_at_most_once():
    import json as _json
    from services import discovery_service as D
    from services import momentum_board_service as MB
    with _schema() as (admin, conn):
        cur = admin.cursor()
        syms = _bulk_fixture(cur)
        s = date(2026, 10, 20)
        today = date.today()
        cur.execute("INSERT INTO digest_runs (session_date, universe_n, up_n, down_n, counts, min_dv, top_n) "
                    "VALUES (%s, %s, 1, 1, '{}'::jsonb, 0, 10)", (today, len(syms)))
        for sym in syms:
            for d in (date(2026, 10, 16), s):
                cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES (%s, %s, 100, 101, 99, 100, 1000000)", (sym, d))
            cur.execute("INSERT INTO digest_stocks (session_date, symbol, category, close, ret1_pct, dv20, list_ranks) "
                        "VALUES (%s, %s, 'breakout', 100, 1.0, 5e7, '{\"gainers\": 1}'::jsonb)", (today, sym))
        admin.commit()
        log = []
        rec = lambda: _RecConn(conn(), log)                                              # noqa: E731
        MB._board_cache.update({"data": None, "ts": 0.0})
        board = MB.MomentumBoardService(rec).get_board(limit=5000)["results"]
        MB._board_cache.update({"data": None, "ts": 0.0})
        out = D.DiscoveryService(rec).search(D.normalise(D.Query(session=s)), export=True)
        assert len(board) == len(syms) and len(out["rows"]) == len(syms)
        assert {r["next_earnings_date"] for r in out["rows"]} == {"2026-11-03"}              # obsolete 10-28 never shown
        heavy = [q for q in log if "earnings_current" in q]
        assert len(heavy) >= 2
        for q in heavy:
            cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + q)
            plan = cur.fetchone()[0]
            plan = _json.loads(plan) if isinstance(plan, str) else plan
            loops = _cte_loops(plan)
            assert loops and all(l <= 1 for _, l in loops), loops
        admin.rollback()
