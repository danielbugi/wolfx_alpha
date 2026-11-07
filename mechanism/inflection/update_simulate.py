#!/usr/bin/env python3
"""Replay the Fundamental Updates night selection (update_select) over exported events. Read-only by construction: it reads a JSON file and prints.

    python mechanism/inflection/update_simulate.py --events v2_events.json --sessions 2026-11-02,2026-11-03,2026-11-04,2026-11-05,2026-11-06 [--json out.json]

The export is one row per event (fundamental_inflection_event + company name, CIK, the feature's provenance notes, and the last fundamentals row on
or before the public date); see docs/product/fu_selection_simulation.sql. An event belongs to the night whose run first detected it: the run that
starts after a session's close and finishes after UTC midnight, so the detection date is the next weekday (Monday's session -> Tuesday's date).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inflection.update_select import SUPPORT_SIGNALS, magnitude, select  # noqa: E402


def session_of(detected_on: date) -> date:
    d = detected_on - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def run(events: List[Dict[str, Any]], sessions: List[date], **kw) -> List[Dict[str, Any]]:
    by_session: Dict[date, List[Dict[str, Any]]] = {}
    for e in events:
        by_session.setdefault(session_of(date.fromisoformat(str(e["first_detected_on"])[:10])), []).append(e)
    posted: Dict[str, date] = {}
    out = []
    for s in sessions:
        evs = by_session.get(s, [])
        r = select(evs, s, posted, **kw)
        for c in r["selected"]:
            posted[c.symbol] = s
        out.append({
            "session": s.isoformat(), "new_events": len(evs), "excluded_by_reason": dict(Counter(reason for _, reason in r["excluded"]).most_common()),
            "eligible_companies": len(r["selected"]) + len(r["outranked"]) + len(r["type_capped"]), "slots_filled": len(r["selected"]),
            "cooldown": [c.symbol for c in r["cooldown"]], "held": [dict(_row(c), reason=why) for c, why in r["held"]],
            "selected": [_row(c) for c in r["selected"]], "type_capped": [_row(c) for c in r["type_capped"]], "outranked": [_row(c) for c in r["outranked"]],
        })
    return out


def _row(c) -> Dict[str, Any]:
    u = c.update
    return {"symbol": c.symbol, "company": u.company_name, "lead": u.label, "families": c.families, "events": sorted(e["event_type"] for e in c.events),
            "lead_change_points": round(magnitude(c.lead["event_type"], c.lead.get("metrics") or {}), 1), "market_cap": u.market_cap["value"] if u.market_cap else None,
            "quarter_end": u.quarter_end, "public_date": u.public_date, "sec": u.links.get("sec"), "figures": [f"{f.label}: {f.value}" for f in u.figures],
            "support": {k: v for k, v in c.support.items()}, "non_operating_share": None if c.non_operating_share is None else round(c.non_operating_share, 2),
            "flags": c.flags}


def _sup(c) -> str:
    return "".join({True: "+", False: "-", None: "?"}[c["support"][k]] for k in SUPPORT_SIGNALS)


def main() -> int:
    p = argparse.ArgumentParser(description="Replay the Fundamental Updates night selection (read-only)")
    p.add_argument("--events", required=True)
    p.add_argument("--sessions", required=True)
    p.add_argument("--json")
    a = p.parse_args()
    events = json.load(open(a.events, encoding="utf-8"))
    res = run(events, [date.fromisoformat(s) for s in a.sessions.split(",")])
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), indent=1)
    for n in res:
        print(f"\n=== session {n['session']}: {n['new_events']} newly detected events, {n['eligible_companies']} eligible companies, {n['slots_filled']} posts")
        print("excluded:", n["excluded_by_reason"])
        for i, c in enumerate(n["selected"], 1):
            print(f"  POST {i}: {c['symbol']} ({c['company']}) - {c['lead']}; families {c['families']}; support {_sup(c)}; non-operating {c['non_operating_share']}; "
                  f"change {c['lead_change_points']} pts; cap {c['market_cap']}; public {c['public_date']}; flags {c['flags']}")
        for c in n["held"]:
            print(f"  held ({c['reason']}): {c['symbol']} - non-operating share {c['non_operating_share']}; change {c['lead_change_points']} pts")
        for c in n["type_capped"][:5]:
            print(f"  type cap: {c['symbol']} - {c['lead']}; families {c['families']}; support {_sup(c)}")
        for c in n["outranked"][:8]:
            print(f"  outranked: {c['symbol']} - {c['lead']}; families {c['families']}; support {_sup(c)}; change {c['lead_change_points']} pts; cap {c['market_cap']}; flags {c['flags']}")
        if len(n["outranked"]) > 8:
            print(f"  ... and {len(n['outranked']) - 8} more outranked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
