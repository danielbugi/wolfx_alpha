"""The research loop: signal-time features, extra conditions with a parent control, the train-only post-mortem, the derived hypothesis, determinism, and the Lab report.
Synthetic panels only (no network, no database)."""
import copy
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from research.strategy_lab import analysis as A  # noqa: E402
from research.strategy_lab import engine as E  # noqa: E402
from research.strategy_lab import loop as L  # noqa: E402
from research.strategy_lab import specs as SP  # noqa: E402
from research.strategy_lab.data import Bars, Panel, Report  # noqa: E402

PARENT = SP.get_spec("earnings_drift", "v1")


def bdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bars_from(dates, close, volume):
    c = np.asarray(close, float)
    o = np.r_[c[0], c[:-1]]
    return Bars(list(dates), o, np.maximum(o, c) * 1.004, np.minimum(o, c) * 0.996, c, np.asarray(volume, float))


def make_panel(edge_when_busy: float, n_symbols: int = 170, seed: int = 21, edge_always: float = 0.0) -> Panel:
    """Half of the events come with a volume spike on the reaction days; the planted post-report drift exists only on those (edge_when_busy) or on all (edge_always)."""
    rng = np.random.default_rng(seed)
    dates = bdays(date(2021, 1, 4), 1300)
    bars, reports = {}, {}
    for k in range(n_symbols):
        sym = f"S{k:03d}"
        r = rng.normal(0.0, 0.012, len(dates))
        vol = np.full(len(dates), 300_000.0)
        reps = []
        for D in range(70 + (k % 13), len(dates) - 70, 63):
            sign = 1 if rng.random() < 0.5 else -1
            busy = rng.random() < 0.5
            r[D], r[D + 1] = sign * 0.025, sign * 0.02
            if busy:
                vol[D:D + 2] = 900_000.0
                r[D + 2:D + 22] += sign * edge_when_busy
            r[D + 2:D + 22] += sign * edge_always
            s = sign * float(rng.uniform(12, 40))
            reps.append(Report(dates[D], 1.0, 1.0 + s / 100, s))
        bars[sym] = bars_from(dates, 50.0 * np.exp(np.cumsum(r)), vol)
        reports[sym] = reps
    return Panel(bars, reports, "d" * 64, {"prices_rows": 0, "earnings_rows": sum(len(v) for v in reports.values())})


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ features and extra conditions
def test_event_features_from_hand_built_bars():
    dates = bdays(date(2021, 1, 4), 60)
    c = np.full(60, 100.0)
    v = np.full(60, 1000.0)
    D = 40
    c[D], c[D + 1] = 103.0, 106.0
    v[D], v[D + 1] = 3000.0, 2000.0
    b = bars_from(dates, c, v)
    atr = E.atr14(b.h, b.l, b.c)
    f = E.event_features(b, atr, D, 1, 25.0, (106.0 / 100.0 - 1) * 100)
    assert f["direction"] == 1.0 and f["surprise_abs"] == 25.0 and f["reaction_dir"] == pytest.approx(6.0)
    assert f["relvol_event"] == pytest.approx(2.5)                                      # mean(3000, 2000) / 1000
    assert f["gap_dir"] == pytest.approx((b.o[D] / b.c[D - 1] - 1) * 100)
    assert f["pre_trend_20_dir"] == pytest.approx(0.0)
    short = E.event_features(b, atr, D, -1, -25.0, 6.0)
    assert short["reaction_dir"] == pytest.approx(-6.0) and short["direction"] == -1.0


def test_extra_conditions_filter_signals_and_the_parent_keeps_the_superset():
    child = SP.get_spec("earnings_confluence", "v1")
    panel = make_panel(0.0, n_symbols=40)
    c_parent, c_child = Counter(), Counter()
    sym = "S000"
    sp = E.detect(PARENT, sym, panel.bars[sym], panel.reports[sym], c_parent)
    sc = E.detect(child, sym, panel.bars[sym], panel.reports[sym], c_child)
    parent_sig = {(s.signal_idx, s.direction) for s in sp if s.kind == E.SIGNAL}
    child_sig = {(s.signal_idx, s.direction) for s in sc if s.kind == E.SIGNAL}
    child_parent = {(s.signal_idx, s.direction) for s in sc if s.kind == E.PARENT}
    assert child_parent == parent_sig and child_sig <= parent_sig and child_sig
    assert all(s.features["relvol_event"] >= 2.0 and s.features["range_expansion"] >= 1.5 for s in sc if s.kind == E.SIGNAL)
    assert c_child["not_signal_extra_condition_failed"] == len(parent_sig) - len(child_sig)


def test_a_missing_feature_never_satisfies_an_extra_condition():
    assert not E._extra_ok([{"feature": "relvol_event", "op": ">=", "value": 2.0}], {"relvol_event": float("nan")})
    assert not E._extra_ok([{"feature": "x", "op": ">=", "value": 0.0}], {})
    assert E._extra_ok([{"feature": "a", "op": "<=", "value": 1.0}], {"a": 1.0})


def test_features_use_nothing_after_the_signal_bar():
    D = 100
    dates = bdays(date(2021, 1, 4), 300)
    rng = np.random.default_rng(2)
    close = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
    close[D], close[D + 1] = close[D - 1] * 1.025, close[D - 1] * 1.05
    vol = np.full(300, 500_000.0)
    a = bars_from(dates, close, vol)
    c2, v2 = close.copy(), vol.copy()
    c2[D + 2:] *= np.exp(np.cumsum(rng.normal(0, 0.3, 300 - D - 2)))
    v2[D + 2:] = 1.0
    b = bars_from(dates, c2, v2)
    fa = E.event_features(a, E.atr14(a.h, a.l, a.c), D, 1, 20.0, 5.0)
    fb = E.event_features(b, E.atr14(b.h, b.l, b.c), D, 1, 20.0, 5.0)
    assert fa == fb


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ analysis
def _parent_rows(panel):
    rows = []
    res = E.run_backtest(PARENT, panel, seed=3, n_boot=100, rows_out=rows)
    return res, rows


def test_the_analysis_refuses_anything_that_is_not_the_train_split():
    row = {"split": "test", "features": {}, "net_r": 0.1, "month": "2024-03", "year": 2024, "signal_date": date(2024, 3, 1), "direction": 1}
    with pytest.raises(A.AnalysisError):
        A.analyze([row])
    with pytest.raises(A.AnalysisError):
        A.analyze([])


@pytest.fixture(scope="module")
def planted_loop(tmp_path_factory):
    panel = make_panel(0.008)
    out = tmp_path_factory.mktemp("loop")
    return L.run_loop(panel, out_dir=str(out), code_ref="c" * 40, seed=3, n_boot=150, run_date="2026-10-31"), out, panel


def test_the_analysis_finds_the_planted_subgroup_from_train_alone(planted_loop):
    loop, _, _ = planted_loop
    best = loop["analysis"]["best"]
    assert best["conditions"] and best["conditions"][0]["feature"] in ("relvol_event", "range_expansion") or any(c["feature"] == "relvol_event" for c in best["conditions"])
    assert best["passes_selection"] and best["n"] >= A.MIN_N
    assert loop["derived_hypothesis"]["selection_strength"] == "passes_multiple_testing"


def test_the_derived_child_beats_its_parent_on_the_untouched_test_period(planted_loop):
    loop, _, _ = planted_loop
    child = next(e for e in loop["experiments"] if e["derived_from"] == "train-only subgroup analysis")
    assert child["verdict"] == "PASS_FOR_FORWARD_SHADOW", child["checks"]
    assert child["vs_parent"]["ci95"][0] > 0
    parent = next(e for e in loop["experiments"] if e["parent"] is None)
    assert child["test_expectancy_r"] > parent["test_expectancy_r"]


def test_the_owners_a_priori_hypothesis_is_run_too_and_every_result_is_kept(planted_loop):
    loop, out, _ = planted_loop
    keys = {f"{e['key']} {e['version']}" for e in loop["experiments"]}
    assert {"earnings_drift v1", "earnings_confluence v1"} <= keys and len(keys) == 3
    for e in loop["experiments"]:
        d = os.path.join(str(out), f"{e['key']}_{e['version']}")
        assert os.path.exists(os.path.join(d, "report.md")) and os.path.exists(os.path.join(d, "result.json"))


def test_with_no_edge_anywhere_nothing_passes_and_the_next_steps_say_what_is_blocked():
    panel = make_panel(0.0, n_symbols=120)
    loop = L.run_loop(panel, out_dir=os.path.join(ROOT, "mechanism", ".tmp_loop_null"), code_ref="c" * 40, seed=3, n_boot=100)
    try:
        assert all(e["verdict"] != "PASS_FOR_FORWARD_SHADOW" for e in loop["experiments"])
        steps = " ".join(s["statement"] for s in loop["next_steps"])
        assert "SEC filing acceptance" in steps and "Short interest" in steps and "point-in-time" in steps
        if loop["derived_hypothesis"]:
            assert loop["derived_hypothesis"]["selection_strength"] in ("exploratory", "passes_multiple_testing")
    finally:
        import shutil
        shutil.rmtree(os.path.join(ROOT, "mechanism", ".tmp_loop_null"), ignore_errors=True)


def test_the_choice_of_the_next_hypothesis_does_not_depend_on_the_test_period():
    """Rewrite every price from the test period on: the post-mortem and the registered hypothesis must be identical."""
    a = make_panel(0.008, n_symbols=90)
    b = copy.deepcopy(a)
    test_start = date(2024, 2, 1)
    rng = np.random.default_rng(9)
    for sym, bars in b.bars.items():
        i = next(k for k, d in enumerate(bars.dates) if d >= test_start)
        bars.c[i:] *= np.exp(np.cumsum(rng.normal(0, 0.05, len(bars.c) - i)))
        bars.o[i:], bars.h[i:], bars.l[i:] = bars.c[i:], bars.c[i:] * 1.01, bars.c[i:] * 0.99
    ra, rb = [], []
    E.run_backtest(PARENT, a, seed=3, n_boot=50, rows_out=ra)
    E.run_backtest(PARENT, b, seed=3, n_boot=50, rows_out=rb)
    ta = [r for r in ra if r["kind"] == E.SIGNAL and r["split"] == "train"]
    tb = [r for r in rb if r["kind"] == E.SIGNAL and r["split"] == "train"]
    an_a, an_b = A.analyze(ta, seed=3, n_boot=100), A.analyze(tb, seed=3, n_boot=100)
    assert json.dumps(an_a, sort_keys=True, default=str) == json.dumps(an_b, sort_keys=True, default=str)
    assert L.derive_child(PARENT, an_a) == L.derive_child(PARENT, an_b)


def test_the_loop_is_deterministic(planted_loop, tmp_path):
    loop, _, panel = planted_loop
    again = L.run_loop(panel, out_dir=str(tmp_path), code_ref="c" * 40, seed=3, n_boot=150, run_date="2026-10-31")
    assert json.dumps(loop, sort_keys=True, default=str) == json.dumps(again, sort_keys=True, default=str)


def test_child_spec_is_a_complete_declared_definition():
    child = SP.get_spec("earnings_confluence", "v1")
    assert child["parent"]["spec_hash"] == SP.spec_hash(PARENT) and SP.spec_hash(child) != SP.spec_hash(PARENT)
    assert any("improve on it" in c for c in child["decision_rule"]["pass_for_forward_shadow_requires_all"])
    assert "short_interest" in child["excluded_inputs"] and "point-in-time" in child["excluded_inputs"]["short_interest"]
    assert PARENT.get("parent") is None and SP.spec_hash(PARENT).startswith("1af27575")     # the registered parent's identity is untouched


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ Lab report
REQUIRED = ("schema", "id", "kind", "title", "headline", "generated_at", "code_ref", "status", "use", "caveats", "provenance", "data")


def test_the_lab_report_meets_the_apps_validation_and_the_index_is_updated_idempotently(planted_loop, tmp_path):
    loop, _, _ = planted_loop
    d = str(tmp_path)
    with open(os.path.join(d, "index.json"), "w") as fh:
        json.dump({"schema": "lab_index_v1", "reports": [{"id": "other", "file": "other.json", "kind": "ml", "title": "t", "headline": "h"}]}, fh)
    p1 = L.publish(loop, extract_meta={"prices_rows": 1}, directory=d)
    L.publish(loop, extract_meta={"prices_rows": 1}, directory=d)
    doc = json.load(open(p1))
    assert all(k in doc for k in REQUIRED) and doc["status"] == "research_only" and doc["kind"] == "strategy_lab" and doc["caveats"] and doc["provenance"]["source"]
    assert doc["id"] == "strategy_lab_loop" and doc["data"]["experiments"] and doc["data"]["next_steps"]
    idx = json.load(open(os.path.join(d, "index.json")))
    assert [e["id"] for e in idx["reports"]] == ["other", "strategy_lab_loop"]
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        from services.lab_reports_service import validate
    except Exception:  # pragma: no cover - the backend package is not importable in every environment
        pytest.skip("backend validator not importable here")
    assert validate(doc) == []
