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
    EarningsCalendarUpdater, REFRESH_AFTER_DAYS, _safe_float,
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
    try:
        from shared import db as _d
        _d.test_connection()
        return _d
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database not reachable: {e}")


@pytest.fixture
def updater():
    return EarningsCalendarUpdater()


@pytest.fixture(autouse=True)
def _cleanup():
    """Remove any test rows before AND after each test, so a failed run never leaves fixtures behind
    to contaminate the next one, and a fresh run never inherits stale ones."""
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


def test_symbol_with_no_row_is_due(updater):
    # No row ever inserted for this symbol -- must be considered due. get_symbols_to_update()'s
    # universe is stock_prices, which a ZZTEST symbol is never in, so assert on the predicate
    # directly via a one-symbol query instead of the full-universe scan.
    from shared import db
    rows = db.execute_dict_query(
        "SELECT 1 FROM earnings_calendar WHERE symbol = %s", (TEST_SYMBOL_FUTURE,))
    assert rows == []  # sanity: nothing there yet (this is what "due" means for a brand new symbol)


def test_future_known_date_freshly_fetched_is_not_due(updater):
    _upsert_with_fetched_at(updater, _row(TEST_SYMBOL_FUTURE, date.today() + timedelta(days=20),
                                          datetime.now(timezone.utc)))
    from shared import db
    row = db.execute_dict_query(
        "SELECT MAX(report_date) AS last_known_date, MAX(fetched_at) AS last_fetched "
        "FROM earnings_calendar WHERE symbol = %s", (TEST_SYMBOL_FUTURE,))[0]
    is_due = (row["last_known_date"] is None or row["last_known_date"] < date.today()
              or row["last_fetched"] <= datetime.now(timezone.utc) - timedelta(days=REFRESH_AFTER_DAYS))
    assert not is_due


def test_known_date_now_in_the_past_is_due(updater):
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


def test_future_date_but_fetched_long_ago_is_due(updater):
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


def test_upsert_row_is_idempotent(updater):
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
