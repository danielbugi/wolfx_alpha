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


def test_over_several_budgeted_nights_the_whole_stale_cohort_is_worked_off_and_never_succeeded_symbols_are_not_starved(env, monkeypatch):
    """A synchronised cohort (12 stale symbols) with a budget of 5 symbols a night, plus 3 symbols that never succeed. Each night takes the head of the real due list; successes get a fresh
    updated_at (as upsert_quarter does), failures do not. The cohort is cleared in ceil(12/5)=3 nights, the never-succeeded symbols are first attempted the very night the cohort runs out,
    and from then on they are the only thing due (they keep being retried, nothing else waits behind them)."""
    conn = env.admin()
    cur = conn.cursor()
    today = dt.date(2026, 10, 14)
    stale = [f"SIM{i:02d}" for i in range(12)]
    never = ["SIMNEVER_A", "SIMNEVER_B", "SIMNEVER_C"]
    for sym in stale + never:
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,1,1,1,1,1,1) ON CONFLICT DO NOTHING", (sym, today))
    for i, sym in enumerate(stale):
        cur.execute("INSERT INTO quarterly_fundamentals (symbol, quarter, updated_at) VALUES (%s, %s, now() - make_interval(days => %s))", (sym, dt.date(2026, 6, 30), 60 - i))   # SIM00 is the stalest
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
    mine = lambda lst: [x for x in lst if x.startswith("SIM")]
    nights = []
    for night in range(1, 6):
        batch = mine(u.get_symbols_to_update())[:5]
        nights.append(batch)
        for sym in batch:
            if sym in stale:                                                              # success: refreshed exactly as the real upsert does
                cur.execute("UPDATE quarterly_fundamentals SET updated_at = now() WHERE symbol = %s", (sym,))
        conn.commit()
    assert nights[0] == stale[0:5] and nights[1] == stale[5:10]                           # stalest first, five a night
    assert nights[2] == stale[10:12] + never                                              # the cohort runs out on night 3 and the never-succeeded symbols are attempted that same night
    assert nights[3] == never and nights[4] == never                                      # afterwards only they remain due; no fresh symbol is ever waiting behind them
    conn.close()
