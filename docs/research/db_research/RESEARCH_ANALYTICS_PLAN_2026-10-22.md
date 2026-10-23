# Research analytics: implementation plan (MFE, post-exit trajectory, c1/c2 comparison, Research Coverage UI)

Status: **plan for owner review**, 2026-10-22. No code is written. No ledger row is modified, and no model is trained.

**Builds on:**
- PR #57 (measured availability): MFE derivable for 2,863 ledger rows, post-exit for 1,529.
- PR #56 (contracts c1/c2 with assignment certainty).
- PR #52's query-shape rule: aggregate once, never per-row lookups into a materialised CTE, and an `EXPLAIN` loops guard.

**Common rules for every metric:**
- **Execution rule:** the live **1R first-target** rule (contract c1/c2: close entry, stop at level, no gaps, no cost). It is never mixed with the 3R backtest `r_single`. Executable variants (next open, gaps, costs) are labelled separately, as in Notebook 02.
- **Provenance:** **derived** from observed `stock_prices` bars, computed at read time. Nothing is written back to `signal_ledger`.
- **Sample-size discipline:** every value is `{value, n, state}` (`no_data` / `preliminary` / `ok`). There is also a **sessions floor**: a metric whose rows span fewer than 5 signal sessions is `preliminary` whatever its `n`. Same-day trades move together; Notebook 01 found the effective sample is closer to the number of days.
- **Path coverage:** each row reports `bars_used` and `bars_expected`. A row is excluded, with a coded reason, if it is held (`evaluation_flag`), if an invalid bar lies on its path (same rule as `evaluate_signal_ledger._invalid_bar_reason`, pinned by a drift test), or if a recorded price discontinuity lies in its window.

## 1. Maximum favourable excursion (in-trade)

- **Definition:**
  - long: `(max(high) − entry) / R`; short: `(entry − min(low)) / R`.
  - Measured over the bars **after** `signal_date`, up to and including the resolving bar (resolved rows) or `last_evaluated_date` (open rows), stopping at the first invalid bar.
  - `R = |entry − stop|`, the ledger's own unit.
- **Query:** one aggregate. Join `stock_prices` on `(symbol, date BETWEEN signal_date+1 AND path_end)`, group by `signal_ledger.id`. That is about 3k rows × ≤ 20 bars. There is no LATERAL; the plan guard asserts every CTE scan runs once.
- **Validation (the key test):** the same walk must reproduce the stored `mae_r` **exactly** for every row (MAE is stored by the evaluator). Any mismatch means the path, scale or window is wrong, and the metric is refused.
- **Served as:** `average_mfe_r` (today `not_available`), MFE distribution buckets, and "share of winners whose MFE exceeded 2R / 3R". This last one is the in-trade evidence for the "exited too early" question.
- **Tests:** a pure path-replay function; parity with `mae_r` on fixtures and on the scratch schema; exclusions (held, invalid bar, discontinuity); the plan-shape guard; sample-size states.

## 2. Post-exit trajectory

- **Definition:** for resolved rows, the directional move from the **exit price** (target, stop or expiry close) to the close **k bars after** `resolved_date`, for k = 1, 5, 10, 20. It is reported in R (entry R unit) and in %.
- **Label:** "after the trade; not part of the strategy's result".
- **Coverage:** per horizon, `n` = rows that already have k bars after exit; the rest are `pending`, never zero. Rows with a discontinuity between exit and exit+k are excluded.
- **Query:** one aggregate using a row-numbered bar sequence per symbol (window function over `stock_prices` for the symbols involved, from the earliest exit). No per-row lookups.
- **Served as:** median and mean post-exit R by exit type (target / stop / expiry) and direction, plus "share of target exits that kept going ≥ +1R within 20 bars".
- **Tests:** horizon edges (bars not yet available give `pending`), weekend and holiday sessions (market-session counting, never calendar days), discontinuity exclusion, and the plan guard.

## 3. Strategy c1/c2 comparison

- **Dimension:** `contract` in `performance.DIMENSIONS`. The SQL CASE is **generated from the registry** (`contracts.contracts_for`), not hand-written: `signal_date >= effective_from_session` picks c2, otherwise c1. A second dimension, `contract_certainty`, comes from the cohort table (high / medium_high).
- **Metrics:** the existing breakdown metrics plus the **censoring-corrected locked-in R by session k** (k = 3, 5, 8, 10; Notebook 01 method). This matters because c1's two cohorts are the oldest and most resolved, so raw resolved-only R would compare unequal maturities.
- **Honest limits, shown in the response:**
  - c1 = 853 rows over **2 sessions**, so it stays `preliminary` under the sessions floor.
  - The guards also changed the universe, so c1 vs c2 is a different-population comparison, not a controlled experiment.
  - Every value cites the contract hashes.
- **Tests:** CASE generation equals the registry (drift); the assignment edges 10-01 / 10-02 / 10-07 / 10-09; locked-in R on fixtures; the sessions floor.

## 4. Research Coverage UI (frontend)

The Research tab (`frontend/src/components/strategies/ResearchTab.tsx`) consumes PR #57's fields:
- **Capability list:** a state badge (available / partial / unavailable), a provenance tag (observed / derived), rows, a period (`first → last`), a coverage bar (`ledger_rows_covered / ledger_rows_total`), "computed here" versus "derivable, not computed yet", and the point-in-time limits as a tooltip.
- **Forward outcomes:** the box shows the real label counts ("29,427 labels, horizons 1/3/5 sessions; summaries not computed yet") instead of "Not collected yet".
- **Lineage coverage:** uses the measured `collected` flags.
- **Signal detail:** the contract chip (`c2 · high certainty`), with the evidence link.

**Tests:** component tests for each state (unavailable, partial, available, derivable), and that `null` is never rendered as 0. This is frontend only and uses no new data.

## 5. Order, cost and release

| Step | Scope | Bound / risk | Release |
|---|---|---|---|
| A | MFE (read-time) + parity with `mae_r` | One aggregate over ≤ ~60k bar rows; plan guard; refused on parity failure | Backend (health-gated, latency thresholds) |
| B | Post-exit trajectory | One windowed aggregate; pending horizons explicit | Backend |
| C | Contract dimension + locked-in R | Pure CASE + existing breakdown SQL | Backend (after #56 is live) |
| D | Research Coverage UI | Display only | Vercel (frontend), after #57 is live |

**Each step:**
- A real-Postgres test in `db-bootstrap-integration`.
- A production-volume read-only latency check in the backend container **before** the deploy (the PR #52 lesson).
- Thresholds declared before release.
- Rollback = the previous backend image or Vercel deployment.

**Not proposed:**
- Storing MFE or post-exit values on the ledger (that would need a migration and evaluator changes).
- Any ML use of these outcome metrics.
