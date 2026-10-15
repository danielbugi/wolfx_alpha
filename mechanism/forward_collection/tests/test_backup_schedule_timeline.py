"""The nightly backup schedule against the REAL timeline of the other jobs, day by day across both countries' clock changes (2026-10-15 .. 2028-10-15).

Inputs are the tracked unit files (the pipeline timer and the backup timer) plus measured durations: the pipeline ran 84-85 minutes on every normal night and 133 minutes on its slowest night; with the
bounded quarterly step the worst case is ~141 minutes (steps 1-7 ~70 min + 55 min step-8 limit + ~16 min); the backup takes ~2 minutes (RandomizedDelaySec adds up to 5 minutes before it starts).

Two properties are asserted for the schedule that is installed (the backup timer in deploy/db):
  1. it never overlaps a pipeline run, even the slowest run on the retry path, on any day of the two years;
  2. its recovery point is COMPLETE: it starts after the morning collector, the paper tracker's last fire and the notices, so one dump contains the whole previous trading day's evidence.
It also records WHY the previous schedule (fixed 23:30 UTC) and the 21:00 New York candidate were not kept."""
import datetime as dt
import os
import re
from zoneinfo import ZoneInfo

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
UTC, JLM, NY = ZoneInfo("UTC"), ZoneInfo("Asia/Jerusalem"), ZoneInfo("America/New_York")
START, DAYS = dt.date(2026, 10, 15), 731
NORMAL_MIN, SLOWEST_SEEN_MIN, STAGE2_WORST_MIN = 85, 133, 141
BACKUP_MIN, BACKUP_JITTER_MIN, PIPELINE_JITTER_MIN = 3, 5, 1


def _calendars(path):
    """Every `OnCalendar=*-*-* HH:MM:SS <Zone>` of a unit file as (hour, minute, ZoneInfo)."""
    out = []
    for line in open(os.path.join(ROOT, path), encoding="utf-8").read().replace("\r\n", "\n").splitlines():
        m = re.match(r"^OnCalendar=\*-\*-\* (\d\d):(\d\d):00 (\S+)$", line.strip())
        if m:
            out.append((int(m.group(1)), int(m.group(2)), ZoneInfo(m.group(3))))
    return out


def instant(day, hour, minute, tz):
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(UTC)


def days():
    return [START + dt.timedelta(days=i) for i in range(DAYS)]


def pipeline_fires(day):
    """The primary and retry fires whose evening belongs to `day` (UTC instants, in schedule order)."""
    return sorted(instant(day, h, m, tz) for h, m, tz in _calendars("deploy/vps/donchian-pipeline.timer"))


def run_intervals(day, scenario):
    first, retry = pipeline_fires(day)[:2]
    j = dt.timedelta(minutes=PIPELINE_JITTER_MIN)
    mins = lambda n: dt.timedelta(minutes=n)
    if scenario == "normal":                     # the first fire passes the freshness gate; the retry is a no-op
        return [(first, first + j + mins(NORMAL_MIN))]
    if scenario == "first_fire_fails_then_retry":  # the old daily pattern: ~15 min failure, then a full run at the retry fire
        return [(first, first + j + mins(15)), (retry, retry + j + mins(NORMAL_MIN))]
    if scenario == "slowest_seen_on_retry":       # the 2026-10-07 night (133 min) happening on the retry path
        return [(first, first + j + mins(15)), (retry, retry + j + mins(SLOWEST_SEEN_MIN))]
    if scenario == "stage2_worst_on_retry":       # bounded quarterly step at its 55 min limit, on the retry path
        return [(first, first + j + mins(15)), (retry, retry + j + mins(STAGE2_WORST_MIN))]
    raise ValueError(scenario)


SCENARIOS = ("normal", "first_fire_fails_then_retry", "slowest_seen_on_retry", "stage2_worst_on_retry")


def backup_window(day_utc_date, fire):
    return fire, fire + dt.timedelta(minutes=BACKUP_JITTER_MIN + BACKUP_MIN)


def backup_fires(cal, first_day, last_day):
    h, m, tz = cal[0]
    out = []
    d = first_day
    while d <= last_day:
        out.append(instant(d, h, m, tz))
        d += dt.timedelta(days=1)
    return sorted(out)


def overlap_days(cal, scenario):
    """Days (by the pipeline's evening date) on which a backup window intersects a pipeline run of that scenario."""
    fires = backup_fires(cal, START - dt.timedelta(days=2), START + dt.timedelta(days=DAYS + 2))
    bad = []
    for d in days():
        runs = run_intervals(d, scenario)
        for f in fires:
            b0, b1 = backup_window(None, f)
            if any(b0 < r1 and r0 < b1 for r0, r1 in runs):
                bad.append(d)
                break
    return bad


def completeness_gaps(cal):
    """For every backup fire: it must start after the collector's last fire (08:15 JLM), the midday notice (09:00 JLM) and the tracker's last fire (11:30 JLM) of the JERUSALEM DAY IT FALLS ON,
    so that one dump holds the previous trading day's collector rows, notices and paper-tracker session. Returns the (backup instant, missing job) pairs."""
    needed = [(8, 15), (9, 0), (11, 30)]
    gaps = []
    for b in backup_fires(cal, START - dt.timedelta(days=1), START + dt.timedelta(days=DAYS)):
        jd = b.astimezone(JLM).date()
        for nh, nm in needed:
            if b < instant(jd, nh, nm, JLM) + dt.timedelta(minutes=10):
                gaps.append((b, f"{nh:02d}:{nm:02d}"))
    return gaps


CANDIDATES = {
    "previous: 23:30 UTC": [(23, 30, UTC)],
    "candidate: 21:00 America/New_York": [(21, 0, NY)],
    "installed: deploy/db/donchian-nightly-backup.timer": None,
}


def installed():
    return _calendars("deploy/db/donchian-nightly-backup.timer")


# ---------------------------------------------------------------------------------------------------------------------------------- the installed schedule
def test_the_backup_timer_has_exactly_one_calendar_and_keeps_its_other_settings():
    text = open(os.path.join(ROOT, "deploy/db/donchian-nightly-backup.timer"), encoding="utf-8").read().replace("\r\n", "\n")
    assert len(installed()) == 1 and "Persistent=true" in text and "RandomizedDelaySec=300" in text and "WantedBy=timers.target" in text


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_backup_never_overlaps_a_pipeline_run_on_any_day_in_two_years_even_in_the_slowest_scenarios(scenario):
    assert overlap_days(installed(), scenario) == []


def test_the_backup_recovery_point_is_complete_on_every_day_in_two_years():
    assert completeness_gaps(installed()) == []


def test_the_backup_leaves_a_long_idle_margin_before_the_next_pipeline_and_after_the_last_one():
    h, m, tz = installed()[0]
    for d in days():
        b0 = instant(d, h, m, tz)
        runs = run_intervals(d, "stage2_worst_on_retry")
        prev_end = max(r1 for r0, r1 in run_intervals(d - dt.timedelta(days=1), "stage2_worst_on_retry"))
        next_start = min(r0 for r0, r1 in runs)
        assert b0 - prev_end >= dt.timedelta(hours=6), d                       # at least 6 h after the slowest possible run ended
        assert next_start - (b0 + dt.timedelta(minutes=BACKUP_JITTER_MIN + BACKUP_MIN)) >= dt.timedelta(hours=8), d      # at least 8 h before the next primary fire


# ---------------------------------------------------------------------------------------------------------------------------------- why the alternatives were not kept
def test_the_previous_fixed_utc_schedule_overlaps_the_new_primary_run_every_winter_night():
    prev = CANDIDATES["previous: 23:30 UTC"]
    winter_normal = [d for d in overlap_days(prev, "normal") if d.month in (11, 12, 1, 2)]
    assert len(winter_normal) > 100, "the previous schedule overlaps the pipeline's NORMAL run through the US winter, which is why it had to move"


def test_the_21_00_new_york_candidate_overlaps_the_retry_path_on_slow_nights_and_leaves_the_morning_evidence_unprotected():
    cand = CANDIDATES["candidate: 21:00 America/New_York"]
    assert overlap_days(cand, "normal") == [] and overlap_days(cand, "first_fire_fails_then_retry") == []
    assert len(overlap_days(cand, "slowest_seen_on_retry")) > 300 and len(overlap_days(cand, "stage2_worst_on_retry")) > 300
    gaps = completeness_gaps(cand)
    assert len(gaps) > 1000, "it starts before the collector, the notices and the tracker of the same day, so their immutable rows wait for the NEXT night's dump"
