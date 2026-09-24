# mechanism/alerts/schedule.py
"""
"Today's schedule" for the Telegram Control Center: what is configured to post today, and whether it already has.

Deliberately NOT a live read of Windows Task Scheduler (platform-specific, and market_calendar.py's own docstring makes the same choice for the
same reason: "deliberately does not import the `shared` package"). Built instead from the senders' OWN state files - the same
`data/session_state.json`, `data/notice_state.json`, `data/earnings_today_state.json` the scheduled scripts themselves read and write - so this
can only ever say what those scripts' own state agrees is true, and works unchanged wherever this is hosted.

The state-KEY formats below are copied from, not imported from, the senders that own them (send_channel_notices.py keys by the owner's configured
ALERTS_TIMEZONE date; send_earnings_today.py deliberately keys by the NY calendar date instead - "who reports today" is a US-market-date question,
and this module copies that exact choice, not a uniform one of its own):
importing those modules would also import `shared.db` and open a second, redundant connection pool inside this process (see
services/telegram_control_service.py's docstring for the same reasoning). test_channel_control.py checks the formats here still match those
files' own `_key` lines, so a change on either side is caught rather than silently drifting. The session-gated jobs (digest, extra post) need no
such copy: their state key is a trading-session date, not a "today" key, so `market_calendar.check_new_session`/`last_processed` already carry
the full, correct logic - this module only asks them, never recomputes what "the latest session" means.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from shared import market_calendar as mc

NY = ZoneInfo("America/New_York")
DATA = mc.ROOT / "data"
NOTICE_STATE = DATA / "notice_state.json"
EARNINGS_STATE = DATA / "earnings_today_state.json"


@dataclass(frozen=True)
class ScheduleJob:
    key: str          # a stable id for this row
    label: str
    local_time: str   # "HH:MM", the job's registered Task Scheduler trigger time (kept here as data, not read live - see the module docstring)
    gate: str          # "session" (market_calendar.check_new_session) | "day" (is_trading_day + a plain date key) | "always" (every day)
    slot: int = 0      # only meaningful for gate="always": send_channel_notices.py's own slot number (1 or 2), part of its state key


JOBS: List[ScheduleJob] = [
    ScheduleJob("digest", "Daily digest", "06:00", "session"),
    ScheduleJob("posts", "Extra channel post", "06:00", "session"),
    ScheduleJob("earnings", "Who reports today", "11:00", "day"),
    ScheduleJob("notice1", "Midday notice", "12:00", "always", slot=1),
    ScheduleJob("notice2", "Evening notice", "20:00", "always", slot=2),
]


def _read_json(path: Path) -> Dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _local_time_passed(now: datetime, tz: ZoneInfo, hhmm: str) -> bool:
    h, m = (int(x) for x in hhmm.split(":"))
    return now.astimezone(tz).time() >= dtime(h, m)


def _pending_or_overdue(now: datetime, tz: ZoneInfo, local_time: str) -> str:
    return "overdue" if _local_time_passed(now, tz, local_time) else "pending"


def _session_status(job: ScheduleJob, target: str, now: datetime, tz: ZoneInfo) -> Dict[str, Any]:
    # "posts" posts under a different state key on Sundays (the weekly recap instead) - send_channel_posts.py's own rule: `'recap' if kinds ==
    # ['recap'] else 'posts'`, and its Sunday branch is `now_local.weekday() == 6` (ALERTS_TIMEZONE-local, same `tz` this module was given).
    base = "recap" if job.key == "posts" and now.astimezone(tz).weekday() == 6 else job.key
    gate = mc.check_new_session(f"{base}:{target}", now=now)
    if not gate.run:
        return {"status": "done", "reason": gate.reason}
    return {"status": _pending_or_overdue(now, tz, job.local_time), "reason": gate.reason}


def _day_status(target: str, now: datetime, tz: ZoneInfo, local_time: str) -> Dict[str, Any]:
    # send_earnings_today.py keys its state by the NY calendar date (`datetime.now(tz).astimezone(NY).date()`), not the owner's local date -
    # "who reports today" is inherently a US-market-date question, and this module copies that exact resolution, not the notices' Israel-local one.
    ny_today = now.astimezone(NY).date()
    if not mc.is_trading_day(ny_today):
        return {"status": "skipped", "reason": "not a US trading day"}
    sent = f"{target}:{ny_today.isoformat()}" in _read_json(EARNINGS_STATE)             # send_earnings_today.py's own _key(target, day)
    if sent:
        return {"status": "done", "reason": "sent today"}
    status = _pending_or_overdue(now, tz, local_time)
    # This job's own state file only ever records a real SEND - a day with no covered-universe reporters is a legitimate, silent no-op (it
    # never marks itself sent), so "overdue" here can honestly mean either "hasn't run yet" or "ran, found nothing to post": check its log to tell.
    reason = "not sent yet today, or ran and had nothing to post - check logs/earnings_today_*.log" if status == "overdue" else "not sent yet today"
    return {"status": status, "reason": reason}


def _always_status(target: str, today_local: date, slot: int, now: datetime, tz: ZoneInfo, local_time: str) -> Dict[str, Any]:
    sent = f"{target}:{today_local.isoformat()}:{slot}" in _read_json(NOTICE_STATE)      # send_channel_notices.py's own _key(target, day, slot)
    if sent:
        return {"status": "done", "reason": "sent today"}
    return {"status": _pending_or_overdue(now, tz, local_time), "reason": "not sent yet today"}


def today_schedule(target: str, tz: ZoneInfo, now: datetime) -> List[Dict[str, Any]]:
    """One row per known job, in trigger-time order: label, local_time, status (done | pending | overdue | skipped), and why."""
    today_local = now.astimezone(tz).date()
    rows = []
    for job in JOBS:
        if job.gate == "session":
            state = _session_status(job, target, now, tz)
        elif job.gate == "day":
            state = _day_status(target, now, tz, job.local_time)
        else:
            state = _always_status(target, today_local, job.slot, now, tz, job.local_time)
        rows.append({"key": job.key, "label": job.label, "local_time": job.local_time, **state})
    return rows
