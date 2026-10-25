"""Overview movers = Smart Screener v2 queries for one explicit session (DiscoveryService.movers).

Pure part: the movers lists are exactly the Screener queries the Overview's "View all" links open (the link strings are
pinned here AND in frontend/src/lib/__tests__/screenerV2.test.ts, so a change on either side fails a test), and the SQL
is one bounded, read-only statement.

Real-Postgres part (self-skips without a superuser database; CI's db-bootstrap-integration job runs it): a throwaway
database built from docker-compose.yml's own migration list, with the two stale cases found in production on
2026-10-25 -- a symbol whose newest bar is the day before the session (PSKY) and a symbol whose unusual volume was
three sessions ago (BRZE) -- plus the price/liquidity/history floor, a half-loaded newer date, missing indicators, parity
with the Screener's own search, and a constant number of statements however many symbols there are."""
import os
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import discovery_service as D  # noqa: E402

# The Overview's "View all" hrefs (frontend moversScreenerHref), without the session parameter the page adds.
LINKS = {
    "top_gainers": "min_close=1&min_dollar_vol_20=1000000&sort=change_1d&order=desc",
    "top_losers": "min_close=1&min_dollar_vol_20=1000000&sort=change_1d&order=asc",
    "unusual_volume": "min_close=1&min_dollar_vol_20=1000000&min_volume_ratio=2&sort=volume_ratio&order=desc",
}


def _link_query(key):
    return D.normalise(D.parse_params([tuple(kv.split("=", 1)) for kv in LINKS[key].split("&")]))


# ------------------------------------------------------------------------------------------------------------- pure
@pytest.mark.parametrize("key", list(LINKS))
def test_each_movers_list_is_the_query_its_view_all_link_opens(key):
    qr, _ = D.MOVERS[key]
    link = _link_query(key)
    assert (qr.sort, qr.order) == (link.sort, link.order)
    assert D.normalise(qr).ranges == link.ranges
    assert D.build_where(D.normalise(qr)) == D.build_where(link)


def test_gainers_and_losers_keep_only_rises_and_falls_and_unusual_volume_adds_nothing():
    assert [D.MOVERS[k][1] for k in LINKS] == ["change_1d > 0", "change_1d < 0", "TRUE"]


def test_movers_sql_is_one_bounded_read_only_statement_over_one_base():
    sql, params = D.movers_sql()
    assert sql.count("base AS (") == 1                             # the universe is evaluated once for all three lists
    assert sql.count("LIMIT %(limit)s") == 3
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE)\b", sql.upper().replace("CREATE_", ""))
    bound, named = D._bind(sql, {}, params)
    assert len(params) == 14 and "%s" not in bound


def test_limit_is_bounded():
    svc = D.DiscoveryService(lambda: None)
    for bad in (0, 51):
        with pytest.raises(D.InvalidRequest):
            svc.movers(bad)


# ---------------------------------------------------------------------------------------------------- real Postgres
def _connect_args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


def _init_files():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
        pairs = re.findall(r"\./(mechanism/[\w./-]+\.sql):/docker-entrypoint-initdb\.d/(\d+)_", fh.read())
    return [os.path.join(ROOT, p) for p, _ in sorted(pairs, key=lambda x: int(x[1]))]


@contextmanager
def _fresh_db():
    """A throwaway DATABASE with every production migration applied, dropped afterwards."""
    import psycopg2
    try:
        args = _connect_args()
        boot = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    cur = boot.cursor()
    cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    superuser = cur.fetchone()[0]
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'trading_user'")
    if not superuser or not cur.fetchone():
        boot.close()
        pytest.skip("needs a superuser and the 'trading_user' role the baseline migrations assume")
    boot.rollback()
    boot.autocommit = True
    dbname = "movers_" + uuid.uuid4().hex[:10]
    boot.cursor().execute(f'CREATE DATABASE "{dbname}"')
    db_args = dict(args, dbname=dbname)
    try:
        conn = psycopg2.connect(**db_args)
        for path in _init_files():
            with open(path, encoding="utf-8") as fh:
                conn.cursor().execute(fh.read())
            conn.commit()
        conn.close()
        yield lambda: psycopg2.connect(**db_args)
    finally:
        c = boot.cursor()
        c.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{dbname}' AND pid <> pg_backend_pid()")
        c.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        boot.close()


S = date(2026, 10, 23)                                            # the session (a Friday)


def _sessions(n=25, end=S):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _load(conn):
    days = _sessions()
    p = days[-2]
    cur = conn.cursor()

    def bar(sym, d, close, volume=1_000_000):
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sym, d, close, close, close, close, close, volume))

    def ti(sym, d, ratio):
        cur.execute("INSERT INTO technical_indicators (symbol, date, volume_ratio) VALUES (%s,%s,%s)", (sym, d, ratio))

    closes = {"UP": 12.0, "DOWN": 8.0, "FLAT": 10.0, "VOL": 10.0, "BRZE": 10.0}   # VOL and BRZE: volume cases only
    for sym, last in closes.items():
        for d in days:
            bar(sym, d, last if d == S else 10.0)
        ti(sym, S, 3.0 if sym == "VOL" else 0.58 if sym == "BRZE" else 1.0)
    ti("BRZE", days[-4], 7.81)                                    # unusual THREE sessions ago, normal on the session
    for d in days[:-1]:                                           # PSKY: newest bar the day BEFORE the session, +50%
        bar("PSKY", d, 15.0 if d == p else 10.0)
    ti("PSKY", p, 9.0)
    for d in days:
        bar("PENNY", d, 0.8 if d == S else 0.5, 100_000_000)      # +60% but under $1
        bar("THIN", d, 14.0 if d == S else 10.0, 1_000)           # +40% but ~$10k a day
    ti("PENNY", S, 5.0)
    ti("THIN", S, 5.0)
    for d in days[-10:]:                                          # +30% with only 10 sessions of history
        bar("SHORT", d, 13.0 if d == S else 10.0)
    ti("SHORT", S, 4.0)
    for sym in list(closes) + ["PSKY", "PENNY", "THIN", "SHORT"]:
        cur.execute("INSERT INTO daily_fundamentals (symbol, date, sector, market_cap) VALUES (%s,%s,'Technology',1e9)", (sym, S))
    bar("UP", S + timedelta(days=3), 50.0)                        # a half-loaded newer date: never the session
    conn.commit()


@pytest.fixture(scope="module")
def db():
    with _fresh_db() as connect:
        conn = connect()
        _load(conn)
        conn.close()
        yield connect


def test_one_session_and_stale_symbols_are_never_current_movers(db):
    m = D.DiscoveryService(db).movers(5)
    assert m["session"] == "2026-10-23" and m["previous_session"] == "2026-10-22"     # not the half-loaded 10-26
    assert [r["symbol"] for r in m["top_gainers"]] == ["UP"]       # PSKY's +50% was on 10-22: not a 10-23 mover
    assert [r["symbol"] for r in m["top_losers"]] == ["DOWN"]
    assert [r["symbol"] for r in m["unusual_volume"]] == ["VOL"]   # BRZE's 7.81x was three sessions ago: 0.58x now
    assert m["top_gainers"][0]["change_1d"] == pytest.approx(20.0)
    assert m["unusual_volume"][0]["volume_ratio"] == pytest.approx(3.0)
    assert (m["top_gainers_total"], m["top_losers_total"], m["unusual_volume_total"]) == (1, 1, 1)
    assert m["universe"] == 8                                      # every symbol WITH a bar on the session (no PSKY)
    assert D.DiscoveryService(db).search(D.Query(symbols=("PSKY",)))["total"] == 0  # no 10-23 row at all for a 10-22 bar


def test_floor_excludes_penny_thin_and_short_history_stocks(db):
    m = D.DiscoveryService(db).movers(50)
    listed = {r["symbol"] for k in LINKS for r in m[k]}
    assert not listed & {"PENNY", "THIN", "SHORT", "PSKY", "BRZE"}


def test_each_list_equals_the_first_rows_of_the_screener_view_its_link_opens(db):
    svc = D.DiscoveryService(db)
    m = svc.movers(5)
    for key in LINKS:
        view = svc.search(_link_query(key))
        assert view["session"] == m["session"]
        sign = {"top_gainers": lambda r: r["change_1d"] > 0, "top_losers": lambda r: r["change_1d"] < 0}.get(key, lambda r: True)
        expect = [r for r in view["rows"] if sign(r)][:5]
        assert [r["symbol"] for r in m[key]] == [r["symbol"] for r in expect], key
        for a, b in zip(m[key], expect):
            for col in ("close", "change_1d", "volume_ratio", "dollar_vol_20", "market_cap", "sector"):
                assert a[col] == b[col], (key, a["symbol"], col)


def test_missing_indicators_for_the_session_leave_unusual_volume_empty_and_say_so(db):
    conn = db()
    conn.cursor().execute("DELETE FROM technical_indicators WHERE date = %s", (S,))
    try:
        m = D.DiscoveryService(lambda: _NoCommit(conn)).movers(5)
        assert m["unusual_volume"] == [] and m["coverage"]["volume_ratio"] == 0
        assert [r["symbol"] for r in m["top_gainers"]] == ["UP"]  # prices alone still rank the gainers
    finally:
        conn.rollback()
        conn.close()


def test_a_constant_number_of_statements_however_many_symbols(db):
    conn = db()
    executed = []
    m = D.DiscoveryService(lambda: _Counting(conn, executed)).movers(5)
    assert m["top_gainers"]
    assert len(executed) == 4          # SET TRANSACTION READ ONLY, newest date, session window, the one movers statement


def test_volume_column_does_not_change_the_screener_rows():
    assert "volume" not in D.ROW_COLUMNS                           # Screener responses keep their exact shape


class _NoCommit:
    """The service closes its connection: keep the test's uncommitted DELETE alive until the test rolls it back."""

    def __init__(self, conn):
        self._c = conn

    def cursor(self):
        return self._c.cursor()

    def rollback(self):
        pass

    def close(self):
        pass


class _Counting(_NoCommit):
    def __init__(self, conn, log):
        super().__init__(conn)
        self._log = log

    def cursor(self):
        cur = self._c.cursor()
        log = self._log

        class C:
            def execute(self, *a):
                log.append(a[0])
                return cur.execute(*a)

            def __getattr__(self, n):
                return getattr(cur, n)
        return C()

    def close(self):
        self._c.close()
