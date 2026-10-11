"""Financial anomaly detection and recurring-vs-one-time classification from SEC XBRL facts. PURE: stdlib only.

INTERPRETATION, separate from the facts (`sec_financials`) and from the filing's own identity (`filing_contract`): versioned rules, deterministic, no model. Thresholds are declared here, a priori,
and were not tuned on price outcomes. Every result carries its lineage: the accessions the numbers came from and the as-of date, so "what was known at the decision point" is reproducible.

Rules (v1)
  revenue_acceleration         revenue YoY >= 20% AND at least 10 percentage points above the previous quarter's YoY (both as known on the filing date)
  net_income_surge             net income YoY >= 50% on a positive prior-year base, or a swing from a loss to a profit (`net_income_turnaround`)
  operating_margin_expansion   operating margin up >= 3 points YoY with positive operating income
Nature (only when at least one rule fired)
  recurring_operating          revenue YoY >= 10% AND (operating income YoY >= 25% OR operating margin up >= 2 points): the improvement is visible in operations
  one_time_suspected           net income improved far more than operations: NI YoY exceeds operating-income YoY by >= 50 points, or net margin up >= 8 points while operating margin moved < 2 points
  unclassified                 otherwise
This is a screen for human/ML follow-up, not a verdict: a one-time label means "net income moved without a matching operating move", not that a specific item was identified in the filing text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence

from market_intelligence import sec_financials as S

CLASSIFIER = "financial_anomaly_rules"
VERSION = "v1"
REV_YOY_MIN, REV_ACCEL_MIN = 0.20, 0.10
NI_YOY_MIN = 0.50
OP_MARGIN_UP_MIN = 0.03
RECURRING_REV_MIN, RECURRING_OI_YOY_MIN, RECURRING_OP_MARGIN_UP = 0.10, 0.25, 0.02
ONE_TIME_GAP, ONE_TIME_NET_MARGIN_UP, ONE_TIME_OP_MARGIN_MAX = 0.50, 0.08, 0.02


@dataclass(frozen=True)
class QuarterMetrics:
    accession: str
    filed: date
    form: str
    period_end: date
    metrics: Mapping[str, Optional[float]]
    lineage: Mapping[str, str]            # measure -> accession the current value came from
    missing: Sequence[str] = ()


@dataclass(frozen=True)
class Anomaly:
    accession: str
    period_end: date
    filed: date
    rules: Sequence[str]
    nature: str
    metrics: Mapping[str, Optional[float]]
    classifier: str = CLASSIFIER
    classifier_version: str = VERSION


def _year_ago(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:                                  # 29 February
        return d.replace(year=d.year - 1, day=28)


def _yoy(cur: Optional[float], prior: Optional[float]) -> Optional[float]:
    return None if cur is None or prior is None or prior <= 0 else cur / prior - 1


def _margin(num: Optional[float], den: Optional[float]) -> Optional[float]:
    return None if num is None or den is None or den <= 0 else num / den


def quarter_metrics(facts: Sequence[S.FinancialFact], accession: str, filed: date) -> Optional[QuarterMetrics]:
    """Metrics for the quarter reported by `accession`, using ONLY values filed on or before `filed` (point in time)."""
    series = {g: S.quarter_series(facts, g, filed) for g in S.GROUPS}
    rev = {q.end: q for q in series["revenue"].values() if q.accession == accession}
    if not rev:
        return None
    end = max(rev)
    cur: Dict[str, Optional[float]] = {}
    lineage: Dict[str, str] = {}
    missing: List[str] = []
    for g in ("revenue", "net_income", "operating_income"):
        q = series[g].get(end)
        cur[g] = q.value if q else None
        if q:
            lineage[g] = q.accession
        else:
            missing.append(g)
    target_py = _year_ago(end)
    py: Dict[str, Optional[float]] = {}
    for g in ("revenue", "net_income", "operating_income"):
        q = S.nearest(series[g], target_py, 12)
        py[g] = q.value if q else None
    # previous quarter and ITS prior-year quarter, as known on the same date
    prior_q = [q for q in series["revenue"].values() if 70 <= (end - q.end).days <= 110]
    prev_yoy = None
    if prior_q:
        pq = max(prior_q, key=lambda q: q.end)
        pq_py = S.nearest(series["revenue"], _year_ago(pq.end), 12)
        prev_yoy = _yoy(pq.value, pq_py.value if pq_py else None)
    rev_yoy = _yoy(cur["revenue"], py["revenue"])
    om, om_py = _margin(cur["operating_income"], cur["revenue"]), _margin(py["operating_income"], py["revenue"])
    nm, nm_py = _margin(cur["net_income"], cur["revenue"]), _margin(py["net_income"], py["revenue"])
    ni_py = py["net_income"]
    metrics: Dict[str, Optional[float]] = {
        "revenue": cur["revenue"], "revenue_prior_year": py["revenue"], "revenue_yoy": rev_yoy, "revenue_yoy_prev_quarter": prev_yoy,
        "revenue_acceleration": None if rev_yoy is None or prev_yoy is None else rev_yoy - prev_yoy,
        "net_income": cur["net_income"], "net_income_prior_year": ni_py, "net_income_yoy": _yoy(cur["net_income"], ni_py),
        "net_income_turnaround": 1.0 if (ni_py is not None and cur["net_income"] is not None and ni_py <= 0 < cur["net_income"]) else 0.0,
        "operating_income": cur["operating_income"], "operating_income_prior_year": py["operating_income"], "operating_income_yoy": _yoy(cur["operating_income"], py["operating_income"]),
        "operating_margin": om, "operating_margin_change": None if om is None or om_py is None else om - om_py,
        "net_margin": nm, "net_margin_change": None if nm is None or nm_py is None else nm - nm_py,
    }
    form = next((q.form for q in rev.values()), "10-Q")
    return QuarterMetrics(accession, filed, form, end, metrics, lineage, tuple(missing))


def detect(m: QuarterMetrics) -> Optional[Anomaly]:
    x = m.metrics
    rules: List[str] = []
    if x["revenue_yoy"] is not None and x["revenue_acceleration"] is not None and x["revenue_yoy"] >= REV_YOY_MIN and x["revenue_acceleration"] >= REV_ACCEL_MIN:
        rules.append("revenue_acceleration")
    if x["net_income_turnaround"] == 1.0:
        rules.append("net_income_turnaround")
    elif x["net_income_yoy"] is not None and x["net_income_yoy"] >= NI_YOY_MIN and (x["net_income"] or 0) > 0:
        rules.append("net_income_surge")
    if x["operating_margin_change"] is not None and x["operating_margin_change"] >= OP_MARGIN_UP_MIN and (x["operating_income"] or 0) > 0:
        rules.append("operating_margin_expansion")
    if not rules:
        return None
    return Anomaly(m.accession, m.period_end, m.filed, tuple(rules), nature(x), x)


def nature(x: Mapping[str, Optional[float]]) -> str:
    rev, oi_yoy, om_chg = x["revenue_yoy"], x["operating_income_yoy"], x["operating_margin_change"]
    ni_yoy, nm_chg = x["net_income_yoy"], x["net_margin_change"]
    if rev is not None and rev >= RECURRING_REV_MIN and ((oi_yoy is not None and oi_yoy >= RECURRING_OI_YOY_MIN) or (om_chg is not None and om_chg >= RECURRING_OP_MARGIN_UP)):
        return "recurring_operating"
    ni_jump = x["net_income_turnaround"] == 1.0 or (ni_yoy is not None and oi_yoy is not None and ni_yoy - oi_yoy >= ONE_TIME_GAP)
    margin_gap = nm_chg is not None and nm_chg >= ONE_TIME_NET_MARGIN_UP and (om_chg is None or abs(om_chg) < ONE_TIME_OP_MARGIN_MAX)
    if ni_jump and (oi_yoy is None or oi_yoy < RECURRING_OI_YOY_MIN) or margin_gap:
        return "one_time_suspected"
    return "unclassified"
