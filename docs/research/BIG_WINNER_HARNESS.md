# Big Winner Research Harness — implementation, results, bias report

Status: research only. Nothing here is deployed, scheduled, imported by a service, or connected to the live tracker. No model was trained. No production write, migration, timer or pin change was made.
Harness code: `ml_training/experiments/bw_harness/` (code_ref `7d5339648889`, after the interval correction in section 4a). Evidence: `docs/research/evidence/big_winner_harness_2026-10-17/`.
Design and pre-registration: `docs/research/BIG_WINNER_DISCOVERY.md` (PR #26).

**Evidence class.** The 2018–2026 history was examined by earlier lift tables, so there is no untouched historical holdout. Every historical number below is hypothesis-generation or replication evidence, never confirmation. Only forward data can confirm.

## 1. Implementation summary

| Requirement | Where |
|---|---|
| Read-only dataset generator | `io_read.py` (session refused unless the server reports read-only; SELECT only), `features.py` (pure builder), `run.py` |
| One canonical label | `labels.py`: signal at the close of bar i, entry = open of bar i+1, 1R = 2 x ATR14(i); 3R / 5R / 10R as separate indicators; horizons 20 / 60 / 120 bars; MFE kept apart from the terminal close; incomplete windows are NaN ("immature"), never negatives; price-discontinuity windows excluded. A test proves equivalence with `mechanism/research/opportunity.favourable_excursion` |
| Independent episodes | `episodes.py`: per (symbol, direction), a signal starts a new episode when it is >= 120 bars after the previous kept one. Inference uses episode-first signals only |
| Provenance of every column | `provenance.py`: Observed PIT / Reconstructed / Revised price-derived / Unavailable (plus label-hindsight and key). `check_dataset` fails the run if a column has no class |
| Five pre-registered hypotheses | `hypotheses.py`, thresholds frozen on the train segment, no retuning |
| Matching, within-date, splits, purge, uncertainty, permutation, Holm, negative control | `matching.py`, `splits.py`, `inference.py`, `hypotheses.py` |
| Reproducible outputs | `manifest.json`: dataset / config / results hashes, sample counts, seeds |
| Forward report | `forward.py` (SELECT only; immature never counted as a negative) |
| Survivorship sensitivity, R vs return labels | `survivorship.py`; both label families reported for every effect |

One post-hoc module, `posthoc.py`, is labelled NOT PRE-REGISTERED and produces no verdict (section 3.2).

**Boundary checks (all by test):** no import of the harness from any service, no DB write statement, no model-training import, read-only session enforced.

## 2. Tests and reproducibility

- `python -m pytest ml_training/tests -q`: 124 passed, 2 skipped (3 new interval regression tests) (the full `ml_training/tests` suite, including `test_bw_harness_core.py`, `test_bw_harness_stats.py`, `test_bw_harness_forward_repro.py`).
- Tests cover: label equivalence with the existing excursion code, episode independence, split/purge arithmetic, builder causality (no future bar changes a feature) and determinism, matching balance, null calibration, planted effects (recovered), date-confound handling (Mantel-Haenszel), permutation, Holm, bootstrap, verdict rules, survivorship algebra, forward maturity, hash reproducibility, and the read-only / no-write / no-model guards.
- Permutation-test calibration, measured on 150 synthetic nulls: rejects 6.0% at 0.05 and 13.3% at 0.10 (slightly liberal; Monte Carlo se about 2% and 3%).
- Bootstrap interval coverage was investigated and corrected after owner review: section 4a.
- **Real-data reproducibility.** Two independent runs (separate containers) produced byte-identical dataset, `results.json`, `posthoc.json` and `provenance.json`. The dataset hash was recomputed locally from a copy and matched the manifest.

| Item | sha256 |
|---|---|
| dataset (uncompressed, 602,962 rows x 94 cols) | `c0b0d976ded96f526ba5f715df74b35d6a29a188c7d524f689fedb8aff3e617c` |
| config | `cf3994d94c2a8d14ad99990a92515dbec46fc5900dfbb9a1c9649a90d1868617` |
| results.json | `b24ddeafc0203736e4eac939494c5c88274ba293b4cf327f5087d7bd65804f43` (changed from `32f3f90d…0d1e`: interval correction only) |
| posthoc.json | `98789ae151f4373a7dd561ebb4e9b6fd87d8f987c49f04d13a518207681b8274` (changed from `6ac6ffde…63ee`: same reason) |
| provenance.json | `6e182a0d288ef868dfe41ecc325fddbccef4403ab04895ba8003f70e429fd3a9` (unchanged) |

After the correction the dataset hash and the config hash are **unchanged** (the dataset code and every hypothesis definition and threshold were not touched); only the interval endpoints in results and post-hoc changed. Two further independent runs (A and B, code_ref `7d5339648889`) were again byte-identical. Every point estimate, p-value and verdict is identical to the first run; only interval endpoints moved.

The dataset (about 174 MB gz) is not committed; it regenerates deterministically from the code and the read-only database.
Seeds: 20261017; 1000 bootstrap, 2000 permutation resamples. Signal reconciliation: table vs re-detected signals agree exactly (602,962 / 602,962).

Samples: 602,962 signals, 586,761 eligible, 61,583 independent labelled episodes (31,859 long, 29,724 short). Segments (signals / independent episodes): train 204,111 / 22,971; validation 109,009 / 11,263; test 215,268 / 24,155; embargo 74,549; purged 25.

## 3. Results

Primary label: BW5@120 (>= 5R MFE within 120 bars, R-based), long side primary. RR = treated rate / matched-control rate (3 controls, same direction, +-5 sessions, ATR decile, dollar-volume tercile, 60-day-return tercile). Intervals are 95% month-block bootstrap; p = permutation (matched sets fixed); Holm over H1–H5. MH = Mantel-Haenszel within-date estimate. Pass bar: RR >= 1.25 with interval above 1 in validation **and** test, enough years, Holm, return-label agreement, within-date agreement, negative control null.

### 3.1 Pre-registered (long)

| | Seg | Treated / ctrl candidates | Matched sets | BW5 rate T / C | RR [95%] | p (Holm) | MH within-date | Return-label RR |
|---|---|---|---|---|---|---|---|---|
| H1 RS leadership | val | 714 / 2,587 | **30** | .167 / .211 | 0.79 [0.00, 6.00] | .69 (1.0) | 1.16 [0.90, 1.65] | 0.75 [0.0, 4.0] |
| | test | 1,219 / 4,455 | **34** | .382 / .289 | 1.32 [0.60, 2.19] | .43 (.86) | 1.22 [1.08, 1.37] | 2.18 [1.17, 7.0] |
| H2 Index trend + breadth | val | 1,208 / 4,506 | 289 | .138 / .185 | 0.75 [0.45, 1.02] | .087 (.44) | not estimable | 0.45 [0.13, 0.82] |
| | test | 2,845 / 7,194 | 507 | .288 / .346 | 0.83 [0.67, 1.01] | .031 (.16) | not estimable | 0.99 [0.73, 1.28] |
| H3 Near 52-week high | val | 949 / 2,950 | 175 | .206 / .151 | 1.36 [0.86, 1.93] | .17 (.69) | 1.05 [0.83, 1.27] | 1.20 [0.68, 2.63] |
| | test | 3,123 / 3,572 | 305 | .393 / .339 | 1.16 [0.95, 1.37] | .18 (.72) | 1.10 [0.95, 1.26] | 1.13 [0.92, 1.39] |
| H4 Squeeze | val | 2,743 / 2,971 | 982 | .167 / .147 | 1.14 [0.95, 1.43] | .18 (.69) | 1.19 [1.00, 1.45] | 1.08 [0.80, 1.48] |
| | test | 4,427 / 5,612 | 1,386 | .330 / .310 | 1.06 [0.94, 1.18] | .24 (.72) | 1.17 [1.08, 1.25] | 1.03 [0.90, 1.16] |
| H5 Sector leadership | val | pool empty (no observed sector before 2023-07-11) | – | – | – | – | – | – |
| | test | 3,837 treated / **0** controls | 0 | – | – | – | – | – |
| H0 negative control | val | 1,004 / 3,534 | 525 | .152 / .162 | 0.94 [0.73, 1.14] | .64 | 1.09 [0.96, 1.27] | – |
| | test | 2,374 / 5,441 | 786 | .326 / .348 | 0.94 [0.85, 1.07] | .33 | 1.04 [0.94, 1.15] | – |

**Verdicts (pre-registered rules, applied mechanically):**

- **H1 NOT_SUPPORTED.** Matching is degenerate (30 and 34 sets) because the 60-day-return tercile is almost collinear with 60-bar relative strength. The result is uninformative rather than negative. The within-date estimate (1.22 [1.09, 1.36]) is below the 1.25 bar and is not an independent confirmation.
- **H2 NOT_SUPPORTED, adverse direction.** The "strong market" signals produce fewer big winners than the rest (RR 0.75 and 0.83). H2 is a property of the date, so a within-date estimate does not exist. It has the most consistent sign (both segments below 1; permutation p = .087 / .031, Holm .44 / .16), but after the interval correction both intervals include 1 (validation [0.45, 1.02], test [0.67, 1.01]) and nothing survives Holm. It is **exploratory only**: a hypothesis-generating observation that the reversed direction may be worth pre-registering, never a finding, and it is not used to select anything. No mechanism has been tested.
- **H3 NOT_SUPPORTED.** Point estimates positive (1.36, 1.16), intervals include 1, within-date 1.05 / 1.10 includes 1.
- **H4 NOT_SUPPORTED.** The matched estimate is small (1.14, 1.06). The within-date estimate is above 1 in both segments (1.19 [1.02, 1.43], 1.17 [1.09, 1.25]), the only criterion that passed, but it is below the 1.25 bar. Only 4 years carry >= 500 matched sets, so the year-consistency criterion cannot pass. The matched and within-date estimates disagree (1.06 vs 1.17 in test), a sign that the effect, if real, is small and date-dependent.
- **H5 NOT_EVALUABLE_PROVENANCE.** Observed sector exists from 2023-07-11 only (18.3% of eligible signals). The "3 or more same-sector breakouts in 10 sessions" criterion is true for essentially every signal at this universe size, so there are no controls. Not testable as pre-registered.
- **Negative control.** Long side null in both segments (0.94 / 0.94, intervals include 1). **Short side is not null** (validation RR 0.62 [0.40, 0.87]; within-date 0.57 [0.29, 0.87]). The harness therefore cannot be trusted on the short side. All short-side numbers below are exploratory.

**Net: no pre-registered hypothesis is supported on history, none is refuted except H2 (which is contradicted). Two designs (H1, H5) were not testable.**

### 3.2 Post-hoc sensitivity (NOT pre-registered; no verdict; hypothesis-generating at most)

Matching without the 60-day-return tercile (the cause of H1's degeneracy); H5 without its saturated criterion. This was decided after seeing 3.1, carries multiplicity, and cannot change a verdict. Holm values here are reference only.

| | Seg | Sets | RR [95%] | p (Holm ref.) | MH within-date |
|---|---|---|---|---|---|
| H1 | val | 437 | 1.21 [0.92, 1.68] | .19 (.58) | 1.16 [0.87, 1.60] |
| | test | 547 | 1.18 [0.99, 1.38] | .038 (.11) | 1.22 [1.08, 1.36] |
| H2 | val | 291 | 0.70 [0.43, 1.81] | .032 (.13) | n/a |
| | test | 606 | 0.82 [0.69, 1.00] | .008 (.04) | n/a |
| H3 | val | 414 | 1.42 [1.04, 1.87] | .015 (.093) | 1.05 [0.86, 1.28] |
| | test | 744 | 1.25 [1.04, 1.46] | .0015 (.009) | 1.10 [0.95, 1.26] |
| H4 | val | 1,066 | 1.24 [1.01, 1.55] | .018 (.093) | 1.19 [1.01, 1.44] |
| | test | 1,698 | 1.11 [1.03, 1.18] | .025 (.10) | 1.17 [1.09, 1.26] |
| H5 rank only, observed sector | val | pool empty | – | – | – |
| | test | 100 | 1.16 [0.66, 1.90] | .43 | 1.10 [0.86, 1.38] |
| H5 rank only, reconstructed sector (replication only) | val | 943 | 1.08 [0.91, 1.32] | .42 | 1.06 [0.91, 1.27] |
| | test | 1,000 | 0.95 [0.82, 1.07] | .30 | 0.99 [0.84, 1.13] |

Reading: with a less restrictive match, H3 (near 52-week high) shows RR about 1.25–1.4, but the within-date estimate (1.05 / 1.10) does not corroborate it, which points to a date or cross-sectional composition effect rather than a stock-level one. H1 and H4 are weakly positive (about 1.1–1.2). H2 stays adverse, but its intervals now include 1 (section 4a). Sector-rank leadership alone has no signal: on the 100 observed-sector test sets the interval is wide, and the reconstructed-sector replication (sector history is never point-in-time safe) is null. (An earlier draft of this table showed the reconstructed rows as the observed ones; corrected here.) None of this clears the pre-registered bar.

### 3.3 Short side (secondary; unreliable because the negative control fails)

H4s test RR 1.33 [1.09, 1.51], p = .003, within-date 1.46 [1.26, 1.67] is the only stronger-looking short result; validation 1.24 [1.00, 1.66]. H2s val 0.55 [0.28, 1.10] (adverse). H1s and H5s are sparse. Given a non-null negative control (0.62 / 0.75), do not treat these as findings. They are exploratory, and any short-side claim needs the negative-control failure explained first.

## 4. Data quality and bias report

- **Base rates are high and regime-dependent.** At 120 bars, long: >= 3R 51.0%, >= 5R 28.4%, >= 10R 6.5%; short: 5R 12.4%, 10R 1.8%. Return-based label (MFE >= +50% long): 12.4%. Vol-neutral label (3 sigma): 3.4%. R-based labels reward high-ATR% names less than return labels do, so the two families answer different questions; both are reported everywhere.
- **Horizon matters.** Long BW5 is 1.9% at 20 bars, 12.2% at 60, 28.4% at 120.
- **MFE is not a realised return.** Among long BW5 winners, median MFE 7.28R but median terminal 5.1R; 29.4% finished below half the peak and 7.8% finished negative. "Big winner" here means reached, not kept. Realised outcomes (net R of the five strategy variants) cover only 74,032 signals (391 symbols) and are kept in separate columns; treated-vs-control realised comparisons had only 27 matched sets and are uninformative.
- **Regime concentration.** Long BW5 by year: 12.4% (2022) to 38.0% (2020). Monthly rate ranges 4.7% to 69.5% (median 25.4%); the top 10% of months hold 30.4% of winners vs 21.5% of signals.
- **Overlap and clustering.** 67% of signals follow the previous same-symbol-direction signal within 10 bars; 97.5% within 120. Mean 8.79 signals per episode. Up to 1,720 signals on one date (mean 273). Inference is therefore on episode-first signals with month-block resampling, and the matched ratio is complemented by a within-date estimate.
- **Purge finding.** The first full run found 20 train and 5 validation signals whose 120-bar window crossed a segment boundary (symbols with missing bars). Fixed by purging on the actual window-end date; 25 signals are now in the "purged" segment, and the check passes (0 train windows reach validation, 0 validation windows reach test).
- **Provenance.** Price-derived features are REVISED_PRICE_DERIVED (25 columns; adjusted history can change). Sector, as of signal date, is OBSERVED_PIT only from 2023-07-11 (18.3% of eligible signals); the rest is RECONSTRUCTED (9 columns) and, per project policy, never PIT-safe. 42 columns are label/hindsight and are barred from features. There is **no** Observed-PIT feature in the historical dataset apart from the observed-sector rows; historical research is therefore all price-derived. The forward snapshots are the only truly PIT source.
- **Degenerate or non-estimable designs (reported, not hidden):** H1 (matching), H2 (within-date), H5 (group criterion, plus provenance), short-side negative control.
- **Survivorship.** Companies delisted before 2025 are absent; they cannot be examined. Distinct symbols with signals grow from 1,926 (2018) to 2,970 (2026). The one visible cohort contrast: names listed by 2016 vs later, long BW5 27.9% vs 30.7%. Equal missing share in both groups leaves RR unchanged when the missing outcomes are zero; only uneven missing mass matters. Tipping points (treated-group missing share needed to push RR to 1.0, assuming missing winners are never winners): H1 test 24%, H3 val 26% / test 14%, H4 val 12% / test **6%**. The H4 and H3 effects would disappear under modest uneven survivorship; effects are not robust to it. The R-based and return-based labels are both affected; delisted names skew toward large drawdowns rather than large MFE, so the direction of the bias for MFE labels is plausibly toward overstating the base rate more than any ratio, but this is not measurable from the data.
- **Multiplicity and repeated exposure.** Five hypotheses with Holm; a second matching design post-hoc; earlier lift tables already looked at this history. Treat any positive as a prior, not a finding.
- **Interval method corrected** (section 4a). The residual caveat: coverage was measured on synthetic data with independent months; real data have market-wide dependence across months and repeated symbols, so real coverage may still be lower than 95%. Conclusions that rest on an interval lower bound within about 0.02 of 1 (H4 within-date validation lower bound is 1.0002) are knife-edge and are reported as not distinguishable from 1.

### 4a. Bootstrap coverage: what the 90.7% was, and the fix

The 90.7% came from a 150-dataset run (Monte Carlo se about 2.4 points), with only 150 bootstrap resamples. I re-measured on 3,000 null replicates (matched design, 24 test months, true RR = 1 by construction):

| Interval | Coverage of the true RR | MC se |
|---|---|---|
| plain percentile, B = 150 (the small test) | 92.6% | 0.5 |
| plain percentile, B = 1000 (what the harness used) | **93.7%** | 0.4 |
| plain percentile, B = 5000 | 93.7% | 0.4 |
| Hall expanded percentile, B = 5000 | **95.5%** | 0.4 |
| t-quantile on log RR | 96.0% | 0.4 |

Diagnosis: the 90.7% was mostly Monte Carlo noise plus a small test with too few resamples (a test-design issue); the remaining shortfall is **genuine but mild undercoverage (about 1.3 points)** of the plain percentile interval with about 24 clusters, a known small-cluster effect (the bootstrap variance is (M-1)/M of the sample variance and the normal quantile ignores the M-1 degrees of freedom). More resamples do not fix it (93.7% at B = 5000).

Fix: `inference.interval_levels` / `interval` use the Hall expanded percentile levels, alpha' = Phi(sqrt(M/(M-1)) t_{M-1}(alpha/2)) (1.73 / 98.27 at M = 24), for the rate-ratio, difference, Mantel-Haenszel and realised-return intervals. The method is recorded in results.json (`interval_method`). It is chosen from the null calibration only, not from any real result. Regression tests: level arithmetic, a 1,200-replicate coverage guard (expanded 94.3% vs plain 93.3% on its seed, threshold 93.5%), and the recorded-method check. The permutation tests and point estimates are untouched.

Effect on real results (all verdicts unchanged): intervals widen by roughly 1–4% where there are hundreds of sets (much more with tiny sets: H1 validation [0.30, 2.25] becomes [0.00, 6.00]); H2's upper bounds now include 1 (validation [0.45, 1.02], test [0.67, 1.01]); H4's within-date validation lower bound falls from 1.015 to 1.0002; the short-side negative control still fails (validation [0.38, 0.91]).

Interval-based conclusions that remain downgraded: (1) coverage was verified only on synthetic data with independent months; (2) the bootstrap resamples months, so it assumes months are exchangeable, which real markets violate (regimes), so real coverage is probably somewhat below nominal; (3) with very small sets (H1: 30 and 34) percentile intervals are meaningless (the validation interval is [0, 6]). Treat every interval as approximate, and rely on effect size plus the permutation and within-date evidence together.

### 4b. Final statistical limitations

- No untouched historical holdout; all results are hypothesis-generating or replication evidence.
- H1: only 30 and 34 matched sets because the 60-day-return tercile is collinear with relative strength; the pre-registered estimate is uninformative. The post-hoc relaxation (about 440–550 sets, RR 1.18–1.21, lower bounds near 0.92–0.99) is not pre-registered and gives no verdict.
- H5: not evaluable. Observed sector only from 2023-07-11; the group-breakout criterion leaves no controls; the validation pool is empty. Reconstructed sector history is not point-in-time safe, so replication on it is only a plausibility check.
- H2's reversed direction is exploratory only (intervals include 1 after correction, nothing survives Holm).
- **Short side: the negative control FAILED (validation RR 0.62, interval [0.38, 0.91]; within-date 0.57 [0.27, 0.89]). This is preserved as a warning. No short-side predictive claim is made.** The H4s result (test RR 1.33 [1.08, 1.52]) cannot be separated from whatever makes a null covariate look protective on the short side, and is not evidence.
- Survivorship: delisted names absent; the H4 effect would vanish at about 6% uneven missing mass; see section 4.
- Intervals approximate (4a); permutation test slightly liberal (6.0% at the 5% level).
- Five hypotheses plus a second matching design plus short mirrors: multiplicity beyond the Holm family is not corrected.

### 4c. Production pins re-verified (read-only, from the actual configuration sources, 2026-10-17)

| Component | Source | Value |
|---|---|---|
| Mechanism / pipeline / notices | `/opt/donchian/CURRENT_MECHANISM_SHA`, read by `run_pipeline.sh` | `5c511899505a` |
| Forward collector | same pin; arming file `/opt/donchian/FORWARD_COLLECTION_ARMED` must equal it exactly (checked by `run_forward_collection.sh`) | pin `5c511899505a`, armed file `5c511899505a` (match) |
| Paper tracker | its own `/opt/donchian/CURRENT_PAPER_SHA`; arming file `PAPER_TRACKER_ARMED` (checked by `run_paper_tracker.sh`) | pin `45c2ac39249d`, armed file `45c2ac39249d` (match) |
| Bot | `/opt/donchian/env/mechanism_image_tag.env` (`IMAGE_TAG`), running container | `575fa5dc0599` (container matches) |
| Backend | `/opt/donchian/CURRENT_SHA`, running container | `ecc8ecba8c1b` (container matches) |

Nothing was modified. The earlier cleanup removed the scratch directory and containers; this round's scratch is removed too (final report).

## 5. Forward validation readiness

Forward report (read-only, as of the last bar 2026-10-16): 2,737 triggered signals recorded since 2026-10-09 (2,238 immature, 499 with no entry bar yet); **0 labelled at any horizon**; none counted as a negative. Feature snapshots are complete for 9,407 of 9,789 (partial 382); sector present for 9,690 (from `daily_fundamentals`), missing for 99. Approximate first maturity: 20 bars about 2026-11-09, 60 bars about 2027-01-04, 120 bars about 2027-03-29.

Implication: the 20-bar horizon is the first checkpoint, but BW5@20 has a 1.9% base rate, so power will be low for a long time; the 120-bar label is the meaningful one and cannot be read before about April 2027. Sector is now observed PIT, so H5 becomes testable forward only. The report runs on demand and needs no schedule.

## 6. Proposed next research step (for owner review; nothing here is started)

1. **Pre-registration v2 (documentation only):** fix the degenerate designs without looking at more outcomes: drop the 60-day-return tercile from matching (H1), replace the saturated H5 group criterion with sector-rank only and define it as forward-only, define H2 as a date-level hypothesis with a time-series test (no within-date contrast), and state the minimum matched sets per segment (>= 300). Add H2 in its reversed direction as its own hypothesis because the adverse result was consistent.
2. **Explain the short-side negative-control failure** (likely a mechanical relation between volume expansion and downside range) before any short claim.
3. **Accumulate forward evidence** with the existing read-only report, reading 20-bar labels in November as a sanity check only and 120-bar labels from April 2027 as the confirmatory read.
4. **Optional:** obtain delisting data (separate approval; the "new data sets on hold" directive applies) to turn the survivorship tipping points into a measured bias.

No production integration is proposed. Stop for owner review.
