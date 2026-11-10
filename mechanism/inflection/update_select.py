#!/usr/bin/env python3
"""Fundamental Updates: which events become Telegram posts on one night. Pure and deterministic (no database, no network, no sending).

Owner rules (2026-11-07, revised the same day after the first simulation):
  - at most 3 posts a night, at most 1 per company, at most 2 posts with the same lead event type; empty slots are fine (never relax a rule to fill one);
  - require a verified SEC filing and point-in-time validity; never flood the channel with a historical backlog;
  - prefer profitability, operating-margin and revenue inflections, and several INDEPENDENT inflections in the same quarter;
  - prefer improvements supported by operating income, operating margin, revenue growth and cash flow; an extreme net-income swing must not outrank
    an operationally supported improvement by its size alone; consider one-off / nonrecurring items where the source data permits (done with the
    non-operating divergence heuristic below: a proxy, not proof of a nonrecurring item);
  - no predictive investment score: nothing here uses prices, returns or the inflection score.

Each event is first screened (the first failing rule is its exclusion reason):
  not_public_type            limited repricing (a price judgement on an internal score)
  date_not_sec_verified      the public date is not an SEC acceptance / filing date
  no_filing_accession        the quarter's SEC filing (accession) is unknown
  public_too_long_after_quarter_end, detected_before_public_date, fewer_than_two_figures, explanation_inputs_missing   (update_model rules)
  public_after_session       dated after the session it would be posted for (cannot be, so refused)
  backlog_not_fresh          public more than FRESH_DAYS before the session: old news, e.g. a first run or a coverage expansion writing history
  below_size_floor           market cap at publication below MIN_MARKET_CAP, or unknown

Operational support (support()) is read from the same quarter's feature metrics (the filing the event came from; nothing later):
  operating_income   operating income above the prior-year quarter's (prior year = op_margin_py x revenue_py)
  operating_margin   operating margin up at least MIN_MARGIN_BPS year over year
  revenue_growth     revenue up at least MIN_REV_GROWTH year over year
  cash_flow          free-cash-flow margin up at least MIN_FCF_PP percentage points year over year
A signal whose inputs are missing (e.g. banks and insurers report no operating income) counts as absent, never as present.

Non-operating divergence heuristic: the share of the year-over-year net-income improvement NOT matched by an operating-income improvement,
  non_operating_divergence = (dNI - dOI) / dNI
  dNI = net_income - net_income_py              must be > 0 (no improvement: undefined)
  dOI = operating_income - op_margin_py x revenue_py   needs both years' operating figures and revenue_py > 0
Every input must be a finite number; otherwise the heuristic is undefined (None) and never holds or demotes anything.
Read: 0 or less = the operating line explains all of it; 1 = none of it; above 1 = operating income fell while net income rose. The rest came from
below the operating line: gains on sales, revaluations, tax effects, interest, discontinued operations.
A net-income turnaround with divergence > MAX_NON_OPERATING_DIVERGENCE is 'not operationally supported': it does not count as an inflection family,
it cannot lead a post while the company has another inflection, and a company whose ONLY basis is such a turnaround is held
(non_operating_divergence). Other event types are never held or flagged by it.
LIMITATIONS (it is a proxy, not proof of a nonrecurring item):
  - the source data (normalized quarterly statements) does not itemize nonrecurring items; the heuristic only sees where the line moved;
  - operating income is pre-tax and net income after tax: a fully operational improvement taxed at rate t shows a NEGATIVE divergence of about
    -t/(1-t) (about -0.27 at 21%), so the heuristic leans toward calling an improvement operational, never the other way;
  - a one-off item INSIDE operating income (a litigation settlement, a restructuring charge, an asset sale booked as operating) is invisible to it;
  - a recurring below-the-line item (a steady equity-method stake, lower interest after debt repayment) is counted as divergence;
  - prior-year operating income is derived from the stored margin and revenue, so rounding in either feeds through;
  - a tiny dNI makes the ratio extreme in either direction; only the threshold is used, never the size, so this changes no ranking;
  - companies without operating income (banks, insurers) are undefined: never held by it, and their operating signals count as absent.

Companies are ranked by:
  1. supported preferred families (net income, operating margin/leverage, revenue): more first;
  2. operational support signals (0-4): more first;
  3. supported independent families (those three plus free cash flow): more first;
  4. priority of the lead event: net income > operating margin > revenue > operating leverage > free cash flow;
  5. size of the lead change, capped at MAGNITUDE_CAP points, so an extreme swing does not win on size alone: larger first;
  6. market cap at publication: larger first; then symbol.
Walking that order, a company is skipped when its lead type already has MAX_PER_TYPE posts (type_mix_cap) and selection stops at MAX_POSTS.
A company posted within COOLDOWN_DAYS is excluded (cooldown). The rest are 'outranked'. This ranking is editorial salience, not a forecast.

Every night's choices are kept for later analysis: decisions() turns select()'s result into one record per input event (outcome, reason, rank,
support signals, divergence, flags and the policy in force); update_simulate.py writes them with --decisions. INTERNAL only, never published.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from inflection.update_model import PUBLIC_TYPES, PublicUpdate, build_update

MAX_POSTS, MAX_PER_TYPE, FRESH_DAYS, COOLDOWN_DAYS = 3, 2, 3, 30
MIN_MARKET_CAP = 300e6
MAGNITUDE_CAP = 50.0                    # points of revenue / margin; above this the size no longer raises the rank
MIN_MARGIN_BPS, MIN_REV_GROWTH, MIN_FCF_PP = 50.0, 0.02, 1.0
MAX_NON_OPERATING_DIVERGENCE = 0.5

FAMILY = {"profitability_turnaround": "net_income", "margin_expansion": "operating", "operating_leverage_inflection": "operating",
          "revenue_acceleration": "revenue", "fcf_turnaround": "cash"}
PREFERRED_FAMILIES = {"net_income", "operating", "revenue"}
PRIORITY = {"profitability_turnaround": 0, "margin_expansion": 1, "revenue_acceleration": 2, "operating_leverage_inflection": 3, "fcf_turnaround": 4}
UNSUPPORTED_PRIORITY = 9                # a not-operationally-supported net-income turnaround sorts behind every other event of the company
SEC_BASES = {"sec_acceptance", "sec_filed_date"}
SUPPORT_SIGNALS = ("operating_income", "operating_margin", "revenue_growth", "cash_flow")
ACCESSION_NOTE = re.compile(r"^prov:q=(\d{10}-\d{2}-\d{6})$")


def accession_of(notes: Optional[Sequence[str]]) -> Optional[str]:
    for n in notes or []:
        m = ACCESSION_NOTE.match(str(n))
        if m:
            return m.group(1)
    return None


def _num(m: Mapping[str, Any], k: str) -> Optional[float]:
    v = m.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def magnitude(event_type: str, m: Mapping[str, Any]) -> float:
    """The size of the change in comparable points (percentage points of revenue or margin)."""
    def g(k):
        return _num(m, k) or 0.0
    return {"profitability_turnaround": g("ni_swing_pct_rev") * 100, "margin_expansion": g("op_margin_yoy_bps") / 100, "revenue_acceleration": g("rev_accel_pp"),
            "operating_leverage_inflection": g("rev_yoy") * 100, "fcf_turnaround": g("fcf_margin_yoy_pp")}.get(event_type, 0.0)


def operating_income_change(fm: Mapping[str, Any]) -> Optional[float]:
    oi, opm_py, rev_py = _num(fm, "operating_income"), _num(fm, "op_margin_py"), _num(fm, "revenue_py")
    if oi is None or opm_py is None or rev_py is None or rev_py <= 0:
        return None
    return oi - opm_py * rev_py


def non_operating_divergence(fm: Mapping[str, Any]) -> Optional[float]:
    """The non-operating divergence heuristic (see the module docstring): None when the data does not permit it."""
    ni, ni_py, d_oi = _num(fm, "net_income"), _num(fm, "net_income_py"), operating_income_change(fm)
    if ni is None or ni_py is None or d_oi is None or ni - ni_py <= 0:
        return None
    return (ni - ni_py - d_oi) / (ni - ni_py)


def support(fm: Mapping[str, Any]) -> Dict[str, Optional[bool]]:
    """Operational support signals of one quarter: True / False, or None when the inputs are missing."""
    d_oi, bps, rev, fcf = operating_income_change(fm), _num(fm, "op_margin_yoy_bps"), _num(fm, "rev_yoy"), _num(fm, "fcf_margin_yoy_pp")
    return {"operating_income": None if d_oi is None else d_oi > 0, "operating_margin": None if bps is None else bps >= MIN_MARGIN_BPS,
            "revenue_growth": None if rev is None else rev >= MIN_REV_GROWTH, "cash_flow": None if fcf is None else fcf >= MIN_FCF_PP}


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
    families: List[str]                 # supported independent families
    market_cap: float
    support: Dict[str, Optional[bool]] = field(default_factory=dict)
    non_operating_divergence: Optional[float] = None
    flags: List[str] = field(default_factory=list)
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


def _company(sym: str, items: List[Tuple[Dict[str, Any], PublicUpdate]]) -> Tuple[Company, Optional[str]]:
    fm = items[0][0].get("feature_metrics") or {}          # one feature row per symbol and quarter: the same for every event of the company
    sup, nod = support(fm), non_operating_divergence(fm)
    ni_unsupported = nod is not None and nod > MAX_NON_OPERATING_DIVERGENCE

    def prio(e):
        return UNSUPPORTED_PRIORITY if e["event_type"] == "profitability_turnaround" and ni_unsupported else PRIORITY[e["event_type"]]

    items.sort(key=lambda t: (prio(t[0]), -magnitude(t[0]["event_type"], t[0].get("metrics") or {}), t[0]["event_type"]))
    lead, u = items[0]
    flags = []
    if ni_unsupported and any(e["event_type"] == "profitability_turnaround" for e, _ in items):
        flags.append("non_operating_divergence")
    if any(e["event_type"] == "profitability_turnaround" and magnitude(e["event_type"], e.get("metrics") or {}) > MAGNITUDE_CAP for e, _ in items):
        flags.append("extreme_net_income_swing")
    if all(v is None for v in sup.values()):
        flags.append("operating_support_unverifiable")
    fams = sorted({FAMILY[e["event_type"]] for e, _ in items if prio(e) != UNSUPPORTED_PRIORITY})
    c = Company(sym, lead, u, [e for e, _ in items], fams, market_cap_at_publication(lead) or 0.0, sup, nod, flags)
    if not fams:
        return c, "non_operating_divergence"
    n_support = sum(1 for v in sup.values() if v)
    c.rank_key = (-len(PREFERRED_FAMILIES & set(fams)), -n_support, -len(fams), prio(lead),
                  -round(min(magnitude(lead["event_type"], lead.get("metrics") or {}), MAGNITUDE_CAP), 6), -c.market_cap, sym)
    return c, None


def select(events: Sequence[Mapping[str, Any]], session: date, posted: Mapping[str, date], *, max_posts: int = MAX_POSTS, max_per_type: int = MAX_PER_TYPE,
           fresh_days: int = FRESH_DAYS, min_market_cap: float = MIN_MARKET_CAP, cooldown_days: int = COOLDOWN_DAYS) -> Dict[str, Any]:
    """One session's newly detected events -> {'selected', 'outranked', 'type_capped', 'cooldown': [Company], 'held': [(Company, reason)],
    'excluded': [(event, reason)]}."""
    excluded: List[Tuple[Mapping[str, Any], str]] = []
    by_symbol: Dict[str, List[Tuple[Dict[str, Any], PublicUpdate]]] = {}
    for ev in events:
        u, reason = screen(ev, session, fresh_days=fresh_days, min_market_cap=min_market_cap)
        if reason:
            excluded.append((ev, reason))
        else:
            by_symbol.setdefault(ev["symbol"], []).append((dict(ev), u))
    companies, cooldown, held = [], [], []
    for sym, items in by_symbol.items():
        c, reason = _company(sym, items)
        last = posted.get(sym)
        if reason:
            held.append((c, reason))
        elif last is not None and (session - last).days < cooldown_days:
            cooldown.append(c)
        else:
            companies.append(c)
    companies.sort(key=lambda c: c.rank_key)
    selected, outranked, type_capped = [], [], []
    per_type: Dict[str, int] = {}
    for c in companies:
        t = c.lead["event_type"]
        if len(selected) >= max_posts:
            outranked.append(c)
        elif per_type.get(t, 0) >= max_per_type:
            type_capped.append(c)
        else:
            selected.append(c)
            per_type[t] = per_type.get(t, 0) + 1
    return {"selected": selected, "outranked": outranked, "type_capped": type_capped, "cooldown": cooldown, "held": held, "excluded": excluded}


# ---------------------------------------------------------------------------------------------------------------- the decision record
POLICY_VERSION = "fu-select-2026-11-08"


def policy(**kw) -> Dict[str, Any]:
    """The rules a night was selected under (the defaults, overridden by select()'s keyword arguments), stored with every decision."""
    p = {"policy_version": POLICY_VERSION, "max_posts": MAX_POSTS, "max_per_type": MAX_PER_TYPE, "fresh_days": FRESH_DAYS, "cooldown_days": COOLDOWN_DAYS,
         "min_market_cap": MIN_MARKET_CAP, "magnitude_cap": MAGNITUDE_CAP, "min_margin_bps": MIN_MARGIN_BPS, "min_rev_growth": MIN_REV_GROWTH,
         "min_fcf_pp": MIN_FCF_PP, "max_non_operating_divergence": MAX_NON_OPERATING_DIVERGENCE}
    p.update(kw)
    return p


# outcome -> the reason recorded when the outcome has no more specific one
_REASON = {"selected": "selected", "outranked": "post_limit_reached", "type_capped": "type_mix_cap", "cooldown": "cooldown"}


def decisions(result: Mapping[str, Any], session: date, **policy_kw) -> List[Dict[str, Any]]:
    """select()'s result as a flat, JSON-ready record: ONE row per input event, with its outcome and reason, so every night's choices (and every
    refusal) can be analysed later. INTERNAL: never published (it holds the non-operating divergence, rank keys and support signals).

    outcome    selected | outranked | type_capped | cooldown | held | excluded     (the company's outcome; an excluded event has its own)
    reason     the exclusion rule, the hold reason, 'type_mix_cap', 'post_limit_reached', 'cooldown' or 'selected'
    is_lead    the event the company's post is (or would be) about; the others are its supporting events
    rank       position in the night's ranking among companies still competing for a slot (selected, type_capped, outranked), 1 = first; else None
    Prices, returns and scores are not part of the selection, so they are not part of the record either."""
    pol = policy(**policy_kw)
    ranked = sorted(list(result["selected"]) + list(result["type_capped"]) + list(result["outranked"]), key=lambda c: c.rank_key)
    rank = {c.symbol: i for i, c in enumerate(ranked, 1)}
    rows: List[Dict[str, Any]] = []

    def base(e: Mapping[str, Any]) -> Dict[str, Any]:
        return {"session": session.isoformat(), "policy": pol, "event_id": e.get("id"), "symbol": e.get("symbol"), "event_type": e.get("event_type"),
                "quarter_end": str(e.get("quarter_end"))[:10] if e.get("quarter_end") else None,
                "available_at": str(e.get("available_at"))[:10] if e.get("available_at") else None, "availability_basis": e.get("availability_basis"),
                "first_detected_on": str(e.get("first_detected_on"))[:10] if e.get("first_detected_on") else None}

    def company_rows(c: Company, outcome: str, reason: str) -> None:
        for e in c.events:
            rows.append(dict(base(e), outcome=outcome, reason=reason, is_lead=e is c.lead,
                             rank=rank.get(c.symbol), rank_key=list(c.rank_key) or None, families=list(c.families), support=dict(c.support),
                             non_operating_divergence=c.non_operating_divergence, flags=list(c.flags), market_cap=c.market_cap or None,
                             lead_change_points=magnitude(e["event_type"], e.get("metrics") or {})))

    for outcome in ("selected", "type_capped", "outranked", "cooldown"):
        for c in result[outcome]:
            company_rows(c, outcome, _REASON[outcome])
    for c, why in result["held"]:
        company_rows(c, "held", why)
    for e, why in result["excluded"]:
        rows.append(dict(base(e), outcome="excluded", reason=why, is_lead=None, rank=None, rank_key=None, families=None, support=None,
                         non_operating_divergence=None, flags=None, market_cap=market_cap_at_publication(e) if e.get("available_at") else None,
                         lead_change_points=magnitude(e.get("event_type"), e.get("metrics") or {})))
    rows.sort(key=lambda r: (r["outcome"] != "selected", r["rank"] is None, r["rank"] or 0, r["symbol"] or "", r["event_type"] or ""))
    return rows
