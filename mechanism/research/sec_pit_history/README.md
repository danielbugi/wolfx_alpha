# SEC point-in-time inflection history: exploratory study (2026-11-03)

**Status: EXPLORATORY.** Nothing here is a validated edge, and it was not run as a confirmatory test. The reserved confirmation window (events public 2023-01-01 to 2025-03-31) was not examined: holdout rows are dropped before any price is read, and the code asserts this.

## What was done
1. **Extract** (`extract.sh`, read-only session). Contents: every SEC raw fact (2,766,288 rows), 79,817 accepted filings, 1,876 companies (one symbol per CIK, as the live step 8e chooses), the latest sector labels, the universe and the production `inflection_v2_sec_pit` events. Files go to `data/` (git-ignored); their sha256 hashes are in `reconstruction.json`.
2. **Reconstruction** (`reconstruct.py`). The same code as nightly step 8e (`inflection.pit.pit_items` followed by `inflection.pipeline.build_symbol_pit`), run over every quarter instead of the last 200 days.
   - Base events only: the composite "limited repricing" event needs prices and is not reconstructed.
3. **Study** (`study.py`, design fixed in its docstring before any outcome was computed). Output: `study_output.txt` and `study_results.json`.
   - The rerun is byte-identical.
   - Prices: the hashed extract `aefa1522…`, 2016-09-20 to 2026-10-30.

## Data quality of the reconstruction
| Check | Result |
|---|---|
| Point-in-time look-ahead audit (`pit.audit_leakage`) | **0 violations** in 99,527 company-quarters |
| Reproduces production (quarters first public from 2026-04-17) | **1,968 / 1,968 base events identical** (date, strength, severity); 0 only in production, 0 only offline |
| Companies / company-quarters / base events | 1,875 / 99,527 / 31,039 |
| Event companies by year | 44 (2009) → 470 (2012) → 755 (2019) → 949 (2022) → 954 (2026) |
| Availability basis | 75.9% SEC acceptance; 24.1% filing date only (no acceptance time; the filing date is never earlier than acceptance, so this errs late) |
| Acceptance time (ET), where known | 60.5% after the close, 27.1% during the session, 12.3% before the open; entry is therefore always the **next** session's open |
| Days from quarter end to first public filing | p5 25 · p50 38 · p95 403 |
| Stale first appearances (more than 120 days) | 8,118 company-quarters (mostly comparatives in a later filing) yield **162 events offline / 4 in production** (DRVN, DIN, FUBO): old quarters reported as new. Excluded from the study. |
| Partial data (no prior-year or previous quarter) | 46.1% of company-quarters |
| Quarter restated since first publication | 15.1%; the stored feature keeps the as-published figures |

**Limitations, not corrected:**
- **Survivorship.** Every company is one that has prices in our database today; delisted, bankrupt and acquired companies are absent. This most likely understates halving rates for events and controls alike.
- **Coverage.** 1,690 of 3,048 mapped universe filers (55.4%); the gate requires 80%.
  - 6,048 mapped CIKs have never been checked by the nightly crawl.
  - Banks, REITs and BDCs are largely missing their core line items.
- **Sector.** The latest label, not point-in-time. It only decides whether Financials and Real Estate are excluded from margin/FCF events.
- **Corporate actions.** Windows touching a recorded price discontinuity are dropped. Symbols come from today's ticker map, so ticker reuse and mergers are not handled.
- **Prices start 2016-09.** With the 252-session history rule, outcomes begin in late 2017. Events from 2009–2016 exist but have no outcome.

## Exploratory findings
**Pre-holdout sample:** public before 2023; 6,577 company-quarters, 1,196 companies, 64 entry months. Excess return is measured against same-date SEC-covered stocks matched on volatility decile and size tercile, with month-equal weighting and 3-month block bootstrap 95% intervals.

| Horizon | Excess (all) | Excess ex-2020 | Doubled / matched | Halved / matched |
|---|---|---|---|---|
| 20 sessions | +0.01% [−0.53, +0.50] | +0.19% [−0.25, +0.60] | | |
| 60 sessions | +0.54% [−0.42, +1.61] | +0.21% [−0.52, +0.91] | | |
| 120 sessions | +1.81% [+0.11, +3.43] | +1.26% [+0.09, +1.77] | | |
| 250 sessions | +3.68% [+0.33, +7.06] | +1.52% [−0.44, +2.51] | 3.6% / 3.4% (+0.37% [−0.25, +1.17]) | 4.3% / 5.4% (−0.70% [−1.39, −0.00]) |

1. **No short-horizon effect.** At 20 and 60 sessions the intervals include zero; the market prices the filing at or before release.
2. **A small long-horizon tilt in the mean, not the median.** The 250-session raw mean is +9.7% against a median of +3.1%. Excluding 2020 the excess roughly halves (+1.5%) and its interval includes zero. At most this is a weak drift, mostly episode-dependent.
3. **Inflections do NOT select future big winners.** The doubling rate matches the matched controls (3.6% vs 3.4%; ex-2020 −0.2%).
   - Reverse view: future doublers had a recent event 26.8% of the time, against 25.8% for all stock-months, a lift of only 1.04×.
   - Within volatility deciles, doubling is +0.68 pp [+0.23, +1.16].
4. **What inflections do is cut the left tail.**
   - Halving is −0.70 pp overall and −2.2 pp [−3.3, −1.3] for company-quarters with 2+ event types.
   - By type: margin expansion −1.7 pp, operating leverage −1.5 pp, profitability turnaround −2.3 pp. All intervals exclude zero.
   - Reverse view, within volatility deciles: −0.97 pp [−1.56, −0.52].
   - This matches the Market Behavior Atlas: the observable fundamental state is a loss filter, not a winner selector.
5. **FCF turnaround is the only type with more doublers:** 7.3% vs 5.7% matched, +1.76 pp [+0.29, +3.65]. This is one of about 40 intervals in the study, so it is a hypothesis, not a finding.
6. **Price state at entry (among inflections):** near the 52-week high, halving is −1.2 pp [−2.25, −0.13] and doubling is not raised. Far below the high, doubling is 5.3% but halving 8.9%. Here too, price state acts as a risk filter.
7. **Post-holdout window** (public from 2025-04, already inspected by the yfinance studies):
   - 823 company-quarters with a 250-session outcome, but only **8 entry months, a single market episode**.
   - High-severity doubled +6.1 pp; stacked +8.0 pp.
   - This contradicts the pre-holdout pattern and shows how strongly the result depends on the regime. It is not evidence.

**Caveats on inference:**
- 250-session windows of neighbouring months overlap, so 3-month blocks probably understate the width of the long-horizon intervals.
- About 40 intervals were computed; a few excluding zero by chance is expected.

## Research readiness
| Gate / requirement | Status | Evidence |
|---|---|---|
| Point-in-time correctness | **PASS** | 0 look-ahead violations; 1,968/1,968 identical to production |
| Holdout untouched | **PASS** | Removed before prices are read (`exploration_events`, tested) |
| Coverage ≥ 80% of mapped filers | **FAIL** | 55.4% |
| Holdout events with complete 20-session windows (≥ 150) | **PASS (data)** | Holdout has 5,444 timely events, 3,659 company-quarters, 1,225 companies and 110 public weeks; counts only, no outcomes. The live gate still reads 0 because production stores only the last 200 days. |
| Independent companies ≥ 100 / public weeks ≥ 40 | **PASS (data)** | 1,225 / 110 (as above) |
| Survivorship-free universe | **FAIL** | Only companies priced today |
| Corporate actions / ticker history | **UNKNOWN** | Discontinuity filter only; no permanent-ID mapping |
| Point-in-time sector | **FAIL (minor)** | Latest label |

**Verdict.** A confirmatory "Fundamental Inflection vs Future Big Winners" test is **not ready**: coverage and survivorship fail. The point-in-time machinery is ready and verified.

Given these exploratory results, the hypothesis worth pre-registering is the **left-tail** one (inflections, especially stacked ones, reduce the matched halving rate), not a big-winner one.

## Reproduce
```bash
bash mechanism/research/sec_pit_history/extract.sh                 # read-only; needs the deploy key
python mechanism/research/sec_pit_history/reconstruct.py --workers 6
python mechanism/research/sec_pit_history/study.py --prices <right_tail/atlas price extract dir> > mechanism/research/sec_pit_history/study_output.txt
```
