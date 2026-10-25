# Ledger analytics: read-only research plan (Notebook 03)

Status: **plan for owner review**, 2026-10-25. Nothing is implemented, no ledger row is changed, no trading rule changes, and no result here is a claim of profitability.

**Builds on:**
- Notebook 01 (censoring-corrected locked-in R, day clustering) and Notebook 02 (executable simulation `nb02_lib.simulate`, discovery/OOS split, negative OOS result).
- [EXECUTION_RULE_PARITY_AUDIT.md](EXECUTION_RULE_PARITY_AUDIT.md): the live 1R first-target rule vs the 3R backtest `r_single`. Never mixed.
- [RESEARCH_ANALYTICS_PLAN_2026-10-22.md](RESEARCH_ANALYTICS_PLAN_2026-10-22.md): the product-side metric designs for MFE, post-exit trajectory, c1/c2 and the Research Coverage UI. This plan is the **research** counterpart: a notebook, not an API.
- [STRATEGY_VERSION_CONTRACT.md](STRATEGY_VERSION_CONTRACT.md) / contracts c1 (from 2026-09-28) and c2 (from 2026-10-02).

## 0. The ledger today (read-only, 2026-10-25)

| | Bearish (−1) | Bullish (+1) | All |
|---|---|---|---|
| Signals | 2,037 | 1,579 | 3,616 (one row = one signal) |
| Open | 865 | 801 | 1,666 |
| Stopped (−1R) | 566 | 315 | 881 |
| Target 1 / 2 / 3 (+1 / +2 / +3R) | 593 / 9 / 4 | 442 / 17 / 4 | 1,069 |
| Expired | 0 | 0 | 0 |

- 18 signal sessions, 2026-09-28 → 2026-10-23 (no ledger rows for the 09-29 and 09-30 sessions). Median 150 new signals per session (90th percentile 401, max 498).
- Up to 1,938 signals open on the same day.
- **No row has reached the 20-session expiry yet.** Every closed row resolved fast (target or stop). The closed cohort is therefore **not** a sample of the strategy's outcomes; it is a sample of its *quickest* outcomes. Any "resolved-only" figure (win rate, average R) is biased. Section 1 exists for this.

## 1. Closed versus open cohorts

**Question:** how different are the rows that have resolved from the rows still open, and how much does that bias a resolved-only headline?

**Method:**
- **Cohort by signal session.** Every metric is reported per signal session and by sessions elapsed since the signal (k = 1…20), never pooled across unequal ages.
- **Locked-in R by session k** (Notebook 01): for each signal, the R it had locked in by session k: +1 if the target was touched by k, −1 if stopped by k, otherwise 0 (open), and expiry close R at 20. Averaged only over signals that are at least k sessions old. This is censoring-correct by construction.
- **Open-row mark-to-market** (descriptive, labelled as unrealised): the open R at the last evaluated close, by age.
- **Survival view:** a Kaplan–Meier-style curve of time-to-resolution, with stop and target as competing events. The share still open at k = 20 becomes measurable once the 09-28 cohort reaches 20 sessions (from about 2026-10-26).

**Output:** a table of resolved-only vs locked-in-by-k figures with the gap stated, and a dated statement of when each k becomes fully observed.

**Guardrail:** the notebook never prints a resolved-only win rate or mean R without the matching locked-in-by-k figure beside it.

## 2. Bullish versus bearish expectancy

**Question:** is the difference between directions real, or day composition?

**Method:**
- Locked-in R at fixed k (5, 10 and 20 when observable), by direction.
- **Day-cluster bootstrap:** resample sessions, not rows. Same-day signals share one market move, and the effective sample is near the number of sessions (18 today), not the 3,616 rows.
- **Market-move control:** the same figures split by the S&P 500's move over each cohort's window, up versus down. A direction effect that flips with the market is reported as market beta, not as an edge.
- **Multiple comparisons:** Holm correction across every direction × k × split tested.
- **Discovery/OOS:** sessions before the midpoint are discovery and later sessions confirm. With 18 sessions the honest expected result is "insufficient sessions", and the notebook says so.

**Output:** the per-direction expectancy with bootstrap intervals, and an explicit `sessions_needed` estimate for a decision.

## 3. Execution-adjusted R

**Question:** what does the live rule earn when executed as a trader could execute it?

The ledger rule (contract c1/c2) is an idealised **close-entry, stop-at-level, no-gap, no-cost** rule. The notebook reprices each signal with `nb02_lib.simulate`:

| Variant | Entry | Stop/target fill | Cost |
|---|---|---|---|
| A, ledger parity | signal close | at level | 0 |
| B, next open | next session open | at level | 0 |
| C, + gaps | next open | at the open when it gaps through | 0 |
| D, + costs | as C | as C | 10 bps per side (sensitivity: 5 and 20) |

- **Parity first:** variant A must reproduce the stored `outcome_r` for every resolved row, or the notebook stops. This is the selfcheck pattern of Notebook 02.
- Reported per direction and per contract, by locked-in R at k. The A→D decay is the main result.

**Guardrail:** variants B–D are labelled "research simulation, not the strategy's recorded result" wherever they appear.

## 4. MFE and post-exit trajectories

Same definitions as RESEARCH_ANALYTICS_PLAN_2026-10-22 §1–2, computed in the notebook:
- **In-trade MFE:** measured over observed bars after the signal date up to the resolving bar. The same walk must reproduce stored `mae_r` exactly (validation).
- **Intrabar ordering:** a bar that touches both the target and the stop is resolved stop-first, the conservative choice. The notebook reports how many rows that rule decides; if the share is material, results are shown both ways.
- **Post-exit:** the move from the exit price to k = 1, 5, 10, 20 sessions after `resolved_date`. Horizons not yet observed are `pending`, never zero. Rows with a recorded discontinuity in the window are excluded.

**Question answered:** "Did target exits leave money on the table?" The answer is the share of target exits that went on another ≥ 1R within 20 sessions, against the share of stop exits that later recovered to the target.

**Guardrail:** this describes trades that already happened. It is not evidence that a wider target would have been better, because a wider target changes which trades reach it. Any rule comparison goes through section 6's simulation with its own OOS split.

## 5. c1/c2 rule-contract comparison

- **Assignment:** `contracts.assignment()` (PR #56) gives each signal its contract and certainty cohort: 09-28…10-01 c1 high; 10-02…10-06 c2 high; 10-07…10-08 c2 medium-high; 10-09+ c2 high.
- **Metrics:** sections 1–3 per contract, at the same k, with each figure citing the contract hashes.
- **Honest limits, printed with the result:**
  - c1 is 853 rows from only **2** signal sessions (2026-09-28 and 10-01), so it is `preliminary` under the 5-session floor.
  - The guards changed the universe, so c1 vs c2 is a comparison of different populations, not a controlled experiment.
  - c1's cohorts are the oldest and most resolved; only locked-in-by-k is comparable.

## 6. Portfolio-level simulation (overlapping positions, capital constraints)

**Why:** the ledger takes every signal: a median 150 a session, with up to 1,938 open at once. No account can do that, so a per-signal average R is not a portfolio return.

**Model (research only):**
- **Capital:** a notional $100,000 account (a parameter, not advice).
- **Risk per trade:** a fixed fraction of equity at 1R, with sensitivities 0.25%, 0.5% and 1%. Position size = risk ÷ (entry − stop).
- **Capacity:** at most N concurrent positions (5, 10, 20), a gross exposure cap (100% and 200% of equity), and a per-sector cap.
- **Selection when signals exceed free slots** (this matters most), with three pre-registered rules:
  1. random, seeded and averaged over 1,000 draws, as the baseline;
  2. the Strength Score at signal time, only if it is stored point-in-time for that session, else excluded;
  3. by liquidity (20-session dollar volume).

  A rule only counts as better if it beats random out of sample.
- **Execution:** variant D of section 3 (next open, gaps, costs). Exits follow the contract rule. Positions that are still open at the data end are marked to market and labelled.
- **Outputs:** equity curve, CAGR is **not** reported (under 2 months of data), total return over the window, max drawdown, exposure over time, signals skipped for capacity, and turnover. Each comes with day-cluster bootstrap intervals and the random-selection band.

**Guardrails:**
- No look-ahead: only data available at the signal session's close, under point-in-time rules from LEDGER_DATA_AVAILABILITY_MATRIX.md.
- The headline is always "against random selection under the same constraints", never an absolute return.
- No result is published in the product or the channel; it stays internal research.

## 7. Delivery and sequencing

| Step | Content | Depends on |
|---|---|---|
| 1 | `03_ledger_cohorts_and_execution.ipynb` (§1–3, §5) plus a selfcheck (parity with `outcome_r`, locked-in R on fixtures) | Read-only export as for Notebook 02 (`default_transaction_read_only=on`) |
| 2 | §4 in the same notebook (MFE parity with `mae_r`) | Step 1 |
| 3 | `04_portfolio_simulation.ipynb` (§6), random-selection band first | Steps 1–2; ≥ 20 observed sessions for the first full-k cohort (from ≈ 2026-10-26) |
| 4 | Report for owner review: what is measurable now, what needs more sessions, and the dates they arrive | — |

**Not in scope:** migrations, collectors, writers, ML training, changes to the live rule or contracts, and any product or channel claim.
