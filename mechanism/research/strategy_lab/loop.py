"""The research loop: completed experiment -> post-mortem (train only) -> next hypothesis, registered as a new strategy version -> controlled backtest on the untouched test
period against the parent and the baselines -> every result kept -> a short report and a Lab report for the app.

    parent (e.g. earnings_drift v1) ──run──> result + TRAIN rows ──analysis──> failure patterns + best subgroup ──> child spec (registered, never edited)
    child specs (the owner's a-priori hypothesis and the derived one) ──run once on the test period──> verdicts (the parent is a control) ──> loop report + Lab report

Failures are first-class: every run, including a NO_EDGE_DEMONSTRATED one, gets its report and result file and is recorded by `record`.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from research.lab.manifest import canonical_hash
from research.strategy_lab import analysis as A
from research.strategy_lab import engine, report, specs
from research.strategy_lab.data import Panel

LAB_REPORT_ID = "strategy_lab_loop"


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def derive_child(parent: Mapping[str, Any], analysis: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The registered next hypothesis from a train-only analysis, or None when no candidate had enough observations."""
    best = analysis.get("best")
    if not best:
        return None
    ah = canonical_hash({k: analysis[k] for k in ("schema", "source_rows", "n_rows", "candidates_tested", "edges", "best")})
    strength = "passes the multiple-testing selection rule" if best["passes_selection"] else "EXPLORATORY: did not pass the multiple-testing selection rule"
    hypothesis = (f"Within {parent['key']} {parent['version']} signals, those with {A.readable(best['conditions'])} keep drifting by more than the parent's other signals "
                  f"(chosen on the train period only: n {best['n']}, mean {best['mean_r']:+.3f} R; {strength}).")
    return specs.child_spec(
        parent, key=f"{parent['key']}_refined", version=f"a{ah[:8]}", hypothesis=hypothesis, extra=best["conditions"],
        derived_from={"kind": "train-only subgroup analysis", "analysis_sha256": ah, "candidates_tested": analysis["candidates_tested"], "selection_rule": analysis["selection_rule"],
                      "train": {"n": best["n"], "mean_r": best["mean_r"], "ci95": best["ci95"], "adjusted_ci": best["adjusted_ci"], "stable_across_halves": best["stable_across_halves"],
                                "passes_selection": best["passes_selection"]}})


def summarize(spec: Mapping[str, Any], result: Mapping[str, Any], *, spec_hash: str, run_hash: str, report_sha: str) -> Dict[str, Any]:
    cap, bps = int(spec["exit"]["primary_max_hold_bars"]), int(spec["costs_bps_round_trip"]["primary"])
    cells, cd = result["cells"], result["controls_diff"]
    g = lambda split, d="both", kind="signal": cells.get(f"{kind}|{split}|hold{cap}|{d}|{bps}bps", {})      # noqa: E731
    parent = spec.get("parent")
    return {"key": spec["key"], "version": spec["version"], "parent": f"{parent['key']} {parent['version']}" if parent else None, "hypothesis": spec["hypothesis"],
            "derived_from": (spec.get("derived_from") or {}).get("kind"), "verdict": result["verdict"],
            "n_test": g("test").get("n", 0), "test_expectancy_r": g("test").get("expectancy_r"), "test_ci95": (g("test").get("ci") or {}).get("ci95"),
            "test_long_r": g("test", "long").get("expectancy_r"), "test_short_r": g("test", "short").get("expectancy_r"),
            "n_train": g("train").get("n", 0), "train_expectancy_r": g("train").get("expectancy_r"),
            "vs_random": cd.get(f"test|hold{cap}|signal_minus_random"), "vs_all_reports": cd.get(f"test|hold{cap}|signal_minus_all_reports"),
            "vs_parent": cd.get(f"test|hold{cap}|signal_minus_parent"), "checks": result["decision_checks"],
            "spec_hash": spec_hash, "run_hash": run_hash, "report_sha256": report_sha}


def next_steps(summaries: Sequence[Mapping[str, Any]], analysis: Mapping[str, Any], derived: Optional[Mapping[str, Any]] = None) -> List[Dict[str, str]]:
    """Rule-based follow-ups from what the runs showed (never from the test period of an unrun hypothesis)."""
    out: List[Dict[str, str]] = []
    passed = [s for s in summaries if s["verdict"] == "PASS_FOR_FORWARD_SHADOW"]
    for s in passed:
        out.append({"status": "ready", "statement": f"Record {s['key']} {s['version']} signals forward as a shadow (no orders) and re-test on the new data once at least 150 new positions exist."})
    looks = len(summaries)
    if not passed and derived:
        out.append({"status": "proposed", "statement":
                    f"Forward holdout: record {derived['key']} {derived['version']} signals from the next session on as a shadow (no orders) and judge it once at least 150 new positions exist. "
                    f"The test period has now been looked at by {looks} strategies, so it can no longer serve as confirmation for any further hypothesis; new evidence has to come from data dated after this run."})
    if not passed:
        pats = {p["pattern"] for p in analysis.get("failure_patterns", [])}
        if any(p.startswith("longs") or p.startswith("shorts") for p in pats):
            out.append({"status": "proposed", "statement": "Test a one-direction variant: the train analysis found one side losing more than the pool (see failure patterns); register it as its own version and run it once on the test period."})
        out.append({"status": "blocked on data", "statement": "Replace yfinance report dates with SEC filing acceptance times (8-K item 2.02) so the reaction window is anchored to when the report was public, "
                                                         "then rerun the parent. Needs the SEC contact identity; the pipeline is built and dormant (PR #78)."})
        out.append({"status": "blocked on data", "statement": "Short interest as a feature: no point-in-time source exists today (only the current value), so it cannot be tested without leaking the future. Needs a dated vendor feed."})
    return out


def run_loop(panel: Panel, *, out_dir: str, code_ref: str, seed: int = 20261031, n_boot: int = 1000, parent: Sequence[str] = ("earnings_drift", "v1"),
             owner_specs: Sequence[Sequence[str]] = (("earnings_confluence", "v1"),), run_date: str = "") -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    parent_spec = specs.get_spec(*parent)
    rows: List[Dict[str, Any]] = []
    parent_result = engine.run_backtest(parent_spec, panel, seed=seed, n_boot=n_boot, rows_out=rows)
    train_rows = [r for r in rows if r["kind"] == engine.SIGNAL and r["split"] == "train"]
    analysis = A.analyze(train_rows, seed=seed, n_boot=max(300, n_boot // 2))

    plan = [parent_spec] + [specs.get_spec(*s) for s in owner_specs]
    derived = derive_child(parent_spec, analysis)
    if derived is not None and all(specs.spec_hash(derived) != specs.spec_hash(p) for p in plan):
        plan.append(derived)
    results = {specs.spec_hash(parent_spec): parent_result}
    summaries: List[Dict[str, Any]] = []
    for sp in plan:
        sh = specs.spec_hash(sp)
        res = results.get(sh) or engine.run_backtest(sp, panel, seed=seed, n_boot=n_boot)
        rh = engine.run_hash(sp, panel.data_hash, code_ref, seed)
        text = report.render(sp, res, spec_hash=sh, data_hash=panel.data_hash, code_ref=code_ref, run_hash=rh, seed=seed, data_meta=panel.meta)
        rsha = report.report_sha256(text)
        d = os.path.join(out_dir, f"{sp['key']}_{sp['version']}")
        os.makedirs(d, exist_ok=True)
        _write(os.path.join(d, "report.md"), text)
        _write(os.path.join(d, "result.json"), json.dumps({"spec": sp, "spec_hash": sh, "data_hash": panel.data_hash, "code_ref": code_ref, "run_hash": rh, "seed": seed,
                                                           "report_sha256": rsha, "result": res}, indent=1, sort_keys=True, default=str))
        summaries.append(summarize(sp, res, spec_hash=sh, run_hash=rh, report_sha=rsha))
    steps = next_steps(summaries, analysis, {"key": derived["key"], "version": derived["version"]} if derived else None)
    loop = {"schema": "strategy_lab_loop_v1", "data_hash": panel.data_hash, "code_ref": code_ref, "seed": seed, "run_date": run_date, "parent": f"{parent[0]} {parent[1]}",
            "analysis": analysis, "experiments": summaries, "next_steps": steps,
            "derived_hypothesis": ({"key": derived["key"], "version": derived["version"], "statement": derived["hypothesis"], "conditions": derived["event"]["extra"],
                                    "selection_strength": "passes_multiple_testing" if analysis["best"]["passes_selection"] else "exploratory"} if derived else None)}
    _write(os.path.join(out_dir, "loop.json"), json.dumps(loop, indent=1, sort_keys=True, default=str))
    _write(os.path.join(out_dir, "loop_report.md"), render_loop(loop))
    return loop


def _r(x: Any, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:+.{nd}f}"


def _ci(ci: Any) -> str:
    return f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "n/a"


def render_loop(loop: Mapping[str, Any]) -> str:
    a = loop["analysis"]
    L = [f"# Research loop: {loop['parent']} and what followed", "",
         f"Data `{loop['data_hash'][:16]}`, code `{loop['code_ref'][:12]}`, seed {loop['seed']}. Every number below is net R after the primary cost; the test period was used once per strategy.", "",
         "## Experiments", "", "| Strategy | Hypothesis source | Verdict | Test n | Test net R | 95% interval | vs random | vs parent | Train net R |", "|---|---|---|---|---|---|---|---|---|"]
    for s in loop["experiments"]:
        vr = s["vs_random"] or {}
        vp = s["vs_parent"] or {}
        L.append(f"| {s['key']} {s['version']} | {s['derived_from'] or 'registered by hand'} | **{s['verdict'].replace('_', ' ')}** | {s['n_test']} | {_r(s['test_expectancy_r'])} | {_ci(s['test_ci95'])} | "
                 f"{_r(vr.get('diff'))} {_ci(vr.get('ci95'))} | {_r(vp.get('diff')) if vp else '-'} {_ci(vp.get('ci95')) if vp else ''} | {_r(s['train_expectancy_r'])} |")
    L += ["", "## Failure patterns in the parent (train period only)", ""]
    L += [f"- **{p['pattern']}**: {p['evidence']}" for p in a["failure_patterns"]] or ["- none found"]
    L += ["", f"## Subgroups of the parent's train signals ({a['candidates_tested']} candidates, intervals widened for that)", "",
          "| Candidate | n | Mean net R | Adjusted interval | Halves | Passes |", "|---|---|---|---|---|---|"]
    for e in a["subgroups"][:8]:
        L.append(f"| {e['name']} ({A.readable(e['conditions'])}) | {e['n']} | {_r(e['mean_r'])} | {_ci(e['adjusted_ci'])} | {_r(e['mean_first_half'])} / {_r(e['mean_second_half'])} | {'yes' if e['passes_selection'] else 'no'} |")
    L += ["", f"{a['n_passing']} candidate(s) passed the selection rule." + (" The top candidate was registered anyway as an exploratory hypothesis; only its single run on the untouched test period can confirm it." if a["n_passing"] == 0 and loop["derived_hypothesis"] else ""), ""]
    if loop["derived_hypothesis"]:
        d = loop["derived_hypothesis"]
        L += ["## Registered next hypothesis", "", f"`{d['key']} {d['version']}` ({d['selection_strength'].replace('_', ' ')}): {d['statement']}", ""]
    L += ["## What to do next", ""] + [f"- [{n['status']}] {n['statement']}" for n in loop["next_steps"]] + [""]
    return "\n".join(L)


def lab_report(loop: Mapping[str, Any], *, extract_meta: Mapping[str, Any]) -> Dict[str, Any]:
    """The app-facing Lab report (schema checked by backend/services/lab_reports_service.validate)."""
    exps = loop["experiments"]
    verdicts = ", ".join(f"{e['key']} {e['version']}: {e['verdict'].replace('_', ' ').lower()}" for e in exps)
    return {
        "schema": "lab_report_v1", "id": LAB_REPORT_ID, "kind": "strategy_lab", "title": "Strategy research loop: experiments, post-mortem and next hypothesis",
        "headline": f"{len(exps)} experiments on one data extract ({verdicts}).", "generated_at": loop["run_date"] or "unspecified", "code_ref": loop["code_ref"], "status": "research_only",
        "use": "Shows what each registered strategy version did out of sample, against which baselines, why it failed or passed, and what was registered next. Not signals, not advice.",
        "caveats": ["Earnings data come from yfinance: not point-in-time, and the time of day of a report is unknown (the reaction window spans two sessions).",
                    "Each strategy's thresholds were fixed before its test period was looked at; the loop's own post-mortem sees only the train period.",
                    "A pass authorises a forward shadow run only. It is not a trading claim; fills and costs are modelled.",
                    "Short interest is not used: no point-in-time source exists."],
        "provenance": {"source": "mechanism/research/strategy_lab (reproducible: spec, data and code hashes below)", "data_sha256": loop["data_hash"], "extract": dict(extract_meta),
                       "run_hashes": {f"{e['key']}_{e['version']}": e["run_hash"] for e in exps}},
        "data": {"experiments": exps, "analysis": {k: loop["analysis"][k] for k in ("n_rows", "overall", "candidates_tested", "selection_rule", "subgroups", "n_passing", "failure_patterns", "by_year")},
                 "derived_hypothesis": loop["derived_hypothesis"], "next_steps": loop["next_steps"]}}


def publish(loop: Mapping[str, Any], *, extract_meta: Mapping[str, Any], directory: str) -> str:
    """Write the Lab report and register it in the index (replacing an earlier entry with the same id)."""
    doc = lab_report(loop, extract_meta=extract_meta)
    path = os.path.join(directory, f"{LAB_REPORT_ID}.json")
    _write(path, json.dumps(doc, indent=1, sort_keys=True, default=str))
    idx_path = os.path.join(directory, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {"schema": "lab_index_v1", "reports": []}
    entry = {"id": LAB_REPORT_ID, "file": f"{LAB_REPORT_ID}.json", "kind": "strategy_lab", "title": doc["title"], "headline": doc["headline"]}
    idx["reports"] = [e for e in idx["reports"] if e.get("id") != LAB_REPORT_ID] + [entry]
    _write(idx_path, json.dumps(idx, indent=1, default=str))
    return path
