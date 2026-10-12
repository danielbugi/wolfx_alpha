# Quant Lab phase 2 — multi-strategy research, catalyst intelligence, ML readiness (lab branch; NOT deployed)

Date: 2026-10-11. All experiments ran on an ISOLATED scratch copy of the 2026-10-10 production backup (image `5c511899505a`, own compose project, removed afterwards). No production change: pin,
collector, scan timer, arming file, schema, flags and immutable forward evidence untouched. Evidence: `docs/research/evidence/quant_lab_2026-10-11/`; scripts: `ml_training/experiments/`.
Phase 1 (baseline, regime, ML, ranker stability): [QUANT_LAB_PHASE1_FINDINGS.md](QUANT_LAB_PHASE1_FINDINGS.md).

## 1. Capability audit (what exists; nothing duplicated)

| Capability | Status | Evidence |
|---|---|---|
| Strategy registry | **Operational, one strategy.** `strategies` (1 row: donchian_breakout v1), `research/registry.py` + `repository.resolve_strategy`; capture boundary (id 1) and ledger link to it | tested; live |
| Execution engine / exits | **One engine (the live screener) and one exit model (`plan_outcomes`).** No registry of exit variants existed. | now added: `mechanism/research/exit_policies.py` (24 tests) |
| Signal ledger + outcomes | **Operational.** `signal_ledger` 1,614 rows since 2026-09-28; nightly evaluator; `strategy_analytics` definitions | tested; live |
| Candidate observations / feature snapshots | **Operational, 1 session** (1,460 rows, 80 guard-rejected kept as evidence), immutable | tested; live |
| Historical research / experiment registry | **Implemented and tested, not operational.** dataset manifest, leakage audit, baselines, owner CLI (`research.lab`); `experiment_registration`/`experiment_result`/`dataset_manifest` have 0 rows | tested; empty |
| ML training / evaluation | **Operational nightly (pipeline step 11), evaluate-and-gate.** Honest chronological trainer; nothing passes the gate; `ml_prediction_outcomes` empty | tested; live |
| SEC EDGAR | **Partial.** Contract (`filing_contract`, 45 tests) + pure dry-run collector (`edgar_collector`, tested). **No live transport, no persistence, no scheduler.** The collector refuses to run without an operator name and a real contact email in the User-Agent (SEC fair-access policy). | blocked on an owner-supplied contact |
| Catalyst classification | **Partial.** `catalyst_classification` table + store tested (0 rows); no classifier; facts and interpretation are separated by design | tested; empty |
| News ingestion | **Operational, small.** `news_items` 212 rows (headlines + url + timestamp); not linked to catalysts; headlines alone must never establish a regulatory/clinical fact | live |
| Relative strength / sector | **Implemented, tested; observed snapshots start with the first collection (Tue 2026-10-13).** `sector_observation` 3,083 rows now | tested; live (sector) |
| Frontend | **Strategies workspace, ML stats, track record, system health, screener operational.** No catalyst, exit-policy, market-intelligence or lab-results view existed. | now added: `/lab` |
| Telegram | **Operational production package + private bot.** No experimental-alert contract existed. | now added: `alerts/experimental_alerts.py` (31 tests) |

## 2. Donchian exit-policy comparison (priority 2)

Engine: `mechanism/research/exit_policies.py` — versioned policies with independent per-position accounting (several policies/strategies may hold the same symbol on the same day without sharing state).
Same entries (next open after the breakout close), same risk unit (2 × ATR14), same 60-bar cap, same costs, gap-aware fills, stop-first on ties, no lookahead (mutation-checked). Parameters declared a priori:
A = 2×ATR stop with a fixed 2R or 3R target; B = 10-bar Donchian trailing exit; C = 3×ATR trailing stop, no target; D = 2×ATR initial stop switching to the 10-bar channel after +1R.
Sample: 72,262 breakouts (a deterministic ~12.5 % of 592,687), 361,310 simulated positions.

| Policy | Net expectancy R (10 bps) | 95 % interval | Bullish / bearish | Profit factor | Avg / median bars | Top 5 % of trades' share of gross profit | Trades with \|R\|>10 | Expectancy without them |
|---|---|---|---|---|---|---|---|---|
| A 2R | −0.048 | −0.102 … +0.009 | +0.045 / −0.166 | 0.93 | 18.1 / 13 | 19 % | 8 | −0.048 |
| A 3R | −0.052 | −0.119 … +0.020 | +0.067 / −0.203 | 0.93 | 22.8 / 16 | 24 % | 12 | −0.053 |
| B Donchian trail | −0.054 | −0.121 … +0.024 | −0.003 / −0.143 | 0.90 | 16.7 / 13 | 49 % | 157 | −0.086 |
| C ATR trail | −0.077 | −0.134 … −0.013 | +0.012 / −0.189 | 0.87 | 19.7 / 15 | 41 % | 36 | −0.081 |
| D hybrid | −0.065 | −0.124 … +0.002 | 0.000 / −0.167 | 0.89 | 18.3 / 15 | 45 % | 149 | −0.093 |

* **No exit policy has positive net expectancy across both directions; none beats the fixed-3R exit** (paired differences on the same trades: A2 +0.004, B −0.002, C −0.025, D −0.013 R; every interval includes zero; C comes closest to being worse).
* **Shape differs, expectancy does not.** Trailing exits (B, D) win less often and depend on a few extreme winners: the top 5 % of trades supply 45–49 % of gross profit and 149–157 trades beyond ±10 R carry about +2,000 to +2,300 R; without them B and D are −0.086 and −0.093. The fixed-target exits are steadier (top 5 % = 19–24 %).
* **Costs matter:** at 25 bps round trip all policies are −0.074 to −0.103 R; gross (0 bps) they are −0.031 to −0.060.
* **Direction:** bullish is the only side with a positive result (+0.045 / +0.067 under the fixed-target exits, about zero under the trailing exits); bearish loses under every policy (−0.14 to −0.20).
* **Regime:** every policy's bullish trades did *better* when the equal-weight index was below its 200-day average (e.g. A 3R +0.143 vs +0.051), and bearish trades did worse there (A 3R −0.283); year-by-year the sign is not stable. Per-year, per-sector and breadth splits are in the report.
* Limits: no borrow cost, dividends, halts or portfolio-level position limits; the cumulative-R drawdown of an overlapping-trade sample is not a portfolio drawdown and is not reported as one.

## 3. First catalyst experiment (priority 3)

Chain executed on real point-in-time events: earnings release (= the 8-K Item 2.02 date) → structured facts (EPS actual / estimate / surprise; for 2025-26 the quarter's fundamentals) → anomaly rule → catalyst class →
stock linkage → market-adjusted reaction and post-reaction drift → research result. 39,706 liquid events (price ≥ $2, 20-day dollar volume ≥ $5M), 2,437 symbols, 2021-01 → 2026-09.
Time-correct: the reaction window is [close before the report day → close of the next day] (covers before-open and after-close releases); the drift a trader could capture starts at the **open after** that window.

| EPS surprise | Events | Reaction (market-adj.) | 20-day drift after the reaction |
|---|---|---|---|
| ≤ −10 % | 5,699 | −3.72 % (−4.08 … −3.31) | −0.42 % (−0.99 … +0.19) |
| in line (±2 %) | 4,454 | −1.38 % | −0.24 % |
| ≥ +10 % | 16,376 | +2.45 % (+2.15 … +2.76) | −0.16 % (−0.46 … +0.15) |
| ≥ +25 % | 8,187 | +3.52 % | −0.20 % (−0.52 … +0.15) |

* **The market prices the surprise immediately; there is no post-reaction drift to trade** in this liquid universe (all drift intervals include zero, and the yearly sign of the ≥ +10 % bucket flips between +0.5 % and −0.8 %: no consistent effect).
* **Interaction with breakouts:** a ≥ +10 % surprise within the previous 14 days did not improve bullish breakouts (+0.031 vs +0.037 R baseline); bearish breakouts after a positive surprise were worse (−0.176 vs −0.112 R).
* **Fundamental anomaly rules** (revenue growth ≥ 25 % and +10 pp vs the prior quarter; net-margin jump without an operating-margin move = one-time suspected) matched **7 events**: the quarterly table holds only 2025-26 quarters. No conclusion; this needs SEC XBRL history.
* **Not yet the SEC filing itself.** The live EDGAR step needs an owner-supplied operator contact (name + email) for the User-Agent; I did not invent one or use the owner's personal address. FDA / clinical-trial catalysts need primary sources (FDA documents, ClinicalTrials.gov), never headlines — the alert contract enforces this.
* `catalyst_radar_recent.json`: 40 recent liquid events with surprise ≥ +25 %, each marked "vendor-reported, not SEC-verified" (research feed, not alerts).

## 4. ML readiness by target (priority 4)

| Target | Evaluation | Beats simple baselines? | Status |
|---|---|---|---|
| Catalyst / event significance (is \|market-adjusted reaction\| in the top decile?) | chronological: train < 2024, test ≥ 2024-02-15; threshold fixed on train | AUC 0.703 vs trailing-volatility alone 0.686 and \|surprise\| alone 0.639 | **Valid descriptive target**, small lift; not a trading edge |
| Post-reaction drift after a positive surprise | same split | AUC 0.504 (≈ chance) vs reaction-only baseline 0.508 | **Not predictable** |
| Trade outcome (plan R > 0) | honest trainer + out-of-sample study | AUC 0.522–0.533; below the direction-only baseline 0.539; gate fails | **Not ready; do not promote** |
| Strategy / exit-policy comparison | paired statistics (not ML) | no policy beats fixed 3R | Answered without a model |
| Performance conditional on sector / regime | breakdowns computed | unstable across years | Needs forward data |

No model is promoted; none is wired into the live signal path.

## 5. Frontend and Telegram (priority 5)

* **Built (lab branch, tested, not deployed):** read-only API `/api/lab/reports` (+ `/{id}`; no database; reports must carry provenance, caveats and `research_only` status or they are never served; 18 tests) and the `/lab` page with a view per report
  (strategy baseline, ranker stability, ML evaluation with the gate, exit-policy table, catalyst table and the recent-events feed). `tsc` clean, `next lint` clean, 109 frontend tests pass. Reports are built from the evidence by `build_lab_reports.py` (every headline number is read from the evidence).
* **Telegram alert contract** (`alerts/experimental_alerts.py`): destination is always `experimental`; source URL/accession, publication and ingestion timestamps, extracted facts, verification status, confidence-or-explicitly-none and market context are mandatory;
  FDA/clinical and verified-financial classes are refused unless the source document is verified; vendor figures must be labelled unverified; advice wording is refused. Pure module: it cannot send.
* **Remaining for a Catalyst Radar / Trade Tracker / Performance Attribution UI:** live EDGAR extraction, persistence of facts and classifications, and (for the tracker) per-variant paper accounting in the registry — each needs an owner decision (below).

## 6. Next slices, by research value

1. **Live SEC EDGAR + XBRL companyfacts (needs the operator contact)** → filing-time-stamped (`filed`) quarterly facts back to 2009: unlocks the real anomaly detector, the recurring-vs-one-time test and a statistically useful catalyst sample.
2. **Forward confirmation of the "do not chase extended breakouts" rule** (pre-register the definition; evaluate only later sessions from the capture that already runs).
3. **Register exit-policy variants as strategy identities** (e.g. `donchian_breakout` v1 × exit policy) and account them on the nightly candidates — a registry write, owner approval needed; the engine is ready.
4. **Primary-source regulatory/clinical collectors** (FDA documents, ClinicalTrials.gov) feeding the verified-only alert classes.
5. **Market Intelligence page** once the first observed snapshot exists (Tue), then deploy `/lab` (backend image + Vercel, separate from the mechanism pin).

Decisions needed from the owner: the SEC operator contact; approval to build/deploy a backend image for `/api/lab`; approval for registry writes (variant identities); the frontend baseline branch (`fix/news-and-chart-earnings` has a large uncommitted redesign).
