# Alerts (deep value / turnaround) screen — historical-sample backtest

> **Point-in-time research record, 2026-10-03.** Evidence from one ~15-month historical sample, **not** proof that
> the screen has negative (or any) expected value. Not production code: `backtest.py` is read-only and imported by
> nothing. Re-run it rather than trusting these numbers once the data or the rules change.

> **Preserved on branch `feat/deep-value-balance-sheet` (draft PR, dormant).** It references `deploy/db/qf_legacy_snapshot_audit.sql`,
> which belongs to a separate, unmerged QF-cleanup change and is not part of this branch; the audit result it describes
> (980 mis-dated 2025-07-10 snapshot rows excluded by `fiscal_year IS NOT NULL`) is stated in the text below.

## What was tested

The `/alerts` page rules as shipped in `backend/services/deep_value_service.py` (the script imports
`assess_balance_sheet`, `find_year_ago_quarter` and `MAX_QUARTER_GAP_DAYS` from it — no re-implementation):

| Page rule | In the backtest? |
|---|---|
| Near 12-month low: close ≤ 1.15 × min(daily low, last 252 sessions) | Yes |
| Valuation score ≥ 12/25 | **No — cannot be reconstructed** (see Limitations) |
| No balance-sheet check FAILS (equity / leverage / liquidity / cash-flow) | Yes, on the reported quarter itself |
| Turnaround: latest quarter net income > 0, adjacent prior quarter < 0 | Yes |
| Seasonality flag (same quarter a year earlier profitable) | Reported as a split, data permitting |

Two studies:

* **A. Turnaround event study** — every loss→profit flip, then the page-rule subset.
* **B. Near-low baseline** — every stock within 15% of its 12-month low, no fundamentals at all (≈ the
  "Deep Value Watch" list minus the valuation filter).

## Methodology

**Point in time.** A quarter's figures are treated as known only from its earnings report date: the first
`earnings_calendar.report_date` in `(quarter_end, quarter_end + 100 days]`. A quarter without such a report date is
**dropped**, never assigned an assumed lag.

**Entry timing.** Close of the first trading session strictly *after* the report date (reports are often after the
close; same-day entry would use information not yet public at that close).

**Horizons.** Forward return from the entry close to the close **21** and **63** trading sessions later. An event
whose horizon runs past the last price date has no value for that horizon (hence fewer 63-day than 21-day events).

**Benchmark / excess return.** Stock forward return minus the **equal-weighted mean forward return of every clean
symbol over the identical window** (same entry and exit sessions). Benchmark members' returns are clipped to
[−95%, +500%] to blunt residual data errors; the event stock's own return is not clipped.

**Baseline (B).** First trading session of each month; all symbols within 15% of their 252-session low; mean
excess vs the same equal-weight universe; reported per monthly cohort.

**Universe.** Every symbol with prices in `stock_prices` in the window (3,114). Turnaround events additionally need
rows in `quarterly_fundamentals` with `fiscal_year IS NOT NULL` — which **excludes the 980 mis-dated 2025-07-10
migration-snapshot rows** (`deploy/db/qf_legacy_snapshot_audit.sql`).

**Exclusions.** Prices are not split-adjusted (`adj_close = close` throughout). Any one-day close ratio within 3%
of a common split ratio (2, 3, 4, 5, 8, 10, 15, 20 or the inverse) marks a split-like day; an event is dropped if
one falls in its 252-session look-back or its forward window, and such symbols are dropped from the benchmark for
that window (47 split-like days in the panel).

**Data.** The frozen local reference copy of `trading_production`, prices through **2026-09-23**. Production's
`quarterly_fundamentals` was verified identical for the legacy rows on 2026-10-03 (same 15,885 rows, same
fingerprints); prices/earnings were not separately compared.

## Results (`results.txt`, prices 2024-05-23 → 2026-09-23)

**A. Turnaround events** — 827 loss→profit flips with a report date and clean prices, entries 2025-07-25 →
2026-09-11. Excess return vs the equal-weight universe:

| Group | n (21d / 63d) | Mean excess 21d | Mean excess 63d | Beat universe 63d | t (63d) |
|---|---|---|---|---|---|
| All flips | 815 / 556 | +0.1% | −0.6% | 42% | −0.5 |
| Flips **not** near a low | 713 / 479 | +0.4% | +0.9% | 44% | +0.6 |
| Flips near 12-month low | 102 / 77 | −1.7% | −9.3% | 34% | −3.5 |
| **Page rule** (near low + no balance-sheet fail) | **85 / 64** | **−2.3%** | **−9.1%** | **33%** | −3.0 |
| Near low, balance-sheet FAIL | 17 / 13 | +1.3% | −10.3% | 38% | −2.1 |

Seasonality split of the page rule: year-ago quarter profitable 22, a loss 4, unknown 64. Quarterly history before
mid-2025 is sparse, so **every 63-day page-rule event has an unknown year-ago quarter** — the seasonality flag could
not be evaluated at 63 days.

**B. Near-low baseline** — 13 monthly cohorts at 63 days (≈530 stocks each): mean excess **−3.5%**, negative in
**10 of 13** cohorts, mean share beating the universe 34%. At 21 days: −1.0%, 9 of 15 negative.

**Reading.** In this sample, being near a 12-month low was associated with lagging the equal-weight universe, and
a loss→profit flip did not offset that; flips away from the lows were roughly neutral. The balance-sheet filter
removed too few events (17) to say anything about it.

## Limitations — read before using any number above

1. **One short regime.** ~15 months of entries (Jul 2025 – Sep 2026). A period where losers kept losing
   (momentum) would produce exactly this; a different regime may not.
2. **Clustered, overlapping events.** 31 of the 64 page-rule 63-day events come from one fiscal quarter
   (2025-09-30, entries Oct–Nov 2025: mean −18.6%). Windows overlap and are cross-correlated, so the t-statistics
   overstate significance; the effective number of independent observations is a handful of cohorts.
3. **Valuation filter not reconstructable.** `daily_fundamentals.valuation_score` exists only on scattered dates for
   ~1,000 symbols since July 2025, and its inputs (P/E, P/S, P/B as of each date) were never stored point in time.
   The tested population is therefore *broader* than the page's.
4. **Fundamentals are restated values.** `quarterly_fundamentals` holds the provider's *current* figures for each
   quarter (rows are upserted in place), not necessarily what was first reported. The report date makes the
   *timing* point in time, not the *values*.
5. **Report dates come from one source** (`earnings_calendar`, yfinance-derived); a wrong date shifts the entry.
6. **Survivorship.** The universe is symbols present in today's database; delisted names with no clean quarterly
   history (e.g. the 38 inactive symbols whose only quarterly row is a migration-snapshot row) cannot contribute.
7. **Sector is today's**, not point in time — used only to mark the two balance-sheet ratios "n/a" for Financial
   Services.
8. **No costs, no position sizing, no stops.** Close-to-close, equal-weight, gross.

## Reproduce

```bash
python docs/research/2026-10-03_alerts-deep-value-backtest/backtest.py                 # rewrites results.txt
python docs/research/2026-10-03_alerts-deep-value-backtest/backtest.py --price-end 2026-12-31 --out /tmp/r.txt
```

Needs read access to a `trading_production` copy via the repo-root `.env` (`DB_*`); the connection is opened
read-only. Runtime ≈ 5–10 minutes (loads ~28 months of daily prices for the whole universe).
