"""Report, Lab report and horizon follow-ups for the fundamental-inflection event study. PURE and DETERMINISTIC (no clock): the same spec + result + identifiers render the same bytes."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from research.strategy_lab.inflection_study import ALL_TYPES, HORIZONS, MIN_CELL, PRIMARY


def horizon_followups(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """A cell (other than the primary) whose widened interval sits entirely above zero in the TEST period becomes a registered, forward-only hypothesis: it was found by looking at the test period,
    so it cannot be confirmed there. With only a handful of weekly blocks behind it, it is a lead, not a result."""
    out = []
    for k, c in sorted(result["cells"].items()):
        group, period, h = k.split("|")
        if period != "test" or group in ("earnings_surprise", "price_momentum", "relative_strength") or (group, h) == (PRIMARY["group"], f"h{PRIMARY['horizon']}"):
            continue
        if c.get("n", 0) >= MIN_CELL and c.get("ci95") and c["ci95"][0] > 0:
            out.append({"cell": k, "n": c["n"], "mean": c["mean"], "ci95": c["ci95"], "blocks": c["blocks"],
                        "statement": f"{group.replace('_', ' ')} events outperform their sector over {h[1:]} sessions (seen in the test period: mean {c['mean'] * 100:+.1f}% net, interval "
                                     f"[{c['ci95'][0] * 100:+.1f}%, {c['ci95'][1] * 100:+.1f}%], {c['blocks']} weekly blocks); registered for forward evaluation only."})
    return out


def _p(v: Optional[float], nd: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:+.{nd}f}%"


def _ci(c: Mapping[str, Any]) -> str:
    return "n/a" if not c.get("ci95") else f"[{c['ci95'][0] * 100:+.1f}%, {c['ci95'][1] * 100:+.1f}%]"


VERDICT_TEXT = {
    "PASS_FOR_FORWARD_SHADOW": "PASS FOR FORWARD SHADOW: every pre-declared check passed. This authorises a forward shadow run only; it is not a trading claim.",
    "NO_EDGE_DEMONSTRATED": "NO EDGE DEMONSTRATED: at least one pre-declared check failed on the primary cell. Nothing here supports trading on inflection events as defined.",
    "INSUFFICIENT_DATA": "INSUFFICIENT DATA: too few events with a complete window to decide.",
}


def render_report(spec: Mapping[str, Any], result: Mapping[str, Any], *, spec_hash: str, data_hash: str, code_ref: str, run_hash: str, seed: int) -> str:
    cells, diffs, cnt = result["cells"], result["diffs"], result["counts"]
    L = [f"# Research report: {spec['key']} {spec['version']} (event study)", "", f"**Verdict: {VERDICT_TEXT[result['verdict']]}**", "", "## Identity",
         f"- Spec sha256 `{spec_hash}` | data sha256 `{data_hash}` | code `{code_ref}` | seed {seed}", f"- Run hash `{run_hash}`",
         f"- Engine events: {cnt['events']} from {cnt['features']} company-quarters; {cnt['company_quarters_with_a_base_event']} company-quarters with a base event; public dates {cnt['public_dates'][0]} to {cnt['public_dates'][1]}; "
         f"prices to {cnt['last_price_date']}", "", "## Declared before any return was computed",
         f"- Hypothesis: {spec['hypothesis']}", f"- Entry: {spec['entry']}", f"- Measure: {spec['measure']['forward_excess_return']}",
         f"- Split by the date figures were public: train before {spec['split']['train_end_exclusive']}, test from {spec['split']['test_start_inclusive']}, validation between (looked at, never used to decide). {spec['split']['reason']}.",
         f"- Primary cell: {spec['decision_rule']['primary_cell']}. Other cells are context with intervals widened for the number of cells looked at.", "", "## Decision checks (primary cell)"]
    L += [f"- {'PASS' if c['ok'] else 'FAIL'}: {c['check']} (observed: {c['value']})" for c in result["decision_checks"]]
    L += ["", "## Mean net sector-adjusted excess return by horizon (union of base events)", "", "| Horizon | Period | Events | Mean | 95% interval | Win rate | Without top 1% | Minus matched control |", "|---|---|---|---|---|---|---|---|"]
    for h in HORIZONS:
        for period in ("train", "validation", "test"):
            c = cells.get(f"union|{period}|h{h}", {})
            if not c.get("n"):
                L.append(f"| {h} | {period} | 0 | not testable (no complete window) | | | | |")
                continue
            d = diffs.get(f"{period}|h{h}|union_minus_matched", {})
            L.append(f"| {h} | {period} | {c['n']} | {_p(c['mean'])} | {_ci(c)} | {c['win_rate']:.2f} | {_p(c.get('without_top_1pct'))} | {_p(d.get('mean'))} {_ci(d) if d else ''} |")
    L += ["", "## Baselines in the test period (same measure)", "", "| Group | 20 sessions | 60 sessions |", "|---|---|---|"]
    for g, label in (("union", "Inflection events (union)"), ("earnings_surprise", "Earnings surprise >= +10%"), ("price_momentum", "Price momentum (top decile)"), ("relative_strength", "Relative strength (top quintile)")):
        a, b = cells.get(f"{g}|test|h20", {}), cells.get(f"{g}|test|h60", {})
        L.append(f"| {label} | {_p(a.get('mean'))} {_ci(a)} (n {a.get('n', 0)}) | {_p(b.get('mean'))} {_ci(b)} (n {b.get('n', 0)}) |")
    L += ["", "## By event type (test period, 20 and 60 sessions)", "", "| Event type | Events | 20 sessions | 60 sessions |", "|---|---|---|---|"]
    for t in ALL_TYPES:
        a, b = cells.get(f"{t}|test|h20", {}), cells.get(f"{t}|test|h60", {})
        L.append(f"| {t.replace('_', ' ')} | {a.get('n', 0)} | {_p(a.get('mean'))} {_ci(a)} | {_p(b.get('mean'))} {_ci(b)} |")
    L += ["", "## What the data can and cannot show yet"]
    for h in HORIZONS:
        f = result["feasibility"][str(h)]
        L.append(f"- {h} sessions: {f['events_with_complete_window']} company-quarters have a complete window; the latest public date that does is {f['latest_public_date_complete'] or 'none'}.")
    L += ["- The stored fundamentals cover 5-6 quarters per company, so the events fall in about two reporting seasons: the intervals are clustered by public week and rest on few independent blocks. "
          "Every cell is one market regime. Longer history needs the SEC EDGAR pipeline (waiting for its operator contact).", "",
          "## Follow-up hypotheses registered (forward evaluation only; the test period is not reused as confirmation)", ""]
    for f in result["followups"]:
        e = f["exploratory_train_validation_h20"]
        L.append(f"- **{f['label']}**: {f['n_company_quarters']} company-quarters (train {f['n_by_period']['train']}, validation {f['n_by_period']['validation']}, test {f['n_by_period']['test']}); "
                 f"exploratory train+validation 20-session mean {_p(e.get('mean'))} (n {e.get('n', 0)}). {f['note']}.")
    for hf in result.get("horizon_followups", []):
        L.append(f"- **Horizon lead**: {hf['statement']}")
    L += ["", "## Limits", "- Statements come from yfinance (restated, not point-in-time); the public date is the earnings report date or an assumed lag. Fills are modelled at the open; a flat 20 bps cost; "
          "the sector benchmark includes the stock itself. A PASS would authorise a forward shadow run only.", ""]
    return "\n".join(L)


def lab_report(spec: Mapping[str, Any], result: Mapping[str, Any], *, spec_hash: str, data_hash: str, code_ref: str, run_hash: str, report_sha: str, run_date: str, extract: Mapping[str, Any]) -> Dict[str, Any]:
    p = result["cells"].get("union|test|h20", {})
    return {"schema": "lab_report_v1", "id": "fundamental_inflection_research", "kind": "strategy_lab", "title": "Fundamental inflection: forward sector-adjusted returns after an event",
            "headline": f"{result['verdict'].replace('_', ' ').lower()}: 20-session net excess return in the test period {_p(p.get('mean'))} {_ci(p)} on {p.get('n', 0)} company-quarters.",
            "generated_at": run_date or "unspecified", "code_ref": code_ref, "status": "research_only",
            "use": "Tests whether the inflection engine's events precede sector-adjusted excess returns, against matched non-event companies and simple baselines. Not signals, not advice.",
            "caveats": ["The fundamentals are yfinance statements (not point-in-time) and cover 5-6 quarters, so the events fall in about two reporting seasons: intervals are clustered by week and rest on few independent blocks.",
                        "The declared decision rule is applied to one primary cell; every other cell is context with widened intervals. A pass would authorise a forward shadow run only.",
                        "Follow-up hypotheses are registered for forward evaluation: the test period has been looked at and is not reused as confirmation.",
                        "Fills are modelled (next open, 20 bps round trip); the sector benchmark includes the stock itself."],
            "provenance": {"source": "mechanism/research/strategy_lab/inflection_study (reproducible: spec, data and code hashes below)", "spec_sha256": spec_hash, "data_sha256": data_hash, "run_hash": run_hash,
                           "report_sha256": report_sha, "extract": dict(extract)},
            "data": {"verdict": result["verdict"], "checks": result["decision_checks"], "primary_cell": PRIMARY, "horizons": list(HORIZONS), "cells": result["cells"], "diffs": result["diffs"],
                     "feasibility": result["feasibility"], "counts": result["counts"], "followups": result["followups"], "horizon_followups": result.get("horizon_followups", []),
                     "decision_rule": spec["decision_rule"], "split": spec["split"], "hypothesis": spec["hypothesis"]}}
