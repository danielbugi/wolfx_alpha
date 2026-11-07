#!/usr/bin/env python3
"""Fundamental Updates: which events become Telegram posts on one night. Pure and deterministic (no database, no network, no sending).

Owner rules (2026-11-07): at most 3 posts a night; at most 1 post per company; prefer profitability, operating-margin and revenue inflections;
count several INDEPENDENT inflections in the same quarter; require a verified SEC filing and point-in-time validity; never flood the channel with a
historical backlog.

Each event is first screened (the first failing rule is its exclusion reason):
  not_public_type            limited repricing (a price judgement on an internal score)
  date_not_sec_verified      the public date is not an SEC acceptance / filing date
  no_filing_accession        the quarter's SEC filing (accession) is unknown
  public_too_long_after_quarter_end, detected_before_public_date, fewer_than_two_figures, explanation_inputs_missing   (update_model rules)
  backlog_not_fresh          public more than FRESH_DAYS before the session: old news, e.g. a first run or a coverage expansion writing history
  public_after_session       dated after the session it would be posted for (cannot be, so refused)
  below_size_floor           market cap at publication below MIN_MARKET_CAP, or unknown
Survivors are grouped by company (one post per company). A company's post leads with its highest-priority event; companies are ranked by:
  1. preferred families present (net income, operating margin/leverage, revenue): more first;
  2. independent families present (those three plus free cash flow): more first;
  3. priority of the lead event: net income > operating margin > revenue > operating leverage > free cash flow;
  4. size of the lead change (points of revenue, margin or swing): larger first;
  5. market cap at publication: larger first; then symbol.
A company posted within COOLDOWN_DAYS is excluded (company_posted_recently). The top MAX_POSTS are selected; the rest are 'outranked'.
This ranking is editorial salience (what is most worth a reader's attention), not a forecast: no return is implied by the order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from inflection.update_model import PUBLIC_TYPES, PublicUpdate, build_update

MAX_POSTS, FRESH_DAYS, COOLDOWN_DAYS = 3, 3, 30
MIN_MARKET_CAP = 300e6

FAMILY = {"profitability_turnaround": "net_income", "margin_expansion": "operating", "operating_leverage_inflection": "operating",
          "revenue_acceleration": "revenue", "fcf_turnaround": "cash"}
PREFERRED_FAMILIES = {"net_income", "operating", "revenue"}
PRIORITY = {"profitability_turnaround": 0, "margin_expansion": 1, "revenue_acceleration": 2, "operating_leverage_inflection": 3, "fcf_turnaround": 4}
SEC_BASES = {"sec_acceptance", "sec_filed_date"}
ACCESSION_NOTE = re.compile(r"^prov:q=(\d{10}-\d{2}-\d{6})$")


def accession_of(notes: Optional[Sequence[str]]) -> Optional[str]:
    for n in notes or []:
        m = ACCESSION_NOTE.match(str(n))
        if m:
            return m.group(1)
    return None


def magnitude(event_type: str, m: Mapping[str, Any]) -> float:
    """The size of the change in comparable points (percentage points of revenue or margin)."""
    def g(k):
        v = m.get(k)
        return float(v) if isinstance(v, (int, float)) and v == v else 0.0
    return {"profitability_turnaround": g("ni_swing_pct_rev") * 100, "margin_expansion": g("op_margin_yoy_bps") / 100, "revenue_acceleration": g("rev_accel_pp"),
            "operating_leverage_inflection": g("rev_yoy") * 100, "fcf_turnaround": g("fcf_margin_yoy_pp")}.get(event_type, 0.0)


def market_cap_at_publication(ev: Mapping[str, Any]) -> Optional[float]:
    v = (ev.get("context") or {}).get("valuation_at_publication") or {}
    cap, snap = v.get("market_cap"), v.get("snapshot_date")
    if cap and snap and str(snap)[:10] <= str(ev["available_at"])[:10]:
        return float(cap)
    return None


@dataclass
class Company:
    symbol: str
    lead: Dict[str, Any]
    update: PublicUpdate
    events: List[Dict[str, Any]]
    families: List[str]
    market_cap: float
    rank_key: Tuple = field(default_factory=tuple)


def screen(ev: Mapping[str, Any], session: date, *, fresh_days: int = FRESH_DAYS, min_market_cap: float = MIN_MARKET_CAP) -> Tuple[Optional[PublicUpdate], Optional[str]]:
    if ev.get("event_type") not in PUBLIC_TYPES:
        return None, "not_public_type"
    if ev.get("availability_basis") not in SEC_BASES:
        return None, "date_not_sec_verified"
    acc = ev.get("accession") or accession_of(ev.get("feature_notes"))
    if not acc:
        return None, "no_filing_accession"
    profile = {"date": ev.get("profile_date"), "sector": ev.get("sector"), "industry": ev.get("industry")} if ev.get("profile_date") else None
    u, reason = build_update(ev, company_name=ev.get("company_name"), cik=ev.get("cik"), accession=acc, profile=profile)
    if u is None:
        return None, reason
    public = date.fromisoformat(str(ev["available_at"])[:10])
    if public > session:
        return None, "public_after_session"
    if public < session - timedelta(days=fresh_days):
        return None, "backlog_not_fresh"
    cap = market_cap_at_publication(ev)
    if cap is None or cap < min_market_cap:
        return None, "below_size_floor"
    return u, None


def select(events: Sequence[Mapping[str, Any]], session: date, posted: Mapping[str, date], *, max_posts: int = MAX_POSTS, fresh_days: int = FRESH_DAYS,
           min_market_cap: float = MIN_MARKET_CAP, cooldown_days: int = COOLDOWN_DAYS) -> Dict[str, Any]:
    """{'selected': [Company], 'outranked': [Company], 'excluded': [(event, reason)], 'cooldown': [Company]} for one session's newly detected events."""
    excluded: List[Tuple[Mapping[str, Any], str]] = []
    by_symbol: Dict[str, List[Tuple[Dict[str, Any], PublicUpdate]]] = {}
    for ev in events:
        u, reason = screen(ev, session, fresh_days=fresh_days, min_market_cap=min_market_cap)
        if reason:
            excluded.append((ev, reason))
        else:
            by_symbol.setdefault(ev["symbol"], []).append((dict(ev), u))
    companies, cooldown = [], []
    for sym, items in by_symbol.items():
        items.sort(key=lambda t: (PRIORITY[t[0]["event_type"]], -magnitude(t[0]["event_type"], t[0].get("metrics") or {}), t[0]["event_type"]))
        lead, u = items[0]
        fams = sorted({FAMILY[e["event_type"]] for e, _ in items})
        cap = market_cap_at_publication(lead) or 0.0
        c = Company(sym, lead, u, [e for e, _ in items], fams, cap)
        c.rank_key = (-len(PREFERRED_FAMILIES & set(fams)), -len(fams), PRIORITY[lead["event_type"]], -round(magnitude(lead["event_type"], lead.get("metrics") or {}), 6), -cap, sym)
        last = posted.get(sym)
        (cooldown if last is not None and (session - last).days < cooldown_days else companies).append(c)
    companies.sort(key=lambda c: c.rank_key)
    return {"selected": companies[:max_posts], "outranked": companies[max_posts:], "excluded": excluded, "cooldown": cooldown}
