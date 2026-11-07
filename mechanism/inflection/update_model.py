#!/usr/bin/env python3
"""Fundamental Updates: the PUBLIC view of one inflection event (Telegram card + caption, and later the /signals pages).

One pure function, build_update(), turns an event row plus point-in-time context into a PublicUpdate that holds ONLY allowlisted fields.
Everything public (the card, the caption, the future public API) is rendered from a PublicUpdate, never from the event row, so a field
that is not on the allowlist cannot leak by accident.

Rules (each one tested in tests/test_fundamental_updates.py):
  * NO INTERNALS: strength, score, severity, inflection score, method version, data-quality flags, price reactions, "latest" valuations and
    the engine's own explanation text never reach a PublicUpdate. The explanation is rebuilt here from the reported figures by a fixed template.
  * POINT IN TIME: every value is the event's reported figure or is dated on or before the event's public date (the market cap is taken from
    `valuation_at_publication` only when its snapshot date is on or before that date). Nothing measured after publication (price reaction,
    "since publication" returns, later valuations) is used.
  * VERIFIED DATES ONLY: an event is publishable only when its public date comes from an SEC filing or the company's report date. Assumed lags,
    "first seen" dates and repricing windows are not publishable.
  * FACTS, NOT ADVICE: labels and explanations pass the channel's advice-word guard (ADVICE_WORDS, kept identical to test_channel_content.BANNED).
  * NOTHING INVENTED: a missing figure is left out, never shown as "n/a" or estimated; fewer than two verified figures means no update.

Usage: see update_preview.py (renders previews to files; nothing is sent).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import quote

RENDER_VERSION = "fu1"
BASE_URL_DEFAULT = "https://dashboard.first-light.finance"

# The channel's advice-word guard. Kept identical to mechanism/alerts/tests/test_channel_content.py::BANNED (a test checks it).
ADVICE_WORDS = re.compile(r"\b(buy\w*|sell\w*|targets?|stops?|entry|entries|recommend\w*|signals?|opportunit\w*|picks?|should|profit\w*|"
                          r"winners?|guarantee\w*|alpha|returns?|beat\w*|outperform\w*|earn\w*|money)\b", re.I)

# Public event types: URL slug and public label. improvement_with_limited_repricing is deliberately absent: it is a price judgement built on
# an internal score, not a reported fact.
PUBLIC_TYPES: Dict[str, Tuple[str, str]] = {
    "revenue_acceleration": ("revenue-acceleration", "Revenue growth accelerated"),
    "margin_expansion": ("margin-expansion", "Operating margin expanded"),
    "operating_leverage_inflection": ("operating-leverage", "Operating income outgrew revenue"),
    "profitability_turnaround": ("net-income-turnaround", "Net income turned positive"),
    "fcf_turnaround": ("fcf-turnaround", "Free cash flow turned positive"),
}
SLUG_TO_TYPE = {slug: t for t, (slug, _) in PUBLIC_TYPES.items()}

# Where the public date comes from -> how it is described. Anything else is not publishable.
DATE_SOURCES = {"sec_acceptance": "SEC filing accepted", "sec_filed_date": "SEC filing date", "earnings_calendar": "company's quarterly report date"}

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")

# The ONLY keys a PublicUpdate serialises (and so the only keys the future public API may return). A test pins this set.
PUBLIC_FIELDS = ("key", "symbol", "company_name", "event_type", "label", "quarter_end", "fiscal_label", "public_date", "date_source", "sector", "industry",
                 "market_cap", "figures", "why", "links", "news", "content_hash", "render_version")


@dataclass(frozen=True)
class Figure:
    label: str
    value: str
    compare: Optional[str] = None          # "a year earlier: -3.3%" (already worded)


@dataclass(frozen=True)
class NewsItem:
    published_at: str                      # ISO 8601 UTC
    source: str
    headline: str
    url: str


@dataclass
class PublicUpdate:
    key: str                               # natural key "SYMBOL/quarter_end/slug": stable across re-runs and method versions
    symbol: str
    company_name: Optional[str]
    event_type: str
    label: str
    quarter_end: str
    fiscal_label: str
    public_date: str
    date_source: str
    sector: Optional[str]
    industry: Optional[str]
    market_cap: Optional[Dict[str, str]]   # {"value": "$2.17B", "as_of": "2026-11-05"}
    figures: List[Figure]
    why: str
    links: Dict[str, str]                  # sec, sec_kind ('filing' | 'company_filings'), finviz, detail
    news: List[NewsItem] = field(default_factory=list)
    content_hash: str = ""
    render_version: str = RENDER_VERSION

    def to_public_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: d[k] for k in PUBLIC_FIELDS}


# ------------------------------------------------------------------------------------------------------------------------------- number formatting
def money(v: float) -> str:
    a = abs(v)
    s = f"{a / 1e9:.2f}B" if a >= 1e9 else f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.0f}K"
    return f"-${s}" if v < 0 else f"${s}"


def pct(v: float, signed: bool = False) -> str:
    return f"{v * 100:+.1f}%" if signed else f"{v * 100:.1f}%"


def points(v: float) -> str:
    return f"{v:+.1f} points"


def _num(m: Mapping[str, Any], k: str) -> Optional[float]:
    v = m.get(k)
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def fmt_date(d: str) -> str:
    x = date.fromisoformat(str(d)[:10])
    return f"{x.strftime('%b')} {x.day}, {x.year}"


# ------------------------------------------------------------------------------------------------------------------------------- figures + "why it matters"
def figures_and_why(event_type: str, m: Mapping[str, Any]) -> Tuple[List[Figure], Optional[str]]:
    """Two or three figures and one sentence, from the event's reported metrics only. The sentence is None when its own inputs are missing."""
    g = lambda k: _num(m, k)  # noqa: E731
    figs: List[Figure] = []
    why: Optional[str] = None
    if event_type == "revenue_acceleration":
        rev, rev_py, yoy, prev, accel = g("revenue"), g("revenue_py"), g("rev_yoy"), g("rev_yoy_prev"), g("rev_accel_pp")
        if rev is not None:
            figs.append(Figure("Revenue", money(rev), f"a year earlier: {money(rev_py)}" if rev_py is not None else None))
        if yoy is not None:
            figs.append(Figure("Growth, year over year", pct(yoy, True), f"previous quarter: {pct(prev, True)}" if prev is not None else None))
        if accel is not None:
            figs.append(Figure("Change in growth rate", points(accel)))
        if yoy is not None and prev is not None and yoy > prev:
            why = f"Revenue grew {pct(yoy, True)} from a year earlier, up from {pct(prev, True)} in the previous quarter: growth sped up instead of slowing."
    elif event_type == "margin_expansion":
        om, om_py, bps, yoy = g("op_margin"), g("op_margin_py"), g("op_margin_yoy_bps"), g("rev_yoy")
        if om is not None:
            figs.append(Figure("Operating margin", pct(om), f"a year earlier: {pct(om_py)}" if om_py is not None else None))
        if bps is not None:
            figs.append(Figure("Margin change", points(bps / 100)))
        if yoy is not None:
            figs.append(Figure("Revenue growth, year over year", pct(yoy, True)))
        if om is not None and om_py is not None and om > om_py:
            on_rev = f" while revenue changed {pct(yoy, True)}" if yoy is not None else ""
            why = f"Operating margin moved from {pct(om_py)} to {pct(om)} in a year{on_rev}: more of each dollar of sales now reaches operating income."
    elif event_type == "operating_leverage_inflection":
        yoy, oi, lev = g("rev_yoy"), g("operating_income"), g("op_leverage")
        if yoy is not None:
            figs.append(Figure("Revenue growth, year over year", pct(yoy, True)))
        if oi is not None:
            figs.append(Figure("Operating income", money(oi)))
        if lev is not None and lev > 0:
            figs.append(Figure("Operating income growth vs revenue growth", f"{lev:.1f}x"))
        if yoy is not None and lev is not None and lev > 1 and yoy > 0:
            why = f"Operating income grew {lev:.1f} times as fast as revenue ({pct(yoy, True)} year over year): costs rose more slowly than sales."
    elif event_type == "profitability_turnaround":
        ni, ni_py, ni_pq, swing = g("net_income"), g("net_income_py"), g("net_income_pq"), g("ni_swing_pct_rev")
        if ni is not None:
            figs.append(Figure("Net income", money(ni), f"a year earlier: {money(ni_py)}" if ni_py is not None else None))
        if ni_pq is not None:
            figs.append(Figure("Net income, previous quarter", money(ni_pq)))
        if swing is not None:
            figs.append(Figure("Year-over-year swing, share of revenue", pct(swing)))
        if ni is not None and ni_py is not None and ni > 0 > ni_py:
            why = f"Net income was {money(ni)} this quarter against {money(ni_py)} a year earlier: the company moved from a loss to positive net income."
    elif event_type == "fcf_turnaround":
        fcf, fcf_py, margin, dpp = g("free_cash_flow"), g("free_cash_flow_py"), g("fcf_margin"), g("fcf_margin_yoy_pp")
        if fcf is not None:
            figs.append(Figure("Free cash flow", money(fcf), f"a year earlier: {money(fcf_py)}" if fcf_py is not None else None))
        if margin is not None:
            figs.append(Figure("Free cash flow margin", pct(margin)))
        if dpp is not None:
            figs.append(Figure("Margin change", points(dpp)))
        if fcf is not None and fcf_py is not None and fcf > 0 > fcf_py:
            why = (f"Free cash flow was {money(fcf)} this quarter against {money(fcf_py)} a year earlier: operations now bring in more cash "
                   "than the business spends on running and investing in itself.")
    return figs[:3], why


# ------------------------------------------------------------------------------------------------------------------------------- links
def sec_link(cik: Optional[int], accession: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The exact filing's index page when the accession is known, else the company's filing list; (None, None) without a CIK."""
    if cik is None or int(cik) <= 0:
        return None, None
    cik = int(cik)
    if accession and ACCESSION_RE.match(accession):
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm", "filing"
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=10-Q&owner=include&count=40", "company_filings"


def finviz_link(symbol: str) -> str:
    return f"https://finviz.com/quote.ashx?t={quote(symbol.replace('.', '-'))}"


def natural_key(symbol: str, quarter_end: str, event_type: str) -> str:
    return f"{symbol}/{str(quarter_end)[:10]}/{PUBLIC_TYPES[event_type][0]}"


def detail_link(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/signals/{key}"


# ------------------------------------------------------------------------------------------------------------------------------- build
def content_hash(u: PublicUpdate) -> str:
    """What a correction compares: the facts as displayed (label, dates, figures, explanation). News and links are not part of it."""
    core = {"key": u.key, "label": u.label, "quarter_end": u.quarter_end, "public_date": u.public_date, "date_source": u.date_source,
            "figures": [asdict(f) for f in u.figures], "why": u.why}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ineligibility(ev: Mapping[str, Any], check_freshness: bool = True) -> Optional[str]:
    """Why an event cannot become a public update (None: it can, subject to having enough figures). check_freshness=False is for re-reading an
    ALREADY published event during a correction check: a later re-dating must show up as 'redated', not hide the event."""
    if ev.get("event_type") not in PUBLIC_TYPES:
        return "event_type_not_public"
    if not SYMBOL_RE.match(str(ev.get("symbol") or "")):
        return "bad_symbol"
    if ev.get("availability_basis") not in DATE_SOURCES:
        return "public_date_not_verified"
    detected = ev.get("first_detected_on")
    if check_freshness and (detected is None or date.fromisoformat(str(detected)[:10]) < date.fromisoformat(str(ev["available_at"])[:10])):
        return "detected_before_public_date"          # the re-dated case the alert freshness rule also blocks
    return None


def build_update(ev: Mapping[str, Any], *, company_name: Optional[str] = None, cik: Optional[int] = None, accession: Optional[str] = None,
                 profile: Optional[Mapping[str, Any]] = None, news: Optional[List[NewsItem]] = None,
                 base_url: str = BASE_URL_DEFAULT, check_freshness: bool = True) -> Tuple[Optional[PublicUpdate], Optional[str]]:
    """(update, None) or (None, reason). `profile` is {date, sector, industry} from the last fundamentals row on or before the public date."""
    reason = ineligibility(ev, check_freshness)
    if reason:
        return None, reason
    figs, why = figures_and_why(ev["event_type"], ev.get("metrics") or {})
    if len(figs) < 2:
        return None, "fewer_than_two_figures"
    if why is None:
        return None, "explanation_inputs_missing"
    public = str(ev["available_at"])[:10]
    qe = str(ev["quarter_end"])[:10]
    fy, fq = ev.get("fiscal_year"), ev.get("fiscal_quarter")
    fiscal = f"Fiscal Q{fq} {fy}" if fy and fq else f"Quarter ended {fmt_date(qe)}"
    cap = None
    vpub = ((ev.get("context") or {}).get("valuation_at_publication") or {})
    if _num(vpub, "market_cap") and vpub.get("snapshot_date") and str(vpub["snapshot_date"])[:10] <= public:
        cap = {"value": money(float(vpub["market_cap"])), "as_of": str(vpub["snapshot_date"])[:10]}
    prof = profile if profile and profile.get("date") and str(profile["date"])[:10] <= public else {}
    key = natural_key(ev["symbol"], qe, ev["event_type"])
    sec, sec_kind = sec_link(cik, accession)
    links = {"finviz": finviz_link(ev["symbol"]), "detail": detail_link(base_url, key)}
    if sec:
        links.update(sec=sec, sec_kind=sec_kind)
    u = PublicUpdate(key=key, symbol=ev["symbol"], company_name=(company_name or None), event_type=ev["event_type"], label=PUBLIC_TYPES[ev["event_type"]][1],
                     quarter_end=qe, fiscal_label=fiscal, public_date=public, date_source=DATE_SOURCES[ev["availability_basis"]],
                     sector=prof.get("sector") or None, industry=prof.get("industry") or None, market_cap=cap, figures=figs, why=why,
                     links=links, news=list(news or []))
    u.content_hash = content_hash(u)
    return u, None


# ------------------------------------------------------------------------------------------------------------------------------- corrections
def classify_revision(published: Mapping[str, Any], current: Optional[PublicUpdate]) -> Tuple[str, List[str]]:
    """Compare what was published (its stored public dict) with what the event says now.

    'unchanged'  same facts as displayed (a revision below display precision is not a correction);
    'restated'   a figure or the explanation changed;
    'redated'    the public date or its source changed (the facts may be the same);
    'withdrawn'  the event no longer exists or is no longer publishable.
    Returns (kind, the names of what changed)."""
    if current is None:
        return "withdrawn", ["event"]
    if current.content_hash == published.get("content_hash"):
        return "unchanged", []
    changed = []
    if current.public_date != published.get("public_date") or current.date_source != published.get("date_source"):
        changed.append("public_date")
    old_figs = {f["label"]: (f["value"], f.get("compare")) for f in published.get("figures") or []}
    new_figs = {f.label: (f.value, f.compare) for f in current.figures}
    changed += sorted(k for k in set(old_figs) | set(new_figs) if old_figs.get(k) != new_figs.get(k))
    if current.why != published.get("why"):
        changed.append("why")
    if current.label != published.get("label"):
        changed.append("label")
    figure_or_text = [c for c in changed if c != "public_date"]
    return ("restated" if figure_or_text else "redated"), changed
