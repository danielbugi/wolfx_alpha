"""Fundamental-inflection event study: entry and excess-return arithmetic, controls, the declared decision rule, a planted-edge panel versus a no-edge panel, point-in-time selection,
determinism, follow-up hypotheses and the Lab report. Synthetic data only (no network, no database)."""
import copy
import json
import os
import sys
from datetime import date, timedelta

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from inflection import config as IC  # noqa: E402
from inflection import context as X  # noqa: E402
from inflection import runner as R  # noqa: E402
from research.strategy_lab import inflection_report as RP  # noqa: E402
from research.strategy_lab import inflection_study as S  # noqa: E402
from research.strategy_lab import specs as SP  # noqa: E402

D = date.fromisoformat
SPEC = S.SPEC


def bdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ arithmetic
def test_the_declared_spec_has_a_stable_identity_and_the_rule_is_in_it():
    h = SP.spec_hash(SPEC)
    assert len(h) == 64 and SP.spec_hash(copy.deepcopy(SPEC)) == h
    changed = copy.deepcopy(SPEC)
    changed["decision_rule"]["primary_cell"] = "something else"
    assert SP.spec_hash(changed) != h
    assert SP.get_spec("fundamental_inflection", "v1") == SPEC


def test_period_by_the_date_the_figures_were_public():
    assert S.period_of(SPEC, D("2026-06-14")) == "train" and S.period_of(SPEC, D("2026-06-15")) == "validation"
    assert S.period_of(SPEC, D("2026-07-22")) == "validation" and S.period_of(SPEC, D("2026-07-23")) == "test"


def test_entry_is_after_the_two_session_reaction_window_and_a_weekend_report_uses_the_next_session():
    ds = bdays(D("2026-01-05"), 80)
    p = X.Prices(ds, np.linspace(10, 20, 80), np.linspace(10, 20, 80))
    D0 = ds.index(D("2026-02-10"))
    assert S.entry_for(p, D("2026-02-10"), False) == (D0 + 1, D0 + 2)                  # signal = the close after the reaction window
    assert S.entry_for(p, D("2026-02-10"), True) == (D0, D0 + 1)                        # the composite event is known at the end of its window
    sat = D("2026-02-07")
    assert S.entry_for(p, sat, False) == (ds.index(D("2026-02-09")) + 1, ds.index(D("2026-02-09")) + 2)
    assert S.entry_for(p, D("2027-01-01"), False) is None


def test_excess_return_is_the_open_to_close_move_minus_the_sector_minus_costs():
    ds = bdays(D("2026-01-05"), 60)
    n = len(ds)
    flat = X.Prices(ds, np.full(n, 100.0), np.full(n, 100.0))
    stock_close = np.full(n, 100.0)
    stock_close[30:] = 110.0
    stock_open = np.full(n, 100.0)
    stock_open[30:] = 100.0
    stock = X.Prices(ds, stock_close, stock_open)
    peers = {f"P{i}": flat for i in range(10)}
    peers["S"] = stock
    sec = {s: "Technology" for s in peers}
    idx = X.SectorIndex(peers, sec)
    sector_ret = idx.ret("Technology", ds[29], ds[34])
    got = S.excess(stock, idx, "Technology", 30, 5)
    assert got == pytest.approx((110.0 / 100.0 - 1.0) - sector_ret - S.COST)
    assert S.excess(stock, idx, "Technology", 58, 5) is None                           # the window is not complete: not testable, never extrapolated
    assert S.excess(stock, idx, "Nowhere", 30, 5) is None                              # no sector benchmark: no excess return


def test_block_bootstrap_and_paired_difference_are_deterministic():
    v = np.random.default_rng(1).normal(0.01, 0.05, 300)
    k = [f"W{i % 12}" for i in range(300)]
    assert S.block_ci(v, k, 200, 5) == S.block_ci(v, k, 200, 5) and S.block_ci([], [], 10, 1) == {"n": 0}
    d = S.diff_ci(v, k, v - 0.02, k, 200, 5)
    assert d["diff"] == pytest.approx(0.02, abs=1e-6) and d["ci95"][0] > 0.0199


def test_matching_is_by_sector_and_public_date_deterministic_and_order_independent():
    units = [{"symbol": "U1", "quarter_end": D("2026-06-30"), "sector": "Tech", "available_at": D("2026-08-05")},
             {"symbol": "U2", "quarter_end": D("2026-06-30"), "sector": "Energy", "available_at": D("2026-08-05")}]
    pool = [{"symbol": f"N{i}", "sector": "Tech", "available_at": D("2026-08-05") + timedelta(days=i - 12)} for i in range(25)] + [{"symbol": "E1", "sector": "Energy", "available_at": D("2026-09-30")}]
    a, b = S.matched(units, pool, 7), S.matched(list(reversed(units)), list(reversed(pool)), 7)
    assert a == b and ("U1", D("2026-06-30")) in a and ("U2", D("2026-06-30")) not in a          # no Energy company public within 10 days
    assert abs((a[("U1", D("2026-06-30"))]["available_at"] - D("2026-08-05")).days) <= 10


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the decision rule
def cells(n=300, mean=0.05, lo=0.01, train_mean=0.03, train_n=100, top=0.04, dlo=0.01):
    return {"cells": {"union|test|h20": {"n": n, "mean": mean, "ci95": [lo, 0.09], "without_top_1pct": top}, "union|train|h20": {"n": train_n, "mean": train_mean}},
            "diffs": {"test|h20|union_minus_matched": {"ci95": [dlo, 0.08], "mean": 0.03}}}


def test_every_check_must_pass_and_too_few_events_is_insufficient_not_a_verdict():
    assert S.decide(SPEC, cells())[0] == "PASS_FOR_FORWARD_SHADOW"
    assert S.decide(SPEC, cells(n=149))[0] == "INSUFFICIENT_DATA"
    for bad in (dict(lo=-0.001), dict(dlo=-0.002), dict(train_mean=-0.01), dict(top=-0.001)):
        assert S.decide(SPEC, cells(**bad))[0] == "NO_EDGE_DEMONSTRATED", bad
    assert S.decide(SPEC, cells(train_n=10, train_mean=-0.5))[0] == "PASS_FOR_FORWARD_SHADOW"      # a train period with under 30 events cannot veto


def test_horizon_followups_only_come_from_secondary_test_cells_with_an_interval_above_zero():
    res = {"cells": {"union|test|h20": {"n": 600, "mean": 0.02, "ci95": [0.01, 0.03], "blocks": 9},                  # the primary cell is never its own follow-up
                     "union|test|h60": {"n": 350, "mean": 0.02, "ci95": [0.007, 0.059], "blocks": 3},
                     "margin_expansion|test|h60": {"n": 20, "mean": 0.1, "ci95": [0.05, 0.2], "blocks": 2},          # too few events
                     "union|train|h60": {"n": 100, "mean": 0.05, "ci95": [0.02, 0.08], "blocks": 8},                 # not the test period
                     "price_momentum|test|h60": {"n": 900, "mean": 0.05, "ci95": [0.02, 0.07], "blocks": 5}}}        # a baseline
    got = RP.horizon_followups(res)
    assert [g["cell"] for g in got] == ["union|test|h60"] and "forward evaluation only" in got[0]["statement"] and got[0]["blocks"] == 3


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ a panel with a planted edge
def make_inputs(drift: float, n_event=220, n_control=220, seed=5):
    rng = np.random.default_rng(seed)
    days = bdays(D("2026-01-02"), 217)
    qends = ["2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    reports = {"2025-09-30": D("2025-11-05"), "2025-12-31": D("2026-02-05"), "2026-03-31": D("2026-05-06"), "2026-06-30": D("2026-08-05")}
    quarterly, rep, prices, sector = {}, {}, {}, {}
    for i in range(n_event + n_control):
        sym = f"S{i:03d}"
        event = i < n_event
        quarterly[sym] = [{"quarter": D(q), "fiscal_year": None, "fiscal_quarter": None, "revenue": 100e6, "operating_income": (25e6 if (event and j >= 4) else 10e6), "net_income": 5e6,
                           "operating_cash_flow": 6e6, "free_cash_flow": 3e6, "updated_at": "2026-09-18"} for j, q in enumerate(qends)]
        rep[sym] = sorted(reports.values())
        r = rng.normal(0.0, 0.01, len(days))
        if event:
            for d in (D("2026-02-05"), D("2026-05-06"), D("2026-08-05")):
                k = next(a for a, x in enumerate(days) if x >= d)
                r[k + 2:k + 32] += drift
        close = 50.0 * np.exp(np.cumsum(r))
        prices[sym] = X.Prices(days, close, np.r_[close[0], close[:-1]])
        sector[sym] = "Technology"
    return R.Inputs(quarterly, rep, prices, sector, None, {}, {})


@pytest.fixture(scope="module")
def planted():
    return S.run_study(SPEC, make_inputs(0.006), D("2026-10-30"), seed=3, n_boot=200)


@pytest.fixture(scope="module")
def null():
    return S.run_study(SPEC, make_inputs(0.0), D("2026-10-30"), seed=3, n_boot=200)


def test_a_planted_edge_is_found_out_of_sample_against_matched_controls(planted):
    t = planted["cells"]["union|test|h20"]
    assert planted["verdict"] == "PASS_FOR_FORWARD_SHADOW", planted["decision_checks"]
    assert t["n"] >= 150 and t["mean"] > 0.05 and t["ci95"][0] > 0
    assert planted["diffs"]["test|h20|union_minus_matched"]["ci95"][0] > 0 and planted["cells"]["union|train|h20"]["mean"] > 0


def test_without_an_edge_the_same_study_does_not_pass(null):
    assert null["verdict"] != "PASS_FOR_FORWARD_SHADOW", null["decision_checks"]
    t = null["cells"]["union|test|h20"]
    assert t["ci95"][0] <= 0 <= t["ci95"][1] or t["mean"] <= 0


def test_horizons_the_data_cannot_complete_are_reported_as_such_never_extrapolated(planted):
    f = planted["feasibility"]
    assert f["5"]["events_with_complete_window"] >= f["20"]["events_with_complete_window"] >= f["60"]["events_with_complete_window"] >= f["120"]["events_with_complete_window"]
    assert f["120"]["events_with_complete_window"] < f["5"]["events_with_complete_window"]
    assert planted["cells"]["union|test|h120"].get("n", 0) < planted["cells"]["union|test|h5"]["n"]


def test_the_set_of_events_does_not_depend_on_prices_after_their_windows():
    base = make_inputs(0.006, n_event=40, n_control=40)
    rewritten = make_inputs(0.006, n_event=40, n_control=40)
    for p in rewritten.prices.values():
        i = p.dates.index(D("2026-09-21"))
        p.close[i:] *= 3.0
    a = S.collect(base, D("2026-10-30"), SPEC)
    b = S.collect(rewritten, D("2026-10-30"), SPEC)
    key = lambda c: sorted((u["symbol"], str(u["quarter_end"]), u["period"], u["entry"]) for u in c["units"])      # noqa: E731
    assert key(a) == key(b) and a["n_events"] == b["n_events"]


def test_the_study_and_its_report_are_deterministic_and_complete(planted):
    again = S.run_study(SPEC, make_inputs(0.006), D("2026-10-30"), seed=3, n_boot=200)
    assert json.dumps(planted, sort_keys=True, default=str) == json.dumps(again, sort_keys=True, default=str)
    kw = dict(spec_hash=SP.spec_hash(SPEC), data_hash="d" * 64, code_ref="abc", run_hash="r" * 64, seed=3)
    a, b = RP.render_report(SPEC, planted, **kw), RP.render_report(SPEC, planted, **kw)
    assert a == b
    for needle in ("Verdict:", "Declared before any return was computed", "Decision checks", "Baselines in the test period", "What the data can and cannot show yet", "Follow-up hypotheses registered", "Limits"):
        assert needle in a


def test_followups_are_registered_forward_only_with_exploratory_numbers_that_exclude_the_test_period(planted):
    fu = planted["followups"]
    assert [f["key"] for f in fu] == [f"fundamental_inflection_{k}" for k, _, _ in S.COMBOS]
    for f in fu:
        assert f["status"] == "registered_forward_only" and "not confirmatory" in f["note"]
        assert f["exploratory_train_validation_h20"].get("n", 0) <= f["n_by_period"]["train"] + f["n_by_period"]["validation"]
    spec = S.combo_spec(SPEC, fu[0], "2026-10-31")
    assert spec["forward_only_after"] == "2026-10-31" and spec["parent"]["spec_hash"] == SP.spec_hash(SPEC) and "after 2026-10-31 only" in spec["decision_rule"]["evaluated_on"]
    assert SP.spec_hash(spec) != SP.spec_hash(SPEC)


def test_the_lab_report_meets_the_apps_validation(planted, tmp_path):
    doc = RP.lab_report(SPEC, planted, spec_hash="s" * 64, data_hash="d" * 64, code_ref="abc", run_hash="r" * 64, report_sha="f" * 64, run_date="2026-10-31", extract={"rows": 1})
    for k in ("schema", "id", "kind", "title", "headline", "generated_at", "code_ref", "status", "use", "caveats", "provenance", "data"):
        assert k in doc
    assert doc["status"] == "research_only" and doc["caveats"] and doc["provenance"]["source"] and doc["id"] == "fundamental_inflection_research"
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        from services.lab_reports_service import validate
    except Exception:  # pragma: no cover
        pytest.skip("backend validator not importable here")
    assert validate(doc) == []


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the filing-version-aware (SEC) variant
def test_the_pit_spec_is_its_own_declared_identity_and_excludes_every_date_already_inspected():
    p = SP.get_spec("fundamental_inflection_pit", "v1")
    assert p["key"] == "fundamental_inflection_pit" and SP.spec_hash(p) != SP.spec_hash(SPEC)
    assert p["parent"]["spec_hash"] == SP.spec_hash(SPEC) and "inflection_v2_sec_pit" in p["event"]["source"]
    assert S.period_of(p, D("2019-12-31")) == "train" and S.period_of(p, D("2020-01-01")) == "validation"
    assert S.period_of(p, D("2022-12-31")) == "validation" and S.period_of(p, D("2023-01-01")) == "test"
    assert S.period_of(p, D("2025-03-31")) == "test" and S.period_of(p, D("2025-04-01")) == "excluded"        # the yfinance study's dates: neither training nor confirmation
    assert S.period_of(p, D("2026-08-05")) == "excluded"
    assert S.period_of(SPEC, D("2026-08-05")) == "test"                                                        # the original spec is unchanged


def test_the_runner_uses_filing_version_items_instead_of_latest_quarters_when_given():
    from inflection import pit as PIT
    from data_updaters import sec_statements as SS
    qe = [D("2025-03-31"), D("2025-06-30"), D("2025-09-30"), D("2025-12-31"), D("2026-03-31"), D("2026-06-30")]
    facts = []
    for i, e in enumerate(qe):
        a, f = f"A{i}", e + timedelta(days=30)
        for m, v in (("revenue", 100e6), ("operating_income", 25e6 if i >= 4 else 10e6), ("net_income", 5e6)):
            facts.append(SS.RawFact(m, "t", "USD", v, e - timedelta(days=90), e, 2025, "Q1", "10-Q", a, f))
    inp = make_inputs(0.0, n_event=1, n_control=0)
    inp.quarterly = {}
    inp.pit_items = {"S000": PIT.pit_items(facts, {}, "Technology")}
    feats, events = R.run_universe(inp, D("2026-10-30"))
    assert feats and all(f["method_version"] == IC.METHOD_VERSION_SEC_PIT and f["source"] == IC.SOURCE_SEC_PIT for f in feats)
    assert any(e["event_type"] == IC.MARGIN_EXPANSION for e in events)
