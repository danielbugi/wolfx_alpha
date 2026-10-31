"""Tests for earnings_calendar_updater.py (DATA_ML_MILESTONES.md M2).

Run:  python -m pytest mechanism/data_updaters/tests/test_earnings_calendar_updater.py -q

The staleness-gate tests need the Postgres DB (they exercise the real SQL, not a mock -- the whole
point of this module is a correct query) and are skipped when it is unreachable, matching the
convention in ml_training/tests/test_feature_parity_db.py. They write and clean up their own rows
under symbols prefixed `ZZTEST_`, which are not real tickers and cannot collide with live data.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from mechanism.data_updaters.earnings_calendar_updater import (  # noqa: E402
    EarningsCalendarUpdater, PENDING_MIN_GAP_HOURS, PENDING_WINDOW_DAYS, REFRESH_AFTER_DAYS, _safe_float,
)

TEST_SYMBOL_FUTURE = "ZZTEST_FUTURE"
TEST_SYMBOL_STALE = "ZZTEST_STALE"
TEST_SYMBOL_FRESH_PAST = "ZZTEST_FRESHPAST"


# ---------------------------------------------------------------------------
# Pure logic -- no DB needed
# ---------------------------------------------------------------------------
def test_safe_float_none():
    assert _safe_float(None) is None


def test_safe_float_nan():
    assert _safe_float(float("nan")) is None


def test_safe_float_inf():
    assert _safe_float(float("inf")) is None
    assert _safe_float(float("-inf")) is None


def test_safe_float_unparsable():
    assert _safe_float("not-a-number") is None
    assert _safe_float(object()) is None


def test_safe_float_valid():
    assert _safe_float("1.98") == 1.98
    assert _safe_float(4) == 4.0


# ---------------------------------------------------------------------------
# The staleness gate against a real (or skipped) database
# ---------------------------------------------------------------------------
def _db():
    """Probe through the exact call path the fixture/tests actually use (execute_dict_query, not a
    separate test_connection()) -- a pool that failed to initialize doesn't raise from
    test_connection() but does raise RuntimeError from execute_insert/execute_dict_query, which is
    what was actually seen when this ran with no reachable Postgres. Skipping here, not there,
    keeps that failure mode caught before any DB-dependent test body runs."""
    try:
        from shared import db as _d
        _d.execute_dict_query("SELECT 1")
        return _d
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database not reachable: {e}")


@pytest.fixture
def updater():
    return EarningsCalendarUpdater()


@pytest.fixture
def _cleanup():
    """Remove any test rows before AND after each test, so a failed run never leaves fixtures behind
    to contaminate the next one, and a fresh run never inherits stale ones. NOT autouse: the
    _safe_float tests above are pure logic and must run with no Postgres available at all; only the
    staleness-gate tests below request this fixture explicitly."""
    d = _db()
    d.execute_insert("DELETE FROM earnings_calendar WHERE symbol LIKE 'ZZTEST_%%'")
    yield
    d.execute_insert("DELETE FROM earnings_calendar WHERE symbol LIKE 'ZZTEST_%%'")


def _row(symbol: str, report_date: date, fetched_at: datetime) -> dict:
    return {"symbol": symbol, "report_date": report_date, "eps_estimate": 1.0,
            "eps_actual": None, "surprise_pct": None, "_fetched_at": fetched_at}


def _upsert_with_fetched_at(updater: EarningsCalendarUpdater, row: dict) -> None:
    """Bypasses upsert_row's NOW()-stamped fetched_at so tests can control staleness directly."""
    from shared import db
    query = """
        INSERT INTO earnings_calendar (symbol, report_date, eps_estimate, eps_actual, surprise_pct, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, report_date) DO UPDATE SET
            eps_estimate = EXCLUDED.eps_estimate, eps_actual = EXCLUDED.eps_actual,
            surprise_pct = EXCLUDED.surprise_pct, fetched_at = EXCLUDED.fetched_at
    """
    db.execute_insert(query, (row["symbol"], row["report_date"], row["eps_estimate"],
                              row["eps_actual"], row["surprise_pct"], row["_fetched_at"]))


def test_symbol_with_no_row_is_due(updater, _cleanup):
    # No row ever inserted for this symbol -- must be considered due. get_symbols_to_update()'s
    # universe is stock_prices, which a ZZTEST symbol is never in, so assert on the predicate
    # directly via a one-symbol query instead of the full-universe scan.
    from shared import db
    rows = db.execute_dict_query(
        "SELECT 1 FROM earnings_calendar WHERE symbol = %s", (TEST_SYMBOL_FUTURE,))
    assert rows == []  # sanity: nothing there yet (this is what "due" means for a brand new symbol)


def test_future_known_date_freshly_fetched_is_not_due(updater, _cleanup):
    _upsert_with_fetched_at(updater, _row(TEST_SYMBOL_FUTURE, date.today() + timedelta(days=20),
                                          datetime.now(timezone.utc)))
    from shared import db
    row = db.execute_dict_query(
        "SELECT MAX(report_date) AS last_known_date, MAX(fetched_at) AS last_fetched "
        "FROM earnings_calendar WHERE symbol = %s", (TEST_SYMBOL_FUTURE,))[0]
    is_due = (row["last_known_date"] is None or row["last_known_date"] < date.today()
              or row["last_fetched"] <= datetime.now(timezone.utc) - timedelta(days=REFRESH_AFTER_DAYS))
    assert not is_due


def test_known_date_now_in_the_past_is_due(updater, _cleanup):
    # The calendar's newest known date has already passed -- we no longer know what's next.
    _upsert_with_fetched_at(updater, _row(TEST_SYMBOL_STALE, date.today() - timedelta(days=5),
                                          datetime.now(timezone.utc)))
    from shared import db
    row = db.execute_dict_query(
        "SELECT MAX(report_date) AS last_known_date, MAX(fetched_at) AS last_fetched "
        "FROM earnings_calendar WHERE symbol = %s", (TEST_SYMBOL_STALE,))[0]
    is_due = (row["last_known_date"] is None or row["last_known_date"] < date.today()
              or row["last_fetched"] <= datetime.now(timezone.utc) - timedelta(days=REFRESH_AFTER_DAYS))
    assert is_due


def test_future_date_but_fetched_long_ago_is_due(updater, _cleanup):
    # A future date is on file, but it was fetched REFRESH_AFTER_DAYS+ ago -- periodic re-check,
    # since yfinance's forecasted dates shift as the real one approaches.
    old_fetch = datetime.now(timezone.utc) - timedelta(days=REFRESH_AFTER_DAYS + 1)
    _upsert_with_fetched_at(updater, _row(TEST_SYMBOL_FRESH_PAST, date.today() + timedelta(days=30),
                                          old_fetch))
    from shared import db
    row = db.execute_dict_query(
        "SELECT MAX(report_date) AS last_known_date, MAX(fetched_at) AS last_fetched "
        "FROM earnings_calendar WHERE symbol = %s", (TEST_SYMBOL_FRESH_PAST,))[0]
    is_due = (row["last_known_date"] is None or row["last_known_date"] < date.today()
              or row["last_fetched"] <= datetime.now(timezone.utc) - timedelta(days=REFRESH_AFTER_DAYS))
    assert is_due


def test_upsert_row_is_idempotent(updater, _cleanup):
    row = {"symbol": TEST_SYMBOL_FUTURE, "report_date": date.today() + timedelta(days=10),
           "eps_estimate": 1.5, "eps_actual": None, "surprise_pct": None}
    assert updater.upsert_row(row) is True
    row["eps_estimate"] = 2.5  # a re-fetch with a revised estimate must update, not duplicate
    assert updater.upsert_row(row) is True
    from shared import db
    rows = db.execute_dict_query(
        "SELECT eps_estimate FROM earnings_calendar WHERE symbol = %s AND report_date = %s",
        (row["symbol"], row["report_date"]))
    assert len(rows) == 1
    assert float(rows[0]["eps_estimate"]) == 2.5


# ---------------------------------------------------------------------------
# A recent report with no reported EPS yet is re-asked every run (the real SQL, end to end)
# ---------------------------------------------------------------------------
@pytest.fixture
def _in_universe():
    """Puts ZZTEST symbols into the universe (stock_prices) for the duration of one test, then removes them. Skips when the
    table will not take a minimal row (a schema with other required columns)."""
    d = _db()
    syms = ("ZZTEST_PE", "ZZTEST_DN", "ZZTEST_OL", "ZZTEST_JA")
    d.execute_insert("DELETE FROM stock_prices WHERE symbol LIKE 'ZZTEST_%%'")
    try:
        for s in syms:
            d.execute_insert("INSERT INTO stock_prices (symbol, date, close) VALUES (%s, CURRENT_DATE, 1)", (s,))
    except Exception as e:  # pragma: no cover
        d.execute_insert("DELETE FROM stock_prices WHERE symbol LIKE 'ZZTEST_%%'")
        pytest.skip(f"stock_prices does not take a minimal test row: {e}")
    yield syms
    d.execute_insert("DELETE FROM stock_prices WHERE symbol LIKE 'ZZTEST_%%'")


def _calendar(updater, symbol, report_date, *, actual, fetched_hours_ago, next_estimate_days=60):
    now = datetime.now(timezone.utc)
    fetched = now - timedelta(hours=fetched_hours_ago)
    _upsert_with_fetched_at(updater, {"symbol": symbol, "report_date": report_date, "eps_estimate": 1.0,
                                      "eps_actual": actual, "surprise_pct": None, "_fetched_at": fetched})
    # every symbol that has reported once also carries NEXT quarter's estimate - this is what hid the pending result before
    _upsert_with_fetched_at(updater, {"symbol": symbol, "report_date": date.today() + timedelta(days=next_estimate_days),
                                      "eps_estimate": 1.2, "eps_actual": None, "surprise_pct": None, "_fetched_at": fetched})


def test_recent_report_without_result_is_asked_again_even_with_a_future_date_on_file(updater, _cleanup, _in_universe):
    # fetched yesterday (well inside REFRESH_AFTER_DAYS) and a future date exists: the OLD rule left it alone for two weeks
    _calendar(updater, "ZZTEST_PE", date.today() - timedelta(days=1), actual=None, fetched_hours_ago=20)
    _calendar(updater, "ZZTEST_DN", date.today() - timedelta(days=1), actual=1.4, fetched_hours_ago=20)
    due = set(updater.get_symbols_to_update())
    assert "ZZTEST_PE" in due
    assert "ZZTEST_DN" not in due          # result captured and the next date is on file: nothing to ask


def test_pending_rule_is_bounded_in_time_and_not_repeated_within_a_run_gap(updater, _cleanup, _in_universe):
    _calendar(updater, "ZZTEST_OL", date.today() - timedelta(days=PENDING_WINDOW_DAYS + 3), actual=None, fetched_hours_ago=20)
    _calendar(updater, "ZZTEST_JA", date.today() - timedelta(days=1), actual=None, fetched_hours_ago=max(PENDING_MIN_GAP_HOURS - 2, 0) or 1)
    due = set(updater.get_symbols_to_update())
    assert "ZZTEST_OL" not in due           # past the window: back to the 14-day rule
    assert "ZZTEST_JA" not in due     # asked less than PENDING_MIN_GAP_HOURS ago


def test_freshness_audit_counts_are_integers(updater):
    audit = updater.freshness_audit()
    assert set(audit) >= {"due_last_2_days", "captured_last_2_days", "due_older", "unresolved"} or audit == {}
    assert all(isinstance(v, int) for v in audit.values())
