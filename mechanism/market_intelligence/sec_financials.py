"""SEC XBRL company facts -> verbatim financial facts and POINT-IN-TIME quarterly series. PURE: stdlib only; no network, clock, file, environment or database.

Source: the SEC's `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`. Every entry there names the filing it came from (`accn`), the form, the period (`start`/`end`) and the date it was
filed (`filed`). That makes it possible to answer "what was known at the decision point?" exactly: a series built AS OF a filing uses only values filed on or before that filing, and a later
restatement of a prior-year number is ignored (look-ahead). The values are copied verbatim; the only arithmetic here is the documented Q4 = annual - (Q1+Q2+Q3), flagged `derived`.

Pairs with `filing_contract` (the filing's own identity and acceptance timestamp) and `financial_anomaly` (interpretation, separate and versioned).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

FACT_SCHEMA = "financial_fact_v1"
GROUPS: Mapping[str, Tuple[str, ...]] = {          # tag priority within a group (first wins when two tags report the same period)
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",),
    "operating_income": ("OperatingIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
}
UNIT = {"revenue": "USD", "net_income": "USD", "operating_income": "USD", "eps_diluted": "USD/shares"}
QUARTER_DAYS = (80, 100)
YEAR_DAYS = (350, 380)
PERIODIC_FORMS = ("10-Q", "10-Q/A", "10-K", "10-K/A")


class FinancialFactError(ValueError):
    pass


@dataclass(frozen=True)
class FinancialFact:
    group: str
    tag: str
    unit: str
    value: float
    start: Optional[date]
    end: date
    fy: Optional[int]
    fp: Optional[str]
    form: str
    accession: str
    filed: date

    @property
    def days(self) -> Optional[int]:
        return (self.end - self.start).days if self.start else None


@dataclass(frozen=True)
class QuarterValue:
    group: str
    end: date
    value: float
    accession: str            # the filing the number comes from (for a derived Q4: the 10-K)
    filed: date
    form: str
    derived: bool = False     # True only for Q4 = annual - (Q1+Q2+Q3)


def _d(s: Any) -> Optional[date]:
    return date.fromisoformat(s) if isinstance(s, str) and len(s) >= 10 else None


def parse_companyfacts(doc: Mapping[str, Any], cik: int) -> List[FinancialFact]:
    """Verbatim facts for the tracked groups. A document for another CIK is refused whole (no cross-company attachment). Unusable entries are skipped, never repaired."""
    try:
        doc_cik = int(str(doc.get("cik", "")).lstrip("0") or "0")
    except ValueError:
        doc_cik = 0
    if doc_cik != cik:
        raise FinancialFactError(f"companyfacts document is for CIK {doc.get('cik')!r}, but CIK {cik} was requested; refused as a whole")
    gaap = (doc.get("facts") or {}).get("us-gaap") or {}
    out: Dict[Tuple[str, date, Optional[date], str, float], FinancialFact] = {}
    for group, tags in GROUPS.items():
        for rank, tag in enumerate(tags):
            entries = ((gaap.get(tag) or {}).get("units") or {}).get(UNIT[group]) or []
            for e in entries:
                end, filed, val, accn = _d(e.get("end")), _d(e.get("filed")), e.get("val"), e.get("accn")
                if end is None or filed is None or not isinstance(val, (int, float)) or isinstance(val, bool) or not accn or not e.get("form"):
                    continue
                f = FinancialFact(group, tag, UNIT[group], float(val), _d(e.get("start")), end, e.get("fy"), e.get("fp"), str(e["form"]), str(accn), filed)
                key = (group, end, f.start, accn, f.value)
                prev = out.get(key)
                if prev is None or GROUPS[group].index(prev.tag) > rank:
                    out[key] = f
    return sorted(out.values(), key=lambda f: (f.group, f.end, f.filed, f.accession))


def _latest_before(facts: Iterable[FinancialFact], asof: date) -> Optional[FinancialFact]:
    c = [f for f in facts if f.filed <= asof]
    return max(c, key=lambda f: (f.filed, f.accession, -GROUPS[f.group].index(f.tag))) if c else None   # latest filing; on a tie the higher-priority tag


def quarter_series(facts: Sequence[FinancialFact], group: str, asof: date) -> Dict[date, QuarterValue]:
    """Quarterly values for `group` known on `asof`: each period's LATEST value filed on or before `asof` (so a restatement after `asof` is invisible). Q4 is derived from the annual figure
    and the three quarters of the same fiscal year when all four are known by `asof`."""
    gf = [f for f in facts if f.group == group and f.start is not None and f.days is not None]
    by_period: Dict[Tuple[date, date], List[FinancialFact]] = {}
    for f in gf:
        by_period.setdefault((f.start, f.end), []).append(f)
    quarters: Dict[date, QuarterValue] = {}
    annual: List[FinancialFact] = []
    for (s, e), fs in by_period.items():
        days = (e - s).days
        pick = _latest_before(fs, asof)
        if pick is None:
            continue
        if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
            quarters[e] = QuarterValue(group, e, pick.value, pick.accession, pick.filed, pick.form)
        elif YEAR_DAYS[0] <= days <= YEAR_DAYS[1] and pick.form.startswith("10-K"):
            annual.append(pick)
    for a in annual:
        if a.end in quarters:
            continue
        prior = sorted((q for q in quarters.values() if a.end - timedelta(days=330) < q.end < a.end - timedelta(days=60)), key=lambda q: q.end)
        if len(prior) == 3 and all(abs((prior[i + 1].end - prior[i].end).days - 91) <= 12 for i in range(2)):
            quarters[a.end] = QuarterValue(group, a.end, a.value - sum(q.value for q in prior), a.accession, a.filed, a.form, derived=True)
    return quarters


def nearest(series: Mapping[date, QuarterValue], target: date, tol_days: int = 10) -> Optional[QuarterValue]:
    c = [q for q in series.values() if abs((q.end - target).days) <= tol_days]
    return min(c, key=lambda q: abs((q.end - target).days)) if c else None


def periodic_filings(facts: Sequence[FinancialFact]) -> List[Tuple[str, date, date, str]]:
    """(accession, filed, period_end, form) of every periodic report that carries a tracked fact, ordered by filing date."""
    seen: Dict[str, Tuple[str, date, date, str]] = {}
    for f in facts:
        if f.form in PERIODIC_FORMS and f.group == "revenue":
            cur = seen.get(f.accession)
            if cur is None or f.end > cur[2]:
                seen[f.accession] = (f.accession, f.filed, f.end, f.form)
    return sorted(seen.values(), key=lambda t: (t[1], t[0]))
