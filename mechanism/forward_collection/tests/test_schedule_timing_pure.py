"""The collector's fire schedule (04:00 and 08:15 Asia/Jerusalem, the day after a session), checked against the real market calendar function, both daylight-saving
offsets AND the weeks in which Israel and the US are on different offsets, and against the slowest pipeline night actually observed in production.

What is pinned: which session each fire resolves (from the US calendar, never from a pipeline marker); that the first fire is after the slowest observed pipeline
finish by a real margin (and that the previous 03:15 schedule was not); that both fires fall before the decision deadline; that the design validator rejects a
first fire that is too early; and the order against the ledger evaluator."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path, like every test in this directory)
from forward_collection import contract as C
from research.lab.dataset_contract import decision_deadline
from shared import market_calendar as mc

NY, JER = ZoneInfo("America/New_York"), ZoneInfo("Asia/Jerusalem")
OBSERVED_SLOWEST_FINISH = datetime(2026, 10, 8, 0, 13, 43, tzinfo=timezone.utc)      # pipeline of session 2026-10-07, the 14-day earnings re-fetch night
OBSERVED_NORMAL_FINISH = datetime(2026, 10, 7, 23, 24, 55, tzinfo=timezone.utc)      # pipeline of session 2026-10-06


def weekday_sessions(year=2026):
    """US trading sessions approximated by weekdays (holidays are irrelevant to the resolution rule under test), close 16:00 New York time."""
    out, d = {}, date(year, 1, 1) - timedelta(days=7)
    while d < date(year + 1, 1, 8):
        if d.weekday() < 5:
            out[d] = datetime.combine(d, time(16, 0), NY).astimezone(timezone.utc)
        d += timedelta(days=1)
    return out


SESSIONS = weekday_sessions()
WEEKDAYS = [d for d in SESSIONS if date(2026, 1, 1) <= d <= date(2026, 12, 31)]


def test_the_declared_schedule_is_06_45_and_08_15_in_jerusalem_time():
    assert C.SCHEDULER_DESIGN["fires_local"] == ("06:45", "08:15") and C.SCHEDULER_DESIGN["timezone"] == "Asia/Jerusalem"
    assert "--with-capture-check" in C.SCHEDULER_DESIGN["command"] and "--with-sector-history-check" in C.SCHEDULER_DESIGN["command"]
    assert C.validate_design(1) == []


@pytest.mark.parametrize("label,session,first_utc,first_ny,ny_day_offset", [
    ("summer (both on DST)", date(2026, 7, 6), (3, 45), (23, 45), 0),
    ("winter (neither on DST)", date(2026, 12, 7), (4, 45), (23, 45), 0),
    ("spring mismatch: US on DST, Israel not yet", date(2026, 3, 16), (4, 45), (0, 45), 1),
    ("autumn mismatch: Israel off DST, US still on", date(2026, 10, 26), (4, 45), (0, 45), 1),
])
def test_the_fires_follow_the_local_clock_across_every_daylight_saving_regime(label, session, first_utc, first_ny, ny_day_offset):
    f1, f2 = C.fire_instants_utc(session)
    assert (f1.hour, f1.minute) == first_utc, label
    assert f1.astimezone(JER).strftime("%H:%M") == "06:45" and f2.astimezone(JER).strftime("%H:%M") == "08:15"
    assert (f1.astimezone(NY).hour, f1.astimezone(NY).minute) == first_ny and f1.astimezone(NY).date() == session + timedelta(days=ny_day_offset)
    assert f1.date() == session + timedelta(days=1) and f2.date() == session + timedelta(days=1)          # the calendar day AFTER the session (UTC)


def test_every_fire_of_every_weekday_resolves_that_weekdays_session_from_the_calendar_not_from_the_pipeline():
    for s in WEEKDAYS:
        for f in C.fire_instants_utc(s):
            assert mc.latest_completed(SESSIONS, f) == s, (s, f)          # Tue..Sat for Mon..Fri; Mon..Fri (not weekends) are what the design says


def test_weekend_and_monday_fires_re_resolve_friday_so_they_are_idempotent_re_checks_never_a_new_session():
    friday = date(2026, 10, 9)
    for day in (date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)):        # Sat, Sun, Mon
        for hhmm in C.SCHEDULER_DESIGN["fires_local"]:
            h, m = (int(x) for x in hhmm.split(":"))
            now = datetime.combine(day, time(h, m), JER).astimezone(timezone.utc)
            assert mc.latest_completed(SESSIONS, now) == friday, (day, hhmm)
    before_settle = datetime.combine(date(2026, 10, 12), time(23, 59), JER).astimezone(timezone.utc)      # Monday's close + the 2 h settle is 01:00 Tuesday here
    after_settle = datetime.combine(date(2026, 10, 13), time(1, 30), JER).astimezone(timezone.utc)
    assert mc.latest_completed(SESSIONS, before_settle) == friday and mc.latest_completed(SESSIONS, after_settle) == date(2026, 10, 12)


def test_the_first_fire_leaves_a_real_margin_after_the_slowest_observed_pipeline_and_the_old_03_15_did_not():
    first = C.fire_instants_utc(date(2026, 10, 7))[0]
    assert first - OBSERVED_SLOWEST_FINISH >= timedelta(minutes=45)
    assert first - OBSERVED_NORMAL_FINISH >= timedelta(hours=1)
    old_first = datetime(2026, 10, 8, 3, 15, tzinfo=JER).astimezone(timezone.utc)
    assert old_first - OBSERVED_SLOWEST_FINISH < timedelta(minutes=5)                 # the reason the first fire moved: 77 seconds of margin
    old = dict(C.SCHEDULER_DESIGN, fires_local=("03:15", "08:15"))
    assert any("pipeline" in p for p in C.validate_design(1, old)), "the validator must now reject the old 03:15 first fire"


def test_the_first_fire_follows_the_ledger_evaluator_and_precedes_the_recovery_fire_by_at_least_an_hour():
    h, m = (int(x) for x in C.SCHEDULER_DESIGN["evaluator_local"].split(":"))
    f1, f2 = (tuple(int(x) for x in t.split(":")) for t in C.SCHEDULER_DESIGN["fires_local"])
    assert f1 > (h, m) and (f2[0] * 60 + f2[1]) - (f1[0] * 60 + f1[1]) >= 60


def test_both_fires_are_before_the_decision_deadline_with_one_grace_day_in_every_regime():
    assert C.required_grace_days() == 1
    for s in WEEKDAYS:
        assert max(C.fire_instants_utc(s)) + timedelta(minutes=C.SCHEDULER_DESIGN["timeout_minutes"], seconds=60) < decision_deadline(s, 1), s
        assert C.worst_case_finish_utc(s) - decision_deadline(s, 0) < timedelta(days=1)           # i.e. one grace day is exactly enough


def test_a_first_fire_before_the_scan_or_the_pipeline_is_final_is_rejected_and_06_45_is_the_earliest_accepted():
    ok, bad = [], []
    for hh in range(2, 9):
        for mm in (0, 15, 30, 45):
            d = dict(C.SCHEDULER_DESIGN, fires_local=(f"{hh:02d}:{mm:02d}", "09:30"))
            (bad if any(("pipeline" in p or "scan" in p) for p in C.validate_design(1, d)) else ok).append(f"{hh:02d}:{mm:02d}")
    assert ok[0] == "06:45" and "06:30" in bad and "03:30" in bad                # the scan's start (06:20) + its 25 minute budget dominates the old pipeline limit (03:45)
    assert all(t < "06:45" for t in bad) and all(t >= "06:45" for t in ok)
