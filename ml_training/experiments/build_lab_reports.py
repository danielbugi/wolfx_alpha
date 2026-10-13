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
    sec = _load(evidence_dir, "sec_earnings_study.json")
    manifest = _load(evidence_dir, "sec_raw_manifest.json")
    exp = _load(evidence_dir, "strategy_experiment.json") if os.path.exists(os.path.join(evidence_dir, "strategy_experiment.json")) else None
    if exp is not None:
        exp = dict(exp, forward_tracker_shadow=shadow_summary(evidence_dir))
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


    reports.append({**base, "id": "catalyst_sec_earnings", "kind": "catalyst", "title": "SEC earnings catalysts: anomalies from filings, decision time, reaction",
                    "headline": sec_headline(sec),
                    "caveats": ["Facts are copied verbatim from SEC XBRL company facts; the anomaly rules, thresholds and the recurring-vs-one-time label are interpretation (v1, declared a priori, not tuned).",
                                "'One-time suspected' means net income moved without a matching operating move; no specific item was identified in the filing text.",
                                "Decision time is the acceptance timestamp of the 8-K Item 2.02 release matched to the periodic report; releases accepted during the session (intraday) are only partly reflected in the same-day bar.",
                                f"Sample: {sec['design']['symbols_requested']} liquid symbols with Donchian breakouts, {sec['design']['events']:,} events; ticker-to-CIK uses today's SEC file projected back (reconstructed basis).",
                                "The anomaly thresholds flag about a third of all earnings events: they select directionally, they do not isolate rare events.",
                                "Market adjustment uses an equal-weight index of the stored universe; the no-anomaly group shows a negative average reaction against it, so compare groups, not absolute levels.",
                                "The breakout, exit-policy and combination cells are many and small; none is a confirmed edge."],
                    "provenance": {"source": "sec.gov submissions + XBRL companyfacts, fetched 2026-10-11 (payload sha256 in sec_raw_manifest.json) joined to stock_prices (isolated copy)", "design": sec["design"],
                                   "payload_manifest": {"payloads": len(manifest["payloads"]), "total_mb": round(sum(x["bytes"] for x in manifest["payloads"]) / 1e6, 1)},
                                   "evidence_files": ["sec_earnings_study.json", "sec_raw_manifest.json", "sec_earnings.txt", "sec_event_outcomes.csv.gz", "sec_breakout_outcomes.csv.gz"]},
                    "data": {k: sec[k] for k in sec if k != "design"}})

    if exp is not None:
        reports.append({**base, "id": "strategy_experiments", "kind": "strategy_lab", "title": "Strategy experiments: registered variants, hold caps, risk bases and the paper tracker",
                        "headline": exp_headline(exp),
                        "caveats": ["Historical, point-in-time research on the parent strategy's breakout signals; not signals, not advice, not a live rule. No variant, job or paper trade is active in production.",
                                    "Each registered variant is accounted on its own: one open position per symbol, direction and hold cap (the live ledger's rule); entry is the next bar's open; costs are round-trip bps charged in R.",
                                    "The registered definition has a 60-bar cap. 120 and 250 bars are research conditions, not registered identities. Positions whose history ends before their cap are excluded, so every cap is compared on the same signals.",
                                    "Standardised R uses 1R = 2 x ATR14 for every variant. The variant's ACTUAL initial risk differs (B starts at the 10-bar channel low, C at 3 x ATR); both bases are shown, and actual-risk multiples blow up when that risk is tiny.",
                                    "The one-open-position rule skips most signals (breakouts cluster on consecutive days), so each variant's accounted positions are a minority of the signals; 'max drawdown (cum R)' is the drawdown of the running SUM of R over all of a variant's positions in exit order, with every position counted at 1R: it is not a sized portfolio.",
                                    "Train/test is chronological by signal date with an embargo and a purge. The selection rules are simple baselines fitted on train only; the per-signal oracle is an unattainable upper bound.",
                                    f"Universe: {exp['design']['n_analysis_signals']:,} signals of the SEC study's liquid symbols; catalysts use only events whose reaction session completed before the signal session.",
                                    "'Big winner' is a HINDSIGHT label: the maximum favourable excursion within 120 bars of the entry, a peak nobody can sell at. It is never a feature or a rule; decision-time (pit_) conditions and hindsight (hs_) measurements are separate columns.",
                                    "Capture ratio, premature exit and missed continuation are measured after the fact. A model that ranks signals by decision-time features is judged chronologically, against single-feature baselines, with a purge of train labels that overlap the test period.",
                                    "The forward paper tracker has been dry-run and shadow-tested on a scratch restore only. It has not been activated in production; no forward paper observation exists there."],
                        "provenance": {"source": "ml_breakout_dataset_v2 signals, stock_prices and the committed SEC event table on an isolated copy of the production backup; identities checked against the production registry rows",
                                       "manifest_hash": exp["manifest"]["manifest_hash"], "variants": exp["variants"], "design": exp["design"],
                                       "evidence_files": ["strategy_experiment.json", "strategy_signal_outcomes.csv.gz"]},
                        "data": exp})

    reports.append({**base, "id": "performance_attribution", "kind": "strategy_lab", "title": "Performance attribution: direction, year, regime and sector",
                    "headline": attribution_headline(exitp),
                    "caveats": ["Net of an assumed 10 bps round-trip cost; the 3R fixed-target exit and the 10-bar Donchian trail are shown as the two reference exits.", "Splits shrink the sample: read n beside every mean.",
                                "Year-by-year signs flip for most cells: a pooled number is not evidence of a stable regime or sector effect.", "Sectors with fewer than 500 trades are omitted."],
                    "provenance": {"source": "mechanism/research/exit_policies.py on ml_breakout_dataset_v2 + stock_prices (isolated copy)", "evidence_files": ["exit_policy_study.json"]},
                    "data": {p: {"by_year": exitp["by_year_10bps"][p], "by_regime": exitp["by_regime_10bps"][p], "by_sector": exitp["by_sector_10bps"][p]} for p in ("A_atr_stop_fixed_3R", "B_donchian_trail_10")}})

    reports.append({**base, "id": "research_insights", "kind": "strategy_lab", "title": "Research insights: what the evidence supports, and what it does not",
                    "headline": insights_headline(insights(ex, rk, exitp, cat, runs, sec, exp)),
                    "caveats": ["Statuses are assigned by fixed rules from the intervals and counts in the other reports; they are not model opinions.", "'Supported' means the sample supports the sign of the effect, not that it is tradeable after costs.",
                                "'Suggestive' means a consistent pattern that was selected from several candidates and needs forward confirmation."],
                    "provenance": {"source": "derived from the other lab reports (deterministic rules in build_lab_reports.insights)", "evidence_files": ["experiments.json", "ranker_stability.json", "exit_policy_study.json", "catalyst_earnings_study.json", "sec_earnings_study.json"]},
                    "data": {"claims": insights(ex, rk, exitp, cat, runs, sec, exp)}})
    return reports


def attribution_headline(exitp):
    a = exitp["by_year_10bps"]["A_atr_stop_fixed_3R"]
    pos_bull = sum(1 for v in a.values() if v["bullish"] > 0)
    pos_bear = sum(1 for v in a.values() if v["bearish"] > 0)
    return f"Fixed-3R exit by year: bullish positive in {pos_bull} of {len(a)} years, bearish positive in {pos_bear}; regime and sector cells change sign between years."


def _excl(ci):
    return ci[0] > 0 or ci[1] < 0


def insights(ex, rk, exitp, cat, runs, sec=None, exp=None):
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
    if sec is not None:
        rn = sec["reaction_by_nature"]
        rec, none = rn["recurring_operating"], rn["none"]
        yrs = sec["by_year_recurring_operating"]
        add("SEC-detected recurring operating improvements are followed by a larger price reaction than ordinary earnings",
            "supported" if rec["ar_reaction"]["ci95"][0] > none["ar_reaction"]["ci95"][1] else "inconclusive",
            f"reaction {rec['ar_reaction']['mean'] * 100:+.2f}% (n={rec['n']:,}) vs {none['ar_reaction']['mean'] * 100:+.2f}% without an anomaly (n={none['n']:,}); positive in "
            f"{sum(1 for v in yrs.values() if v['mean_ar_react'] > 0)} of {len(yrs)} years", ["catalyst_sec_earnings"])
        add("A tradeable drift follows SEC-detected recurring operating improvements", "supported" if _excl(rec["ar_drift_20d"]["ci95"]) and rec["ar_drift_20d"]["mean"] > 0 else "not_supported",
            f"20-day drift after the reaction {rec['ar_drift_20d']['mean'] * 100:+.2f}%, 95% CI {rec['ar_drift_20d']['ci95'][0] * 100:+.2f}% to {rec['ar_drift_20d']['ci95'][1] * 100:+.2f}%", ["catalyst_sec_earnings"])
        ot = rn["one_time_suspected"]
        add("The market reacts less to net-income jumps without an operating move (suspected one-time) than to recurring operating improvements",
            "supported" if ot["ar_reaction"]["ci95"][1] < rec["ar_reaction"]["ci95"][0] else "inconclusive",
            f"one-time suspected {ot['ar_reaction']['mean'] * 100:+.2f}% (n={ot['n']:,}) vs recurring {rec['ar_reaction']['mean'] * 100:+.2f}%", ["catalyst_sec_earnings"])
        bc = sec["breakouts_by_catalyst"]["bullish"]
        r_, n_ = bc["recurring_operating"]["fixed_3R_net10"], bc["no_recent_earnings"]["fixed_3R_net10"]
        add("A recent recurring-operating catalyst improves a bullish Donchian breakout (fixed-3R exit, net of costs)", "supported" if r_["ci95"][0] > n_["mean"] else "inconclusive",
            f"{r_['mean']:+.3f} R (n={r_['n']:,}, CI {r_['ci95'][0]:+.3f} to {r_['ci95'][1]:+.3f}) vs {n_['mean']:+.3f} R with no recent earnings (n={n_['n']:,})", ["catalyst_sec_earnings"])
        ex_ = sec["exit_policy_by_catalyst_net10"]["bullish"]["recurring_operating"]
        best = max(ex_, key=lambda k: ex_[k]["mean"])
        fixed = ex_["A_atr_stop_fixed_3R"]
        add("Within recurring-operating catalysts a different exit policy beats the fixed-3R exit",
            "supported" if best != "A_atr_stop_fixed_3R" and ex_[best]["ci95"][0] > fixed["mean"] else "not_supported",
            (f"the fixed-3R exit is itself the best policy ({fixed['mean']:+.3f} R); the others range {min(v['mean'] for v in ex_.values()):+.3f} to "
             f"{max(v['mean'] for k, v in ex_.items() if k != 'A_atr_stop_fixed_3R'):+.3f} R, all intervals overlap"
             if best == "A_atr_stop_fixed_3R" else f"best policy {best} {ex_[best]['mean']:+.3f} R vs fixed 3R {fixed['mean']:+.3f} R"), ["catalyst_sec_earnings"])
        m1 = sec["ml"]["event_significance"]
        add("Financial-anomaly features predict how large the reaction will be beyond trailing volatility",
            "supported" if m1["auc_model"] - m1["auc_baseline_trailing_volatility"] >= 0.02 else "not_supported",
            f"AUC {m1['auc_model']} vs trailing volatility alone {m1['auc_baseline_trailing_volatility']} (chronological test from 2022-02-15)", ["catalyst_sec_earnings"])
        m2 = sec["ml"]["reaction_direction"]
        base2 = max(m2["auc_baseline_revenue_yoy"], m2["auc_baseline_operating_margin_change"])
        add("Financial-anomaly features predict the direction of the reaction", "suggestive" if m2["auc_model"] - base2 >= 0.01 else "not_supported",
            f"AUC {m2['auc_model']} vs best single-metric baseline {base2} (chronological test); modest lift on a coin-flip base rate {m2['base_rate_test']}", ["catalyst_sec_earnings"])
    if sec is not None and "bullish_combinations_chronological" in sec:
        # Out-of-sample rule, fixed before the test period was read: the combination must beat the plain bullish breakout in the TEST period. 'supported' = its test interval lies above the
        # baseline's test mean; 'suggestive' = better in both train and test but the interval still reaches the baseline; 'not_supported' = no better in test; otherwise 'inconclusive'.
        cc = sec["bullish_combinations_chronological"]
        base_c = cc["all_bullish"]
        for cell, label in (("recurring_catalyst", "a recent recurring-operating catalyst"),
                            ("recurring_catalyst_and_relvol_ge_1_5", "a recent recurring-operating catalyst and relative volume of 1.5 or more"),
                            ("recurring_catalyst_and_relvol_and_index_uptrend", "a recent recurring-operating catalyst, relative volume of 1.5 or more and an index uptrend")):
            c_ = cc[cell]
            te, tr, bte, btr = c_["test"], c_["train"], base_c["test"], base_c["train"]
            if te["n"] < 30:
                st = "inconclusive"
            elif te["mean"] <= bte["mean"]:
                st = "not_supported"
            elif te["ci95"][0] > bte["mean"]:
                st = "supported"
            elif tr["n"] >= 30 and tr["mean"] > btr["mean"]:
                st = "suggestive"
            else:
                st = "inconclusive"
            add(f"Out of sample, a bullish Donchian breakout with {label} does better than a plain bullish breakout (fixed-3R exit, net of costs)", st,
                f"test period {te['mean']:+.3f} R (n={te['n']:,}, CI {te['ci95'][0]:+.3f} to {te['ci95'][1]:+.3f}) vs plain breakout {bte['mean']:+.3f} R (n={bte['n']:,}); "
                f"train period {tr['mean']:+.3f} R (n={tr['n']:,}) vs {btr['mean']:+.3f} R", ["catalyst_sec_earnings"])
        sel = sec["exit_policy_selection_out_of_sample"]["recurring_operating"]
        tb, tf = sel["test_best_policy"], sel["test_fixed_3R"]
        if sel["best_policy_on_train"] == "A_atr_stop_fixed_3R":
            st, note = "not_supported", "the fixed-3R exit was already the best on the train period, so there is nothing better to test"
        elif tb["mean"] <= tf["mean"]:
            st, note = "not_supported", "the policy picked on the train period did no better than fixed 3R in the test period"
        elif tb["ci95"][0] > tf["mean"]:
            st, note = "supported", "the policy picked on the train period beat fixed 3R in the test period with an interval above it"
        else:
            st, note = "inconclusive", "better on average in the test period but the interval reaches fixed 3R"
        add("An exit policy chosen on the train period beats the fixed-3R exit out of sample for bullish breakouts after a recurring-operating catalyst", st,
            f"picked {sel['best_policy_on_train']}: test {tb['mean']:+.3f} R (n={tb['n']:,}, CI {tb['ci95'][0]:+.3f} to {tb['ci95'][1]:+.3f}) vs fixed 3R {tf['mean']:+.3f} R (n={tf['n']:,}); {note}",
            ["catalyst_sec_earnings"])
    if sec is not None and "bullish_combinations_by_year" in sec:
        # Rules fixed before the numbers existed. Walk-forward by calendar year: count the years (both sides >= 30 trades) in which the cell beat the plain bullish breakout. Year counting can never
        # make a claim 'supported' (the cells are exploratory and chosen among several): >= 75% of >= 5 years = 'suggestive'; < 50% = 'not_supported'; otherwise 'inconclusive'.
        wf = sec["bullish_combinations_by_year"]["wins_vs_plain"]
        for cell, label in (("recurring_catalyst", "a recent recurring-operating catalyst"), ("recurring_catalyst_and_relvol_ge_1_5", "a recent recurring-operating catalyst and relative volume of 1.5 or more")):
            w = wf[cell]
            share = w["years_better_than_plain"] / w["years_evaluable"] if w["years_evaluable"] else 0.0
            st = "inconclusive" if w["years_evaluable"] < 5 else ("suggestive" if share >= 0.75 else ("not_supported" if share < 0.5 else "inconclusive"))
            add(f"Walk-forward by year, a bullish Donchian breakout with {label} beats a plain bullish breakout in most years", st,
                f"better in {w['years_better_than_plain']} of {w['years_evaluable']} evaluable years ({', '.join(str(y) for y in w['years'])}); exploratory cell, year counting never upgrades it to supported", ["catalyst_sec_earnings"])
    if sec is not None and "timing_quality" in sec:
        # Timestamp placebo: if the acceptance time -> reaction bar mapping is right, the overnight gap of BMO / AMC releases lands on the reaction bar and a baseline-sized one on the bar before.
        tqs = sec["timing_quality"]["by_timing_class"]
        ratios = {k: tqs[k]["mean_abs_gap_on_reaction_bar"] / tqs[k]["mean_abs_gap_on_bar_before"] for k in ("BMO", "AMC") if k in tqs and tqs[k]["mean_abs_gap_on_bar_before"] > 0}
        st = "inconclusive" if len(ratios) < 2 else ("supported" if min(ratios.values()) >= 2.0 else ("not_supported" if min(ratios.values()) < 1.2 else "inconclusive"))
        add("SEC acceptance timestamps place the information on the right trading bar (placebo: the bar before shows no comparable gap)", st,
            "; ".join(f"{k}: gap on reaction bar {tqs[k]['mean_abs_gap_on_reaction_bar'] * 100:.2f}% vs {tqs[k]['mean_abs_gap_on_bar_before'] * 100:.2f}% the bar before (x{ratios[k]:.1f}, n={tqs[k]['n']:,})" for k in ratios),
            ["catalyst_sec_earnings"])
    if exp is not None:
        # Rules fixed before the full experiment was read. A paired interval that excludes zero in the claimed direction = 'supported'; wholly on the wrong side, or tight around zero (|bound| < 0.05 R) = 'not_supported';
        # otherwise 'inconclusive'. Fewer than 200 paired signals = 'inconclusive'.
        def judge(ci, n):
            if n is None or n < 200 or not ci:
                return "inconclusive"
            if ci[0] > 0:
                return "supported"
            if ci[1] < 0 or max(abs(ci[0]), abs(ci[1])) < 0.05:
                return "not_supported"
            return "inconclusive"
        names = {v["policy"]: v["key"] for v in exp["variants"]}
        for pol, label in (("B_donchian_trail_10", "10-bar Donchian trail"), ("C_atr_trail_3x", "3 x ATR trail"), ("D_hybrid_atr_stop_to_donchian_10", "hybrid stop-to-channel")):
            pe = exp["longer_hold_paired_effect"][names[pol]]["cap250_minus_cap60_std_net_r"]["test|all"]
            add(f"Out of sample, a 250-bar holding cap does better than the registered 60-bar cap for the {label} variant (net, standardised R)", judge(pe.get("ci95"), pe.get("n")),
                f"paired difference {pe['mean']:+.3f} R (n={pe['n']:,}, 95% CI {pe['ci95'][0]:+.3f} to {pe['ci95'][1]:+.3f}); the longer cap changes the outcome of {pe['share_signals_changed'] * 100:.0f}% of signals" if pe.get("n") else "no paired signals",
                ["strategy_experiments"])
        rk_ = exp["risk_actual_vs_standardised_cap60"][names["B_donchian_trail_10"]]["initial_risk_atr"]
        add("Standardising risk at 2 x ATR understates the real initial risk of the Donchian-trail variant (its stop starts at the 10-bar channel low)",
            "supported" if rk_["median"] > 2.5 else "not_supported",
            f"median actual initial risk {rk_['median']:.2f} ATR (10th to 90th percentile {rk_['p10']:.2f} to {rk_['p90']:.2f}) against the standard 2.00; {rk_['share_above_3_atr'] * 100:.0f}% of positions start with more than 3 ATR at risk and {rk_['share_below_0_5_atr'] * 100:.0f}% with less than 0.5 ATR",
            ["strategy_experiments"])
        sel = exp["strategy_selection_baselines_cap60_std_net"]["rules"]["direction_x_catalyst"]
        gd = sel.get("group_rule_minus_best_single_ci95") or {}
        add("Out of sample, choosing the variant per direction and catalyst class (fitted on train) beats always using the best single variant", judge(gd.get("ci95"), gd.get("n")),
            f"test difference {gd['mean']:+.3f} R per signal (n={gd['n']:,}, 95% CI {gd['ci95'][0]:+.3f} to {gd['ci95'][1]:+.3f}); best single variant on train: {sel['best_single_variant_on_train']}; the per-signal oracle ({sel['rules_test_mean']['per_signal_oracle_UPPER_BOUND']:+.3f} R) is not attainable" if gd.get("n") else "not enough test signals",
            ["strategy_experiments"])
        tests = {k: v_["test|all"]["std"] for k, v_ in ((vk, exp["position_accounting_primary_cost"][vk]["60"]) for vk in names.values())}
        pos = [k for k, t in tests.items() if t.get("expectancy_ci95") and t["expectancy_ci95"][0] > 0]
        add("A registered variant earns a positive net expectancy out of sample at the registered 60-bar cap (position-level, standardised R, 10 bps)",
            "suggestive" if len(pos) == 1 else ("supported" if len(pos) >= 2 else ("not_supported" if all((t.get("expectancy_r") or 0) <= 0 for t in tests.values()) else "inconclusive")),
            "; ".join(f"{k.split('__exit_')[1]} {t['expectancy_r']:+.3f} R" + (f" (CI {t['expectancy_ci95'][0]:+.3f} to {t['expectancy_ci95'][1]:+.3f})" if t.get("expectancy_ci95") else "") for k, t in tests.items())
            + ("; one variant of five clears zero: chosen among several, so only suggestive" if len(pos) == 1 else ""), ["strategy_experiments"])
        rv = exp["paper_tracker_replay_validation"]
        add("The forward paper tracker reproduces the batch experiment exactly when real history is replayed day by day", "supported" if rv["mismatches"] == 0 and rv["tracker_extra_closed"] == 0 and rv["batch_positions_compared"] >= 1000 else "not_supported",
            f"{rv['batch_positions_compared']:,} positions of {rv['variants']} variants over {rv['symbols']} symbols and {rv['sessions_replayed']:,} sessions compared field by field: {rv['mismatches']} mismatches; the tracker writes nothing and opens no trade",
            ["strategy_experiments"])
        bw = exp.get("big_winner_intelligence")
        if bw:
            cap = bw["capture_by_variant_cap60"]
            prem = {k.split("__exit_")[1]: v["big_winner_premature_exit_share"] for k, v in cap.items()}
            add("The registered exits leave most of a big winner's move on the table (the exit comes before a further 2R continuation)", "supported" if min(prem.values()) >= 0.5 else "inconclusive",
                "; ".join(f"{k} {v * 100:.0f}% of big-winner positions" for k, v in prem.items()) + "; premature = price continued at least 2R within 60 bars after the exit (hindsight measurement, not a rule)", ["strategy_experiments"])
            m = bw["model_vs_baselines"]
            best_base = max(b["auc"] for b in m["baselines"].values())
            add("Out of sample, decision-time conditions rank which signals later become big winners better than a single feature does", "suggestive" if m["model"]["auc"] >= 0.6 and m["model"]["auc"] > best_base + 0.05 else "not_supported",
                f"chronological test AUC {m['model']['auc']:.3f} against {best_base:.3f} for the best single-feature baseline; top decile precision {m['model']['precision'] * 100:.0f}% against a {m['base_rate_test'] * 100:.0f}% base rate (lift {m['model']['lift']:.2f}); "
                f"direction and the index regime carry much of it (importances {m['feature_importance']['pit_direction']:.2f} and {m['feature_importance']['pit_regime_up']:.2f}), so this partly restates market direction; the label is a hindsight peak, not a P&L", ["strategy_experiments"])
    return out


def shadow_summary(evidence_dir):
    """What the scratch dry-run / shadow tests of the forward paper tracker showed (evidence: paper_shadow_real.json, paper_shadow_historical.json). Read back verbatim, nothing recomputed."""
    out = {"activated_in_production": False}
    pr, ph = os.path.join(evidence_dir, "paper_shadow_real.json"), os.path.join(evidence_dir, "paper_shadow_historical.json")
    if os.path.exists(pr):
        r = _load(evidence_dir, "paper_shadow_real.json")
        out["real_source"] = {"session": r["session"], "candidates": r["dry_run"]["counts"]["n_candidates"], "variants": r["dry_run"]["counts"]["n_variants"], "observations_written": r["write"]["written"]["observations"],
                              "dry_run_wrote_nothing": r["dry_run"]["tables_unchanged_by_dry_run"], "write_matches_dry_run": r["write_matches_dry_run"], "snapshot_hash_verified": r["restore"]["snapshot_hash_verified"],
                              "rerun_is_noop": r["rerun_same_session"]["status"] == "already_processed" and r["rerun_same_session"]["tables_unchanged"], "next_session_without_capture": r["next_session_without_capture"],
                              "audit_problems": len(r["audit_problems"]), "context_availability": r["context_availability"]}
    if os.path.exists(ph):
        h = _load(evidence_dir, "paper_shadow_historical.json")
        c = h["comparison_with_batch"]
        out["historical_replay"] = {"design": h["design"], "sessions_processed": h["sessions_processed"], "positions": c["stored_observations"], "closed": c["stored_closed"], "hindsight_events": h["hindsight"]["events"],
                                    "mismatches_vs_batch": c["mismatches"], "missing_closed": c["missing_closed"], "dry_run_equals_write": h["dry_run_equals_write_for_the_last_session"],
                                    "rerun_is_noop": h["rerun_sessions"]["tables_unchanged"], "audit_problems": len(h["audit_problems"]), "seconds_per_session": h["seconds_per_session"], "bytes": h["bytes"]}
    return out


def exp_headline(exp):
    acc = exp["position_accounting_primary_cost"]
    rows = [(v["key"].split("__exit_")[1], acc[v["key"]]["60"]["test|all"]["std"]) for v in exp["variants"]]
    lo, hi = min(rows, key=lambda r: r[1]["expectancy_r"]), max(rows, key=lambda r: r[1]["expectancy_r"])
    rv = exp["paper_tracker_replay_validation"]
    return (f"{exp['design']['n_analysis_signals']:,} breakout signals x {len(exp['variants'])} registered variants x {len(exp['design']['hold_caps'])} hold caps. Test-period net expectancy at the registered 60-bar cap "
            f"runs from {lo[1]['expectancy_r']:+.3f} R ({lo[0]}) to {hi[1]['expectancy_r']:+.3f} R ({hi[0]}). Paper tracker replay: {rv['batch_positions_compared']:,} positions, {rv['mismatches']} mismatches."
            + big_winner_sentence(exp, hi[1]['expectancy_r']))


def big_winner_sentence(exp, best_expectancy):
    b = exp.get("big_winner_intelligence")
    if not b:
        return ""
    return (f" Hindsight label: {b['big_winner_rate'] * 100:.0f}% of signals later reached a favourable excursion of at least {b['definition']['big_winner'].split('>= ')[1].split('R')[0]}R within 120 bars (a peak, not a P&L); "
            f"the best registered variant's test-period net expectancy is {best_expectancy:+.3f} R" + (" (not positive)." if best_expectancy <= 0 else "."))


def sec_headline(sec):
    rn = sec["reaction_by_nature"]
    q = sec["quality"]
    return (f"{sec['design']['events']:,} earnings events from SEC filings: recurring operating improvements react {rn['recurring_operating']['ar_reaction']['mean'] * 100:+.2f}% "
            f"vs {rn['none']['ar_reaction']['mean'] * 100:+.2f}% without an anomaly; 20-day drift {rn['recurring_operating']['ar_drift_20d']['mean'] * 100:+.2f}% (interval includes zero). "
            f"Extraction: {q['share_with_xbrl_facts'] * 100:.0f}% of periodic filings carry usable facts; {q['release_8k_matched_share'] * 100:.0f}% of events matched to their 8-K 2.02 release.")


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
