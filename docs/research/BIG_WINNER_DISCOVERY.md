# Big Winner Intelligence: discovery, point-in-time assessment and research design (read-only; nothing implemented)

Status: 2026-10-17. Research and architecture only. Production was only READ (inventory queries and one memory-capped, read-only analysis container; its temporary credentials file was deleted). No strategy
definition, paper tracker, table, job, timer or pin was touched. Independent of the SEC discovery work and of the 2026-10-21 Stage 2 maintenance. No model was trained.

Question: which point-in-time measurable characteristics distinguish breakout candidates that later reach large favourable excursions (5R, 10R+) from those that do not?

## 1. Existing data inventory

### 1.1 Price history and the historical signal set

| Asset | Content | Facts (2026-10-17) |
|---|---|---|
| `stock_prices` | daily open / high / low / close / adj_close / volume | 6,489,181 rows, 3,114 symbols, 2016-09 .. 2026-10-16. Symbols with a bar: 2,050 (2016) rising to 3,102 (2026) only through new listings (55-219 a year). **47 symbols have a last bar before 2026-10-01 and none before 2025: no company that stopped trading before 2025 is present.** Vendor-revised vintage: the vendor later overwrote history (the ML audit measured ~24% of 2023-2025 indicator rows off by more than 0.5%) |
| `ml_breakout_dataset_v2` | one row per Donchian breakout (close crosses the previous 20-bar channel, previous close inside) that is liquid (20-day dollar volume >= $1M), 60+ bars of history and clear of price discontinuities | **602,962 signals, 3,038 symbols, 2018-01-02 .. 2026-10-16** (333,744 long, 269,218 short); `features` = 22 `price_v1` numbers + 11 sector one-hots; labels `plan_r`, `r_single`, `stopped`, `tp3_hit`, `mae_r` (20-bar trade plan) and the legacy `momentum_score` / `target_binary`. **The whole table was rewritten on 2026-10-17 02:04-02:07Z** (one `created_at` for every row): it is a recomputation from today's prices, not a record of what was known |
| `price_discontinuities`, `price_discontinuity_scan` | 1,041 flagged jumps / non-positive bars, with first-detection time; 12 scan heartbeats | 807 non-positive (2018-2024), 144 jump-up, 90 jump-down |
| `breakouts` | the screener's own historical detections | 66,191 rows from 2023-08-21 (older price basis; not used for research) |

### 1.2 Forward, point-in-time evidenced (since 2026-10-09)

| Asset | Facts |
|---|---|
| `candidate_observation` + `feature_snapshot` | 9,789 observations over 6 sessions (1,456-1,915 evaluated, 250-669 triggered per session); snapshots 9,407 complete, 283 partial with sector, 99 partial without sector; 22 price features + `atr_14`, `dollar_vol_20`, `has_discontinuity_253`, a `missing_features` list, `sector_source` and `sector_asof` |
| `forward_return_label` (fwd_v1) | 14,263 final labels at horizons 1, 3 and 5 sessions so far; the schema only allows 1, 3, 5, 10, 20, 60 |
| `signal_ledger` | 2,630 tracked signals (1,416 open, 1,214 resolved by stop / target) |
| `paper_session` / `paper_observation` / `paper_event` | 2 sessions, 4,985 observations, 3,345 entered, 28 closed; no hindsight event yet (the first needs an entry at least 330 days old) |
| `stock_relative_strength`, `market_snapshot`, `sector_observation`, `sector_performance_daily` | 5 sessions (2026-10-12..16) of observed relative strength and market context; forward sector history (`sector_observation`, 3,084 rows); sector performance from 2026-05-22 |
| `daily_fundamentals` | **dated daily snapshots from 2023-07-11** (528 dates, 3,113 symbols) with sector, industry, market cap, beta, quality scores; 3,087 symbols carry sector and industry |

### 1.3 Experiment datasets and code in the repository

| Asset | Content |
|---|---|
| `evidence/quant_lab_2026-10-11/strategy_signal_outcomes.csv.gz` | 74,032 signals of 391 liquid symbols (2017-03+): decision-time columns (`relvol_signal_bar`, `ret5/20_to_signal`, `atr_pct`, `regime_up_prev_session`, catalyst class) and the net R of the five registered variants at hold caps 60 / 120 / 250 |
| `sec_breakout_outcomes.csv.gz`, `sec_event_outcomes.csv.gz` | 88,604 breakouts with catalyst class and variant R; 9,614 earnings events (timestamps pending the corrected-time rerun) |
| `strategy_experiment.json` `big_winner_intelligence` | the existing Big Winner definition, tiers, capture by variant, decision-time lift tables, one small model against single-feature baselines |
| Code | `mechanism/research/opportunity.py` (excursion, tiers, capture, missed continuation), `ml_training/experiments/big_winner_analysis.py` (purged split, lift tables, AUC), `ml_training/features/price_features.py` (all price features, causal by test), `experiment_runner.signal_features` (`pit_*` columns), `research/lab/*` (manifest-hashed dataset assembly with point-in-time checks; `dataset_manifest` holds 0 rows) |
| Missing entirely | `companies` is empty (no listing or delisting dates); no corporate-action log, float, short interest, options or analyst history; `earnings_calendar` was first fetched 2026-09-23 and is overwritten in place |

## 2. Point-in-time quality assessment

Classes: **O** observed at the time (database-stamped, forward only) · **C** causal from bars up to t, but computed on today's revised price vintage · **R** reconstructed (today's value applied backwards, or built from a biased panel) · **X** missing.

| Feature family | Historical (2018-2026) | Forward (since 2026-10-09) | Notes |
|---|---|---|---|
| Donchian breakout shape (distance past channel in ATR, channel width, squeeze percentile, close location, bar range, gap) | **C** | **O** | causal by test (features on full history equal features on truncated history); vendor vintage can differ from what was known |
| ATR / volatility (`atr_pct`, 14-day ATR) | **C** | **O** | same |
| Relative volume (`vol_ratio_10/50`) | **C** | **O** | volume is also revised by vendors; ratios are scale-free |
| Price / volume structure (returns 5/20/60 d, distance to SMA50/200, 52-week position, RSI, MACD, Bollinger, dollar volume) | **C** | **O** | dollar volume is split-invariant; heavily back-adjusted series (the PARA case) are filtered by the $1M floor |
| Relative strength vs the market | **R** (can be built from bars against an equal-weight index of the surviving universe; SPX only since 2025-09) | **O** (`stock_relative_strength`, 5 sessions) | the historical index inherits survivorship |
| Market regime | **R** (price-based index above its 200-day average, from the survivor panel) | **O** (`market_snapshot`, 5 sessions) | |
| Sector | **R** (the LATEST sector per symbol is applied to every historical signal: `load_sectors`) | **O** (`sector_source`, `sector_asof`) | dated sector exists from 2023-07-11 in `daily_fundamentals` (as of each fetch date) |
| Industry | **X** before 2023-07-11 | dated since 2023-07-11 | never put in the dataset |
| Sector / industry strength, group breadth | **R** | **O** from 2026-10 | |
| Market cap, beta, quality scores | **X** before 2023-07-11 (the builder notes three snapshot windows) | dated daily | |
| Catalysts / earnings | **X** for observed data | SEC feed not live | the earlier study's earnings events are reconstructed and use feed timestamps that are being corrected |
| Delisting and listing events | **X** | **X** | `companies` is empty |

Reading: the price-derived family (shape, volatility, volume, structure) is the only one usable over the full history with a fair "causal" claim. Relative strength, regime, sector and group measures are
reconstructed before 2026-10 and carry survivorship. Everything labelled R must be reported as reconstructed and kept out of any claim of point-in-time proof.

## 3. Big Winner labels: can 5R and 10R be measured consistently?

Four different conventions exist today, so "5R" is not one thing:

| Source | Entry | 1R | Horizon | What it measures |
|---|---|---|---|---|
| `opportunity.py` / `strategy_experiment` / paper tracker hindsight | **open of the bar after the signal** | 2 x ATR14 of the signal bar | 20 / 60 / 120 / 250 bars; tiers 3R / 5R / 10R at 120 | maximum favourable excursion (best high / low reached), hindsight |
| `ml_breakout_dataset_v2` `plan_r`, `tp3_hit`, `mae_r` | close of the signal bar | 2 x ATR14 | 20 bars | a stop-and-target TRADE PLAN with stop-first tie-break; cannot express 5R (targets stop at 3R) |
| `forward_return_label` (fwd_v1) | close of T0 | none (percent) | 1 / 3 / 5 / 10 / 20 / 60 sessions | percent MFE / MAE with `max(high, close)`, benchmark-relative return |
| `signal_ledger` / paper variants | per strategy | strategy stop | until exit | realised R of one exit rule |

**Conclusion:** 5R and 10R are consistently measurable ONLY from bars with the `opportunity.py` convention, over the whole history, and I did so for the full population (below). The stored labels cannot answer it
(the dataset tops out at 3R / 20 bars; fwd_v1 stops at 60 sessions and is in percent). A forward Big Winner label needs a read-only computation from `candidate_observation` plus `stock_prices` (no schema change)
or a new label version later; the paper tracker's own hindsight events arrive about 11 months after entry.

### Measured on the full population (read-only; entry next open, R = 2 x ATR14, windows must be complete and clear of discontinuities; `evidence/big_winner_discovery_2026-10-17/`)

| | Long | Short |
|---|---|---|
| Signals with a complete 120-bar window | 313,476 | 248,007 |
| P(MFE >= 3R / 5R / 10R within 120 bars) | 51.0% / **28.5%** / **6.6%** | 31.6% / **12.2%** / **1.8%** |
| Same at 60 bars: >= 5R / >= 10R | 12.3% / 1.6% | 6.1% / 1.1% |
| Same at 20 bars: >= 5R / >= 10R | 2.0% / 0.2% | 2.1% / 0.4% |
| Return-based label, 120 bars: >= +30% / +50% / +100% | 26.8% / 12.7% / 3.7% | 24.5% / 7.9% / 0.0% |

These reproduce the earlier 391-symbol study (21.3% vs 21.6% overall, 28.5% vs 28.7% long, 12.2% vs 12.3% short) on 8x the symbols: the label is stable across the universe. It is **not rare**: more than a quarter of long
breakouts touch 5R within six months, so "Big Winner" at 5R is a common outcome and 10R (4.5% overall, 6.6% long) is the genuine tail.

**The label depends on the horizon far more than on any feature** (long 5R: 2% at 20 bars, 12% at 60, 28% at 120), and on the year: long 5R rates run from **12.2% (2022) to 41.4% (2020)**, short from 6.3% (2026) to 25.9% (2020). Winners are
concentrated in market-time: the top 10% of months hold 25.3% of long winners against 16.8% of signals; the monthly rate ranges from 7% to 66% (median 27%).

**Denominator behaviour of the R label** (120 bars, by ATR% tercile): for LONGS the 5R rate is almost flat (28.8% / 26.3% / 30.4% low / mid / high ATR%), because ATR normalisation equalises difficulty, while the return-based label explodes
with volatility (>= +50%: 1.6% / 7.6% / 28.9%). For SHORTS the R label is mechanically harder at high ATR% (5R rate 20.5% / 12.2% / 4.1%) because a price cannot fall more than 100%: ten ATR of a 6.8% ATR stock is a 68% drop. So the earlier
"low volatility lifts the Big Winner rate" finding (combined directions) is consistent with a short-side floor effect plus direction mix rather than a long-side volatility edge; separating the two is part of the design.

**MFE is not capturable.** Of the long 5R winners, the median peak is 7.3R but the close at bar 120 is 5.1R; 29.7% ended below half of their peak and 7.9% ended negative.

## 4. Research design

### 4.1 Unit, population, labels
* Unit: a signal (symbol, direction, date t). Decision time = close of t. Population: the frozen Donchian definition, liquid ($1M floor; a $5M variant as sensitivity), 253+ bars of history, no discontinuity in [t-253, t+H], first 253 bars after a listing excluded. Longs and shorts analysed separately, always (direction is the dominant effect).
* Primary label BW5 = MFE_R(120) >= 5 from the next open; BW10 = MFE_R(120) >= 10. Secondary labels: the same at 60 bars; a return-based label (MFE >= +50% long / -40% short) to separate volatility from selection; a vol-neutral label (MFE divided by the 60-day realised daily volatility, not ATR14); a persistence label (close at 120 bars >= half of the peak).
* **MFE and realised results are separate outcomes**, in separate tables: (A) does a feature predict opportunity (MFE tiers); (B) does it predict what an exit rule actually banks (net R of the five registered variants at 10 bps) and the capture ratio; (C) how much of each variant's PnL sits in winners. A feature that lifts BW5 but not realised R is reported as such.

### 4.2 Comparisons
1. **Winner vs non-winner lift tables** within direction and year, buckets fixed in advance.
2. **Matched controls** (primary design): for every BW5 signal, 3 controls drawn from other breakout signals of the SAME direction, within +-5 trading days, matched on ATR% decile, 20-day dollar-volume tercile, 60-day return tercile and (where observed) sector; no control from the same symbol-episode. Compare feature differences with a stratified (conditional) analysis; confidence intervals by symbol-cluster and month-block bootstrap. Matching on ATR% is what neutralises the volatility mechanics of section 3.
3. **Within-date comparison**: all hypotheses are also tested with date fixed effects (winners vs non-winners on the same day) so that a market-wide rally day is not read as a stock-level feature.
4. **Graded outcomes**: dose-response across none / 3R / 5R / 10R, and the early-failure group (stopped within 5 bars), so the contrast is not only "just under 5R vs just over".
5. **Chronological validation**: train signals 2018-01..2021-06, embargo, validation 2022-01..2023-06, test 2024-01..latest signal whose 120-bar window is complete (about 2026-04); rolling-origin by year as a robustness view; buckets and thresholds frozen on train; 180-day label purge between segments (as in the existing harness). Hypotheses are tested once each on validation and once on test.
6. **Negative control**: breakout-bar relative volume (prior lift about 1.0 in both periods) must come out null with the same machinery; if it does not, the machinery is rejected before any result is read.
7. **Permutation null**: labels shuffled within month blocks and direction; every reported lift is placed against that null.

### 4.3 Risks and how the design handles them
| Risk | Handling |
|---|---|
| **Survivorship** (no company that stopped trading before 2025 is in the table; 2,050 survivors at the 2016 start) | cannot be repaired from inside the data. Report every number as "survivors only"; sensitivity bounds (re-weight winners down / assume delisted signals were non-winners at rates from 0 to X%) to show how large a bias would flip a conclusion; prefer within-date and within-cohort contrasts; the forward set is free of it |
| **Label leakage / hindsight** | `hs_*` columns exist only as labels; features built from bars <= t; a test that features computed on data truncated at t equal the stored ones; no feature built from the dataset's own rewritten labels; train-label windows must end before the validation start |
| **Overlapping observations** (67% of signals fall within 10 bars of the previous signal of the same symbol and direction; 97.5% within 120) | the unit of inference is the **independent episode**: greedy non-overlap with 120 bars between kept signals gives **63,891 episodes (33,009 long, 30,882 short; 6.3k-8.6k a year)**, with the same winner rates (27.0% long, 15.0% short). Signal-level numbers are descriptive only; intervals are cluster bootstrapped |
| **Cross-sectional dependence** (mean 273 signals per date, up to 1,720) | date fixed effects; month-block bootstrap |
| **Time heterogeneity** (long 5R from 12% to 41% by year) | all effects reported by year, pooled only with year strata; support requires the same sign in at least 6 of 8 years |
| **Multiple testing** | five pre-registered hypotheses with Holm correction; a wider feature scan allowed only as exploratory with Benjamini-Hochberg FDR and a permutation null, never as confirmation |
| **Data snooping already done** | the earlier lift tables used every period up to 2026-04; there is no unseen historical holdout. Historical findings are hypothesis-grade; confirmation is forward only |
| **Revised vintage** | dollar-volume floor and discontinuity filters; sensitivity on the post-2023 subset; features compared with forward `feature_snapshot` values once enough forward days exist |
| **Mechanical label effects** | both an R label and a return label; ATR% matching; short side analysed with a drop-cap label |

### 4.4 Effective sample
64k independent episodes in total (about 33k long) is ample for effects of ratio 1.1-1.2; what is scarce is regime diversity (one episode per year per market state) and any unbiased universe.

## 5. Five pre-registered hypotheses (priority order; definitions frozen before any result is computed on the new dataset)

Common rules: longs primary, shorts secondary; primary label BW5@120, confirmation labels BW10@120 and the return-based label; matched-control design; validation then test; support criteria in section 5.1.

| # | Hypothesis | Definition (fixed) | Status of prior evidence |
|---|---|---|---|
| **H1** | **Leadership.** Long breakouts of stocks in the top quintile of 60- and 120-day relative strength vs the market have a higher BW5 rate than matched controls from the bottom three quintiles | RS = stock return minus equal-weight index return over the window, ranked cross-sectionally on day t | untested; historical RS is reconstructed (survivor index); forward RS observed |
| **H2** | **Market regime and breadth.** Long breakouts taken when the index is above its 200-day average AND breadth (share of the universe above its 50-day average) is in its top tercile have higher BW5 rates, and **stock-level features add information beyond the market day** (tested with date fixed effects) | index and breadth from the same-day panel | the index-up lift (1.26 in the old test period) was seen already; the new element is breadth and the within-date test |
| **H3** | **New-high breakouts.** Long breakouts within 5% of the 52-week high have higher BW5 than those more than 20% below it | `pct_from_52w_high` | untested |
| **H4** | **Compression.** Breakouts from channel-width squeeze (squeeze percentile <= 20) beat matched controls AFTER matching on ATR% and on the return label; the contrast is evaluated on the vol-neutral label so it is not the section 3 mechanics | `chan_squeeze_pctile` | untested; the old "low ATR%" finding is consistent with a short-side floor effect (section 3) |
| **H5** | **Group leadership.** Long breakouts in a sector whose 60-day relative strength ranks in the top 3 of 11, or with at least 3 other same-sector breakouts in the previous 10 sessions, have higher BW5 | sector and industry only as observed (daily_fundamentals from 2023-07-11) for the confirmatory run; earlier years reported as reconstructed | untested; limited point-in-time history |

**Negative control H0:** breakout-bar relative volume (`vol_ratio_50` top vs bottom quintile) expected null.
Deliberately excluded for now: catalysts / earnings (waiting for the corrected timestamps and a live SEC feed), ML model comparisons (no training yet), any liquidity-dependent claim.

### 5.1 Support criteria (applied mechanically)
A hypothesis counts as **supported (historical)** only if: (a) the BW5 rate ratio against matched controls is at least 1.25 (or at most 0.80 for an adverse feature) with a 95% cluster-bootstrap interval excluding 1 in BOTH validation and test; (b) the same sign in at least 6 of the 8 complete years, each with at least 500 episodes; (c) it survives Holm correction across H1-H5; (d) it holds on the return-based label or is explicitly labelled "R-label only"; (e) it survives ATR% / liquidity matching and the date-fixed-effect test; (f) the negative control H0 is null. Counting years never upgrades a result. "Supported (historical)" is not an edge: it earns a forward test on the paper tracker's candidates with a pre-stated sample.

## 6. What can be done now, and what cannot

| Can be completed now from history | Needs forward observation |
|---|---|
| The consistent label set (20 / 60 / 120 / 250 bars, R and return based, terminal value), base rates by year / direction / cohort | an unbiased universe (delistings), PIT sector / industry before 2023-07, PIT regime and RS provenance (observed series are 5 sessions old) |
| Episode construction, matched-control and fixed-effect machinery, permutation null, negative control | any 120-bar forward label: the first forward signals (2026-10-09) mature on 20 bars about 2026-11-06, 60 bars about 2027-01-06, **120 bars about 2027-04** |
| H1 (reconstructed RS), H3, H4 and the price-structure part of H2 on 2018-2026 | observed-grade H1, H2, H5 and catalysts; the forward confirmation of anything found |
| Survivorship sensitivity bounds | the paper tracker's own hindsight events (about 11 months after entry) as an independent Big Winner record |
| Realised-vs-MFE comparison for the five variants (data already in `strategy_signal_outcomes`) on the full population | |

Forward sample growth is fast: about 480 triggered candidates a session, so by April 2027 roughly 50,000 triggered candidates, which is of the order of 4,000 independent episodes (about 2,000 long) at the historical episode rate; the constraint is regime diversity as much as count.

## 7. Minimal next implementation proposal (scratch / research only; needs your approval)

1. **`bw_dataset_v0` builder** (a research script under `ml_training/experiments`, read-only against production, writes a hashed file): per signal the keys, the decision-time features with a per-column provenance flag (C / R / O), episode id, split label with purge, the label set of section 4.1 (R, percent, vol-neutral, terminal, persistence), the realised net R of the five variants, integrity flags (discontinuity windows, listing age), and the matched-control index. Tests: causal-property of every feature, label correctness on synthetic paths against `opportunity.py`, split purge, determinism by hash. Registered in the existing manifest format.
2. **Pre-registration commit**: the hypotheses, definitions, buckets, matching spec, split dates, support criteria of section 5, committed and hashed BEFORE the first lift is computed.
3. **Analysis harness**: lift tables, matched controls with cluster bootstrap, date fixed effects, permutation null, Holm, negative control; one run per hypothesis per segment, outputs side by side with the evidence grade.
4. **Forward bridge (read-only)**: a weekly report of matured forward labels for `candidate_observation` (20 / 60-bar MFE in R from `stock_prices`, no table written) so the forward comparison is ready when the first windows mature.
No production table, migration, timer, pin or tracker change; no model training. Roughly one working session for steps 1-2, one for step 3.

## 8. Explicit limitations of the historical evidence

1. **Survivors only**: no company that stopped trading before 2025 is in `stock_prices`, `companies` is empty, and the universe is today's list projected back. Long base rates are upward-biased and short base rates downward-biased by an unknown amount.
2. **Revised vintage**: prices and volumes are the vendor's current history; the dataset was recomputed on 2026-10-17. Causal does not mean "as it was seen on the day".
3. **Sector, industry, regime and relative strength are reconstructed** before 2026-10 (sector is today's value applied backwards); observed-grade series are five sessions long.
4. **MFE is a best-case, unfillable number** (best high reached within six months, ignoring gaps, costs and the stop that would have exited first); 30% of long winners ended below half of their peak.
5. **No unseen historical holdout exists**: the prior lift tables and model already used every period up to about 2026-04. Historical results are hypothesis-grade.
6. **Strong time heterogeneity** (long 5R from 12% to 41% a year; 25% of winners in 10% of months): the sample holds a limited number of distinct market states.
7. **Heavy overlap and clustering**: 561k signals are 64k independent episodes; signal-level p-values and AUCs overstate the information.
8. **Label dependence**: the answer changes with the horizon (2% / 12% / 28%), the unit (ATR vs percent) and the direction; the short side has a price floor.
9. **Entry convention mismatch**: stored plan labels use the signal close and a 20-bar plan; the Big Winner label uses the next open and 120 bars. Results must not be mixed across them.
10. **A feature that predicts the label may not predict profit**: nothing here is a trading rule; the five registered variants were slightly negative net of costs out of sample.
