# Phase 2 — Where we are, what is unfinished, and what makes the system better

Date: 2026-10-21. Sources: the VPS database (read-only pull, 2026-10-21), `01_vps_database_and_ledger_research.ipynb` (same folder), the Quant Lab docs on `main`, and a read-only look at the VPS (pins, timers). Nothing was changed in production.

> **Correction (2026-10-21).** The backtest figures below (−0.011 R overall, longs +0.054 R, shorts −0.094 R) are the dataset `r_single` rule: 3R target, close entry, no gaps, no cost. The live ledger uses a first-target (+1R) exit. Executed (next open, gaps, 10 bps), longs are about +0.01 R and shorts about −0.09 R. See `EXECUTION_RULE_PARITY_AUDIT.md` and `NOTEBOOK02_REPORT.md`.

## 1. The one-paragraph truth

The platform is a well-built **measuring instrument**: a clean signal ledger, immutable research capture, forward labels, a hash-chained paper tracker, a 9-year backtest dataset and a Lab UI. What it has **not** yet produced is a trading edge. The 9-year backtest says the plain Donchian-20 breakout is about break-even before costs (−0.011 R) and slightly negative after them; shorts lose (−0.094 R, positive in 1 of 9 years); longs are marginally positive (+0.054 R gross, +0.035 R after 10 bps, positive in 7 of 9 years). The live ledger's friendlier number (+0.064 R) is inflated by trades that have not finished yet.

## 2. What the data said (this pull)

| Question | Answer | Confidence |
|---|---|---|
| Does the live ledger prove an edge? | No. +0.064 R headline, but the same 1,247 oldest signals drift from 0.00 R (k=3 sessions) to −0.09 R (k=10) as slow losers resolve; 39% still open. | Medium (only 5 daily cohorts are old enough) |
| Are shorts good? | Not in 9 years of data (−0.094 R, 1/9 years positive). The live +0.08 R is early-resolver bias plus a short window. | High on the backtest, low on live |
| Is "cheap / wide-stop / grade F wins" real? | No. Wide-stop spread in the backtest is −0.079 R (positive 3/9 years), the opposite of the live cut. | Medium-high |
| Is "don't chase extended breakouts" real? | The pattern is stable (extension vs SMA50 and 20-day return: positive 8 of 9 years, +0.12 to +0.15 R spread), but as a rank it does not make the book net-positive (top 20%: −0.025 R net). It avoids bad trades; it does not create good ones. | Medium |
| Does ML help? | No. Nothing passes the gate; `ml_prediction_outcomes` and `ml_performance_metrics` are empty. | High |
| Do catalysts help? | Earnings surprises are priced in on the day; 20-day drift is indistinguishable from zero. `market_event` is empty; no live catalyst feed. | Medium-high |
| Do exits fix it? | No. Five exit policies, all slightly negative; paper tracker has 5 sessions and only shows the fastest stop-outs so far. | High (backtest), none yet (live) |

## 3. What exists, by state

**Live and feeding data:** prices (6.5M bars, to 2026-10-20), signal ledger (2,864 rows), candidate capture (13,030 rows, 8 sessions), feature snapshots, forward labels (24,211, 7 sessions), paper tracker (5 sessions, 5 variants, 7,103 open), market snapshots (7 sessions, all NEUTRAL), relative strength, earnings calendar, news (small), Lab reports and `/lab` page.

**Built, not switched on:** Catalyst Phase 1A (SEC filings; migration 37 not applied), market-event tables (empty), experiment registry / dataset manifest (0 rows), the bot-pin separation (PR #11, draft).

**Not built:** a validated model, cost/slippage/next-open modelling in the *ledger*, a catalyst classifier, any second strategy, position sizing / portfolio layer.

## 4. Incomplete tasks, ranked by how much they move profitability

### A. Decide whether there is anything to trade (highest value)
1. **Pre-register the decision rule now.** Example: "no capital until the forward tracker shows net R > 0 with a cluster CI above zero over ≥ 60 sessions, in a named slice (e.g., bullish, non-extended)". Write it down before the data arrives so we cannot rationalise later. Costs nothing.
2. **Fix how the ledger is read**: report censoring-corrected R (locked-in R at k sessions) next to the headline everywhere (dashboard, Telegram, `/track-record`). Today the dashboard shows only resolved trades, which flatters recent results.
3. **Make the ledger realistic**: the ledger enters at the signal close; the paper tracker enters at the next open with 0/10/25 bps. Put net-of-cost R on the dashboard and stop describing the ledger number as expectancy.
4. **Test a bullish-only / non-chasing slice forward**, since that is the only thing the backtest leaves standing. The paper tracker already holds the features to apply the rank; we only need an analysis job (no new data source).
5. **Long-short asymmetry**: the platform sends shorts about 63% of the time. Either drop or de-emphasise bearish signals in anything user-facing until the forward data says otherwise.

### B. Measurement integrity (cheap, prevents wrong conclusions)
6. **Two unrelated "quality grades"** (ledger A–F vs capture A–D). Rename one or store both with distinct column names; add the definition to `definitions.py`.
7. **`model_version = 'unknown'`** on 387 ledger rows makes ML coverage look non-zero. Known since 2026-10-03, still unfixed.
8. **Engine parity check** after ~2026-10-26: compare `outcome_r` (live evaluator) to `r_single` (offline simulator) on the overlap, and explain the 64 live signals the offline detector did not flag.
9. **Evaluator run history** is not persisted (only a log file on the VPS). A small table would remove a blind spot.
10. **Survivorship and delisted names** are absent from the backtest; costs are a flat 10 bps. A size-dependent cost model matters most for the cheap, wide-spread stocks that the live ledger flatters.
11. **Experiment registry is unused** (0 rows). Registering the runs behind each Lab report would make results reproducible and stop repeated-looks problems (7 related rankers already tried).

### C. Operations and risk (from the memory notes; verify before acting)
12. **Stage 2 re-pin** (quarterly-fundamentals time budget). Today the pipeline pin is still `5c511899505a`, so the 10-21 window has not happened (or not yet finished). `quarterly_fundamentals` was last written 2026-10-14. Must land before ~2026-11-06, when the next full cohort falls due.
13. **PR #11** (bot pin separation) is still draft; the old proposal was unsafe.
14. **Migration 33** (news thumbnails) was noted as pending on the VPS; confirm.
15. **Failed units on the VPS**: `checkpoint-d-once` (a finished one-time check) and a missing `scanval-scanb` unit. Harmless but noisy; clean up with approval.
16. **Backups**: no genuinely independent off-box copy yet; no required reviewer on the `production` GitHub environment.

### D. Product and learning
17. **Catalysts**: Phase 1A is validated in scratch only; applying migration 37 needs your approval. Even then, the research says earnings are priced in immediately, so the value is context and filtering, not alpha.
18. **New datasets are on hold** (your 2026-10-11 directive) until the new database design lands. Everything above uses existing data only.
19. **Market regime**: `risk_regime_v1` has said NEUTRAL for all 7 observed days and has no validated link to outcomes. Do not market it as a signal.
20. **ML**: stay out of the signal path. If revisited, the target must be the trade's actual R, with embargo ≥ horizon, and it must beat the direction-only baseline (AUC 0.539), which nothing has.
21. **Frontend punch-list** (`FRONTEND_FIX_MILESTONES.md`) is still mostly open.

## 5. Suggested order

| Step | What | Needs from you |
|---|---|---|
| 1 | Write the pre-registered decision rule (A1) and add censoring-corrected R to the dashboard (A2) | Approval of the rule |
| 2 | Fix grade naming and `model_version` (B6, B7) | Nothing (tested change, normal CI) |
| 3 | Stage 2 re-pin window (C12) | Your go and a time window |
| 4 | ~2026-10-26: engine parity check (B8) | Nothing |
| 5 | Forward analysis job for the bullish / non-chasing slice (A4) on the paper tracker | Nothing (read-only) |
| 6 | Weekly review of the tracker against the rule in step 1 | A 15-minute look |
| 7 | Only after step 6 produces a positive net result: sizing, costs by liquidity, and a second strategy | Decision |

## 6. What I would not do yet

Add more data sources, retrain ML, add exit variants, or tune the grade/rank on the 3 weeks of live data. Each adds more ways to find something by luck, and the forward tracker is the one instrument that cannot be fooled by hindsight.
