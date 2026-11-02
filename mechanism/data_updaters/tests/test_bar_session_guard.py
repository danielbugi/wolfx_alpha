"""Write-time price-bar guard (2026-11-02): no bar for a weekend, an exchange holiday, or with impossible OHLC reaches stock_prices.
No network, no database.

The production row these reproduce: CBL on 2026-02-16 (Presidents' Day), a vendor filler bar (open = high = low = close = the previous close,
volume 0) written by the 2026-09-18 backfill. It made 2026-02-16 look like a session on which ~3,000 stocks had no price, which broke every
reader that builds a cross-symbol date axis (a 200-session mean failed for the next 200 sessions in the Market Behavior Atlas).

Run:  python -m pytest mechanism/data_updaters/tests -q      (from the repo root)
"""
import os
import sys
from datetime import date

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from data_updaters import daily_data_updater as dd  # noqa: E402
from shared import market_calendar as mc  # noqa: E402
from shared import session_integrity as si  # noqa: E402

FRI, HOLIDAY, TUE, WED = date(2026, 2, 13), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18)
SESSIONS = {FRI, TUE, WED}                      # the exchange calendar: 2026-02-16 was closed
SPAN = (FRI, WED)


def bar(d, o, h, l, c, v):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "adj_close": c, "volume": v}


# The real CBL rows around the holiday (stock_prices, read-only query 2026-11-02).
CBL = [bar(FRI, 35.0830, 35.9284, 33.4787, 33.6709, 364926),
       bar(HOLIDAY, 33.6709, 33.6709, 33.6709, 33.6709, 0),
       bar(TUE, 33.9206, 35.4769, 33.6998, 35.4481, 257749)]


def test_the_cbl_holiday_filler_bar_is_rejected_and_the_real_bars_kept():
    ok, rej, flat = si.screen_price_bars(CBL, SESSIONS, SPAN)
    assert [r["date"] for r in ok] == [FRI, TUE]
    assert [(r["date"], why) for r, why in rej] == [(HOLIDAY, si.NOT_A_SESSION)]
    assert flat == 0


def test_weekends_are_rejected_even_without_a_calendar_and_holidays_fail_open():
    sat = bar(date(2026, 2, 14), 10, 11, 9, 10, 100)
    ok, rej, _ = si.screen_price_bars([sat, CBL[1]], None, None)
    assert [(r["date"], why) for r, why in rej] == [(sat["date"], si.WEEKEND)]
    assert [r["date"] for r in ok] == [HOLIDAY]                 # no calendar: a weekday holiday cannot be recognised and is kept (fail open)


def test_a_date_outside_the_calendar_span_is_judged_by_weekday_only():
    later = bar(date(2026, 3, 2), 10, 11, 9, 10, 100)          # a Monday after the span the calendar was read for
    ok, rej, _ = si.screen_price_bars([later], SESSIONS, SPAN)
    assert ok == [later] and rej == []


def test_impossible_bars_are_rejected():
    cases = [bar(TUE, 0, 11, 9, 10, 1), bar(TUE, 10, 11, -1, 10, 1), bar(TUE, 10, 9, 11, 10, 1),        # non-positive, negative, low > high
             bar(TUE, 10, 11, 9, 12, 1), bar(TUE, 13, 11, 9, 10, 1), bar(TUE, None, 11, 9, 10, 1), bar(TUE, float("nan"), 11, 9, 10, 1)]
    ok, rej, _ = si.screen_price_bars(cases, SESSIONS, SPAN)
    assert ok == [] and {why for _, why in rej} == {si.INVALID_OHLC}


def test_a_flat_zero_volume_bar_on_a_real_session_is_kept_and_counted():
    halt = bar(WED, 20.0, 20.0, 20.0, 20.0, 0)                  # a trading halt / no-trade day on a real session
    ok, rej, flat = si.screen_price_bars([CBL[0], halt], SESSIONS, SPAN)
    assert len(ok) == 2 and rej == [] and flat == 1


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass


def test_the_updater_never_writes_the_holiday_bar(monkeypatch):
    """bulk_update_stock_prices is the single write path for every provider and for the Tiingo backfill."""
    import psycopg2.extras
    written = []
    monkeypatch.setattr(mc, "sessions_between", lambda a, b: frozenset(d for d in SESSIONS if a <= d <= b))
    monkeypatch.setattr(dd.db, "get_sync_connection", lambda: _Conn(), raising=False)
    monkeypatch.setattr(psycopg2.extras, "execute_values", lambda cur, q, records, **k: written.extend(records))
    u = dd.EnhancedDailyDataUpdater(target_session=TUE)
    n = u.bulk_update_stock_prices("CBL", pd.DataFrame(CBL))
    assert n == 2
    assert sorted(r[1] for r in written) == [FRI, TUE]
    assert u.stats["bars_rejected"] == 1


def test_the_updater_without_a_calendar_still_rejects_weekends(monkeypatch):
    import psycopg2.extras
    written = []
    monkeypatch.setattr(mc, "sessions_between", lambda a, b: None)
    monkeypatch.setattr(dd.db, "get_sync_connection", lambda: _Conn(), raising=False)
    monkeypatch.setattr(psycopg2.extras, "execute_values", lambda cur, q, records, **k: written.extend(records))
    u = dd.EnhancedDailyDataUpdater(target_session=TUE)
    rows = [CBL[0], bar(date(2026, 2, 14), 10, 11, 9, 10, 100), CBL[2]]
    assert u.bulk_update_stock_prices("X", pd.DataFrame(rows)) == 2
    assert u.stats["bars_screened_without_calendar"] == 3
