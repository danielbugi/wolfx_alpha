from datetime import date, timedelta

from data_updaters import sec_statements as S
from inflection import pit as P

QENDS = [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 30), date(2023, 12, 31), date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31)]


def fact(metric, end, value, accn, filed, form="10-Q"):
    return S.RawFact(metric, "tag", "USD", float(value), end - timedelta(days=90), end, 2023, "Q1", form, accn, filed)


def base_facts(rev):
    """One 10-Q per quarter, filed 30 days after quarter end, reporting that quarter's revenue / operating income / net income."""
    out = []
    for i, e in enumerate(QENDS):
        a, f = f"A{i}", e + timedelta(days=30)
        out += [fact("revenue", e, rev[i], a, f), fact("operating_income", e, rev[i] * 0.1, a, f), fact("net_income", e, rev[i] * 0.05, a, f)]
    return out


REV = [100, 100, 100, 100, 100, 110, 110, 110]


def test_each_quarter_uses_only_what_was_public_at_its_first_public_date():
    facts = base_facts(REV)
    # quarter 2023-09-30 (A2) is later restated DOWN to 50 by a filing accepted 2024-08-15 (after Q 2024-06-30 became public, before Q 2024-09-30)
    facts += [fact("revenue", QENDS[2], 50, "R1", date(2024, 8, 15), "10-Q/A")]
    items = {it["feature"]["quarter"]: it for it in P.pit_items(facts, {})}
    # a quarter public BEFORE the restatement compares against the original 100
    m = items[QENDS[5]]["feature"]["metrics"]       # 2024-06-30 vs 2023-06-30 (unaffected) -> 10% growth
    assert round(m["rev_yoy"], 4) == 0.10
    m6 = items[QENDS[6]]["feature"]["metrics"]               # 2024-09-30 first public 2024-10-30, AFTER the restatement: its prior-year quarter (2023-09-30) is the restated 50
    assert m6["revenue_py"] == 50.0
    # the as-published feature of the restated quarter itself still shows the ORIGINAL figure
    assert items[QENDS[2]]["feature"]["metrics"]["revenue"] == 100.0
    assert P.audit_leakage(list(items.values()), P.with_availability(facts, {})) == []


def test_the_latest_value_approach_would_have_leaked_the_restatement():
    facts = base_facts(REV) + [fact("revenue", QENDS[2], 50, "R1", date(2024, 8, 15), "10-Q/A")]
    latest = P.known_quarters(P.with_availability(facts, {}), date(2030, 1, 1))
    assert latest[QENDS[2]]["revenue"].value == 50.0                       # what sec_quarter holds today
    pit = {it["feature"]["quarter"]: it for it in P.pit_items(facts, {}, latest=latest)}
    assert pit[QENDS[2]]["feature"]["metrics"]["revenue"] == 100.0         # but the as-published feature does not move
    assert "restated_since:revenue" in pit[QENDS[2]]["feature"]["notes"]   # and the restatement is reported as information


def test_acceptance_date_beats_filing_date_and_kind_is_recorded():
    facts = base_facts(REV)
    accepted = {"A3": QENDS[3] + timedelta(days=41)}                       # filed 30 days after quarter end but ACCEPTED later
    items = {it["feature"]["quarter"]: it for it in P.pit_items(facts, accepted)}
    assert items[QENDS[3]]["available_at"] == QENDS[3] + timedelta(days=41) and items[QENDS[3]]["availability_basis"] == "sec_acceptance"
    assert items[QENDS[2]]["availability_basis"] == "sec_filed_date"


def test_provenance_names_the_filings_and_rerun_is_identical():
    facts = base_facts(REV)
    a = P.pit_items(facts, {})
    b = P.pit_items(list(reversed(facts)), {})
    assert [x["feature"]["metrics"] for x in a] == [x["feature"]["metrics"] for x in b]
    notes = next(x for x in a if x["feature"]["quarter"] == QENDS[6])["feature"]["notes"]
    assert any(n.startswith("prov:q=A6") for n in notes) and any(n.startswith("prov:py=A2") for n in notes) and any(n.startswith("pit_as_of:") for n in notes)


def test_since_limits_the_work_to_new_quarters():
    facts = base_facts(REV)
    items = P.pit_items(facts, {}, since=date(2024, 9, 1))
    assert [it["feature"]["quarter"] for it in items] == [QENDS[6], QENDS[7]][:len(items)] and len(items) == 2


def test_window_reaches_far_enough_back_for_revenue_acceleration():
    """Acceleration compares this quarter's growth with the previous quarter's growth, which needs that quarter's prior year (~15 months back). A too-short window silently drops it."""
    facts = base_facts([100, 100, 100, 100, 100, 100, 100, 130])
    last = P.pit_items(facts, {})[-1]["feature"]["metrics"]
    assert last["rev_yoy"] is not None and last["rev_yoy_prev"] is not None and last["rev_accel_pp"] is not None
