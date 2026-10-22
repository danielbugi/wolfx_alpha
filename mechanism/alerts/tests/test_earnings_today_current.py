"""The channel's "who reports today" post must not announce a rescheduled report's obsolete date.

2026-09-23..10-21: 62 symbol mentions in the 19 production earnings posts named a date the vendor no longer listed and
on which no EPS was ever reported (the updater never removes a moved date). The post now reads only CURRENT rows,
using the same rule as the backend (backend/services/earnings_current.py, released in c6827ff0ff76).
Pure part: the mechanism copy is identical to the backend rule. Real-Postgres part (self-skips without a database;
db-bootstrap-integration runs it): the real query on a scratch schema.
"""
import importlib.util
import os
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from alerts import earnings_current as MEC  # noqa: E402
from alerts.earnings_today_query import TODAYS_REPORTERS_SQL  # noqa: E402


def _backend_rule():
    spec = importlib.util.spec_from_file_location("backend_earnings_current", os.path.join(ROOT, "backend", "services", "earnings_current.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


UTC = timezone.utc
OLD = datetime(2026, 10, 6, 20, 8, tzinfo=UTC)
NEW = datetime(2026, 10, 20, 19, 52, tzinfo=UTC)
DAY = date(2026, 10, 22)


def row(sym, d, fetched, actual=None):
    return {"symbol": sym, "report_date": d, "eps_estimate": 1.0, "eps_actual": actual, "surprise_pct": None, "fetched_at": fetched}


FIXTURE = [
    # STALE: the vendor moved the report from today to 10-27; the 'today' row was never removed -> must NOT be posted
    row("STALE", date(2026, 7, 22), NEW, actual=1.1), row("STALE", DAY, OLD), row("STALE", date(2026, 10, 27), NEW),
    # NOW: reports today per the latest fetch -> posted
    row("NOW", date(2026, 7, 21), NEW, actual=0.9), row("NOW", DAY, NEW),
    # PARTIAL: latest fetch lost the upcoming row (incomplete) -> nothing superseded -> today's row is still posted
    row("PARTIAL", date(2026, 7, 20), NEW, actual=0.5), row("PARTIAL", date(2026, 4, 20), NEW, actual=0.4), row("PARTIAL", DAY, OLD),
    # UNCOVERED: reports today but is not in the digest's covered universe -> not posted (unchanged behaviour)
    row("UNCOVERED", date(2026, 7, 22), NEW, actual=1.0), row("UNCOVERED", DAY, NEW),
]


# ------------------------------------------------------------------------------------------------------------- drift
def test_the_mechanism_copy_is_the_backend_rule():
    be = _backend_rule()
    assert MEC.EARNINGS_CURRENT_CTES == be.EARNINGS_CURRENT_CTES
    assert MEC.FETCH_RUN_WINDOW == be.FETCH_RUN_WINDOW and str(MEC.FETCH_DAY_TZ) == str(be.FETCH_DAY_TZ)
    a, b = MEC.classify(FIXTURE), be.classify(FIXTURE)
    assert {k: (v.in_latest_fetch, v.latest_fetch_complete, v.is_current) for k, v in a.items()} == \
           {k: (v.in_latest_fetch, v.latest_fetch_complete, v.is_current) for k, v in b.items()}


def test_reference_rule_on_the_fixture():
    st = MEC.classify(FIXTURE)
    assert st[("STALE", DAY)].is_current is False
    assert st[("NOW", DAY)].is_current is True
    assert st[("PARTIAL", DAY)].is_current is True and st[("PARTIAL", DAY)].latest_fetch_complete is False


def test_the_query_reads_current_rows_only_and_never_a_per_row_lateral_into_the_rule():
    assert "earnings_current" in TODAYS_REPORTERS_SQL and "is_current" in TODAYS_REPORTERS_SQL
    for m in re.finditer(r"LATERAL\s*\((.*?)\)\s*\w+\s+ON", TODAYS_REPORTERS_SQL, re.S):
        assert "earnings_current" not in m.group(1)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b", TODAYS_REPORTERS_SQL, re.I)


def test_the_sender_uses_the_shared_query():
    src = open(os.path.join(ROOT, "mechanism", "alerts", "send_earnings_today.py"), encoding="utf-8").read()
    assert "from alerts.earnings_today_query import TODAYS_REPORTERS_SQL" in src
    assert "db.execute_dict_query(TODAYS_REPORTERS_SQL, (day,))" in src
    assert "FROM earnings_calendar ec\n" not in src                       # the old unfiltered query is gone


# -------------------------------------------------------------------------------------------------------- real Postgres
MIGRATIONS = ["create_trading_schema.sql", "add_digest_tables.sql", "add_earnings_calendar_table.sql"]


@contextmanager
def _schema():
    import psycopg2
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        args = dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                    user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
        conn = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "earn_today_" + uuid.uuid4().hex[:10]
    cur = conn.cursor()
    cur.execute(f'CREATE SCHEMA "{schema}"')
    cur.execute(f'SET search_path TO "{schema}"')
    for name in MIGRATIONS:
        with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
            cur.execute(fh.read())
    conn.commit()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
        conn.close()


def _load(cur, extra_symbols=0):
    rows = list(FIXTURE)
    for i in range(extra_symbols):                                  # volume for the plan check
        s = f"V{i:04d}"
        rows += [row(s, date(2026, 7, 15), NEW, actual=1.0), row(s, DAY, OLD), row(s, date(2026, 11, 3), NEW)]
    for r in rows:
        cur.execute("INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at) VALUES (%s,%s,%s,%s,%s,%s)",
                    (r["symbol"], r["report_date"], r["eps_estimate"], r["eps_actual"], r["surprise_pct"], r["fetched_at"]))
    cur.execute("INSERT INTO digest_runs (session_date, universe_n, up_n, down_n, counts, min_dv, top_n) VALUES (%s, 3, 1, 1, '{}'::jsonb, 0, 10)",
                (DAY - timedelta(days=1),))
    covered = ["STALE", "NOW", "PARTIAL"] + [f"V{i:04d}" for i in range(extra_symbols)]
    for s in covered:
        cur.execute("INSERT INTO digest_stocks (session_date, symbol, category, close, ret1_pct, dv20) VALUES (%s, %s, 'breakout', 10, 1, 1e7)",
                    (DAY - timedelta(days=1), s))
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES (%s, %s, 10, 10, 10, 10, 1000)", (s, DAY - timedelta(days=1)))


def test_real_query_posts_only_current_reporters():
    with _schema() as conn:
        cur = conn.cursor()
        _load(cur)
        cur.execute(TODAYS_REPORTERS_SQL, (DAY,))
        got = [r[0] for r in cur.fetchall()]
        assert got == ["NOW", "PARTIAL"]                              # STALE (moved) and UNCOVERED are not posted


def test_real_query_scans_the_rule_once_at_volume():
    import json
    with _schema() as conn:
        cur = conn.cursor()
        _load(cur, extra_symbols=800)
        cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + cur.mogrify(TODAYS_REPORTERS_SQL, (DAY,)).decode())
        plan = cur.fetchone()[0]
        plan = json.loads(plan) if isinstance(plan, str) else plan
        loops = []

        def walk(n):
            if n.get("CTE Name", "").startswith("earnings"):
                loops.append(n.get("Actual Loops"))
            for c in n.get("Plans", []):
                walk(c)
        walk(plan[0]["Plan"])
        assert loops and max(loops) <= 1, loops
        cur.execute(TODAYS_REPORTERS_SQL, (DAY,))
        assert not any(r[0].startswith("V") for r in cur.fetchall())   # every moved 'today' date is suppressed
