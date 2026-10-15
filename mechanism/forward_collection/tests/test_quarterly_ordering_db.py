"""The REAL SQL of QuarterlyFundamentalsUpdater.get_symbols_to_update on a throwaway production-shaped database: which symbols are due, and in what order. Stalest successful first; symbols that never
produced a quarterly row (they fail every night) last; fresh and inactive symbols never."""
import datetime as dt
import logging

import psycopg2.extras
import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
import full_env as FE
from mechanism.data_updaters import quarterly_fundamentals_updater as Q


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env("qord")
    try:
        yield e
    finally:
        teardown()


def test_due_symbols_are_stalest_first_never_succeeded_last_and_fresh_or_inactive_are_excluded(env, monkeypatch):
    conn = env.admin()
    cur = conn.cursor()
    today = dt.date(2026, 10, 14)
    for sym in ("FRESH", "STALE26", "STALE30", "STALE60", "NEVER_B", "NEVER_A", "INACTIVE"):
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,1,1,1,1,1,1)", (sym, today))
    for sym, age in (("FRESH", 3), ("STALE26", 26), ("STALE30", 30), ("STALE60", 60)):
        cur.execute("INSERT INTO quarterly_fundamentals (symbol, quarter, updated_at) VALUES (%s, %s, now() - make_interval(days => %s))", (sym, dt.date(2026, 6, 30), age))
    cur.execute("INSERT INTO inactive_symbols (symbol, reason) VALUES ('INACTIVE', 'test')")
    conn.commit()

    class Shim:
        @staticmethod
        def execute_dict_query(query, params):
            c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            c.execute(query, params)
            return c.fetchall()
    monkeypatch.setattr(Q, "db", Shim)
    u = object.__new__(Q.QuarterlyFundamentalsUpdater)
    u.logger = logging.getLogger("test_quarterly_ordering_db")
    assert u.get_symbols_to_update() == ["STALE60", "STALE30", "STALE26", "NEVER_A", "NEVER_B"]
    assert u.get_symbols_to_update(limit=2) == ["STALE60", "STALE30"]                   # a budgeted/limited run takes the stalest successful symbols first, never the hopeless ones
    conn.close()
