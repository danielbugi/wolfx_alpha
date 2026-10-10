"""Calendar boundaries for the scan and the collector: when is a US session COMPLETE, and which session does each scheduled fire target?

Pure (no database, no network). The REAL calendar the VPS fetched on 2026-10-10 (Alpaca, 74 sessions, 2026-08-26 -> 2026-12-09) is the fixture for
Labor Day, Thanksgiving and the following early close. A full synthetic 2027 calendar (holidays, Good Friday, Independence Day observed, an early close)
pins the same invariants across both daylight-saving transitions of the United States AND Israel (the two change on different dates).

Definition under test (mechanism/shared/market_calendar.py, ONE function for the pipeline gate, the collector and the scan): a session is complete only
when its ACTUAL market close (16:00 New York, 13:00 on early closes) PLUS the settle time (120 min, so a vendor's end-of-day bar can exist) has passed.
"""
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path)
from forward_collection import contract as C
from shared import market_calendar as mc

NY = ZoneInfo("America/New_York")
SETTLE = timedelta(minutes=120)
FIXTURE = Path(__file__).parent / "fixtures" / "market_calendar_2026H2.json"
UTC = timezone.utc


def real_sessions():
    return mc._sessions_from(json.loads(FIXTURE.read_text(encoding="utf-8"))["sessions"])


def close_utc(sessions, d):
    return sessions[d].astimezone(UTC)


def synthetic_2027():
    holidays = {date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5),
                date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24)}
    early = {date(2027, 11, 26): "13:00"}
    raw, d = {}, date(2026, 12, 1)
    while d <= date(2027, 12, 31):
        if d.weekday() < 5 and d not in holidays and d != date(2026, 12, 25):
            raw[d.isoformat()] = early.get(d, "16:00")
        d += timedelta(days=1)
    return mc._sessions_from(raw)


# ---------------------------------------------------------------------------------------------- complete only when the ACTUAL close + settle has passed
def test_the_fixture_is_the_real_calendar_with_the_thanksgiving_early_close_and_the_holidays():
    s = real_sessions()
    assert len(s) == 74 and date(2026, 11, 26) not in s and date(2026, 9, 7) not in s          # Thanksgiving, Labor Day: closed
    assert s[date(2026, 11, 27)].astimezone(NY).time() == time(13, 0)                           # the day after Thanksgiving closes early
    assert s[date(2026, 11, 25)].astimezone(NY).time() == time(16, 0) and s[date(2026, 10, 12)].astimezone(NY).time() == time(16, 0)   # Columbus Day: open


def test_every_real_session_is_incomplete_until_its_own_close_plus_settle():
    s = real_sessions()
    for d, close in s.items():
        c = close.astimezone(UTC)
        earlier = [x for x in s if x < d]
        before = mc.latest_completed(s, c + SETTLE - timedelta(seconds=1))
        assert before != d and (before is None or before < d), d                                 # not complete one second before close + settle
        assert mc.latest_completed(s, c + SETTLE) == d, d                                        # complete exactly at close + settle
        assert mc.latest_completed(s, c - timedelta(minutes=1)) != d                             # and certainly not before the close itself
        if earlier:
            assert mc.latest_completed(s, c) == max(earlier)                                     # at the bell the previous session is still the latest


def test_the_early_close_completes_at_15_00_new_york_not_18_00():
    s = real_sessions()
    ny = lambda y, m, d, h, mi: datetime(y, m, d, h, mi, tzinfo=NY).astimezone(UTC)
    assert mc.latest_completed(s, ny(2026, 11, 27, 14, 59)) == date(2026, 11, 25)                # closed 13:00, settle not yet over
    assert mc.latest_completed(s, ny(2026, 11, 27, 15, 0)) == date(2026, 11, 27)
    assert mc.latest_completed(s, ny(2026, 11, 27, 13, 0)) == date(2026, 11, 25)                 # at the bell itself it is NOT complete


def test_thanksgiving_day_has_no_session_and_the_wednesday_stays_the_latest_until_the_early_close_settles():
    s = real_sessions()
    for hh in (0, 12, 23):
        assert mc.latest_completed(s, datetime(2026, 11, 26, hh, 0, tzinfo=UTC)) == date(2026, 11, 25)
    with pytest.raises(mc.SessionError):
        mc.resolve_session(date(2026, 11, 26), datetime(2026, 11, 28, 12, 0, tzinfo=UTC), s)
    assert mc.resolve_session(None, datetime(2026, 11, 27, 12, 0, tzinfo=UTC), s)[0] == date(2026, 11, 25)       # Fri 07:00 NY: Friday itself is open, not done


def test_weekends_and_holidays_are_never_sessions():
    s = real_sessions()
    now = datetime(2026, 12, 8, 12, 0, tzinfo=UTC)
    d = date(2026, 8, 26)
    while d <= date(2026, 12, 8):
        if d not in s:
            with pytest.raises(mc.SessionError):
                mc.resolve_session(d, now, s)
        d += timedelta(days=1)
    assert mc.resolve_session(None, datetime(2026, 10, 10, 3, 20, tzinfo=UTC), s) == (date(2026, 10, 9), "latest completed session")        # Saturday 03:20Z
    assert mc.resolve_session(None, datetime(2026, 10, 12, 3, 20, tzinfo=UTC), s)[0] == date(2026, 10, 9)                                     # Monday 03:20Z: still Friday
    assert mc.resolve_session(None, datetime(2026, 9, 8, 3, 20, tzinfo=UTC), s)[0] == date(2026, 9, 4)                                        # after Labor Day Monday


# ---------------------------------------------------------------------------------------------- which session does each night's scan and collector target?
EXPECT_SCAN_TARGET = {                      # (fire date in Asia/Jerusalem) -> session the 06:20 scan targets, from the real calendar
    date(2026, 10, 10): date(2026, 10, 9), date(2026, 10, 11): date(2026, 10, 9), date(2026, 10, 12): date(2026, 10, 9), date(2026, 10, 13): date(2026, 10, 12),
    date(2026, 10, 25): date(2026, 10, 23), date(2026, 10, 26): date(2026, 10, 23), date(2026, 10, 27): date(2026, 10, 26),
    date(2026, 11, 1): date(2026, 10, 30), date(2026, 11, 2): date(2026, 10, 30), date(2026, 11, 3): date(2026, 11, 2),
    date(2026, 11, 26): date(2026, 11, 25), date(2026, 11, 27): date(2026, 11, 25), date(2026, 11, 28): date(2026, 11, 27), date(2026, 11, 30): date(2026, 11, 27),
}


@pytest.mark.parametrize("night,expected", sorted(EXPECT_SCAN_TARGET.items()))
def test_the_scan_targets_the_expected_session_on_the_real_calendar(night, expected):
    s = real_sessions()
    scan = C.scan_instant_utc(night - timedelta(days=1))                  # the contract's instant for "the calendar day after the session" == `night`
    assert scan.astimezone(ZoneInfo("Asia/Jerusalem")).date() == night and scan.astimezone(ZoneInfo("Asia/Jerusalem")).time() == time(6, 20)
    assert mc.latest_completed(s, scan) == expected


def test_israel_and_us_dst_shift_the_utc_instant_but_never_the_target_session():
    s = real_sessions()
    at = lambda d: C.scan_instant_utc(d - timedelta(days=1))
    assert at(date(2026, 10, 24)).hour == 3 and at(date(2026, 10, 25)).hour == 4          # Israel leaves daylight time on 2026-10-25
    assert at(date(2026, 10, 31)).hour == 4 and at(date(2026, 11, 2)).hour == 4           # US leaves it on 2026-11-01: no further UTC shift of the schedule
    for d, want in ((date(2026, 10, 24), date(2026, 10, 23)), (date(2026, 10, 25), date(2026, 10, 23)), (date(2026, 11, 2), date(2026, 10, 30))):
        assert mc.latest_completed(s, at(d)) == want


def _each_fire_targets_its_own_session(sessions, first, last):
    """For every session S: the scan and BOTH collector fires of the day after S resolve to S, and each is at least 5 h (the scan is the tightest: 5 h 20 min) after S has completed
    (close + settle), so no fire can ever precede completion, in any combination of the two daylight-saving regimes."""
    ordered = sorted(d for d in sessions if first <= d <= last)
    worst = timedelta(days=99)
    for d in ordered:
        done = sessions[d].astimezone(UTC) + SETTLE
        for inst in [C.scan_instant_utc(d)] + C.fire_instants_utc(d):
            assert mc.latest_completed(sessions, inst) == d, (d, inst)                           # the night after S always targets S itself
            assert inst - done >= timedelta(hours=5), (d, inst, inst - done)
            worst = min(worst, inst - done)
    return worst


def test_every_real_session_is_targeted_by_its_own_nights_fires_with_a_safe_margin():
    s = real_sessions()
    worst = _each_fire_targets_its_own_session(s, date(2026, 8, 26), date(2026, 12, 8))
    assert worst == timedelta(hours=5, minutes=20)                          # the scan itself is the tightest fire, in every DST regime


def test_a_full_synthetic_year_across_both_dst_transitions_keeps_the_same_invariants():
    s = synthetic_2027()
    assert date(2027, 3, 26) not in s and date(2027, 7, 5) not in s and date(2027, 11, 25) not in s and date(2027, 12, 24) not in s      # Good Friday, July 4 obs., Thanksgiving, Christmas obs.
    worst = _each_fire_targets_its_own_session(s, date(2027, 1, 4), date(2027, 12, 22))
    assert worst >= timedelta(hours=5)
    assert s[date(2027, 11, 26)].astimezone(NY).time() == time(13, 0)
    # the US changes on 2027-03-14 and 2027-11-07, Israel on 2027-03-26 and 2027-10-31: the weeks between are the dangerous ones
    for d in (date(2027, 3, 12), date(2027, 3, 15), date(2027, 3, 29), date(2027, 10, 29), date(2027, 11, 1), date(2027, 11, 8)):
        if d in s:
            for inst in [C.scan_instant_utc(d)] + C.fire_instants_utc(d):
                assert mc.latest_completed(s, inst) >= d
    assert mc.latest_completed(s, C.scan_instant_utc(date(2027, 3, 25))) == date(2027, 3, 25)     # Thursday session; Friday 26 (Good Friday) is closed
    assert mc.latest_completed(s, C.scan_instant_utc(date(2027, 3, 26) - timedelta(days=1))) == date(2027, 3, 25)
    assert mc.latest_completed(s, datetime(2027, 3, 29, 3, 20, tzinfo=UTC)) == date(2027, 3, 25)  # Monday after Good Friday: Thursday is the latest
    assert mc.latest_completed(s, datetime(2027, 7, 6, 3, 20, tzinfo=UTC)) == date(2027, 7, 2)    # Tuesday after July 5: the previous Friday
