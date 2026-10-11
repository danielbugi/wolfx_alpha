"""The lab report builder: deterministic, every report passes the serving validator, the trainer-log parser reads the logged numbers back, and the research-insight statuses follow the
evidence by fixed rules (a changed interval changes the status). Pure files in / files out; no database."""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))

import build_lab_reports as B  # noqa: E402
from services.lab_reports_service import validate  # noqa: E402

EVIDENCE = os.path.join(ROOT, "docs", "research", "evidence", "quant_lab_2026-10-11")


def _load(name):
    with open(os.path.join(EVIDENCE, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def reports():
    return {r["id"]: r for r in B.build(EVIDENCE, "test")}


def test_every_built_report_passes_the_serving_validator(reports):
    assert set(reports) == {"strategy_baseline", "ranker_stability", "ml_evaluation", "exit_policies", "catalyst_earnings", "catalyst_radar_recent", "performance_attribution", "research_insights"}
    for rid, r in reports.items():
        assert validate(r) == [], (rid, validate(r))
        assert r["status"] == "research_only" and r["caveats"] and r["headline"]


def test_the_build_is_deterministic(reports):
    again = {r["id"]: r for r in B.build(EVIDENCE, "test")}
    assert json.dumps(again, sort_keys=True, default=str) == json.dumps(reports, sort_keys=True, default=str)


def test_the_trainer_log_parser_reads_the_logged_numbers_back():
    with open(os.path.join(EVIDENCE, "train_momentum.log"), encoding="utf-8") as fh:
        r = B.parse_trainer_log(fh.read())
    assert r["target"] == "momentum" and r["holdout_auc"] == 0.6269 and r["auc_ci95"] == [0.6105, 0.6428] and r["direction_only_baseline_auc"] == 0.541
    assert r["promoted"] is False and r["not_promoted_because"] == ["plan_profit_auc_within_direction"] and len(r["walk_forward_auc"]) == 6 and len(r["gate"]) == 6
    assert r["gate"]["plan_profit_auc_within_direction"]["pass"] is False and r["gate"]["holdout_auc"]["pass"] is True


def test_the_insight_statuses_for_the_committed_evidence(reports):
    st = {c["claim"]: c["status"] for c in reports["research_insights"]["data"]["claims"]}
    by = lambda text: next(v for k, v in st.items() if text in k)  # noqa: E731
    assert by("Bearish Donchian breakouts lose") == "supported"
    assert by("Bullish Donchian breakouts make money") == "inconclusive"
    assert by("trend-regime filter") == "inconclusive"
    assert by("choice among the four exit policies") == "not_supported"
    assert by("ML model ranks") == "not_supported"
    assert by("less extended") == "suggestive"
    assert by("Earnings surprises move the price") == "supported"
    assert by("tradeable drift") == "not_supported"
    assert by("recent positive earnings surprise improves") == "not_supported"


def test_a_changed_interval_changes_the_status():
    ex, rk, exitp, cat = _load("experiments.json"), _load("ranker_stability.json"), _load("exit_policy_study.json"), _load("catalyst_earnings_study.json")
    runs = {n: B.parse_trainer_log(open(os.path.join(EVIDENCE, n + ".log"), encoding="utf-8").read()) for n in ("train_momentum", "train_plan_profit", "train_momentum_novol")}
    ex2 = copy.deepcopy(ex)
    ex2["strategy_baseline"]["bullish"]["ci95"] = [0.01, 0.08]                         # an interval that excludes zero on the positive side
    cat2 = copy.deepcopy(cat)
    cat2["by_surprise_bucket"]["surprise>=+10%"]["ar_drift_20d"]["ci95"] = [0.002, 0.012]
    exit2 = copy.deepcopy(exitp)
    exit2["paired_vs_A_3R_gross_r"]["C_atr_trail_3x"]["ci95"] = [-0.06, -0.01]
    st = {c["claim"]: c["status"] for c in B.insights(ex2, rk, exit2, cat2, runs)}
    assert next(v for k, v in st.items() if "Bullish Donchian breakouts make money" in k) == "supported"
    assert next(v for k, v in st.items() if "tradeable drift" in k) == "supported"
    assert next(v for k, v in st.items() if "choice among the four exit policies" in k) == "supported"


def test_every_headline_number_is_read_from_the_evidence(reports):
    b = _load("experiments.json")["strategy_baseline"]
    assert f"{b['all']['n']:,}" in reports["strategy_baseline"]["headline"] and f"{b['bullish']['mean_r']:+.3f}" in reports["strategy_baseline"]["headline"]
    cat = _load("catalyst_earnings_study.json")
    assert f"{cat['design']['events']:,}" in reports["catalyst_earnings"]["headline"]
