"""SEC XBRL company facts -> verbatim facts -> normalized quarterly figures, with provenance. PURE: stdlib only; no network, clock, file, environment or database.

Source: `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`. Every value there names the filing it came from (`accn`), the form, the period (`start`/`end`)
and the date it was filed (`filed`), so every figure below can say where it came from and when it became public, and a view "as known on date D" can be
rebuilt exactly (`asof`): a restatement filed after D is invisible.

What is normalized (a company's quarter is one row per metric and period end):
  income statement (USD, EPS in USD/share)  revenue, gross_profit, operating_income, net_income, eps_diluted
  cash flow (USD)                           operating_cash_flow, capex        -> free_cash_flow = operating_cash_flow - capex (basis `computed`)
  balance sheet (USD, instants)             total_assets, total_liabilities, total_equity, cash

How a quarter is obtained (the `basis`):
  reported              a value the filing itself states for a ~3-month period (10-Q, and the Q1 of a cash-flow statement)
  ytd_difference        year-to-date cash flow (6 and 9 months) minus the previous year-to-date, which is how filers report it
  annual_less_quarters  Q4 = the 10-K annual figure minus the year-to-date (or minus the three reported quarters). Never for EPS.
  instant               a balance-sheet value at the period end
  computed              arithmetic on two other rows of the same period (free cash flow)
Nothing is estimated or filled: when a component is missing the quarter is simply absent. EPS is only ever the reported figure (it cannot be differenced).

Restatements and amendments: for a given period the value from the LATEST filing wins (a 10-K/A or a later filing that re-reports a prior period
supersedes it); the earlier values stay in the raw facts, so nothing is lost and `asof` shows what was known before.
Fiscal labels: XBRL's fy/fp describe the FILING, not a comparative period it also carries, so a quarter gets a fiscal year/quarter only from the
filing for which that period is the current one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "sec_quarter_v1"
PERIODIC_FORMS = ("10-Q", "10-Q/A", "10-K", "10-K/A", "10-KT", "10-KT/A", "10-QT")

INCOME: Mapping[str, Tuple[str, ...]] = {          # first tag that a filing reports for a period wins
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
}
CASHFLOW: Mapping[str, Tuple[str, ...]] = {
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
}
INSTANT: Mapping[str, Tuple[str, ...]] = {
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
}
TAGS: Mapping[str, Tuple[str, ...]] = {**INCOME, **CASHFLOW, **INSTANT}
UNIT: Mapping[str, str] = {m: ("USD/shares" if m == "eps_diluted" else "USD") for m in TAGS}
KIND: Mapping[str, str] = {**{m: "income" for m in INCOME}, **{m: "cashflow" for m in CASHFLOW}, **{m: "instant" for m in INSTANT}}

QUARTER_DAYS = (80, 100)
YEAR_DAYS = (350, 380)
_FP_QUARTER = {"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4}


class SecStatementError(ValueError):
    pass


@dataclass(frozen=True)
class RawFact:
    metric: str
    tag: str
    unit: str
    value: float
    start: date              # instants: start == end
    end: date
    fy: Optional[int]
    fp: Optional[str]
    form: str
    accession: str
    filed: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass(frozen=True)
class QuarterRow:
    period_end: date
    metric: str
    value: float
    unit: str
    basis: str
    fiscal_year: Optional[int]
    fiscal_quarter: Optional[int]
    accession: str
    filed: date
    form: str


def _d(s: Any) -> Optional[date]:
    return date.fromisoformat(s[:10]) if isinstance(s, str) and len(s) >= 10 else None


def parse_companyfacts(doc: Mapping[str, Any], cik: int) -> List[RawFact]:
    """Verbatim facts for the tracked metrics from periodic reports. A document for another CIK is refused whole (no cross-company attachment).
    Unusable entries (no date, no filing, non-numeric) are skipped, never repaired."""
    try:
        doc_cik = int(str(doc.get("cik", "")).lstrip("0") or "0")
    except ValueError:
        doc_cik = 0
    if doc_cik != cik:
        raise SecStatementError(f"companyfacts document is for CIK {doc.get('cik')!r}, but CIK {cik} was requested; refused as a whole")
    gaap = (doc.get("facts") or {}).get("us-gaap") or {}
    best: Dict[Tuple[str, date, date, str], Tuple[int, RawFact]] = {}
    for metric, tags in TAGS.items():
        for rank, tag in enumerate(tags):
            entries = ((gaap.get(tag) or {}).get("units") or {}).get(UNIT[metric]) or []
            for e in entries:
                end, filed, val, accn, form = _d(e.get("end")), _d(e.get("filed")), e.get("val"), e.get("accn"), e.get("form")
                if end is None or filed is None or not isinstance(val, (int, float)) or isinstance(val, bool) or not accn or form not in PERIODIC_FORMS:
                    continue
                start = _d(e.get("start"))
                if KIND[metric] == "instant":
                    start = end
                elif start is None or start >= end:
                    continue
                f = RawFact(metric, tag, UNIT[metric], float(val), start, end, e.get("fy"), e.get("fp"), str(form), str(accn), filed)
                key = (metric, start, end, f.accession)
                cur = best.get(key)
                if cur is None or rank < cur[0]:      # the filing's highest-priority tag for this period
                    best[key] = (rank, f)
    return sorted((f for _, f in best.values()), key=lambda f: (f.metric, f.end, f.start, f.filed, f.accession))


def _latest(facts: Iterable[RawFact]) -> RawFact:
    return max(facts, key=lambda f: (f.filed, f.accession))


def _labels(facts: Sequence[RawFact]) -> Dict[date, Tuple[Optional[int], Optional[int]]]:
    """period end -> (fiscal year, fiscal quarter), taken from the earliest filing for which that period is the CURRENT one (XBRL's fy/fp describe the
    filing, so a comparative inside a later filing says nothing about its own period). A period no filing treats as current gets no label."""
    cur: Dict[str, Tuple[date, Optional[int], Optional[str], date]] = {}
    for f in facts:
        c = cur.get(f.accession)
        if c is None or f.end > c[0]:
            cur[f.accession] = (f.end, f.fy, f.fp, f.filed)
        elif f.end == c[0] and c[1] is None and f.fy is not None:
            cur[f.accession] = (f.end, f.fy, f.fp, f.filed)
    out: Dict[date, Tuple[Optional[int], Optional[int]]] = {}
    for accn, (end, fy, fp, filed) in sorted(cur.items(), key=lambda kv: (kv[1][3], kv[0])):
        if end not in out and fy is not None:
            out[end] = (fy, _FP_QUARTER.get(fp or ""))
    return out


def _fiscal(period_end: date, labels: Mapping[date, Tuple[Optional[int], Optional[int]]]) -> Tuple[Optional[int], Optional[int]]:
    return labels.get(period_end, (None, None))


def _is_q(days: int) -> bool:
    return QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]


def _flow_rows(metric: str, facts: Sequence[RawFact], labels) -> List[QuarterRow]:
    """Quarterly values of one duration metric: reported quarters, year-to-date differences, and Q4 from the annual figure."""
    by_period: Dict[Tuple[date, date], RawFact] = {}
    groups: Dict[Tuple[date, date], List[RawFact]] = {}
    for f in facts:
        groups.setdefault((f.start, f.end), []).append(f)
    for k, fs in groups.items():
        by_period[k] = _latest(fs)

    rows: Dict[date, QuarterRow] = {}

    def add(end: date, value: float, basis: str, src: Sequence[RawFact]) -> None:
        newest = _latest(src)
        fy, fq = _fiscal(end, labels)
        rows[end] = QuarterRow(end, metric, value, UNIT[metric], basis, fy, fq, newest.accession, max(s.filed for s in src), newest.form)

    for (s, e), f in by_period.items():                    # 1. reported ~3-month values
        if _is_q(f.days):
            add(e, f.value, "reported", [f])
    if metric == "eps_diluted":                            # EPS cannot be differenced
        return sorted(rows.values(), key=lambda r: r.period_end)

    cum: Dict[date, Dict[date, RawFact]] = {}              # 2. cumulative values from a common start: Q1 (reported), 6M, 9M, FY
    for (s, e), f in by_period.items():
        if _is_q(f.days) or 170 <= f.days <= 195 or 260 <= f.days <= 285 or (YEAR_DAYS[0] <= f.days <= YEAR_DAYS[1] and f.form.startswith("10-K")):
            cum.setdefault(s, {})[e] = f
    for s, ends in cum.items():
        ordered = sorted(ends)
        for prev, nxt in zip(ordered, ordered[1:]):
            gap = (nxt - prev).days
            if _is_q(gap) and nxt not in rows:
                basis = "annual_less_quarters" if ends[nxt].days >= YEAR_DAYS[0] else "ytd_difference"
                add(nxt, ends[nxt].value - ends[prev].value, basis, [ends[prev], ends[nxt]])
    for (s, e), f in by_period.items():                    # 3. Q4 from the annual figure and the three reported quarters (when no 9-month figure exists)
        if YEAR_DAYS[0] <= f.days <= YEAR_DAYS[1] and f.form.startswith("10-K") and e not in rows:
            prior = sorted((r for r in rows.values() if e - timedelta(days=330) < r.period_end < e - timedelta(days=60)), key=lambda r: r.period_end)
            if len(prior) == 3 and all(abs((prior[i + 1].period_end - prior[i].period_end).days - 91) <= 12 for i in range(2)):
                qfacts = {k[1]: v for k, v in by_period.items() if _is_q(v.days)}
                add(e, f.value - sum(r.value for r in prior), "annual_less_quarters", [f] + [qfacts[r.period_end] for r in prior if r.period_end in qfacts])
    return sorted(rows.values(), key=lambda r: r.period_end)


def _instant_rows(metric: str, facts: Sequence[RawFact], labels) -> List[QuarterRow]:
    groups: Dict[date, List[RawFact]] = {}
    for f in facts:
        groups.setdefault(f.end, []).append(f)
    out = []
    for e, fs in groups.items():
        f = _latest(fs)
        fy, fq = _fiscal(e, labels)
        out.append(QuarterRow(e, metric, f.value, UNIT[metric], "instant", fy, fq, f.accession, f.filed, f.form))
    return sorted(out, key=lambda r: r.period_end)


def normalize(facts: Sequence[RawFact], asof: Optional[date] = None) -> List[QuarterRow]:
    """Normalized quarterly rows known on `asof` (everything when None), ordered by metric then period end."""
    fs = [f for f in facts if asof is None or f.filed <= asof]
    labels = _labels(fs)
    out: List[QuarterRow] = []
    for metric in TAGS:
        mf = [f for f in fs if f.metric == metric]
        if not mf:
            continue
        out.extend(_instant_rows(metric, mf, labels) if KIND[metric] == "instant" else _flow_rows(metric, mf, labels))
    by = {(r.metric, r.period_end): r for r in out}
    for (m, e), r in list(by.items()):                     # free cash flow: only when both parts exist for the same period
        if m == "operating_cash_flow" and ("capex", e) in by:
            c = by[("capex", e)]
            newest = r if (r.filed, r.accession) >= (c.filed, c.accession) else c
            fy, fq = (r.fiscal_year, r.fiscal_quarter) if newest is r else (c.fiscal_year, c.fiscal_quarter)
            out.append(QuarterRow(e, "free_cash_flow", r.value - c.value, "USD", "computed", fy, fq, newest.accession, newest.filed, newest.form))
    return sorted(out, key=lambda r: (r.metric, r.period_end))
