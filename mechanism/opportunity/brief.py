"""Opportunity Intelligence v1: assemble the owner's change briefs for ONE explicit session.

    python mechanism/opportunity/brief.py --session 2026-11-10 [--days 14] [--json out.json]

Read-only (repository.read_only). Writes nothing, sends nothing, is scheduled nowhere.

Point in time: the brief for session S uses only what the database held at the KNOWLEDGE CUTOFF = (S + 1 day) 12:00 UTC, i.e. after that
session's overnight pipeline. A row first detected, revised, recomputed or re-fetched after the cutoff is excluded (the value known at the time
cannot be proven), and the exclusion is counted in `pit`. Prices: closes on sessions <= S only.

Selection (deterministic): every company-quarter with at least one reported accounting change (HEADLINE_TYPES) public in the window. 'Converging'
= the fundamental class supports (two or more accounting facts improved, none worsened) AND at least one more independent class (EPS surprise,
price reaction) supports AND no class contradicts. Order: evidence.order_key. No score, no prediction.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inflection.update_model import PUBLIC_TYPES  # noqa: E402
from opportunity import evidence as E  # noqa: E402

NY = ZoneInfo("America/New_York")
DEFAULT_WINDOW_DAYS = 14
EARNINGS_AFTER_FILING_DAYS = 3        # an earnings release up to 3 days after the 10-Q acceptance still belongs to the same quarter
PREDICTIVE = re.compile(r"\b(buy\w*|sell\w*|targets?|recommend\w*|should|will\s+(rise|fall|climb|drop|go)|outperform\w*|guarantee\w*|alpha|"
                        r"winners?|picks?|upside|undervalued|bargain|opportunit\w*|signals?)\b", re.I)


CAVEATS = [
    "Market response is how the price moved, not evidence that the business is better; it is shown as a separate dimension.",
    "No verified pre-release EPS consensus is stored (the vendor row is overwritten after the release), so expectations are not counted.",
    "Prices are unadjusted (adj_close equals close in this database); windows with a detected discontinuity or a one-day move matching a 2x-20x split "
    "ratio are marked unreliable. Smaller splits (3:2, 5:4) cannot be told apart from a large earnings move without corporate-action data.",
    "Categories describe evidence. None of them is a forecast, a rating or a recommendation; outcomes are not measured yet (Evidence Ledger).",
]


def cutoff(session: date) -> datetime:
    return datetime.combine(session + timedelta(days=1), time(12, 0), tzinfo=timezone.utc)


def _aware(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.combine(v, time(0, 0), tzinfo=timezone.utc)


def match_earnings(rows: Sequence[Mapping[str, Any]], quarter_end: date, filing_day: date, session: date) -> Optional[Mapping[str, Any]]:
    """The earnings report of this quarter: the latest report date after the quarter end, no later than 3 days after the filing and the session."""
    last = min(filing_day + timedelta(days=EARNINGS_AFTER_FILING_DAYS), session)
    hits = [r for r in rows if quarter_end < r["report_date"] <= last]
    return max(hits, key=lambda r: r["report_date"]) if hits else None


def build(data: Mapping[str, Any], session: date, window_days: int = DEFAULT_WINDOW_DAYS) -> Dict[str, Any]:
    cut = cutoff(session)
    pit = Counter()
    groups: Dict[tuple, List[Mapping[str, Any]]] = defaultdict(list)
    for e in data["events"]:
        if _aware(e["first_detected_at"]) > cut:
            pit["event_detected_after_cutoff"] += 1
            continue
        if _aware(e["updated_at"]) > cut:
            pit["event_revised_after_cutoff"] += 1
            continue
        groups[(e["symbol"], e["quarter_end"])].append(e)

    earnings: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for r in data["earnings"]:
        earnings[r["symbol"]].append(r)
    sessions = [d for d in data["sessions"] if d <= session]
    disc: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for d in data.get("discontinuities", []):
        if _aware(d["detected_at"]) <= cut:                    # a discontinuity found only after the cutoff was not known then
            disc[d["symbol"]].append(d)
    pre_release = data.get("pre_release", {})                 # (symbol, report_date) -> verified consensus snapshot; none stored today

    briefs = []
    for (symbol, qe), evs in sorted(groups.items()):
        evs = sorted(evs, key=lambda e: e["event_type"])
        head = evs[0]
        accepted = _aware(head.get("accepted_at"))
        public_day = accepted.astimezone(NY).date() if accepted else head["available_at"]
        public_at = accepted.astimezone(timezone.utc).isoformat() if accepted else head["available_at"].isoformat()
        form = head.get("form") or "10-Q/10-K"
        source = f"SEC {form} {head['accession']}" if head.get("accession") else "SEC filing (accession not recorded)"

        if _aware(head["feature_computed_at"]) > cut:
            pit["feature_recomputed_after_cutoff"] += 1
            business = [E.Fact(f, E.UNAVAILABLE, note="figures recomputed after the knowledge cutoff") for f in E.FUNDAMENTAL_FACTS]
        else:
            business = E.business_facts(head["feature_metrics"], source, public_at)

        er = match_earnings(earnings.get(symbol, []), qe, public_day, session)
        if er and _aware(er["fetched_at"]) > cut:
            pit["earnings_refetched_after_cutoff"] += 1
            expectations, er_day = E.expectations_fact(None, None, "the vendor row was refreshed after the knowledge cutoff; the value known then cannot be proven"), None
        else:
            expectations, er_day = E.expectations_fact(er, pre_release.get((symbol, er["report_date"])) if er else None), (er["report_date"] if er else None)

        first_public = min(d for d in (public_day, er_day) if d is not None)
        market = E.market_fact(symbol, sessions, first_public, data["bars"].get(symbol, {}), data["bench"], disc.get(symbol, ()))
        assessment = E.assess(business, expectations, market)
        briefs.append({
            "version": E.BRIEF_VERSION, "symbol": symbol, "company_name": head.get("company_name"), "quarter_end": qe.isoformat(),
            "filing": {"form": form, "accession": head.get("accession"), "accepted_at": public_at, "source": source},
            "first_public": first_public.isoformat(),
            "what_changed": [{"event_type": e["event_type"], "label": PUBLIC_TYPES.get(e["event_type"], (None, e["event_type"]))[1],
                              "lines": list(e.get("explanation") or []), "source": source, "available_at": public_at} for e in evs],
            "evidence": {"business": [E.fact_dict(f) for f in business], "expectations": E.fact_dict(expectations), "market": E.fact_dict(market)},
            "assessment": assessment, "contradictions": E.contradictions(business, expectations, market),
        })

    ordered = E.order(briefs)
    by_cat = Counter(b["assessment"]["category"] for b in ordered)
    return {"version": E.BRIEF_VERSION, "session": session.isoformat(), "knowledge_cutoff": cut.isoformat(), "window_days": window_days,
            "calendar_source": data.get("calendar_source"),
            "counts": {"company_quarters": len(ordered), "converging": sum(b["assessment"]["converging"] for b in ordered),
                       "by_category": {k: by_cat.get(k, 0) for k in E.CATEGORY_LABELS},
                       "expectations_verified": sum(b["assessment"]["dimensions"]["expectations"] in (E.SUPPORTS, E.CONTRADICTS, E.NEUTRAL) for b in ordered),
                       "market_unreliable": sum(b["assessment"]["dimensions"]["market"] == E.UNRELIABLE for b in ordered)},
            "category_labels": E.CATEGORY_LABELS,
            "caveats": CAVEATS,
            "pit_exclusions": dict(sorted(pit.items())), "briefs": ordered}


def wording_violations(report: Mapping[str, Any]) -> List[str]:
    """Generated or stored text that reads as advice or a forecast (owner pages too: the product is descriptive)."""
    texts = []
    for b in report["briefs"]:
        for w in b["what_changed"]:
            texts += w["lines"]
        texts += [c["text"] for c in b["contradictions"]] + [b["assessment"]["summary"], b["assessment"]["category_label"]]
    return [t for t in texts if PREDICTIVE.search(t)]


def run(connect, session: date, window_days: int = DEFAULT_WINDOW_DAYS) -> Dict[str, Any]:
    from opportunity import repository as R
    start = session - timedelta(days=window_days)
    with connect() as conn:
        data = R.load(conn, session, start, E.HEADLINE_TYPES, start - timedelta(days=120))
    return build(data, session, window_days)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", required=True, type=date.fromisoformat)
    ap.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--json", help="write the full report to this file")
    a = ap.parse_args(argv)
    from shared.database import db
    if not db.initialize_sync_pool():
        print("OI_BRIEF refused: no database connection")
        return 2
    try:
        rep = run(db.get_sync_connection, a.session, a.days)
    finally:
        db.close_pools()
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True, default=str)
    print("OI_BRIEF " + json.dumps({k: rep[k] for k in ("session", "knowledge_cutoff", "counts", "pit_exclusions")}, sort_keys=True))
    bad = wording_violations(rep)
    if bad:
        print(f"OI_BRIEF wording guard: {len(bad)} line(s) read as advice or a forecast")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
