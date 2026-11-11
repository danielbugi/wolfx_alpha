"""Opportunity Intelligence v1, the pure core: three SEPARATE evidence dimensions, their verdicts, the category and the ordering.

No database, no network, no wall clock. Inputs are rows the repository already bounded by the session; the knowledge-cutoff filters are in brief.py.

The three dimensions answer different questions and are never merged into one number:
  business      - did the reported business improve? The SEC filing's figures versus a year earlier, grouped by UNDERLYING ACCOUNTING FACT
                  (update_select.FAMILY), so derived metrics of one fact count once: revenue (growth, acceleration, QoQ); operating (income, margin,
                  leverage); net income; cash (FCF, FCF margin, conversion). A net-income gain mostly unexplained by operating income is HELD.
  expectations  - did the result beat what was expected BEFORE the release? Counts only with a verified PRE-RELEASE consensus snapshot.
                  earnings_calendar is overwritten on every fetch, so a row holding the reported EPS was always written after the release and
                  its estimate is not a verified pre-release consensus: shown as unverified vendor data, never counted (audit 2026-11-12).
  market        - how did the price respond, versus the S&P 500? A response, not proof of business quality. Flagged UNRELIABLE (not counted)
                  when the window may be distorted: a detected price discontinuity, a one-day move matching a common split ratio, or a missing
                  bar. stock_prices.adj_close equals close in this database, so returns are unadjusted and a split cannot be corrected.

Nothing here is a score, a rating or a forecast. The category and the ordering are deterministic rules over counts of facts (docs section 5).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from inflection import update_select as S

BRIEF_VERSION = "brief_v1"
NY = ZoneInfo("America/New_York")

# business / expectations verdicts
SUPPORTS, CONTRADICTS, NEUTRAL, MIXED, UNAVAILABLE, HELD, UNVERIFIED = "supports", "contradicts", "neutral", "mixed", "unavailable", "held", "unverified"
# market response verdicts (deliberately not 'supports': a price move is not evidence of business quality)
POSITIVE, NEGATIVE, FLAT, UNRELIABLE = "positive", "negative", "flat", "unreliable"

# Fixed, documented thresholds (changing one is a versioned change of BRIEF_VERSION).
SURPRISE_IN_LINE_PCT = 2.0      # |EPS surprise| below this is 'in line'
REACTION_NEUTRAL_PP = 1.0       # |move vs S&P 500| below this many percentage points is 'flat'
REACTION_SESSIONS = 3           # window: close before the first public day -> close of the 3rd session from that day
# and their inverses. 1.25x / 1.5x (5:4, 3:2) are deliberately absent: on real data (2026-11-10 run) they matched 38 ordinary earnings-day moves
# of +-20-25%; without corporate-action data such splits cannot be told apart from a reaction (stated in brief.CAVEATS).
SPLIT_FACTORS = (2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 20.0)
SPLIT_TOLERANCE = 0.03          # a one-day close ratio within 3% of a split factor is treated as a possible split
FUNDAMENTAL_FACTS = ("revenue", "operating", "net_income", "cash")
HEADLINE_TYPES = tuple(S.FAMILY)  # reported accounting changes (improvement_with_limited_repricing is price-built: excluded)

CATEGORY_CONFIRMED = "business_improved_market_confirmed"
CATEGORY_DISCOVERY = "business_improved_market_not_confirming"
CATEGORY_UNCHECKED = "business_improved_market_unreadable"
CATEGORY_MIXED = "business_mixed"
CATEGORY_WEAK = "business_not_improved"
CATEGORY_LABELS = {
    CATEGORY_CONFIRMED: "Business improved; market response positive",
    CATEGORY_DISCOVERY: "Business improved; market response flat or negative",
    CATEGORY_UNCHECKED: "Business improved; market response unreliable or not yet available",
    CATEGORY_MIXED: "Business evidence mixed",
    CATEGORY_WEAK: "Business did not clearly improve",
}


@dataclass(frozen=True)
class Figure:
    label: str
    value: Optional[float]
    unit: str                   # usd | pct | pp | usd_per_share
    source: str
    available_at: str           # ISO date or timestamp: when it became public (vendor data: when it was fetched)


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


# ---------------------------------------------------------------- business
def business_facts(fm: Mapping[str, Any], source: str, available_at: str) -> List[Fact]:
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


def business_verdict(facts: Sequence[Fact]) -> str:
    s = sum(f.verdict == SUPPORTS for f in facts)
    c = sum(f.verdict == CONTRADICTS for f in facts)
    if s == 0 and c == 0:
        return UNAVAILABLE if all(f.verdict == UNAVAILABLE for f in facts) else NEUTRAL
    if s >= 2 and c == 0:
        return SUPPORTS
    if c > s:
        return CONTRADICTS
    return MIXED


# ---------------------------------------------------------------- expectations
def release_start(report_date: date) -> datetime:
    """The earliest moment a release dated `report_date` can be public: 00:00 New York that day (before-the-open releases)."""
    return datetime.combine(report_date, time(0, 0), tzinfo=NY)


def expectations_fact(row: Optional[Mapping[str, Any]], pre_release: Optional[Mapping[str, Any]] = None, unavailable_reason: str = "") -> Fact:
    """`row` = the earnings_calendar row of the quarter. `pre_release` = a VERIFIED consensus snapshot {eps_estimate, observed_at, source}
    observed before the release (none is stored today: the hook exists so a first-seen store can enable the dimension without a code change)."""
    if not row:
        return Fact("expectations", UNAVAILABLE, note=unavailable_reason or "no earnings report matched to this filing")
    rd = row["report_date"]
    actual = num(row, "eps_actual")
    fetched = _aware(row.get("fetched_at"))
    if pre_release and actual is not None:
        est, seen = num(pre_release, "eps_estimate"), _aware(pre_release.get("observed_at"))
        if est is not None and seen is not None and seen < release_start(rd) and est != 0:
            sp = (actual - est) / abs(est) * 100
            src = pre_release.get("source") or "pre-release consensus snapshot"
            figs = [Figure("Reported EPS", actual, "usd_per_share", "earnings_calendar (vendor)", _iso(fetched)),
                    Figure("Consensus EPS before the release", est, "usd_per_share", src, _iso(seen)),
                    Figure("EPS versus the pre-release consensus", sp, "pct", src + " + earnings_calendar", _iso(fetched))]
            v = SUPPORTS if sp >= SURPRISE_IN_LINE_PCT else CONTRADICTS if sp <= -SURPRISE_IN_LINE_PCT else NEUTRAL
            return Fact("expectations", v, figs, f"report date {rd.isoformat()}; consensus observed {_iso(seen)}, before the release")
    vendor = [Figure("Reported EPS (vendor)", actual, "usd_per_share", "earnings_calendar (vendor, unverified)", _iso(fetched)),
              Figure("Consensus estimate (vendor, stored after the release)", num(row, "eps_estimate"), "usd_per_share", "earnings_calendar (vendor, unverified)", _iso(fetched)),
              Figure("EPS surprise (vendor, unverified)", num(row, "surprise_pct"), "pct", "earnings_calendar (vendor, unverified)", _iso(fetched))]
    if actual is None:
        return Fact("expectations", UNAVAILABLE, vendor, f"report date {rd.isoformat()}; reported EPS not captured")
    return Fact("expectations", UNVERIFIED, vendor,
                f"report date {rd.isoformat()}; the consensus was stored {_iso(fetched)[:10] if fetched else 'at an unknown time'}, after the release, and no "
                f"pre-release snapshot is stored, so the surprise is not verified and is not counted")


# ---------------------------------------------------------------- market response
def reaction_window(sessions: Sequence[date], first_public: date) -> Optional[tuple]:
    """(start_session, end_session): the close of the last session BEFORE the first public day, and the close of the REACTION_SESSIONS-th
    session on or after it. Covers a release before the open (reacts that day) and after the close (reacts the next day)."""
    before = [d for d in sessions if d < first_public]
    after = [d for d in sessions if d >= first_public]
    if not before or len(after) < REACTION_SESSIONS:
        return None
    return before[-1], after[REACTION_SESSIONS - 1]


def split_like(ratio: float) -> Optional[float]:
    for f in SPLIT_FACTORS:
        for x in (f, 1 / f):
            if abs(ratio - x) / x <= SPLIT_TOLERANCE:
                return x
    return None


def market_fact(symbol: str, sessions: Sequence[date], first_public: date, bars: Mapping[date, float], bench: Mapping[date, float],
                discontinuities: Sequence[Mapping[str, Any]] = ()) -> Fact:
    w = reaction_window(sessions, first_public)
    if w is None:
        return Fact("market", UNAVAILABLE, note=f"the {REACTION_SESSIONS}-session window from {first_public.isoformat()} is not complete at this session")
    a, b = w
    days = [d for d in sessions if a <= d <= b]
    missing = [d for d in days if not bars.get(d) or not bench.get(d)]
    if missing:
        return Fact("market", UNAVAILABLE, note=f"missing close for {symbol} or the S&P 500 on {', '.join(d.isoformat() for d in missing)}")
    r, ri = (bars[b] / bars[a] - 1) * 100, (bench[b] / bench[a] - 1) * 100
    ex = r - ri
    at = b.isoformat()
    figs = [Figure(f"{symbol} move {a.isoformat()} to {b.isoformat()} (unadjusted closes)", r, "pct", "stock_prices close", at),
            Figure("S&P 500 move, same window", ri, "pct", "market_index_prices ^GSPC close", at),
            Figure("Move relative to the S&P 500", ex, "pp", "stock_prices, market_index_prices", at)]
    flags = [f"price discontinuity detected on {_iso(d['date'])} ({d.get('kind')}, close ratio {num(d, 'ratio') or 0:.3f})" for d in discontinuities if a < d["date"] <= b]
    for prev, cur in zip(days, days[1:]):
        f = split_like(bars[cur] / bars[prev])
        if f is not None:
            flags.append(f"one-day close ratio {bars[cur] / bars[prev]:.3f} on {cur.isoformat()} matches a {f:.3g}x split ratio")
    if flags:
        return Fact("market", UNRELIABLE, figs, "window may be distorted, not counted: " + "; ".join(flags))
    v = POSITIVE if ex >= REACTION_NEUTRAL_PP else NEGATIVE if ex <= -REACTION_NEUTRAL_PP else FLAT
    return Fact("market", v, figs, f"window {a.isoformat()} close to {b.isoformat()} close")


# ---------------------------------------------------------------- category, agreement and ordering
def assess(business: Sequence[Fact], expectations: Fact, market: Fact) -> Dict[str, Any]:
    dims = {"business": business_verdict(business), "expectations": expectations.verdict, "market": market.verdict}
    agree = [k for k, v in dims.items() if v in (SUPPORTS, POSITIVE)]
    disagree = [k for k, v in dims.items() if v in (CONTRADICTS, NEGATIVE)]
    b = dims["business"]
    if b != SUPPORTS:                                      # the category is about the BUSINESS first; the market only describes the response to it
        category = CATEGORY_MIXED if b == MIXED else CATEGORY_WEAK
    elif market.verdict == POSITIVE:
        category = CATEGORY_CONFIRMED
    elif market.verdict in (FLAT, NEGATIVE):
        category = CATEGORY_DISCOVERY
    else:
        category = CATEGORY_UNCHECKED
    return {"dimensions": dims, "agreeing": agree, "disagreeing": disagree, "category": category, "category_label": CATEGORY_LABELS[category],
            "converging": category == CATEGORY_CONFIRMED and not disagree,
            "business_improved": [f.family for f in business if f.verdict == SUPPORTS],
            "business_worsened": [f.family for f in business if f.verdict == CONTRADICTS],
            "summary": f"Business: {b}; expectations: {expectations.verdict}; market response: {market.verdict}"}


def contradictions(business: Sequence[Fact], expectations: Fact, market: Fact) -> List[Dict[str, str]]:
    out = [{"family": f.family, "kind": "contradicts" if f.verdict == CONTRADICTS else "caution", "text": f.note or LABELS[f.family] + " went the other way"}
           for f in business if f.verdict in (CONTRADICTS, HELD)]
    if expectations.verdict == CONTRADICTS:
        out.append({"family": "expectations", "kind": "contradicts", "text": "EPS came in below the pre-release consensus"})
    if market.verdict == NEGATIVE:
        out.append({"family": "market", "kind": "contradicts", "text": "The price fell relative to the S&P 500 after the release"})
    if market.verdict == UNRELIABLE:
        out.append({"family": "market", "kind": "caution", "text": market.note})
    return out


def order_key(b: Mapping[str, Any]):
    """Unchanged evidence ordering (owner decision 2026-11-11): agreement first, then a supported business change, more agreeing dimensions,
    fewer disagreeing, more improved accounting facts, fewer worsened, newest first public day, symbol."""
    a = b["assessment"]
    return (not a["converging"], a["dimensions"]["business"] != SUPPORTS, -len(a["agreeing"]), len(a["disagreeing"]), -len(a["business_improved"]),
            len(a["business_worsened"]), -date.fromisoformat(b["first_public"]).toordinal(), b["symbol"])


def order(briefs: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(briefs, key=order_key)


LABELS = {"revenue": "Revenue", "operating": "Operating income", "net_income": "Net income", "cash": "Free cash flow",
          "expectations": "EPS versus expectations", "market": "Market response versus the S&P 500"}


def fact_dict(f: Fact) -> Dict[str, Any]:
    d = asdict(f)
    d["label"] = LABELS.get(f.family, f.family)
    d["figures"] = [x for x in d["figures"] if x["value"] is not None]
    return d


def _aware(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.combine(v, time(0, 0), tzinfo=timezone.utc)


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v.astimezone(timezone.utc) if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return v.isoformat() if isinstance(v, date) else str(v)
