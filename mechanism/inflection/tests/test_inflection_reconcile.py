from datetime import date

from inflection import config as C
from inflection import reconcile as R

Q = date(2025, 9, 30)
K = ("ABC", Q)


def test_each_difference_gets_exactly_one_named_cause():
    base = {"net_income": 5.0, "revenue": 100.0, "net_income_py": -1.0, "net_income_pq": -2.0, "ni_swing_pct_rev": 0.06}
    assert R.classify(C.PROFITABILITY_TURNAROUND, base, None)[0] == "quarter_missing_in_other"
    assert R.classify(C.PROFITABILITY_TURNAROUND, base, {**base, "net_income": None})[0] == "input_missing_in_other"
    assert R.classify(C.PROFITABILITY_TURNAROUND, base, {**base, "net_income": 7.0})[0] == "input_differs"
    assert R.classify(C.PROFITABILITY_TURNAROUND, base, {**base, "net_income_py": None})[0] == "history_coverage"
    assert R.classify(C.PROFITABILITY_TURNAROUND, base, {**base, "net_income_py": -3.0})[0] == "history_differs"
    edge = {**base, "ni_swing_pct_rev": 0.0205}
    assert R.classify(C.PROFITABILITY_TURNAROUND, edge, dict(edge))[0] == "threshold_boundary"
    assert R.classify(C.PROFITABILITY_TURNAROUND, base, dict(base))[0] == "unexplained"      # identical inputs and nowhere near the line: left visible
    assert R.classify(C.LIMITED_REPRICING, base, dict(base))[0] == "composite_dependency"


def test_reconcile_counts_agreement_and_only_uses_shared_quarters():
    m = {"net_income": 5.0, "revenue": 100.0, "net_income_py": -1.0, "net_income_pq": -2.0, "ni_swing_pct_rev": 0.06}
    other = ("XYZ", Q)
    res = R.reconcile({K: m, other: m}, {K: {**m, "net_income_py": None}}, {K: [C.PROFITABILITY_TURNAROUND], other: [C.PROFITABILITY_TURNAROUND]}, {K: []})
    assert res["shared_company_quarters"] == 1 and res["quarters_only_in_a"] == 1
    assert res["events_only_in_a"] == 1 and res["events_in_both"] == 0
    assert res["cause_totals"]["history_coverage"] == 1 and sum(res["cause_totals"].values()) == 1
    assert res["examples"]["history_coverage"][0]["symbol"] == "ABC"


def test_availability_gap_distribution():
    a = {K: date(2025, 11, 5), ("D", Q): date(2025, 11, 1)}
    b = {K: date(2025, 11, 1), ("D", Q): date(2025, 11, 1)}
    g = R.availability_gap(a, b)
    assert g["n"] == 2 and g["a_later"] == 1 and g["same_day"] == 1 and g["max"] == 4
