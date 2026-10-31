"""Point-in-time fundamental features per (symbol, fiscal quarter). PURE (standard library only).

A feature of quarter t uses only quarters at or before t (the quarter itself, the previous quarter, the same quarter a year earlier and their predecessors); it never looks at later
quarters. Comparisons are year-on-year first (seasonality cancels), quarter-on-quarter is context only. Missing data is never filled: a feature whose inputs are missing, or whose
denominator is not positive, is None and the reason is listed in `notes`.

When each quarter became public is separate (`availability`): the earnings report on or after quarter end when the earnings calendar has one, otherwise a conservative assumed lag.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from inflection import config as C


@dataclass(frozen=True)
class QuarterData:
    quarter: date
    fiscal_year: Optional[int]
    fiscal_quarter: Optional[int]
    revenue: Optional[float]
    operating_income: Optional[float]
    net_income: Optional[float]
    operating_cash_flow: Optional[float]
    free_cash_flow: Optional[float]
    updated_at: Optional[str] = None


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def quarter_from_row(r: Mapping[str, Any]) -> QuarterData:
    q = r["quarter"]
    return QuarterData(q if isinstance(q, date) else date.fromisoformat(str(q)[:10]), r.get("fiscal_year"), r.get("fiscal_quarter"), _num(r.get("revenue")), _num(r.get("operating_income")),
                       _num(r.get("net_income")), _num(r.get("operating_cash_flow")), _num(r.get("free_cash_flow")), None if r.get("updated_at") is None else str(r["updated_at"]))


def _near(rows: Sequence[QuarterData], idx: int, days: int, tol: int) -> Optional[QuarterData]:
    """The earlier quarter whose end is `days` +- `tol` before rows[idx]'s (closest wins); only strictly earlier quarters are looked at."""
    target = rows[idx].quarter - timedelta(days=days)
    best, gap = None, tol + 1
    for j in range(idx - 1, -1, -1):
        g = abs((rows[j].quarter - target).days)
        if g <= tol and g < gap:
            best, gap = rows[j], g
        if (rows[idx].quarter - rows[j].quarter).days > days + tol + 40:
            break
    return best


def _ratio_growth(cur: Optional[float], base: Optional[float]) -> Optional[float]:
    if cur is None or base is None or cur <= 0 or base <= 0:
        return None
    return cur / base - 1.0


def availability(quarter_end: date, report_dates: Sequence[date]) -> Tuple[date, str]:
    """(date the figures were public, basis). The first earnings report in [quarter end + 5, + 130] days; otherwise quarter end + the assumed lag."""
    lo, hi = quarter_end + timedelta(days=C.AVAILABILITY_MIN_DAYS), quarter_end + timedelta(days=C.AVAILABILITY_MAX_DAYS)
    for d in sorted(report_dates):
        if lo <= d <= hi:
            return d, "earnings_calendar"
    return quarter_end + timedelta(days=C.ASSUMED_LAG_DAYS), f"assumed_lag_{C.ASSUMED_LAG_DAYS}d"


def compute_features(rows: Sequence[QuarterData], sector: Optional[str] = None) -> List[Dict[str, Any]]:
    """One feature dict per quarter, oldest first. Keys: quarter, fiscal_year, fiscal_quarter, metrics (floats or None), notes (why something is None), quality ('ok'|'partial')."""
    rs = sorted(rows, key=lambda r: r.quarter)
    non_std = sector in C.NON_STANDARD_SECTORS
    out: List[Dict[str, Any]] = []
    for i, q in enumerate(rs):
        py = _near(rs, i, C.YEAR_DAYS, C.YEAR_TOL)
        pq = _near(rs, i, C.QUARTER_DAYS, C.QUARTER_TOL)
        notes: List[str] = []
        m: Dict[str, Optional[float]] = {}

        m["revenue"], m["net_income"], m["operating_income"], m["free_cash_flow"] = q.revenue, q.net_income, q.operating_income, q.free_cash_flow
        m["revenue_py"], m["net_income_py"] = (py.revenue if py else None), (py.net_income if py else None)
        m["net_income_pq"] = pq.net_income if pq else None
        if py is None:
            notes.append("no_prior_year_quarter")
        if pq is None:
            notes.append("no_previous_quarter")

        m["rev_yoy"] = _ratio_growth(q.revenue, py.revenue if py else None)
        m["rev_qoq"] = _ratio_growth(q.revenue, pq.revenue if pq else None)
        # growth of the previous quarter (needs the quarter before it and ITS prior year): the acceleration base
        rev_yoy_prev = None
        if pq is not None:
            j = rs.index(pq)
            pq_py = _near(rs, j, C.YEAR_DAYS, C.YEAR_TOL)
            rev_yoy_prev = _ratio_growth(pq.revenue, pq_py.revenue if pq_py else None)
        m["rev_yoy_prev"] = rev_yoy_prev
        m["rev_accel_pp"] = None if m["rev_yoy"] is None or rev_yoy_prev is None else (m["rev_yoy"] - rev_yoy_prev) * 100.0
        if m["rev_yoy"] is not None and m["rev_yoy_prev"] is None:
            notes.append("no_growth_history_for_acceleration")
        inorganic = bool((m["rev_qoq"] is not None and m["rev_qoq"] > C.INORGANIC_REV_QOQ) or (m["rev_yoy"] is not None and m["rev_yoy"] > C.INORGANIC_REV_YOY))
        m["inorganic_suspect"] = 1.0 if inorganic else 0.0
        if inorganic:
            notes.append("revenue_jump_suspect_acquisition_or_reclassification")

        # operating margin and operating leverage (not meaningful for banks, insurers, REITs)
        if non_std:
            m["op_margin"] = m["op_margin_py"] = m["op_margin_yoy_bps"] = m["op_leverage"] = None
            notes.append("non_standard_reporting_sector_no_operating_metrics")
        else:
            opm = (q.operating_income / q.revenue) if (q.operating_income is not None and q.revenue and q.revenue > 0) else None
            opm_py = (py.operating_income / py.revenue) if (py and py.operating_income is not None and py.revenue and py.revenue > 0) else None
            m["op_margin"], m["op_margin_py"] = opm, opm_py
            m["op_margin_yoy_bps"] = None if opm is None or opm_py is None else (opm - opm_py) * 10000.0
            oi_g = _ratio_growth(q.operating_income, py.operating_income if py else None)
            m["op_leverage"] = None
            if oi_g is not None and m["rev_yoy"] is not None and m["rev_yoy"] >= 0.02:
                m["op_leverage"] = oi_g / m["rev_yoy"]
            elif q.operating_income is not None and py and py.operating_income is not None and (q.operating_income <= 0 or py.operating_income <= 0):
                notes.append("operating_leverage_undefined_non_positive_operating_income")

        # free cash flow (not meaningful for REITs / financials as reported)
        if non_std:
            m["fcf_margin"] = m["fcf_margin_py"] = m["fcf_margin_yoy_pp"] = None
            m["free_cash_flow_py"] = None
        else:
            fcf_m = (q.free_cash_flow / q.revenue) if (q.free_cash_flow is not None and q.revenue and q.revenue > 0) else None
            fcf_m_py = (py.free_cash_flow / py.revenue) if (py and py.free_cash_flow is not None and py.revenue and py.revenue > 0) else None
            m["fcf_margin"], m["fcf_margin_py"] = fcf_m, fcf_m_py
            m["fcf_margin_yoy_pp"] = None if fcf_m is None or fcf_m_py is None else (fcf_m - fcf_m_py) * 100.0
            m["free_cash_flow_py"] = py.free_cash_flow if py else None
            if q.free_cash_flow is None:
                notes.append("free_cash_flow_missing")

        # profit swing and cash conversion
        m["ni_swing_pct_rev"] = None
        if q.net_income is not None and py and py.net_income is not None and q.revenue and q.revenue > 0:
            m["ni_swing_pct_rev"] = (q.net_income - py.net_income) / q.revenue
        m["cash_conversion"] = None
        if q.operating_cash_flow is not None and q.net_income is not None and q.net_income > 0:
            m["cash_conversion"] = q.operating_cash_flow / q.net_income
        elif q.net_income is not None and q.net_income <= 0:
            notes.append("cash_conversion_undefined_non_positive_net_income")
        m["operating_cash_flow"] = q.operating_cash_flow

        needed = ["revenue", "operating_income", "net_income"]
        missing = [k for k in needed if m.get(k) is None]
        if missing:
            notes.append("missing:" + ",".join(missing))
        out.append({"quarter": q.quarter, "fiscal_year": q.fiscal_year, "fiscal_quarter": q.fiscal_quarter, "updated_at": q.updated_at, "metrics": m,
                    "notes": notes, "quality": C.DATA_QUALITY_OK if not missing and py is not None else C.DATA_QUALITY_PARTIAL})
    return out
