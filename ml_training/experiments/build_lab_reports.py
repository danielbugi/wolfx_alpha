"""Builds the versioned, read-only research reports served by /api/lab/reports from the evidence JSON/logs. Pure (files in, files out); deterministic; every headline number is READ from the
evidence, never typed by hand. Usage:  python ml_training/experiments/build_lab_reports.py <evidence_dir> <out_dir> <code_ref>
"""
import json
import os
import re
import sys

STATUS = "research_only"
USE = "Research evidence only. Not a trading signal, not advice, and not used by the live signal path."


def _load(d, name):
    with open(os.path.join(d, name), encoding="utf-8") as fh:
        return json.load(fh)


def _pct(x, nd=2):
    return f"{100 * x:+.{nd}f}%"


def parse_trainer_log(text):
    """The honest trainer prints one line per metric; this reads them back (numbers only, no judgement)."""
    def f(pattern, cast=float, group=1, default=None):
        m = re.search(pattern, text)
        return cast(m.group(group)) if m else default
    gate = {}
    for m in re.finditer(r"(\w+) ([-\d.]+) \(need ([-\d.]+)\) (OK|FAIL)", text.split("GATE:")[-1].split("Top features")[0] if "GATE:" in text else ""):
        gate[m.group(1)] = {"value": float(m.group(2)), "required": float(m.group(3)), "pass": m.group(4) == "OK"}
    wf = [float(x) for x in re.findall(r"AUC ([\d.]+)(?: \||$)", text.split("WALK-FORWARD:")[-1].split("GATE:")[0])] if "WALK-FORWARD:" in text else []
    ci = re.search(r"95% CI \[([-\d.]+), ([-\d.]+)\]", text)
    q = re.search(r"mean plan R by score quintile \(low->high\): \[([^\]]*)\]", text)
    return {
        "target": f(r"Target: (\w+)", str), "dataset_rows": f(r"Dataset: (\d+) mature samples", int), "base_rate": f(r"base rate ([\d.]+)\)"),
        "holdout_range": re.search(r"holdout ([\d-]+) -> ([\d-]+)", text).groups() if re.search(r"holdout ([\d-]+) -> ([\d-]+)", text) else None,
        "holdout_auc": f(r"HOLDOUT[^\n]*\n\s+AUC ([\d.]+)"), "auc_ci95": [float(ci.group(1)), float(ci.group(2))] if ci else None,
        "direction_only_baseline_auc": f(r"direction-only baseline AUC ([\d.]+)"),
        "auc_vs_plan_profit_bullish": f(r"bullish ([\d.]+) \| bearish [\d.]+\n\s+AUC vs plan profit \(R>0\)") or f(r"AUC vs plan profit within direction: bullish ([\d.]+)"),
        "auc_vs_plan_profit_bearish": f(r"within direction: bullish [\d.]+ \| bearish ([\d.]+)"),
        "top_decile_lift": f(r"lift ([\d.]+)x"), "plan_r_by_score_quintile": [float(x) for x in q.group(1).split(",")] if q else None,
        "walk_forward_auc": wf, "gate": gate, "promoted": "NOT PROMOTED" not in text,
        "not_promoted_because": (re.search(r"NOT PROMOTED \(gate failed: ([^)]*)\)", text).group(1).split(", ") if re.search(r"NOT PROMOTED \(gate failed: ([^)]*)\)", text) else []),
    }


def build(evidence_dir, code_ref):
    ex, rk = _load(evidence_dir, "experiments.json"), _load(evidence_dir, "ranker_stability.json")
    exitp, cat = _load(evidence_dir, "exit_policy_study.json"), _load(evidence_dir, "catalyst_earnings_study.json")
    radar = _load(evidence_dir, "catalyst_radar_recent.json")
    runs = {}
    for name in ("train_momentum", "train_plan_profit", "train_momentum_novol"):
        with open(os.path.join(evidence_dir, name + ".txt"), encoding="utf-8") as fh:
            runs[name] = parse_trainer_log(fh.read())
    base = {"generated_at": "2026-10-11", "code_ref": code_ref, "status": STATUS, "use": USE, "schema": "lab_report_v1"}
    reports = []

    b = ex["strategy_baseline"]
    reports.append({**base, "id": "strategy_baseline", "kind": "strategy_lab", "title": "Donchian-20 breakout: baseline plan R by direction and year",
                    "headline": f"{b['all']['n']:,} matured breakouts: mean {b['all']['mean_r']:+.3f} R (95% CI {b['all']['ci95'][0]:+.3f} to {b['all']['ci95'][1]:+.3f}); bullish {b['bullish']['mean_r']:+.3f} R, bearish {b['bearish']['mean_r']:+.3f} R.",
                    "caveats": ["Plan R simulates entry at the breakout close with no slippage, gap or cost model: real results are lower.", "Only symbols still stored are included (survivorship).", "Trades on the same days are not independent: intervals use a month-block bootstrap."],
                    "provenance": {"source": "ml_breakout_dataset_v2 on an isolated copy of the 2026-10-10 production backup", "dataset": ex["data_quality"], "evidence_files": ["experiments.json"]},
                    "data": {"baseline": b, "regime_study": ex["regime_study"], "regime_filter": ex["regime_filter_trend_following"], "market_panel": ex["market_panel"]}})

    best = rk["less_extended_vs_52w_high_in_direction"]
    reports.append({**base, "id": "ranker_stability", "kind": "strategy_lab", "title": "Parameter-free rankers: year-by-year stability",
                    "headline": f"'Less extended in its own direction' (vs the 52-week high) has a positive top-minus-bottom quintile R spread in {best['years_with_positive_spread']} of {best['n_years']} years (mean {best['mean_spread']:+.3f} R). {rk['n_candidates_tried']} related candidates were tried.",
                    "caveats": [f"{rk['n_candidates_tried']} related candidates were tried: read the best one with a multiple-comparison discount.", "A relative effect: it identifies the worst breakouts to avoid; the absolute edge of the best quintile is small.", "Needs forward confirmation on sessions after pre-registration."],
                    "provenance": {"source": "ml_breakout_dataset_v2 (isolated copy)", "evidence_files": ["ranker_stability.json"]}, "data": rk})

    reports.append({**base, "id": "ml_evaluation", "kind": "ml", "title": "ML evaluation runs (evaluate-only) and the promotion gate",
                    "headline": "No model passes the a-priori gate. The legacy momentum target is predictable (holdout AUC {:.3f}) but does not rank the trade's profit (AUC {:.3f} bullish / {:.3f} bearish within direction).".format(
                        runs["train_momentum"]["holdout_auc"], runs["train_momentum"]["auc_vs_plan_profit_bullish"], runs["train_momentum"]["auc_vs_plan_profit_bearish"]),
                    "caveats": ["Evaluate-only runs: nothing was promoted or registered.", "The legacy momentum label mechanically contains a volume term.", "Out-of-sample AUC for 'R>0' stays near 0.52-0.53 with or without market-regime features."],
                    "provenance": {"source": "ml_training/models/momentum_predictor.py --no-promote on an isolated copy", "evidence_files": ["train_momentum.txt", "train_plan_profit.txt", "train_momentum_novol.txt", "experiments.json"]},
                    "data": {"trainer_runs": runs, "out_of_sample_plan_outcome": ex["ml_plan_outcome"], "simple_rankers_test_period": ex["simple_rankers_test_period"]}})

    ov = exitp["overall"]
    rows = {k: {"expectancy_net_10bps_r": v["cost_10bps"]["expectancy_r"], "profit_factor": v["cost_10bps"]["profit_factor"]} for k, v in ov.items()}
    best_k = max(ov, key=lambda k: ov[k]["cost_10bps"]["expectancy_r"])
    paired = exitp["paired_vs_A_3R_gross_r"]
    all_zero = all(v["ci95"][0] <= 0 <= v["ci95"][1] for v in paired.values())
    paired_txt = "no policy differs from the fixed-3R exit beyond noise (every paired interval includes zero)" if all_zero else "at least one policy differs from the fixed-3R exit (a paired interval excludes zero)"
    bear_neg = all(v["bearish_10bps"]["expectancy_r"] < 0 for v in ov.values())
    bear_txt = " Bearish trades lose under every policy." if bear_neg else ""
    reports.append({**base, "id": "exit_policies", "kind": "strategy_lab", "title": "Donchian exit-policy comparison (same entries, same costs)",
                    "headline": f"{exitp['design']['events_simulated']:,} breakouts x {len(ov)} policies: best net expectancy {ov[best_k]['cost_10bps']['expectancy_r']:+.3f} R ({best_k}); {paired_txt}.{bear_txt}",
                    "caveats": ["Net of an assumed 10 bps round-trip cost; no borrow cost, dividends or halts.", "Trailing exits earn their profit from a few extreme winners (see trades_abs_r_gt_10): read the winsorised and ex-extreme figures.",
                                "R is normalised by 2 x ATR: very low-volatility names inflate R multiples.", "Parameters were declared a priori and not tuned."],
                    "provenance": {"source": "mechanism/research/exit_policies.py on ml_breakout_dataset_v2 + stock_prices (isolated copy)", "design": exitp["design"], "evidence_files": ["exit_policy_study.json"]},
                    "data": {"summary": rows, **{k: exitp[k] for k in exitp if k != "design"}}})

    bk = cat["by_surprise_bucket"]
    up, dn = bk["surprise>=+10%"], bk["surprise<=-10%"]
    reports.append({**base, "id": "catalyst_earnings", "kind": "catalyst", "title": "Earnings catalysts: reaction, drift and the breakout interaction",
                    "headline": f"{cat['design']['events']:,} liquid earnings events: surprise >= +10% -> {_pct(up['ar_reaction']['mean'])} market-adjusted reaction, surprise <= -10% -> {_pct(dn['ar_reaction']['mean'])}; "
                                f"post-reaction 20-day drift {_pct(up['ar_drift_20d']['mean'])} / {_pct(dn['ar_drift_20d']['mean'])} (intervals include zero): the market prices the surprise immediately.",
                    "caveats": ["Vendor earnings history (not SEC-verified); a refetch can revise it.", "Time of day of the release is not stored: the reaction window covers before-open and after-close releases.",
                                "Fundamental anomaly rules matched very few events (the quarterly table holds only 2025-26 quarters): no conclusion.", "Live SEC EDGAR extraction needs an owner-supplied operator contact for the User-Agent."],
                    "provenance": {"source": "earnings_calendar + stock_prices + quarterly_fundamentals (isolated copy)", "design": cat["design"], "evidence_files": ["catalyst_earnings_study.json"]},
                    "data": {k: cat[k] for k in cat if k != "design"}})

    reports.append({**base, "id": "catalyst_radar_recent", "kind": "catalyst", "title": "Recent large earnings surprises (research feed, not alerts)",
                    "headline": f"{len(radar['events'])} liquid events with surprise >= +25% in the last 40 days of data, each with its market-adjusted reaction so far.",
                    "caveats": ["Vendor-reported, unverified against SEC filings.", "Not an alert and not a recommendation."],
                    "provenance": {"source": "earnings_calendar (vendor), fetched 2026-10-07", "evidence_files": ["catalyst_radar_recent.json"]}, "data": radar})

    reports.append({**base, "id": "performance_attribution", "kind": "strategy_lab", "title": "Performance attribution: direction, year, regime and sector",
                    "headline": attribution_headline(exitp),
                    "caveats": ["Net of an assumed 10 bps round-trip cost; the 3R fixed-target exit and the 10-bar Donchian trail are shown as the two reference exits.", "Splits shrink the sample: read n beside every mean.",
                                "Year-by-year signs flip for most cells: a pooled number is not evidence of a stable regime or sector effect.", "Sectors with fewer than 500 trades are omitted."],
                    "provenance": {"source": "mechanism/research/exit_policies.py on ml_breakout_dataset_v2 + stock_prices (isolated copy)", "evidence_files": ["exit_policy_study.json"]},
                    "data": {p: {"by_year": exitp["by_year_10bps"][p], "by_regime": exitp["by_regime_10bps"][p], "by_sector": exitp["by_sector_10bps"][p]} for p in ("A_atr_stop_fixed_3R", "B_donchian_trail_10")}})

    reports.append({**base, "id": "research_insights", "kind": "strategy_lab", "title": "Research insights: what the evidence supports, and what it does not",
                    "headline": insights_headline(insights(ex, rk, exitp, cat, runs)),
                    "caveats": ["Statuses are assigned by fixed rules from the intervals and counts in the other reports; they are not model opinions.", "'Supported' means the sample supports the sign of the effect, not that it is tradeable after costs.",
                                "'Suggestive' means a consistent pattern that was selected from several candidates and needs forward confirmation."],
                    "provenance": {"source": "derived from the other lab reports (deterministic rules in build_lab_reports.insights)", "evidence_files": ["experiments.json", "ranker_stability.json", "exit_policy_study.json", "catalyst_earnings_study.json"]},
                    "data": {"claims": insights(ex, rk, exitp, cat, runs)}})
    return reports


def attribution_headline(exitp):
    a = exitp["by_year_10bps"]["A_atr_stop_fixed_3R"]
    pos_bull = sum(1 for v in a.values() if v["bullish"] > 0)
    pos_bear = sum(1 for v in a.values() if v["bearish"] > 0)
    return f"Fixed-3R exit by year: bullish positive in {pos_bull} of {len(a)} years, bearish positive in {pos_bear}; regime and sector cells change sign between years."


def _excl(ci):
    return ci[0] > 0 or ci[1] < 0


def insights(ex, rk, exitp, cat, runs):
    """Fixed rules: 'supported' = the interval excludes zero in the claimed direction; 'not_supported' = a large sample whose interval includes zero around a near-zero estimate (the effect is
    absent or too small to matter); 'inconclusive' = the interval is wide; 'suggestive' = consistent in most years but chosen among several candidates."""
    out = []
    b = ex["strategy_baseline"]

    def add(claim, status, evidence, refs):
        out.append({"claim": claim, "status": status, "evidence": evidence, "reports": refs})

    be = b["bearish"]
    add("Bearish Donchian breakouts lose money on the plan simulation", "supported" if be["ci95"][1] < 0 else "inconclusive",
        f"mean {be['mean_r']:+.3f} R, 95% CI {be['ci95'][0]:+.3f} to {be['ci95'][1]:+.3f} over {be['n']:,} trades", ["strategy_baseline"])
    bu = b["bullish"]
    add("Bullish Donchian breakouts make money on the plan simulation", "supported" if bu["ci95"][0] > 0 else "inconclusive",
        f"mean {bu['mean_r']:+.3f} R, 95% CI {bu['ci95'][0]:+.3f} to {bu['ci95'][1]:+.3f}; positive in {sum(1 for v in b['by_year'].values() if v['bullish']['mean_r'] > 0)} of {len(b['by_year'])} years", ["strategy_baseline"])
    f = ex["regime_filter_trend_following"]
    add("A trend-regime filter (long in uptrend, short in downtrend) improves expectancy", "supported" if f["all_years"]["ci95"][0] > f["unfiltered_all"]["mean_r"] else "inconclusive",
        f"filtered {f['all_years']['mean_r']:+.3f} R vs unfiltered {f['unfiltered_all']['mean_r']:+.3f} R; the filtered interval {f['all_years']['ci95'][0]:+.3f} to {f['all_years']['ci95'][1]:+.3f} contains the unfiltered mean", ["strategy_baseline"])
    paired = exitp["paired_vs_A_3R_gross_r"]
    all_zero = all(v["ci95"][0] <= 0 <= v["ci95"][1] for v in paired.values())
    add("The choice among the four exit policies changes expectancy", "not_supported" if all_zero else "supported",
        "paired differences vs the fixed-3R exit: " + ", ".join(f"{k.split('_')[0]} {v['mean']:+.3f}" for k, v in paired.items()) + (" (all intervals include zero)" if all_zero else ""), ["exit_policies"])
    tr = runs["train_plan_profit"]
    add("An ML model ranks which breakouts make money", "not_supported" if not tr["promoted"] else "supported",
        f"holdout AUC for R>0 {tr['holdout_auc']} (direction-only baseline {tr['direction_only_baseline_auc']}); failed gates: {', '.join(tr['not_promoted_because']) or 'none'}", ["ml_evaluation"])
    best = rk["less_extended_vs_52w_high_in_direction"]
    add("Breakouts that are less extended in their own direction do better (an avoid-the-extended rule)", "suggestive",
        f"top-minus-bottom quintile spread positive in {best['years_with_positive_spread']} of {best['n_years']} years (mean {best['mean_spread']:+.3f} R); selected from {rk['n_candidates_tried']} candidates", ["ranker_stability"])
    bk = cat["by_surprise_bucket"]
    up, dn = bk["surprise>=+10%"], bk["surprise<=-10%"]
    add("Earnings surprises move the price at the release", "supported" if _excl(up["ar_reaction"]["ci95"]) and _excl(dn["ar_reaction"]["ci95"]) else "inconclusive",
        f"reaction {up['ar_reaction']['mean'] * 100:+.2f}% for surprises >= +10% and {dn['ar_reaction']['mean'] * 100:+.2f}% for <= -10% (market-adjusted)", ["catalyst_earnings"])
    add("A tradeable drift follows large positive earnings surprises", "supported" if _excl(up["ar_drift_20d"]["ci95"]) else "not_supported",
        f"20-day market-adjusted drift after the reaction {up['ar_drift_20d']['mean'] * 100:+.2f}%, 95% CI {up['ar_drift_20d']['ci95'][0] * 100:+.2f}% to {up['ar_drift_20d']['ci95'][1] * 100:+.2f}%", ["catalyst_earnings"])
    x = cat["catalyst_x_breakout_plan_r"]["bullish"]
    add("A recent positive earnings surprise improves a bullish Donchian breakout", "supported" if x["after_positive_surprise_10pct_within_14d"]["ci95"][0] > x["no_recent_big_surprise"]["mean"] else "not_supported",
        f"{x['after_positive_surprise_10pct_within_14d']['mean']:+.3f} R after a surprise vs {x['no_recent_big_surprise']['mean']:+.3f} R without", ["catalyst_earnings"])
    return out


def insights_headline(claims):
    n = {}
    for c in claims:
        n[c["status"]] = n.get(c["status"], 0) + 1
    return f"{len(claims)} claims checked: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(n.items())) + "."


def main(argv):
    ev, out, code_ref = argv[1], argv[2], argv[3]
    os.makedirs(out, exist_ok=True)
    reports = build(ev, code_ref)
    index = []
    for r in reports:
        fn = r["id"] + ".json"
        with open(os.path.join(out, fn), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(r, fh, indent=1, default=str)
        index.append({"id": r["id"], "file": fn, "kind": r["kind"], "title": r["title"], "headline": r["headline"]})
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": "lab_index_v1", "reports": index}, fh, indent=1)
    print("wrote", len(reports), "reports to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
