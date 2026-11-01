"""Structured reconciliation of two inflection event sets (SEC-sourced vs yfinance-sourced) on the company-quarters both cover. PURE (standard library only).

For every event that exists in one source and not in the other it names ONE cause, from an ordered list, so the counts add up and every class has concrete examples:

    quarter_missing_in_other   the other source has no feature row for that company-quarter at all (coverage, not disagreement)
    input_missing_in_other     an input the event needs is present in one source and absent in the other (e.g. a capex tag the SEC mapping does not carry)
    input_differs              the same input exists in both but differs by more than TOLERANCE (field definition / mapping, e.g. free cash flow)
    history_coverage           the quarter's own inputs agree, but only one source holds the prior-year / previous-quarter it compares against (yfinance keeps 5-6 quarters)
    history_differs            the quarter's own inputs agree, the prior-year / previous-quarter values the event compares against differ by more than TOLERANCE
    threshold_boundary         all inputs agree within TOLERANCE, but the deciding metric is within BOUNDARY of its threshold in one source: a hair either side of the line
    composite_dependency       the composite event (improvement with limited repricing) depends on the base events and on prices; it differs because those do
    unexplained                none of the above: listed so it can be investigated, never hidden

Thresholds are NOT tuned here, and the engine is never changed to increase agreement: this only explains differences. Availability dates are compared separately (they are
different by construction: SEC = filing acceptance, yfinance = earnings-report date).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from inflection import config as C

TOLERANCE = 0.01          # inputs within 1% (relative) are "the same figure"
BOUNDARY = 0.10           # a deciding metric within 10% (relative) of its threshold counts as a boundary case

CAUSES = ("quarter_missing_in_other", "input_missing_in_other", "input_differs", "history_coverage", "history_differs", "threshold_boundary", "composite_dependency", "unexplained")

# per event type: (current-quarter inputs, history inputs (prior-year / previous-quarter), deciding metric, its threshold)
INPUTS: Mapping[str, Tuple[Tuple[str, ...], Tuple[str, ...], str, float]] = {
    C.REVENUE_ACCELERATION: (("revenue", "rev_yoy"), ("revenue_py", "rev_yoy_prev"), "rev_accel_pp", C.THRESHOLDS[C.REVENUE_ACCELERATION]["accel_pp_min"]),
    C.MARGIN_EXPANSION: (("op_margin", "operating_income", "revenue"), ("op_margin_py",), "op_margin_yoy_bps", C.THRESHOLDS[C.MARGIN_EXPANSION]["opm_yoy_bps_min"]),
    C.OPERATING_LEVERAGE: (("operating_income", "revenue", "rev_yoy"), ("op_margin_py", "revenue_py"), "op_leverage", C.THRESHOLDS[C.OPERATING_LEVERAGE]["leverage_min"]),
    C.PROFITABILITY_TURNAROUND: (("net_income", "revenue"), ("net_income_py", "net_income_pq"), "ni_swing_pct_rev", C.THRESHOLDS[C.PROFITABILITY_TURNAROUND]["swing_pct_rev_min"]),
    C.FCF_TURNAROUND: (("free_cash_flow", "revenue"), ("free_cash_flow_py",), "fcf_margin_yoy_pp", C.THRESHOLDS[C.FCF_TURNAROUND]["fcf_margin_yoy_pp_min"]),
}


def _rel(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    d = max(abs(a), abs(b))
    return 0.0 if d == 0 else abs(a - b) / d


def _compare(keys: Iterable[str], x: Mapping[str, Any], y: Mapping[str, Any]) -> Tuple[List[str], List[Tuple[str, Any, Any]]]:
    """(keys present in exactly one source, [(key, a, b)] present in both and differing by more than TOLERANCE)."""
    missing, differs = [], []
    for k in keys:
        a, b = x.get(k), y.get(k)
        if (a is None) != (b is None):
            missing.append(k)
        elif a is not None and (_rel(a, b) or 0.0) > TOLERANCE:
            differs.append((k, a, b))
    return missing, differs


def classify(event_type: str, feat_a: Optional[Mapping[str, Any]], feat_b: Optional[Mapping[str, Any]]) -> Tuple[str, str]:
    """(cause, one-line detail) for an event present in source A's feature row `feat_a` and absent from B's `feat_b` (or the reverse; call it with the sides in either order).
    Both arguments are the `metrics` dicts of the feature rows, or None when that source has no row for the quarter."""
    if feat_a is None or feat_b is None:
        return "quarter_missing_in_other", "only one source has a feature row for this company-quarter"
    if event_type == C.LIMITED_REPRICING:
        return "composite_dependency", "composite event: depends on the base events and on prices after publication"
    cur, hist, decide, thr = INPUTS[event_type]
    missing, differs = _compare(cur, feat_a, feat_b)
    if missing:
        return "input_missing_in_other", "present in only one source: " + ", ".join(missing)
    if differs:
        return "input_differs", "; ".join(f"{k}: {a:.6g} vs {b:.6g}" for k, a, b in differs)
    missing_h, differs_h = _compare(hist, feat_a, feat_b)
    if missing_h:
        return "history_coverage", "history present in only one source: " + ", ".join(missing_h)
    if differs_h:
        return "history_differs", "; ".join(f"{k}: {a:.6g} vs {b:.6g}" for k, a, b in differs_h)
    for side in (feat_a, feat_b):
        v = side.get(decide)
        if v is not None and thr != 0 and abs(v - thr) / abs(thr) <= BOUNDARY:
            return "threshold_boundary", f"{decide} = {v:.6g}, {abs(v - thr) / abs(thr) * 100:.1f}% from the threshold {thr:g}"
    # inputs agree within tolerance but the DECIDING metric straddles the threshold only through rounding of the inputs: still a boundary effect when the two values bracket it
    va, vb = feat_a.get(decide), feat_b.get(decide)
    if va is not None and vb is not None and (va - thr) * (vb - thr) < 0:
        return "threshold_boundary", f"{decide}: {va:.6g} vs {vb:.6g} bracket the threshold {thr:g}"
    return "unexplained", f"{decide}: {va} vs {vb}"


def reconcile(features_a: Mapping[Tuple[str, date], Mapping[str, Any]], features_b: Mapping[Tuple[str, date], Mapping[str, Any]],
              events_a: Mapping[Tuple[str, date], Sequence[str]], events_b: Mapping[Tuple[str, date], Sequence[str]], max_examples: int = 3) -> Dict[str, Any]:
    """Compare two sources over the company-quarters BOTH have a feature row for (`shared`) and report agreement and the causes of every difference.

    features_*: {(symbol, quarter_end): metrics}; events_*: {(symbol, quarter_end): [event types]}. Differences are counted per event type and cause."""
    shared = sorted(set(features_a) & set(features_b))
    by_type: Dict[str, Dict[str, Any]] = {}
    examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    cause_total: Counter = Counter()
    for key in shared:
        ea, eb = set(events_a.get(key, ())), set(events_b.get(key, ()))
        for t in sorted(ea | eb):
            row = by_type.setdefault(t, {"both": 0, "a_only": 0, "b_only": 0, "causes": Counter()})
            if t in ea and t in eb:
                row["both"] += 1
                continue
            side = "a_only" if t in ea else "b_only"
            row[side] += 1
            cause, detail = classify(t, features_a[key], features_b[key])
            row["causes"][cause] += 1
            cause_total[cause] += 1
            ex = examples[cause]
            if len(ex) < max_examples:
                ex.append({"symbol": key[0], "quarter_end": key[1].isoformat(), "event_type": t, "present_in": "A" if side == "a_only" else "B", "detail": detail})
    n_both = sum(r["both"] for r in by_type.values())
    n_a, n_b = sum(r["a_only"] for r in by_type.values()), sum(r["b_only"] for r in by_type.values())
    return {"shared_company_quarters": len(shared), "quarters_only_in_a": len(set(features_a) - set(features_b)), "quarters_only_in_b": len(set(features_b) - set(features_a)),
            "events_in_both": n_both, "events_only_in_a": n_a, "events_only_in_b": n_b,
            "agreement": None if (n_both + n_a + n_b) == 0 else round(n_both / (n_both + n_a + n_b), 4),
            "by_type": {t: {**{k: v for k, v in r.items() if k != "causes"}, "causes": dict(r["causes"])} for t, r in sorted(by_type.items())},
            "cause_totals": {c: cause_total.get(c, 0) for c in CAUSES}, "examples": {c: examples.get(c, []) for c in CAUSES if examples.get(c)},
            "tolerance": TOLERANCE, "boundary": BOUNDARY}


def availability_gap(a: Mapping[Tuple[str, date], date], b: Mapping[Tuple[str, date], date]) -> Dict[str, Any]:
    """Distribution of (A's availability date - B's) in days over the shared company-quarters. For SEC (A) vs yfinance (B) this is filing acceptance vs earnings-report date."""
    gaps = sorted((a[k] - b[k]).days for k in set(a) & set(b))
    if not gaps:
        return {"n": 0}

    def pct(p: float) -> int:
        return gaps[min(len(gaps) - 1, int(p * len(gaps)))]
    return {"n": len(gaps), "min": gaps[0], "p10": pct(0.10), "median": pct(0.50), "p90": pct(0.90), "max": gaps[-1],
            "a_later": sum(1 for g in gaps if g > 0), "same_day": sum(1 for g in gaps if g == 0), "a_earlier": sum(1 for g in gaps if g < 0)}
