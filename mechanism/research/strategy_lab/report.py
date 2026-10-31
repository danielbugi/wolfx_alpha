"""Automatic research report. PURE and DETERMINISTIC: the same spec + results + identifiers always render the same bytes (no clock, no environment), so the report's
sha256 is stored with the run and a rerun can be compared byte for byte. Wording is plain: it states what was measured, what was declared in advance and what was
not shown. It never claims an edge the decision rule did not authorise, and it never uses performance language beyond the measured numbers.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping

VERDICT_TEXT = {
    "PASS_FOR_FORWARD_SHADOW": "PASS FOR FORWARD SHADOW. Every pre-declared check passed on the out-of-sample period. This authorises recording the strategy's signals forward "
                               "(shadow, no orders) to see whether the result survives new data. It is not a claim that the strategy makes money.",
    "NO_EDGE_DEMONSTRATED": "NO EDGE DEMONSTRATED. At least one pre-declared check failed on the out-of-sample period. Nothing here supports trading this strategy as defined.",
    "INSUFFICIENT_DATA": "INSUFFICIENT DATA. Too few out-of-sample positions to decide either way (the rule needs at least 150).",
}


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _ci(c: Mapping[str, Any]) -> str:
    ci = c.get("ci95") or (c.get("ci") or {}).get("ci95")
    return f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "n/a"


def render(spec: Mapping[str, Any], result: Mapping[str, Any], *, spec_hash: str, data_hash: str, code_ref: str, run_hash: str, seed: int, data_meta: Mapping[str, Any]) -> str:
    cells, cd = result["cells"], result["controls_diff"]
    cap = int(spec["exit"]["primary_max_hold_bars"])
    bps = int(spec["costs_bps_round_trip"]["primary"])
    L: List[str] = []
    a = L.append
    a(f"# Strategy research report: {spec['key']} {spec['version']}")
    a("")
    a(f"**Verdict: {VERDICT_TEXT[result['verdict']]}**")
    a("")
    a("## Identity (everything needed to reproduce this)")
    a(f"- Strategy spec sha256: `{spec_hash}`")
    a(f"- Data (prices + earnings extract) sha256: `{data_hash}`")
    a(f"- Code: `{code_ref}`  |  Seed: `{seed}`")
    a(f"- Run hash: `{run_hash}`")
    a(f"- Extract: prices {data_meta.get('prices_rows', '?')} rows, {data_meta.get('prices_symbols', '?')} symbols, {data_meta.get('prices_min_date', '?')} to {data_meta.get('prices_max_date', '?')}; "
      f"earnings {data_meta.get('earnings_rows', '?')} reports with a reported EPS")
    a("")
    a("## What was tested (declared before any result was looked at)")
    a(f"- Hypothesis: {spec['hypothesis']}")
    ev = spec["event"]
    if spec.get("parent"):
        a(f"- Refines {spec['parent']['key']} {spec['parent']['version']} (spec `{spec['parent']['spec_hash'][:12]}`): the same event, exit, split and costs, plus extra conditions on signal-time features. "
          f"The parent's own signals are a control.")
        a("- Extra conditions: " + "; ".join(f"{c['feature']} {c['op']} {c['value']:g}" for c in spec["event"]["extra"]))
        df = spec.get("derived_from") or {}
        a(f"- How the conditions were chosen: {df.get('kind', 'n/a')}" + (f" ({df['candidates_tested']} candidates looked at on the TRAIN period only)" if df.get("candidates_tested") else ""))
        if spec.get("excluded_inputs"):
            a("- Left out on purpose: " + "; ".join(f"{k}: {v}" for k, v in spec["excluded_inputs"].items()))
    a(f"- Event: a report whose surprise is at least {ev['min_abs_surprise_pct']:g}% in size and whose first price reaction ({ev['reaction']['min_abs_return_pct']:g}% to "
      f"{ev['reaction']['max_abs_return_pct']:g}%) points the same way. Long after a beat, short after a miss.")
    a(f"- Entry: next session's open after the reaction window. Exit: {spec['exit']['initial_risk']} stop, {spec['exit']['target']} target, "
      f"{spec['exit']['primary_max_hold_bars']}-session cap (a {spec['exit']['sensitivity_max_hold_bars']}-session cap is shown as context).")
    a(f"- Liquidity: price at least ${spec['liquidity_at_signal']['min_price']:g} and average dollar volume of at least "
      f"${spec['liquidity_at_signal']['min_avg_dollar_volume_20_sessions_before_report']:,.0f} in the 20 sessions before the report.")
    sp = spec["split"]
    a(f"- Split by signal date: train {sp['train_start']} to before {sp['train_end_exclusive']}; embargo to {sp['test_start_inclusive']}; test from {sp['test_start_inclusive']}. {sp['purge']}.")
    a(f"- Costs: {bps} bps round trip is the primary case; {', '.join(str(int(b)) for b in spec['costs_bps_round_trip']['sensitivity'])} bps shown.")
    a("- Controls: every report with the same exit and no thresholds (what the thresholds add), and a random date of the same stock (what just being in this stock at this time gives).")
    a("")
    a("## Decision rule and checks (primary cell: test period, both directions, "
      f"{cap}-session hold, {bps} bps)")
    for c in result["decision_checks"]:
        a(f"- {'PASS' if c['ok'] else 'FAIL'}: {c['check']} (observed: {_fmt(c['value'])})")
    a("")
    a("## Results (net R after costs; 1R = 2 x ATR14 at the signal)")
    a("")
    a("| Strategy | Period | Direction | Positions | Net expectancy R | 95% interval | Win rate | Profit factor | Without top 1% of winners |")
    a("|---|---|---|---|---|---|---|---|---|")
    for kind, label in (("signal", "Signal"), ("parent_signal", "Control: parent strategy"), ("all_reports", "Control: all reports"), ("random_date", "Control: random date")):
        for split in ("train", "test"):
            for d in ("both", "long", "short"):
                c = cells.get(f"{kind}|{split}|hold{cap}|{d}|{bps}bps")
                if not c or not c.get("n"):
                    continue
                a(f"| {label} | {split} | {d} | {c['n']} | {_fmt(c.get('expectancy_r'), 4)} | {_ci(c)} | {_fmt(c.get('win_rate'))} | {_fmt(c.get('profit_factor'))} | {_fmt(c.get('expectancy_without_top_1pct'), 4)} |")
    a("")
    a("### Signal minus controls (same costs and exit, month-block bootstrap)")
    for split in ("train", "test"):
        for cp in sorted({int(spec["exit"]["primary_max_hold_bars"]), int(spec["exit"]["sensitivity_max_hold_bars"])}):
            for name in ("signal_minus_random", "signal_minus_all_reports", "signal_minus_parent"):
                c = cd.get(f"{split}|hold{cp}|{name}", {})
                if "diff" in c:
                    a(f"- {split}, {cp}-session hold, {name.replace('_', ' ')}: {c['diff']:+.4f} R, 95% interval [{c['ci95'][0]:+.4f}, {c['ci95'][1]:+.4f}] (n {c['n_a']} vs {c['n_b']})")
    a("")
    a("### Cost sensitivity (test period, both directions, signal)")
    for cp in sorted({int(spec["exit"]["primary_max_hold_bars"]), int(spec["exit"]["sensitivity_max_hold_bars"])}):
        row = []
        for b in spec["costs_bps_round_trip"]["sensitivity"]:
            c = cells.get(f"signal|test|hold{cp}|both|{int(b)}bps")
            row.append(f"{int(b)} bps: {_fmt(c.get('expectancy_r'), 4) if c else 'n/a'}")
        a(f"- {cp}-session hold: " + "; ".join(row))
    a("")
    a("### By signal year (primary hold and cost)")
    a("| Year | Positions | Net expectancy R |")
    a("|---|---|---|")
    for y, v in result["by_year"].items():
        a(f"| {y} | {v['n']} | {_fmt(v['expectancy_r'], 4)} |")
    a("")
    a("## How many events were dropped, and why")
    for k, v in result["event_accounting"].items():
        a(f"- {k.replace('_', ' ')}: {v}")
    a("")
    a("## Limits that apply to this result")
    a("- The earnings data come from yfinance: not point-in-time (a restated or moved date looks as if it was always final) and the time of day of the report is unknown, which is why the "
      "reaction window spans two sessions. The strategy's signal is taken only after both sessions have closed.")
    a("- No parameter was tuned on this data; the thresholds are round numbers fixed beforehand. Even so, one strategy, two directions, two hold caps and three costs were looked at, "
      "and only the primary cell decides.")
    a("- Fills are modelled (next open, stop at its level or the gap, a flat cost in basis points). Small-cap spreads and borrow costs for shorts can be worse than the costs shown.")
    a("- A PASS authorises a forward shadow run only. It is not a trading claim, and past results do not establish future ones.")
    a("")
    return "\n".join(L)


def report_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
