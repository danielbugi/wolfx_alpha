"""Unit tests for the trading-day gate (mechanism/shared/market_calendar.py). No network, no database.

Run:  python -m pytest mechanism/alerts/tests -q      (from the repo root)
"""
import os
import sys
from datetime import date, datetime, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism", "shared"))

import market_calendar as mc  # noqa: E402

NY = mc.NY


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def ny(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY)


def cal(*days, early=()):
    """Sessions for the given dates, 16:00 close (13:00 for `early`)."""
    return {date(*d): mc._close_dt(date(*d), "13:00" if d in early else "16:00") for d in days}


# Week of Mon 2026-09-14 .. Fri 09-18, then Mon 09-21 is a (hypothetical) HOLIDAY, Tue 09-22 trades.
CAL = cal((2026, 9, 14), (2026, 9, 15), (2026, 9, 16), (2026, 9, 17), (2026, 9, 18), (2026, 9, 22), (2026, 9, 23))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(mc, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.delenv("MARKET_SETTLE_MINUTES", raising=False)


def check(key, now, **kw):
    return mc.check_new_session(key, now=now, sessions=CAL, **kw)


# ------------------------------------------------------------------ latest completed session
def test_session_is_not_complete_until_close_plus_settle():
    assert mc.latest_completed(CAL, ny(2026, 9, 15, 15, 0)) == date(2026, 9, 14)    # still trading
    assert mc.latest_completed(CAL, ny(2026, 9, 15, 17, 0)) == date(2026, 9, 14)    # closed, inside the 120 min settle window
    assert mc.latest_completed(CAL, ny(2026, 9, 15, 18, 30)) == date(2026, 9, 15)


def test_early_close_completes_earlier():
    c = cal((2026, 11, 25), (2026, 11, 27), early=[(2026, 11, 27)])
    assert mc.latest_completed(c, ny(2026, 11, 27, 15, 30)) == date(2026, 11, 27)   # 13:00 close + 2 h
    assert mc.latest_completed(c, ny(2026, 11, 27, 14, 0)) == date(2026, 11, 25)


def test_settle_minutes_is_configurable(monkeypatch):
    monkeypatch.setenv("MARKET_SETTLE_MINUTES", "0")
    assert mc.latest_completed(CAL, ny(2026, 9, 15, 16, 1)) == date(2026, 9, 15)


# ------------------------------------------------------------------ is_trading_day (DATA_ML_MILESTONES.md M3)
def test_is_trading_day_true_for_a_real_session():
    assert mc.is_trading_day(date(2026, 9, 18), sessions=CAL) is True


def test_is_trading_day_false_for_a_weekend():
    assert mc.is_trading_day(date(2026, 9, 20), sessions=CAL) is False               # Sunday, not in CAL


def test_is_trading_day_false_for_a_holiday_between_two_sessions():
    assert mc.is_trading_day(date(2026, 9, 21), sessions=CAL) is False               # the hypothetical holiday
    assert mc.is_trading_day(date(2026, 9, 22), sessions=CAL) is True                # trades again the next day


def test_is_trading_day_defaults_today_in_new_york(monkeypatch):
    import market_calendar
    monkeypatch.setattr(market_calendar, "get_sessions", lambda today: (CAL, "given"))
    assert mc.is_trading_day(date(2026, 9, 18)) is True
    assert mc.is_trading_day(date(2026, 9, 20)) is False


# ------------------------------------------------------------------ the gate
def test_runs_after_a_new_session_and_not_twice():
    now = utc(2026, 9, 16, 3, 0)                       # Wed 06:00 Jerusalem = Tue 23:00 New York: the 09-15 session is done
    d = check("pipeline", now)
    assert d.run and d.session == date(2026, 9, 15)
    mc.mark_processed("pipeline", d.session)
    again = check("pipeline", utc(2026, 9, 16, 9, 0))
    assert not again.run and "already processed" in again.reason


def test_weekend_after_a_processed_friday_is_skipped():
    mc.mark_processed("pipeline", date(2026, 9, 18))
    for now in (utc(2026, 9, 19, 3, 0), utc(2026, 9, 20, 3, 0), utc(2026, 9, 21, 3, 0)):    # Sat, Sun, Mon 06:00 Jerusalem
        assert not check("pipeline", now).run


def test_holiday_is_skipped_and_the_next_session_runs():
    mc.mark_processed("pipeline", date(2026, 9, 18))
    assert not check("pipeline", utc(2026, 9, 22, 3, 0)).run          # Tue 06:00 Jerusalem: Mon 09-21 was a holiday
    d = check("pipeline", utc(2026, 9, 23, 3, 0))                     # Wed 06:00: Tue 09-22 completed
    assert d.run and d.session == date(2026, 9, 22)


def test_a_failed_run_is_retried_because_it_was_never_marked():
    now = utc(2026, 9, 16, 3, 0)
    assert check("pipeline", now).run          # run starts ... fails ... mark_processed is never called
    assert check("pipeline", utc(2026, 9, 16, 4, 0)).run


def test_a_missed_day_catches_up_to_the_latest_session():
    mc.mark_processed("pipeline", date(2026, 9, 14))
    d = check("pipeline", utc(2026, 9, 18, 3, 0))                     # Mon's marker, nothing since -> latest completed = 09-17
    assert d.run and d.session == date(2026, 9, 17)


def test_keys_are_independent_per_job_and_channel():
    mc.mark_processed("digest:prod", date(2026, 9, 15))
    now = utc(2026, 9, 16, 3, 0)
    assert not check("digest:prod", now).run
    assert check("digest:dev", now).run
    assert check("pipeline", now).run


def test_force_runs_anyway():
    mc.mark_processed("pipeline", date(2026, 9, 18))
    d = check("pipeline", utc(2026, 9, 20, 3, 0), force=True)
    assert d.run and d.session == date(2026, 9, 18)


def test_mark_never_moves_backwards():
    mc.mark_processed("pipeline", date(2026, 9, 18))
    mc.mark_processed("pipeline", date(2026, 9, 10))                  # e.g. a manual --date backfill
    assert mc.last_processed("pipeline") == date(2026, 9, 18)


def test_corrupt_state_file_is_treated_as_empty():
    mc.STATE_PATH.write_text("{not json", encoding="utf-8")
    assert check("pipeline", utc(2026, 9, 16, 3, 0)).run


def test_empty_calendar_fails_open():
    d = mc.check_new_session("pipeline", now=utc(2026, 9, 16, 3, 0), sessions={})
    assert d.run and d.session is None


# ------------------------------------------------------------------ data freshness guard
def test_require_data_current_aborts_when_prices_lag_the_session():
    gate = check("digest:dev", utc(2026, 9, 16, 3, 0))                # session 09-15 completed
    mc.require_data_current(date(2026, 9, 15), gate)                  # up to date: fine
    with pytest.raises(SystemExit, match="run the data pipeline first"):
        mc.require_data_current(date(2026, 9, 14), gate)


# ------------------------------------------------------------------ calendar source fallbacks
def test_no_calendar_available_falls_back_to_weekdays_and_warns(monkeypatch, capsys):
    monkeypatch.setattr(mc, "_fetch_alpaca", lambda s, e: (_ for _ in ()).throw(RuntimeError("offline")))
    sessions, source = mc.get_sessions(date(2026, 9, 21))
    assert source == "weekday-fallback"
    assert date(2026, 9, 18) in sessions and date(2026, 9, 19) not in sessions and date(2026, 9, 20) not in sessions
    assert "WARNING" in capsys.readouterr().err


def test_fetched_calendar_is_cached_then_reused_without_the_network(monkeypatch):
    calls = []

    def fake(start, end):
        calls.append((start, end))
        return {"2026-09-18": "16:00", "2026-09-22": "16:00", "2026-11-27": "13:00"}

    monkeypatch.setattr(mc, "_fetch_alpaca", fake)
    s1, src1 = mc.get_sessions(date(2026, 9, 21))
    s2, src2 = mc.get_sessions(date(2026, 9, 21))
    assert (src1, src2) == ("alpaca", "cache") and len(calls) == 1
    assert s2[date(2026, 11, 27)].hour == 13                          # the early close survives the cache round trip


def test_stale_cache_is_used_when_the_refresh_fails(monkeypatch):
    monkeypatch.setattr(mc, "_fetch_alpaca", lambda s, e: {"2026-09-18": "16:00"})
    mc.get_sessions(date(2026, 9, 21))                                # writes the cache
    monkeypatch.setattr(mc, "_fetch_alpaca", lambda s, e: (_ for _ in ()).throw(RuntimeError("offline")))
    sessions, source = mc.get_sessions(date(2026, 10, 1))             # cache is 10 days old -> refresh attempted -> fails
    assert source == "stale-cache" and date(2026, 9, 18) in sessions
