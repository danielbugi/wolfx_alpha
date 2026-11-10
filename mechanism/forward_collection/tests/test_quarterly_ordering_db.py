"""The REAL SQL of QuarterlyFundamentalsUpdater.get_symbols_to_update on a throwaway production-shaped database: which symbols are due, and in what order. Earnings-triggered symbols (reported recently, quarter
not stored yet) first; then stalest successful; symbols that never produced a quarterly row (they fail every night) last; fresh and inactive symbols never."""
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


def test_a_recent_report_whose_quarter_is_missing_makes_a_symbol_due_first_and_only_until_the_quarter_arrives(env, monkeypatch):
    """Dates are relative to the database's CURRENT_DATE, like the production query. Q2 (ends ~130 days before 'today') is the newest stored quarter unless noted."""
    conn = env.admin()
    cur = conn.cursor()
    cur.execute("SELECT CURRENT_DATE")
    today = cur.fetchone()[0]
    d = lambda n: today - dt.timedelta(days=n)                                                     # noqa: E731
    syms = {  # symbol: (days since last check, newest stored quarter, report dates as days ago)
        "E_OLDREP": (3, d(130), [9]),          # reported 9 days ago, quarter missing, checked 3 days ago      -> due, first (oldest report)
        "E_NEWREP": (2, d(130), [2]),          # reported 2 days ago, quarter missing                          -> due, second
        "E_HAVEQ": (1, d(40), [9]),            # reported 9 days ago, but the reported quarter is stored        -> not due
        "E_TODAY": (0, d(130), [5]),           # quarter missing but already checked today                     -> not due (retry tomorrow)
        "E_SAMEDAY": (2, d(130), [0]),         # reports today: not a day old yet                               -> not due
        "E_TOOOLD": (2, d(130), [30]),         # report outside the 21-day window                               -> not due
        "E_FUTURE": (2, d(130), [-5]),         # only a future report date                                      -> not due
        "E_BACKSTP": (40, d(40), []),         # no report, but 40 days since the last check                    -> due via the 25-day backstop, after the earnings ones
    }
    for sym in list(syms) + ["E_NEVER"]:
        cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,1,1,1,1,1,1) ON CONFLICT DO NOTHING", (sym, today))
    for sym, (age, quarter, reports) in syms.items():
        cur.execute("INSERT INTO quarterly_fundamentals (symbol, quarter, updated_at) VALUES (%s, %s, now() - make_interval(days => %s))", (sym, quarter, age))
        for r in reports:
            cur.execute("INSERT INTO earnings_calendar (symbol, report_date) VALUES (%s, %s)", (sym, d(r)))
    cur.execute("INSERT INTO earnings_calendar (symbol, report_date) VALUES ('E_NEVER', %s)", (d(4),))   # never produced a row: stays last, never promoted
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
    mine = lambda lst: [x for x in lst if x.startswith("E_")]                                    # noqa: E731
    assert mine(u.get_symbols_to_update()) == ["E_OLDREP", "E_NEWREP", "E_BACKSTP", "E_NEVER"]

    # A check the next day that still finds no new quarter bumps updated_at (as upsert_quarter does): due again only on a later day.
    cur.execute("UPDATE quarterly_fundamentals SET updated_at = now() WHERE symbol = 'E_OLDREP'")
    conn.commit()
    assert "E_OLDREP" not in u.get_symbols_to_update()
    cur.execute("UPDATE quarterly_fundamentals SET updated_at = now() - interval '1 day' WHERE symbol = 'E_OLDREP'")
    conn.commit()
    assert mine(u.get_symbols_to_update())[0] == "E_OLDREP"
    # Once the reported quarter is stored, the report no longer makes it due.
    cur.execute("INSERT INTO quarterly_fundamentals (symbol, quarter, updated_at) VALUES ('E_OLDREP', %s, now() - interval '1 day')", (d(40),))
    conn.commit()
    assert "E_OLDREP" not in u.get_symbols_to_update()
    conn.close()
