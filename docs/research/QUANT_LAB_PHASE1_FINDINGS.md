# Quant Lab Phase 1 — audit, real historical results, and the shortest path to a working frontend

Date: 2026-10-11. Status: research results from an ISOLATED scratch copy of the 2026-10-10 production backup (image `5c511899505a`, scratch compose project, removed afterwards). **No production change of any kind**: no pin, schema, flag, timer, arming-file or evidence change; nothing was added to the live signal path.
Raw evidence (this commit): `docs/research/evidence/quant_lab_2026-10-11/`. Re-runnable scripts: `ml_training/experiments/`.

## 1. Headline (read this first)

| Question | Answer from the real data |
|---|---|
| Does the strategy make money? | **Not demonstrated.** 592,687 matured breakouts (2018–2026, 3,034 symbols): mean **−0.018 R** per trade, 95 % CI [−0.063, +0.030]. Bullish **+0.042 R** (CI [−0.024, +0.116], positive in 6 of 9 years); bearish **−0.095 R** (CI [−0.180, −0.007], negative in 7 of 9 years). The plan simulation has **no slippage, gap or cost model** and enters at the breakout close, so real R is lower. |
| Does the ML model help? | **No usable edge.** The legacy "momentum" target is predictable (holdout AUC **0.627**, stable 0.59–0.64 across six walk-forward folds) but that label mechanically contains a volume term and does **not** rank the trade's profit: AUC within direction **0.506 bullish / 0.492 bearish**. Re-targeted at the actual plan profit (R>0): AUC **0.533** (CI [0.509, 0.557]) — *below* the direction-only baseline 0.539. The a-priori promotion gate refuses both. |
| Does a market-regime filter help? | **Not robustly.** "Bullish only when the equal-weight index is above its 200-day average, bearish only below": mean R −0.006 vs −0.018 unfiltered, CI [−0.067, +0.054]; better in 5 of 9 years, worse in 4. Adding regime features to the model lifts out-of-sample AUC 0.522 → 0.531 (within noise). |
| Is anything consistently informative? | **One family of simple, parameter-free rules: do not chase extended breakouts.** Scoring a breakout as better when it is *less extended in its own direction* — a bullish breakout still well below its 52-week high rather than at a fresh high (a bearish one closer to its 52-week high rather than deep below it); less stretched beyond the 50-day average; a weaker 20-day move in its direction; a tighter channel squeeze; a lower ATR % gives a top-minus-bottom quintile R spread that is **positive in 7–8 of 9 calendar years**. The best, "less extended vs the 52-week high", has a mean yearly spread of **+0.168 R** (top quintile +0.038 R, bottom quintile −0.130 R). **Caveat:** 7 related candidates were tried (declared up front); the family-wise chance of ≥8/9 years by luck is roughly 10 %. It is a *risk-avoidance* signal (it mostly identifies the worst 20 %), not a buy signal: dropping the bottom quintile moves the average from about −0.018 R to about +0.010 R — still marginal. It needs forward confirmation, which the R1–R3 capture now provides. |

Consequence for the product: **present these as research findings with their uncertainty, rank by evidence-backed risk labels, and keep ML out of the signal path.** That is also the most useful honest dashboard content available today.

## 2. What exists (audit)

| Area | What is in place | Real data now |
|---|---|---|
| Prices | `stock_prices`: 6,473,771 daily bars, 2016-09-20 → 2026-10-09, 3,114 symbols; 1,041 `price_discontinuities` rows over 123 symbols (flagged, excluded from labels) | yes |
| Research dataset | `ml_breakout_dataset_v2` (builder `build_dataset.py`, features `price_features.py`, one source for train and serve): 600,584 breakouts 2018-01-02 → 2026-10-09, 0 duplicate (symbol,date); 592,687 matured, 7,897 not yet; only `dist_sma200_pct` has >1 % NaN (1.9 %) | yes |
| Strategy engine / outcomes | Plan simulation `plan_outcomes` (2×ATR stop, 2/4/6×ATR tranches, 20 bars); `signal_ledger` 1,614 rows since 2026-09-28; `strategy_analytics` (win/R/held definitions) | ledger yes (short history) |
| Candidate observations | `candidate_observation` / `feature_snapshot` 1,460 rows (session 2026-10-09, 80 guard-rejected kept as evidence), capture run complete, immutable | yes (1 session; grows nightly) |
| ML | Honest trainer `momentum_predictor.py` (chronological holdout + embargo, date-block bootstrap, walk-forward, a-priori gate); `ml_models` has 1 row (2026-09-18, pre-rewrite); `ml_prediction_outcomes`, `ml_performance_metrics` empty | reports exist only inside the pipeline container volume |
| Experiment registry | `experiment_registration`, `experiment_result`, `dataset_manifest`, `forward_return_label` — tables exist, **0 rows** | no |
| Market intelligence | `market_snapshot` / `universe_snapshot` / `stock_relative_strength`: tables exist, **0 rows** until the collector's first run (Tue 2026-10-13 03:45Z); `sector_observation` 3,083 rows | not yet |
| Backend (deployed image `963ab19318b7`) | routers: screener, stock, alpha, strategy(rank), ml_stats, track_record, system_health, strategy_intelligence (summary / data-health / signals / research: capture-runs, candidates, snapshots), market_intelligence (snapshot / history / definitions) | yes |
| Backend (lab branch only) | `strategy_performance` (contract / performance / outcomes / breakdowns) — **not in the deployed image** | no |
| Frontend | pages: `/` dashboard, `/screener`, `/strategy`, `/strategies` + `/strategies/[key]` (tabs: Overview, Directions, Signals, Candidates, Research funnel, Capture health, Data health, Performance), `/ml-stats`, `/track-record`, `/performance`, `/system-health`, `/alerts`, `/telegram`, `/users` | see §4 |
| Not present | any page for market intelligence, a research/lab results page, an opportunity-ranking endpoint with evidence | — |

## 3. Experiments executed (all on real rows, isolated copy)

1. **Honest trainer, evaluate-only (`--no-promote`)**, three configurations, ~75 s each: momentum target; plan-profit target; momentum without the three volume features. Logs in the evidence folder. All three **not promoted**: the momentum target fails only `plan_profit_auc_within_direction` (0.492 < 0.52); plan-profit fails four gates (holdout AUC 0.533 < 0.55, no lift over the direction baseline, top-decile lift 1.10 < 1.15). Removing volume features drops the momentum AUC 0.627 → 0.586, confirming the label's volume term.
2. **Strategy baseline by direction and year**, with a month-block bootstrap (trades on the same days are not independent).
3. **Market-regime study** from the price panel itself (equal-weight index and breadth, point-in-time, 2,528 sessions): trend and breadth splits per direction, per year.
4. **Out-of-sample ML on the exact trade outcome** (train < 2024-01-01, embargo, test ≥ 2024-02-15, 197,150 rows): price features AUC 0.522, +regime 0.531, no volume 0.523; top-decile mean R −0.008 / −0.058 / −0.012 against −0.046 for all.
5. **Ranker stability** for 7 declared parameter-free rankers across 9 calendar years (§1).

**Not yet done / known limits:** transaction costs and next-open fills are not modelled; survivorship (only symbols still in the table; delisted names absent); overlapping consecutive breakouts of one symbol are counted separately; the "extension" scores are `-(pct_from_52w_high × direction)` and `-(dist_sma50_pct × direction)` with `pct_from_52w_high = (close / 252-day high − 1) × 100 ≤ 0`; per-direction quintile spreads (bullish +0.046 / bearish +0.028 R for the 52-week one, 7 and 5 positive years of 9) must be shown separately because the effect is weaker for shorts.

## 4. Readiness by requested module

| Module | Backend | Frontend | Real data now | Gap (smallest) | Needs |
|---|---|---|---|---|---|
| **Strategy Lab / experiment results** | none | none | **yes** (the results above) | read-only endpoint serving versioned research artifacts (JSON with code SHA, dataset size, date) + one page | backend image, Vercel deploy |
| **ML Intelligence** | `/api/ml-stats` exists but only shows registry rows and model files; the three evaluation runs are invisible | `/ml-stats` exists | yes (trainer reports) | add "evaluation runs + gate" from the same artifacts; clear banner "ML is not used in signals" | same as above |
| **Forward Research monitoring** | strategy_intelligence research/capture/data-health endpoints are deployed; no endpoint for scan heartbeat, collector runs or first snapshot | `/strategies/[key]` Research + Data health tabs exist | capture/funnel yes; scan 3 rows; collector from Tue | one small read-only status endpoint + a panel | backend image, Vercel deploy |
| **Market Intelligence Dashboard** | `/api/market-intelligence/{snapshot,history,definitions}` deployed | **no page** | **no** until Tue 2026-10-13 (then one observed snapshot per day) | a page; optional reconstructed history (writes provenance-marked rows — owner decision) or serve the offline regime/breadth series as a research artifact meanwhile | Vercel deploy (+ backfill approval if wanted) |
| **Opportunity Scanner** | `/api/screener`, `/api/strategy/rank`, `/api/alpha/finder` exist but rank by legacy alignment/ML defaults | `/screener`, `/strategy` exist | latest session candidates yes (1,460) | new endpoint ranking the latest session's guard-passing candidates by the evidence-backed extension rank with its measured quintile R, never "AI picks" | backend image, Vercel deploy |

## 5. Shortest path (independently deployable releases; each needs explicit owner approval)

None of these touches the mechanism pin, the collector, scan timers, arming file or database schema.

1. **R-LAB (Strategy Lab + ML Intelligence evidence)** — artifacts in the repo → read-only API → one `/lab` page + a section on `/ml-stats`. No database, no migration, no live-path change. *Fastest real, defensible content.*
2. **R-FWD (Forward Research monitoring)** — one read-only status endpoint (scan rows, collector runs, snapshot counts and stamps) + a panel on the Research tab; becomes meaningful after Tue.
3. **R-MKT (Market Intelligence page)** — frontend page on the deployed endpoints once the first observed snapshot exists (Tue); decide separately on any reconstructed backfill.
4. **R-OPP (Opportunity Scanner)** — evidence-ranked list of the latest session's candidates, each with the measured quintile R and the guard/liquidity flags.
5. **Forward confirmation** — pre-register the "extension" rank (definition, direction, quintile cut, metric) and let the capture accumulate; evaluate on sessions after the registration date only.

Open decisions for the owner: (a) which frontend branch is the baseline — the working tree on `fix/news-and-chart-earnings` has a large uncommitted redesign; I would build the new pages on a clean branch and merge afterwards; (b) approval to build a backend image (separate from the mechanism pin) for R-LAB/R-FWD/R-OPP; (c) whether to backfill reconstructed market history.
