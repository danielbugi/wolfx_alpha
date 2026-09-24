#!/usr/bin/env python3
# mechanism/shared/market_calendar.py
"""
US equity-market calendar and the "is there a NEW session to process?" gate.

Why: the daily pipeline and the Telegram senders used to run on every scheduled tick. On weekends and NYSE holidays
that re-fetched ~3,000 symbols for nothing, and the senders re-posted the previous session's digest (their only guard
was a weekday list, which cannot know about holidays). The question that matters is not "is today a weekday" but
"has a US session COMPLETED that this job has not already processed?" -- that also self-heals a missed day.

    latest completed session = newest session whose close + MARKET_SETTLE_MINUTES (default 120) has passed
    run only if that session is newer than the one recorded for this job (data/session_state.json)

Calendar source, in order: Alpaca's market-calendar endpoint (official, includes holidays and early closes, uses the
ALPACA_API_KEY / ALPACA_API_SECRET already in .env) -> the on-disk cache in data/ (refreshed weekly) -> a Mon-Fri
fallback. The fallback FAILS OPEN (treats every weekday as a session): a wrongly skipped day loses data, a wrongly
run day only wastes work, and the downstream coverage / stale-data checks still guard the run.

CLI (used by automation_pipeline.sh):
    python mechanism/shared/market_calendar.py gate --key pipeline [--force]   # exit 0 = run (stdout: session), 3 = skip
    python mechanism/shared/market_calendar.py mark --key pipeline --session 2026-09-18
    python mechanism/shared/market_calendar.py status
Only stdlib + requests (+ python-dotenv when present); deliberately does not import the `shared` package (no DB pool).
"""
import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
NY = ZoneInfo("America/New_York")
CACHE_PATH = ROOT / "data" / "market_calendar_cache.json"
STATE_PATH = ROOT / "data" / "session_state.json"

ALPACA_CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
LOOKBACK_DAYS, LOOKAHEAD_DAYS = 45, 60      # calendar window written to the cache
CACHE_MAX_AGE_DAYS = 7
NEEDED_LOOKBACK_DAYS = 10                   # the cache must reach this far back to be usable
EXIT_SKIP = 3                               # `gate` exit code: nothing new to process

Sessions = Dict[date, datetime]             # trading date -> close (tz-aware, New York)


@dataclass
class Decision:
    run: bool
    session: Optional[date]                 # latest completed session (None only if the calendar is empty)
    reason: str
    source: str                             # alpaca | cache | stale-cache | weekday-fallback


def _settle_minutes() -> int:
    return int(os.getenv("MARKET_SETTLE_MINUTES", "120"))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass


def _close_dt(day: date, close_hhmm: str) -> datetime:
    h, m = (int(x) for x in close_hhmm.split(":")[:2])
    return datetime.combine(day, time(h, m), tzinfo=NY)


def _fetch_alpaca(start: date, end: date) -> Dict[str, str]:
    import requests
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_API_SECRET")
    if not (key and secret):
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET not set")
    r = requests.get(ALPACA_CALENDAR_URL, params={"start": start.isoformat(), "end": end.isoformat()},
                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=10)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("Alpaca returned an empty calendar")
    return {row["date"]: row["close"] for row in rows}      # {"2026-11-27": "13:00"} (early closes are real values)


def _read_cache() -> Optional[dict]:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _sessions_from(raw: Dict[str, str]) -> Sessions:
    return {date.fromisoformat(d): _close_dt(date.fromisoformat(d), c) for d, c in raw.items()}


def _cache_covers(cache: dict, today: date) -> bool:
    return (date.fromisoformat(cache["start"]) <= today - timedelta(days=NEEDED_LOOKBACK_DAYS)
            and date.fromisoformat(cache["end"]) >= today)


def get_sessions(today: Optional[date] = None) -> Tuple[Sessions, str]:
    """(sessions, source). `today` is the New York date."""
    _load_env()
    today = today or datetime.now(NY).date()
    cache = _read_cache()
    if cache and _cache_covers(cache, today) and (today - date.fromisoformat(cache["fetched"])).days <= CACHE_MAX_AGE_DAYS:
        return _sessions_from(cache["sessions"]), "cache"
    start, end = today - timedelta(days=LOOKBACK_DAYS), today + timedelta(days=LOOKAHEAD_DAYS)
    try:
        raw = _fetch_alpaca(start, end)
        _write_json_atomic(CACHE_PATH, {"fetched": today.isoformat(), "start": start.isoformat(), "end": end.isoformat(),
                                        "sessions": raw})
        return _sessions_from(raw), "alpaca"
    except Exception as e:                                   # network, credentials, bad payload: never block the caller
        print(f"[market_calendar] calendar fetch failed ({type(e).__name__}: {e})", file=sys.stderr)
    if cache and _cache_covers(cache, today):
        return _sessions_from(cache["sessions"]), "stale-cache"
    print("[market_calendar] WARNING: no calendar available - assuming every Mon-Fri is a session (holidays not detected)",
          file=sys.stderr)
    days = (today - timedelta(days=LOOKBACK_DAYS) + timedelta(days=i) for i in range(LOOKBACK_DAYS + 8))
    return {d: _close_dt(d, "16:00") for d in days if d.weekday() < 5}, "weekday-fallback"


def latest_completed(sessions: Sessions, now: Optional[datetime] = None) -> Optional[date]:
    """Newest session whose close + settle time has passed (so a vendor's end-of-day bar can exist)."""
    now = now or datetime.now(timezone.utc)
    ready = [d for d, close in sessions.items() if close + timedelta(minutes=_settle_minutes()) <= now]
    return max(ready) if ready else None


def is_trading_day(day: Optional[date] = None, sessions: Optional[Sessions] = None) -> bool:
    """Is `day` (New York date, default today) an actual US trading session -- a plain "was the market
    open on this calendar date" check, distinct from check_new_session's "has a session COMPLETED that
    a job has not processed yet". For same-day pre-market content (e.g. "who reports today") that must
    stay silent on weekends/holidays regardless of any job's processed-state, not depend on whether a
    PRIOR session has settled. Fails open (True) only via get_sessions()'s own weekday-fallback, exactly
    like the rest of this module -- never silently skips a real trading day on a broken calendar."""
    day = day or datetime.now(timezone.utc).astimezone(NY).date()
    if sessions is None:
        sessions, _ = get_sessions(day)
    return day in sessions


def _read_state() -> Dict[str, str]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_processed(key: str) -> Optional[date]:
    v = _read_state().get(key)
    return date.fromisoformat(v) if v else None


def mark_processed(key: str, session: date) -> None:
    """Record that `key` finished `session`. Only ever moves forward (a manual --date backfill can't rewind it)."""
    state = _read_state()
    if state.get(key, "") >= session.isoformat():
        return
    state[key] = session.isoformat()
    _write_json_atomic(STATE_PATH, state)


def check_new_session(key: str, now: Optional[datetime] = None, force: bool = False,
                      sessions: Optional[Sessions] = None, source: str = "given") -> Decision:
    """Should job `key` run now? True when a completed US session exists that the job has not processed yet."""
    if sessions is None:
        sessions, source = get_sessions((now or datetime.now(timezone.utc)).astimezone(NY).date())
    latest = latest_completed(sessions, now)
    if latest is None:
        return Decision(True, None, "calendar has no completed session - running (fail open)", source)
    last = last_processed(key)
    if force:
        return Decision(True, latest, f"forced (latest completed session {latest})", source)
    if last is not None and latest <= last:
        return Decision(False, latest,
                        f"no new US session: latest completed is {latest}, already processed by '{key}'. "
                        "Market closed (weekend/holiday) or the run was already done - use --force to override", source)
    return Decision(True, latest, f"new US session {latest}" + (f" (last processed: {last})" if last else " (first run)"), source)


def require_data_current(data_session: date, gate: Decision) -> None:
    """Abort when a newer session has completed than the price data holds (the updater has not caught up)."""
    if gate.session and data_session < gate.session:
        raise SystemExit(f"ABORT: session {gate.session} has completed but the newest price data is {data_session}; "
                         "run the data pipeline first (nothing sent).")


# ------------------------------------------------------------------ CLI
def _cli() -> int:
    ap = argparse.ArgumentParser(description="US market calendar gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate", help="exit 0 = run (prints the session date), 3 = skip (prints why)")
    g.add_argument("--key", required=True)
    g.add_argument("--force", action="store_true")
    m = sub.add_parser("mark", help="record a session as processed for a key")
    m.add_argument("--key", required=True)
    m.add_argument("--session", required=True)
    sub.add_parser("status", help="show the calendar source, latest completed session and stored state")
    td = sub.add_parser("trading-day", help="exit 0 = today (or --date) is a trading day, 3 = it is not")
    td.add_argument("--date", help="check this date instead of today (YYYY-MM-DD)")
    args = ap.parse_args()

    if args.cmd == "trading-day":
        day = date.fromisoformat(args.date) if args.date else None
        ok = is_trading_day(day)
        print("trading day" if ok else "not a trading day")
        return 0 if ok else EXIT_SKIP

    if args.cmd == "gate":
        d = check_new_session(args.key, force=args.force)
        print(d.session.isoformat() if d.run and d.session else d.reason)
        print(f"[market_calendar] {d.reason} [calendar: {d.source}]", file=sys.stderr)
        return 0 if d.run else EXIT_SKIP
    if args.cmd == "mark":
        mark_processed(args.key, date.fromisoformat(args.session))
        print(f"[market_calendar] '{args.key}' marked processed through {args.session}")
        return 0
    sessions, source = get_sessions()
    now = datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    nxt = min((d for d in sessions if d >= today and sessions[d] + timedelta(minutes=_settle_minutes()) > now), default=None)
    print(f"calendar source : {source}\nnow (New York)  : {now.astimezone(NY):%Y-%m-%d %H:%M %Z}\n"
          f"latest completed: {latest_completed(sessions, now)}\nnext session    : {nxt}\nstate ({STATE_PATH.name}): {_read_state()}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
