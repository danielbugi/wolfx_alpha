#!/usr/bin/env python3
"""Fundamental Updates: the nightly publisher, OWNER CHAT ONLY (rollout gate 4 in docs/product/FUNDAMENTAL_UPDATES.md). DORMANT.

Nothing runs this: no pipeline step, unit or timer calls it. Activation steps and rollback: docs/operations/FUNDAMENTAL_UPDATES_DEPLOYMENT.md.

One explicit market session per run (never inferred from the wall clock):
  1. load the session's candidate events: inflection_v2_sec_pit, public within FRESH_DAYS before the session (the same columns as
     docs/product/fu_selection_simulation.sql, so the night is chosen exactly as the reviewed simulation chose it);
  2. read the cooldown (companies already published in the last COOLDOWN_DAYS) from the publication ledger;
  3. select at most 3 companies (update_select.select: 1 per company, 2 per lead type, empty slots allowed);
  4. --record: write each selected update to the publication ledger (migration 41; version 1, append-only, ON CONFLICT DO NOTHING);
  5. --send:  send each RECORDED update ONCE to the owner's private chat (photo card + caption + buttons), never a channel.

Duplicate-notification guards (each one alone prevents a repeat):
  * the ledger's UNIQUE (update_key, version): an update is published once, re-runs report 'exists';
  * an exactly-once delivery claim per update (inflection_alert_delivery, dedupe key 'fu_owner:<update_key>:v1', UNIQUE): a re-run, the
    00:15 retry or a second process cannot send the same update twice; a FAILED send may be retried, a claimed or sent one never;
  * the 30-day company cooldown (a company is not posted again for a different quarter/type within 30 days);
  * the per-session cap holds ACROSS runs: the session's own earlier picks (version-1 rows published inside the session's window, from its 16:00
    New York close to the next weekday's close) are exempt from the cooldown, so a re-run reproduces the same picks ('exists') instead of
    choosing the next companies, and no run records more than MAX_POSTS updates for one session. --record is refused outside that window (a late
    manual run would otherwise land in the next session's window);
  * the legacy owner alerts must be OFF: sending refuses while INFLECTION_ALERTS_ENABLED=1, so one event can never reach the owner twice
    (once as an old alert, once as a Fundamental Update).

Gates. Default is a dry run that prints the night's picks and writes nothing.
  --record needs migration 41 (refuses cleanly without it).
  --send   needs --record, FU_OWNER_SEND_ENABLED=1 and INFLECTION_ALERTS_ENABLED not 1. PROD_SENDING_ENABLED is not involved (no channel).
Corrections (--revise; RECORD-ONLY, never sends): updates first published in the last REVISE_DAYS are rebuilt from their event's current
state and compared with what was published (update_model.classify_revision). A changed fact becomes a new ledger version (restated / redated /
withdrawn); nothing is posted, edited or deleted in Telegram, and the owner-chat delivery key is per update and version 1 only, so a correction can
never be re-sent as a new update. Without FU_CORRECTIONS_ENABLED=1 it only reports what it would record. A mass-change breaker refuses to record
anything when more than MAX_REVISIONS_PER_RUN updates would change in one run (a sign of a data problem, not of real corrections).
News stays off (owner decision: no public news until Alpaca redistribution rights are verified): updates carry no headlines.

    python mechanism/inflection/update_publish.py --session 2026-11-09                 # dry run
    python mechanism/inflection/update_publish.py --session 2026-11-09 --record        # ledger only
    python mechanism/inflection/update_publish.py --session 2026-11-09 --record --send  # ledger + owner chat
    python mechanism/inflection/update_publish.py --session 2026-11-09 --revise         # corrections: report (or record, if enabled); never sends
Exit: 0 ok (including "nothing to do" and dry runs) | 1 at least one send failed | 2 refused (bad arguments or a gate).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional
from zoneinfo import ZoneInfo

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from inflection import update_select as S  # noqa: E402

SOURCE_VERSION = "inflection_v2_sec_pit"
CHANNEL = "fu_owner"                     # inflection_alert_delivery.channel for this path
RECIPIENT = "owner"                      # a label, never an address
SEND_ENV = "FU_OWNER_SEND_ENABLED"
CORRECTIONS_ENV = "FU_CORRECTIONS_ENABLED"
REVISE_DAYS = 120
MAX_REVISIONS_PER_RUN = 10
LEGACY_ENV = "INFLECTION_ALERTS_ENABLED"
EXIT_OK, EXIT_SEND_FAILED, EXIT_REFUSED = 0, 1, 2

_EVENT_SELECT = """
SELECT e.id, e.symbol, e.event_type, e.severity, e.strength, e.fiscal_year, e.fiscal_quarter, e.quarter_end, e.available_at, e.availability_basis, e.data_quality,
       e.method_version, e.metrics, e.context, (e.first_detected_at AT TIME ZONE 'UTC') AS first_detected_utc, (e.first_detected_at AT TIME ZONE 'UTC')::date AS first_detected_on,
       co.company_name, sc.cik, f.quality_notes AS feature_notes, f.metrics AS feature_metrics, p.date AS profile_date, p.sector, p.industry, p.market_cap AS profile_market_cap
  FROM fundamental_inflection_event e
  LEFT JOIN LATERAL (SELECT c.company_name FROM companies c WHERE c.symbol = e.symbol LIMIT 1) co ON true
  LEFT JOIN sec_symbol_cik sc ON sc.symbol = e.symbol
  LEFT JOIN fundamental_feature f ON f.method_version = e.method_version AND f.symbol = e.symbol AND f.quarter_end = e.quarter_end
  LEFT JOIN LATERAL (SELECT d.date, d.sector, d.industry, d.market_cap FROM daily_fundamentals d WHERE d.symbol = e.symbol AND d.date <= e.available_at ORDER BY d.date DESC LIMIT 1) p ON true
 """
EVENTS_SQL = _EVENT_SELECT + " WHERE e.method_version = %s AND e.available_at BETWEEN %s AND %s ORDER BY e.symbol, e.event_type"
EVENTS_BY_ID_SQL = _EVENT_SELECT + " WHERE e.id = ANY(%s)"

# The latest version of every update first published within the window, with the event it came from (internal provenance).
RECENT_SQL = """SELECT DISTINCT ON (p.update_key) p.update_key, p.event_id, p.version, p.kind
                  FROM fundamental_update_publication p
                 WHERE p.update_key IN (SELECT update_key FROM fundamental_update_publication WHERE version = 1 AND published_at >= %s)
                 ORDER BY p.update_key, p.version DESC"""

NY = ZoneInfo("America/New_York")
SESSION_PICKS_SQL = """SELECT update_key, symbol FROM fundamental_update_publication
                        WHERE version = 1 AND published_at >= %s AND published_at < %s"""

POSTED_SQL = """SELECT symbol, MAX(published_at)::date AS last FROM fundamental_update_publication
                 WHERE version = 1 AND published_at >= %s GROUP BY symbol"""


def _json_ready(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Dates to ISO strings, exactly like the JSON export the simulation reads."""
    return json.loads(json.dumps(dict(row), default=str))


def load_events(db, session: date, fresh_days: int = S.FRESH_DAYS) -> List[Dict[str, Any]]:
    rows = db.execute_dict_query(EVENTS_SQL, (SOURCE_VERSION, session - timedelta(days=fresh_days), session))
    return [_json_ready(r) for r in rows]


def ledger_present(db) -> bool:
    return bool(db.execute_dict_query("SELECT to_regclass('fundamental_update_publication') IS NOT NULL AS ok")[0]["ok"])


def load_posted(db, session: date, cooldown_days: int = S.COOLDOWN_DAYS) -> Dict[str, date]:
    rows = db.execute_dict_query(POSTED_SQL, (session - timedelta(days=cooldown_days),))
    return {r["symbol"]: r["last"] for r in rows}


def session_window(session: date):
    """[the session's 16:00 New York close, the next weekday's 16:00 New York close): every scheduled run for the session (22:45 UTC and its 00:15
    retry, or a weekend retry) falls inside; the next session's run, which starts after that session's own close, never does."""
    nxt = session + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return datetime.combine(session, time(16), tzinfo=NY), datetime.combine(nxt, time(16), tzinfo=NY)


def load_session_picks(db, session: date) -> Dict[str, str]:
    """update_key -> symbol of the version-1 updates already published for this session (inside its window)."""
    start, end = session_window(session)
    return {r["update_key"]: r["symbol"] for r in db.execute_dict_query(SESSION_PICKS_SQL, (start, end))}


def delivery_key(update_key: str) -> str:
    return f"{CHANNEL}:{update_key}:v1"


def gate(record: bool, send: bool, env: Mapping[str, str], ledger_ok: bool) -> Optional[str]:
    """None when the requested run may proceed, else the refusal reason."""
    if send and not record:
        return "--send needs --record: only updates written to the publication ledger are ever sent"
    if record and not ledger_ok:
        return "migration 41 is not applied (fundamental_update_publication is absent): nothing recorded or sent"
    if send and env.get(SEND_ENV, "0").strip() != "1":
        return f"{SEND_ENV} is not 1: nothing sent"
    if send and env.get(LEGACY_ENV, "0").strip() == "1":
        return f"{LEGACY_ENV}=1: the legacy owner alerts are still on, so an event could reach the owner twice; turn them off first"
    return None


def run(session: date, *, db, ledger=None, delivery=None, sender: Optional[Callable[[Any], None]] = None, record: bool = False, send: bool = False,
        env: Mapping[str, str] = os.environ, log: Callable[[str], None] = print, now: Optional[datetime] = None) -> Dict[str, Any]:
    """`ledger`: update_ledger.Ledger (publish). `delivery`: alerts.Delivery (claim/mark). `sender(update)`: sends one update to the owner chat.
    `now`: the wall clock, used ONLY to refuse --record outside the session's window (the session itself is always explicit)."""
    ledger_ok = ledger_present(db)
    refused = gate(record, send, env, ledger_ok)
    if not refused and record:
        start, end = session_window(session)
        t = now or datetime.now(timezone.utc)
        if not start <= t < end:
            refused = (f"--record for session {session} is only allowed inside its window {start.isoformat()} .. {end.isoformat()} "
                       f"(now {t.astimezone(timezone.utc).isoformat()}): nothing recorded or sent")
    out: Dict[str, Any] = {"session": session.isoformat(), "record": record, "send": send, "refused": refused, "selected": [], "excluded": 0,
                           "outranked": 0, "cooldown": 0, "published": 0, "exists": 0, "session_full": 0, "sent": 0, "skipped_already_claimed": 0,
                           "send_failed": 0}
    if refused:
        log(f"FU_PUBLISH refused: {refused}")
        return out
    events = load_events(db, session)
    picks = load_session_picks(db, session) if ledger_ok else {}
    mine = set(picks.values())
    posted = {sym: d for sym, d in (load_posted(db, session) if ledger_ok else {}).items() if sym not in mine}   # this session's picks stay eligible
    r = S.select(events, session, posted)
    out.update(excluded=len(r["excluded"]), outranked=len(r["outranked"]), cooldown=len(r["cooldown"]))
    for c in r["selected"]:
        u = c.update
        item = {"symbol": c.symbol, "key": u.key, "event_type": u.event_type, "public_date": u.public_date, "ledger": None, "delivery": None}
        out["selected"].append(item)
        if not record:
            continue
        if u.key not in picks and len(picks) >= S.MAX_POSTS:
            item["ledger"] = "session_full"                       # the session already has its MAX_POSTS updates (an earlier run)
            out["session_full"] += 1
            continue
        item["ledger"] = ledger.publish(u, event_id=c.lead.get("id"), method_version=SOURCE_VERSION)        # 'published' | 'exists'
        out[item["ledger"]] = out.get(item["ledger"], 0) + 1
        picks.setdefault(u.key, c.symbol)
        if not send:
            continue
        key = delivery_key(u.key)
        if not delivery.claim(key, CHANNEL, RECIPIENT, c.lead.get("id")):
            item["delivery"] = "already_claimed"
            out["skipped_already_claimed"] += 1
            continue
        try:
            sender(u)
        except Exception as e:  # noqa: BLE001 -- the type only: a message can carry a URL with the token in it
            delivery.mark(key, False, type(e).__name__)
            item["delivery"] = "failed"
            out["send_failed"] += 1
            continue
        delivery.mark(key, True)
        item["delivery"] = "sent"
        out["sent"] += 1
    log("FU_PUBLISH " + json.dumps({k: v for k, v in out.items() if k != "selected"}, sort_keys=True))
    for item in out["selected"]:
        log(f"FU_PUBLISH pick {item['symbol']} {item['event_type']} public {item['public_date']} ledger={item['ledger']} delivery={item['delivery']}")
    return out


def rebuild(ev: Optional[Mapping[str, Any]]):
    """The update as the event says NOW (freshness not re-checked), or None when the event is gone or no longer publishable."""
    if ev is None or ev.get("event_type") not in S.PUBLIC_TYPES or ev.get("availability_basis") not in S.SEC_BASES:
        return None
    acc = S.accession_of(ev.get("feature_notes"))
    if not acc:
        return None
    from inflection.update_model import build_update
    profile = {"date": ev.get("profile_date"), "sector": ev.get("sector"), "industry": ev.get("industry")} if ev.get("profile_date") else None
    u, _reason = build_update(ev, company_name=ev.get("company_name"), cik=ev.get("cik"), accession=acc, profile=profile, check_freshness=False)
    return u


def revise(session: date, *, db, ledger, env: Mapping[str, str] = os.environ, log: Callable[[str], None] = print) -> Dict[str, Any]:
    """Corrections for recently published updates. RECORD-ONLY: never claims, sends, edits or deletes a message."""
    from inflection.update_model import classify_revision
    out: Dict[str, Any] = {"session": session.isoformat(), "checked": 0, "would_change": {}, "recorded": {}, "refused": None}
    if not ledger_present(db):
        out["refused"] = "migration 41 is not applied"
        log(f"FU_REVISE refused: {out['refused']}")
        return out
    recent = db.execute_dict_query(RECENT_SQL, (session - timedelta(days=REVISE_DAYS),))
    ids = [r["event_id"] for r in recent if r.get("event_id") is not None]
    events = {e["id"]: e for e in (_json_ready(r) for r in db.execute_dict_query(EVENTS_BY_ID_SQL, (ids,)))} if ids else {}
    plan = []
    for r in recent:
        out["checked"] += 1
        if r["kind"] == "withdrawn":
            continue                                                    # never revived automatically
        current = rebuild(events.get(r["event_id"]))
        last = ledger.latest(r["update_key"])
        kind, _changed = classify_revision(last["public_json"], current)
        if kind != "unchanged":
            plan.append((r, current))
            out["would_change"][kind] = out["would_change"].get(kind, 0) + 1
    enabled = env.get(CORRECTIONS_ENV, "0").strip() == "1"
    if len(plan) > MAX_REVISIONS_PER_RUN:
        out["refused"] = f"{len(plan)} updates would change in one run (limit {MAX_REVISIONS_PER_RUN}): nothing recorded; review the source data first"
    elif not enabled:
        out["refused"] = f"{CORRECTIONS_ENV} is not 1: reported only, nothing recorded"
    else:
        for r, current in plan:
            res = ledger.revise(r["update_key"], current, r["event_id"], SOURCE_VERSION)
            out["recorded"][res] = out["recorded"].get(res, 0) + 1
    log("FU_REVISE " + json.dumps(out, sort_keys=True))
    return out


def owner_sender():
    """The real sender: card + caption + buttons to the owner's PRIVATE chat (target fixed to 'owner'); text fallback if the photo fails."""
    from alerts.telegram_client import TelegramClient
    from inflection.update_card import render_update_card
    from inflection.update_format import buttons, caption, text_message
    client = TelegramClient.from_env("owner", dry_run=False)

    def send(u) -> None:
        cap, _dropped = caption(u)
        try:
            client.send_photo(render_update_card(u), cap, reply_markup=buttons(u), kind="fundamental_update")
        except Exception:  # noqa: BLE001 -- one fallback; a second failure propagates and the claim is marked failed
            client.send_message(text_message(u), reply_markup=buttons(u), kind="fundamental_update")
    return send


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fundamental Updates nightly publisher (owner chat only). Default: dry run.")
    p.add_argument("--session", required=True, type=date.fromisoformat, help="the US market session (explicit; never inferred)")
    p.add_argument("--record", action="store_true", help="write the selected updates to the publication ledger (migration 41)")
    p.add_argument("--send", action="store_true", help=f"send recorded updates to the owner chat (needs --record and {SEND_ENV}=1)")
    p.add_argument("--revise", action="store_true", help=f"corrections only: report, or record with {CORRECTIONS_ENV}=1; never sends")
    a = p.parse_args(argv)
    if a.revise and (a.record or a.send):
        print("FU_PUBLISH refused: --revise runs on its own (it never records new updates or sends)")
        return EXIT_REFUSED
    try:
        from dotenv import load_dotenv
        load_dotenv(MECH.parent / ".env")
    except ImportError:
        pass
    from shared import db
    from inflection.alerts import DbDelivery
    from inflection.update_ledger import Ledger
    if a.revise:
        out = revise(a.session, db=db, ledger=Ledger(db.get_sync_connection))
        return EXIT_REFUSED if out["refused"] and "would change" in out["refused"] else EXIT_OK
    ledger_ok = ledger_present(db)
    if gate(a.record, a.send, os.environ, ledger_ok):
        run(a.session, db=db, record=a.record, send=a.send)          # logs the refusal
        return EXIT_REFUSED
    out = run(a.session, db=db, ledger=Ledger(db.get_sync_connection) if a.record else None, delivery=DbDelivery(db) if a.send else None,
              sender=owner_sender() if a.send else None, record=a.record, send=a.send)
    return EXIT_SEND_FAILED if out["send_failed"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
