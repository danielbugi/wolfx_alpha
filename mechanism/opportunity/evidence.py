"""Opportunity Intelligence v0, the pure core: evidence families, their verdicts, convergence and ordering.

No database, no network, no wall clock. Inputs are rows the repository already filtered to what was known at the knowledge cutoff
(repository.py); this module only classifies and explains them.

Evidence CLASSES are independent sources of information about one company-quarter:
  fundamental  - the SEC filing's figures versus a year earlier
  surprise     - reported EPS versus the vendor's consensus estimate (an expectation gap, not a year-over-year change)
  reaction     - the stock's price move versus the S&P 500 around the first public moment

Inside the fundamental class, evidence is grouped by UNDERLYING ACCOUNTING FACT (update_select.FAMILY), so derived metrics of one fact are never
counted twice: revenue growth, quarter-over-quarter growth and acceleration are all one 'revenue' fact; operating income, operating margin and
operating leverage are one 'operating' fact; free cash flow, FCF margin and cash conversion are one 'cash' fact.

Nothing here is a score or a prediction. The ordering keys are counts of facts and shown as those facts (docs: section 5).
"""
from __future__ import annotations

import math
from decimal import Decimal
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from inflection import update_select as S

BRIEF_VERSION = "brief_v0"
SUPPORTS, CONTRADICTS, NEUTRAL, MIXED, UNAVAILABLE, HELD = "supports", "contradicts", "neutral", "mixed", "unavailable", "held"

# Fixed, documented thresholds (changing one is a versioned change of BRIEF_VERSION).
SURPRISE_IN_LINE_PCT = 2.0      # |EPS surprise| below this is 'in line'
REACTION_NEUTRAL_PP = 1.0       # |excess move vs S&P 500| below this many percentage points is 'no clear reaction'
REACTION_SESSIONS = 3           # reaction window: close before the first public day -> close of the 3rd session from that day
FUNDAMENTAL_FACTS = ("revenue", "operating", "net_income", "cash")
HEADLINE_TYPES = tuple(S.FAMILY)  # event types that are a reported accounting change (improvement_with_limited_repricing is price-built: excluded)


@dataclass(frozen=True)
class Figure:
    label: str
    value: Optional[float]
    unit: str                   # usd | pct | pp | bps | x | usd_per_share
    source: str
    available_at: str           # ISO date or timestamp: when this figure became public (or, for vendor data, when it was last fetched)


@dataclass
class Fact:
    family: str
    verdict: str
    figures: List[Figure] = field(default_factory=list)
    note: str = ""


def num(m: Mapping[str, Any], k: str) -> Optional[float]:
    v = (m or {}).get(k)
    if isinstance(v, Decimal):
        v = float(v)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


# ---------------------------------------------------------------- fundamental class
def fundamental_facts(fm: Mapping[str, Any], source: str, available_at: str) -> List[Fact]:
    """The four accounting facts of one filing, each one verdict. `fm` = fundamental_feature.metrics (inflection_v2_sec_pit)."""
    def fig(label, key, unit, scale=1.0, value=None):
        v = value if value is not None else num(fm, key)
        return Figure(label, None if v is None else v * scale, unit, source, available_at)

    out: List[Fact] = []
    rev = num(fm, "rev_yoy")
    v = UNAVAILABLE if rev is None else SUPPORTS if rev >= S.MIN_REV_GROWTH else CONTRADICTS if rev < 0 else NEUTRAL
    out.append(Fact("revenue", v, [fig("Revenue", "revenue", "usd"), fig("Revenue a year earlier", "revenue_py", "usd"),
                                   fig("Revenue growth, year over year", "rev_yoy", "pct", 100), fig("Change in growth rate", "rev_accel_pp", "pp")]))

    d_oi = S.operating_income_change(fm)
    v = UNAVAILABLE if d_oi is None else SUPPORTS if d_oi > 0 else CONTRADICTS if d_oi < 0 else NEUTRAL
    out.append(Fact("operating", v, [fig("Operating income", "operating_income", "usd"), fig("Operating income change, year over year", None, "usd", value=d_oi),
                                     fig("Operating margin", "op_margin", "pct", 100), fig("Operating margin a year earlier", "op_margin_py", "pct", 100)]))

    ni, ni_py = num(fm, "net_income"), num(fm, "net_income_py")
    div = S.non_operating_divergence(fm)
    note = ""
    if ni is None or ni_py is None:
        v = UNAVAILABLE
    elif ni > ni_py:
        v = SUPPORTS
        if div is not None and div > S.MAX_NON_OPERATING_DIVERGENCE:
            v, note = HELD, f"{div * 100:.0f}% of the net income improvement is not matched by operating income (non-operating items likely)"
    else:
        v = CONTRADICTS if ni < ni_py else NEUTRAL
    out.append(Fact("net_income", v, [fig("Net income", "net_income", "usd"), fig("Net income a year earlier", "net_income_py", "usd")], note))

    fcf_pp = num(fm, "fcf_margin_yoy_pp")
    v = UNAVAILABLE if fcf_pp is None else SUPPORTS if fcf_pp >= S.MIN_FCF_PP else CONTRADICTS if fcf_pp <= -S.MIN_FCF_PP else NEUTRAL
    out.append(Fact("cash", v, [fig("Free cash flow", "free_cash_flow", "usd"), fig("Free cash flow a year earlier", "free_cash_flow_py", "usd"),
                                fig("Free cash flow margin", "fcf_margin", "pct", 100), fig("Free cash flow margin a year earlier", "fcf_margin_py", "pct", 100)]))
    return out


def fundamental_verdict(facts: Sequence[Fact]) -> str:
    s = sum(f.verdict == SUPPORTS for f in facts)
    c = sum(f.verdict == CONTRADICTS for f in facts)
    if s == 0 and c == 0:
        return UNAVAILABLE if all(f.verdict == UNAVAILABLE for f in facts) else NEUTRAL
    if s >= 2 and c == 0:
        return SUPPORTS
    if c > s:
        return CONTRADICTS
    return MIXED


# ---------------------------------------------------------------- surprise class
def surprise_fact(row: Optional[Mapping[str, Any]], unavailable_reason: str = "") -> Fact:
    if not row:
        return Fact("surprise", UNAVAILABLE, note=unavailable_reason or "no earnings report matched to this filing")
    sp, src, at = num(row, "surprise_pct"), "earnings_calendar (vendor consensus)", _iso(row.get("fetched_at"))
    figs = [Figure("Reported EPS", num(row, "eps_actual"), "usd_per_share", src, at), Figure("Consensus EPS estimate", num(row, "eps_estimate"), "usd_per_share", src, at),
            Figure("EPS surprise", sp, "pct", src, at)]
    note = f"report date {_iso(row.get('report_date'))}"
    if sp is None:
        return Fact("surprise", UNAVAILABLE, figs, note + "; no surprise figure")
    v = SUPPORTS if sp >= SURPRISE_IN_LINE_PCT else CONTRADICTS if sp <= -SURPRISE_IN_LINE_PCT else NEUTRAL
    return Fact("surprise", v, figs, note)


# ---------------------------------------------------------------- reaction class
def reaction_window(sessions: Sequence[date], first_public: date) -> Optional[tuple]:
    """(start_session, end_session): the close of the last session BEFORE the first public day, and the close of the REACTION_SESSIONS-th
    session on or after it. Covers a release before the open (reacts that day) and after the close (reacts the next day)."""
    before = [d for d in sessions if d < first_public]
    after = [d for d in sessions if d >= first_public]
    if not before or len(after) < REACTION_SESSIONS:
        return None
    return before[-1], after[REACTION_SESSIONS - 1]


def reaction_fact(symbol: str, sessions: Sequence[date], first_public: date, bars: Mapping[date, float], bench: Mapping[date, float]) -> Fact:
    w = reaction_window(sessions, first_public)
    if w is None:
        return Fact("reaction", UNAVAILABLE, note=f"the {REACTION_SESSIONS}-session window from {first_public.isoformat()} is not complete at this session")
    a, b = w
    pa, pb, ia, ib = bars.get(a), bars.get(b), bench.get(a), bench.get(b)
    if None in (pa, pb, ia, ib) or not pa or not ia:
        return Fact("reaction", UNAVAILABLE, note=f"missing close for {symbol} or the S&P 500 on {a.isoformat()} or {b.isoformat()}")
    r, ri = (pb / pa - 1) * 100, (ib / ia - 1) * 100
    ex = r - ri
    at = b.isoformat()
    figs = [Figure(f"{symbol} move {a.isoformat()} to {b.isoformat()}", r, "pct", "stock_prices close", at),
            Figure("S&P 500 move, same window", ri, "pct", "market_index_prices ^GSPC close", at),
            Figure("Move relative to the S&P 500", ex, "pp", "stock_prices, market_index_prices", at)]
    v = SUPPORTS if ex >= REACTION_NEUTRAL_PP else CONTRADICTS if ex <= -REACTION_NEUTRAL_PP else NEUTRAL
    return Fact("reaction", v, figs, f"window {a.isoformat()} close to {b.isoformat()} close")


# ---------------------------------------------------------------- convergence and ordering
def convergence(fund: Sequence[Fact], surprise: Fact, reaction: Fact) -> Dict[str, Any]:
    classes = {"fundamental": fundamental_verdict(fund), "surprise": surprise.verdict, "reaction": reaction.verdict}
    sup = [k for k, v in classes.items() if v == SUPPORTS]
    con = [k for k, v in classes.items() if v == CONTRADICTS]
    converging = classes["fundamental"] == SUPPORTS and len(sup) >= 2 and not con
    return {"classes": classes, "supporting": sup, "contradicting": con, "converging": converging,
            "fundamental_supporting": [f.family for f in fund if f.verdict == SUPPORTS],
            "fundamental_contradicting": [f.family for f in fund if f.verdict == CONTRADICTS],
            "summary": f"{len(sup)} of 3 evidence classes support, {len(con)} contradict"}


def contradictions(fund: Sequence[Fact], surprise: Fact, reaction: Fact) -> List[Dict[str, str]]:
    out = [{"family": f.family, "kind": "contradicts" if f.verdict == CONTRADICTS else "caution", "text": f.note or LABELS[f.family] + " went the other way"}
           for f in fund if f.verdict in (CONTRADICTS, HELD)]
    for f in (surprise, reaction):
        if f.verdict == CONTRADICTS:
            out.append({"family": f.family, "kind": "contradicts", "text": LABELS[f.family] + " went the other way"})
    return out


def order_key(b: Mapping[str, Any]):
    c = b["convergence"]
    return (not c["converging"], c["classes"]["fundamental"] != SUPPORTS, -len(c["supporting"]), len(c["contradicting"]), -len(c["fundamental_supporting"]),
            len(c["fundamental_contradicting"]), -date.fromisoformat(b["first_public"]).toordinal(), b["symbol"])


def order(briefs: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(briefs, key=order_key)


LABELS = {"revenue": "Revenue", "operating": "Operating income", "net_income": "Net income", "cash": "Free cash flow",
          "surprise": "EPS versus the consensus estimate", "reaction": "Price move versus the S&P 500"}


def fact_dict(f: Fact) -> Dict[str, Any]:
    d = asdict(f)
    d["label"] = LABELS.get(f.family, f.family)
    d["figures"] = [x for x in d["figures"] if x["value"] is not None]
    return d


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return v.isoformat() if isinstance(v, date) else str(v)
