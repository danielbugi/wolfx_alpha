"""Deterministic event detection and the explainable inflection score. PURE.

Every event carries its numbers (current and previous values), a strength 0..100 (50 at the detection threshold, 100 at the declared "strong" value), a severity, and plain-language
explanation lines built only from those numbers. The inflection score is a transparent weighted sum of the event strengths (see config.SCORE_WEIGHTS); its components are stored with
every event so the screen can show exactly where a score came from. Nothing here predicts anything.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from inflection import config as C


def lin(x: float, t_min: float, t_strong: float) -> float:
    """50 at the threshold, 100 at the strong value, linear, clamped to 0..100."""
    if t_strong == t_min:
        return 100.0 if x >= t_min else 0.0
    return max(0.0, min(100.0, 50.0 + 50.0 * (x - t_min) / (t_strong - t_min)))


def money(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    s = f"{a / 1e9:.2f}B" if a >= 1e9 else f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.0f}K"
    return f"-${s}" if v < 0 else f"${s}"


def pct(v: Optional[float], nd: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:+.{nd}f}%"


def _event(kind: str, strength: float, metrics: Mapping[str, Any], lines: Sequence[str]) -> Dict[str, Any]:
    s = round(max(0.0, min(100.0, strength)), 1)
    return {"event_type": kind, "strength": s, "severity": C.severity(s, C.SEVERITY_STRENGTH), "metrics": dict(metrics), "explanation": list(lines)}


def detect_base_events(m: Mapping[str, Optional[float]], sector: Optional[str] = None) -> List[Dict[str, Any]]:
    """The five fundamentals-only event types for one quarter's metrics (features.compute_features -> 'metrics')."""
    out: List[Dict[str, Any]] = []
    if m.get("inorganic_suspect"):
        return out                                                         # a suspect revenue jump is not an inflection: recorded as a quality note, never an event
    non_std = sector in C.NON_STANDARD_SECTORS
    cc = m.get("cash_conversion")
    low_cc = cc is not None and cc < C.LOW_CASH_CONVERSION

    t = C.THRESHOLDS[C.REVENUE_ACCELERATION]
    y, a, qoq = m.get("rev_yoy"), m.get("rev_accel_pp"), m.get("rev_qoq")
    if y is not None and a is not None and t["rev_yoy_min"] <= y <= t["rev_yoy_max"] and a >= t["accel_pp_min"] and (qoq is None or qoq > t["qoq_min"]):
        out.append(_event(C.REVENUE_ACCELERATION, lin(a, t["accel_pp_min"], t["accel_pp_strong"]),
                          {"rev_yoy": y, "rev_yoy_prev": m.get("rev_yoy_prev"), "rev_accel_pp": a, "revenue": m.get("revenue"), "revenue_py": m.get("revenue_py"), "rev_qoq": qoq},
                          [f"Revenue grew {pct(y)} year over year, up from {pct(m.get('rev_yoy_prev'))} the quarter before: an acceleration of {a:+.1f} percentage points.",
                           f"Revenue {money(m.get('revenue'))} versus {money(m.get('revenue_py'))} a year earlier."]))

    t = C.THRESHOLDS[C.MARGIN_EXPANSION]
    bps, opm = m.get("op_margin_yoy_bps"), m.get("op_margin")
    if not non_std and bps is not None and opm is not None and bps >= t["opm_yoy_bps_min"] and opm > t["opm_min"] and (m.get("rev_yoy") is None or m["rev_yoy"] >= t["rev_yoy_min"]):
        out.append(_event(C.MARGIN_EXPANSION, lin(bps, t["opm_yoy_bps_min"], t["opm_yoy_bps_strong"]),
                          {"op_margin": opm, "op_margin_py": m.get("op_margin_py"), "op_margin_yoy_bps": bps, "rev_yoy": m.get("rev_yoy")},
                          [f"Operating margin rose to {opm * 100:.1f}% from {(m.get('op_margin_py') or 0) * 100:.1f}% a year earlier ({bps:+.0f} basis points).",
                           f"Operating income {money(m.get('operating_income'))} on revenue {money(m.get('revenue'))}."]))

    t = C.THRESHOLDS[C.OPERATING_LEVERAGE]
    lev = m.get("op_leverage")
    if not non_std and lev is not None and lev >= t["leverage_min"] and m.get("rev_yoy") is not None and m["rev_yoy"] >= t["rev_yoy_min"]:
        out.append(_event(C.OPERATING_LEVERAGE, lin(lev, t["leverage_min"], t["leverage_strong"]),
                          {"op_leverage": lev, "rev_yoy": m.get("rev_yoy"), "operating_income": m.get("operating_income")},
                          [f"Operating income grew {lev:.1f} times as fast as revenue (revenue {pct(m.get('rev_yoy'))} year over year).",
                           "Costs are growing more slowly than sales."]))

    t = C.THRESHOLDS[C.PROFITABILITY_TURNAROUND]
    ni, ni_py, ni_pq, sw = m.get("net_income"), m.get("net_income_py"), m.get("net_income_pq"), m.get("ni_swing_pct_rev")
    oi_ok = non_std or (m.get("operating_income") is not None and m["operating_income"] > 0)
    if ni is not None and ni > 0 and ni_py is not None and ni_py <= 0 and oi_ok and sw is not None and sw >= t["swing_pct_rev_min"]:
        s = lin(sw, t["swing_pct_rev_min"], t["swing_pct_rev_strong"])
        seq = ni_pq is not None and ni_pq <= 0
        if seq:
            s += C.SEQUENTIAL_BONUS
        if cc is not None and low_cc:
            s -= C.CASH_CONVERSION_PENALTY
        lines = [f"Net income turned positive: {money(ni)} this quarter versus {money(ni_py)} a year earlier (a swing of {sw * 100:.1f}% of revenue)."]
        if seq:
            lines.append(f"It was also a loss in the previous quarter ({money(ni_pq)}), so this is not only a year-ago comparison.")
        if low_cc:
            lines.append(f"Caution: operating cash flow was only {cc:.2f} times net income.")
        out.append(_event(C.PROFITABILITY_TURNAROUND, s, {"net_income": ni, "net_income_py": ni_py, "net_income_pq": ni_pq, "ni_swing_pct_rev": sw, "cash_conversion": cc}, lines))

    t = C.THRESHOLDS[C.FCF_TURNAROUND]
    f, f_py, fm = m.get("free_cash_flow"), m.get("free_cash_flow_py"), m.get("fcf_margin_yoy_pp")
    if not non_std and f is not None and f > 0 and f_py is not None and f_py <= 0 and fm is not None and fm >= t["fcf_margin_yoy_pp_min"]:
        s = lin(fm, t["fcf_margin_yoy_pp_min"], t["fcf_margin_yoy_pp_strong"])
        if low_cc:
            s -= C.CASH_CONVERSION_PENALTY
        lines = [f"Free cash flow turned positive: {money(f)} this quarter versus {money(f_py)} a year earlier (margin {fm:+.1f} percentage points)."]
        if low_cc:
            lines.append(f"Caution: operating cash flow was only {cc:.2f} times net income.")
        out.append(_event(C.FCF_TURNAROUND, s, {"free_cash_flow": f, "free_cash_flow_py": f_py, "fcf_margin": m.get("fcf_margin"), "fcf_margin_yoy_pp": fm, "cash_conversion": cc}, lines))
    return out


def score(base_events: Sequence[Mapping[str, Any]], m: Mapping[str, Optional[float]]) -> Dict[str, Any]:
    """The inflection score 0..100 and its components (what each event type and the quality bonus contributed)."""
    comps: List[Dict[str, Any]] = []
    total = 0.0
    by = {e["event_type"]: e for e in base_events}
    for kind, w in C.SCORE_WEIGHTS.items():
        s = by[kind]["strength"] if kind in by else 0.0
        c = w * s
        total += c
        comps.append({"component": kind, "strength": s, "weight": w, "contribution": round(c, 2)})
    cc, ni = m.get("cash_conversion"), m.get("net_income")
    bonus = C.QUALITY_BONUS_WEIGHT * 100.0 if (cc is not None and cc >= C.GOOD_CASH_CONVERSION and ni is not None and ni > 0 and base_events) else 0.0
    total += bonus
    comps.append({"component": "cash_conversion_quality", "strength": 100.0 if bonus else 0.0, "weight": C.QUALITY_BONUS_WEIGHT, "contribution": round(bonus, 2)})
    return {"score": round(min(100.0, total), 1), "components": comps, "n_event_types": len(base_events)}


def limited_repricing(sc: Mapping[str, Any], m: Mapping[str, Optional[float]], excess_return: Optional[float], base_events: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The composite event: a real inflection the market has not priced in. `excess_return` = the stock's return over the repricing window minus its sector's (None when the window has
    not completed: the event is then not yet knowable and is NOT emitted)."""
    p = C.LIMITED_REPRICING_PARAMS
    if excess_return is None or sc["score"] < p["score_min"] or sc["n_event_types"] < p["min_event_types"] or excess_return > p["max_excess_return"]:
        return None
    types = ", ".join(C.LABELS[e["event_type"]].lower() for e in base_events)
    e = _event(C.LIMITED_REPRICING, sc["score"], {"inflection_score": sc["score"], "event_types": [e["event_type"] for e in base_events], "excess_return_window": excess_return,
                                                  "window_sessions": p["reprice_sessions"]},
               [f"Several fundamentals improved at once ({types}), inflection score {sc['score']:.0f}.",
                f"Over the {p['reprice_sessions']} sessions after the reaction window the stock returned {pct(excess_return)} relative to its sector: little of the improvement shows in the price yet."])
    e["severity"] = C.severity(sc["score"], C.SEVERITY_SCORE)
    return e
