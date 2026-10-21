# Notebook 02 report — earnings exposure, market regime, sector strength

**For owner review.** Date: 2026-10-21. Read-only research only. No migration, collector, provider, ML training, activation, trading change, production write or deployment was made. Stage 2 and the private preview were not touched.

Files (this folder):

| File | What it is |
|---|---|
| `02_earnings_regime_sector_research.ipynb` / `.html` | The executed notebook |
| `nb02_lib.py` | Simulator, point-in-time features and statistics |
| `queries/*.sql` + `queries/export_nb02.sh` | The reproducible read-only queries; every session is forced `default_transaction_read_only=on` |
| `data/` | The exports; git-ignored |

## 1. Bottom line

None of the three tracks makes the breakout strategy profitable, alone or combined. The only robust, useful result is about **risk**: an earnings report shortly after entry multiplies the left tail (gap losses beyond the stop) without changing the average. The other consistent signal is that **short breakouts lose**, worst in weak-breadth and high-volatility markets.

## 2. Data availability and point-in-time audit

| Input | Coverage | Point-in-time status | How it was used |
|---|---|---|---|
| Prices (OHLC) | 4.2M bars, 3,114 symbols, 2020-11 → 2026-10 | Observed; survivorship (delisted names absent) | Outcomes, regime, sector returns |
| Past report dates | 2021+ dense (≈ 9–11k/yr); 2018–2019 ≈ 150–200 rows/yr | PIT-safe (past events) | "Days since report", post-earnings bucket |
| Next report date | Same | **Retrospective.** 100% of historical rows were fetched after the event (first fetch 2026-09-23). `fetched_at` is overwritten, so nothing records when a date became known. | Labelled `retro`; never claimed as known-at-entry |
| Projected next date (last + 91 days) | Same | PIT-safe proxy | Labelled `proj`; median error 0 sessions, within ±3 sessions only 54% of the time |
| Release time (before open / after close) | Not stored | — | Report-on-signal-day kept as its own bucket |
| VIX / S&P / Russell | 2025-09-18 onward only | Observed | VIX tested on 1 year: **exploratory** |
| Long-history regime | Built from the equal-weight price panel (trend vs 200-day, breadth, 20-day realised vol) | PIT (closes ≤ t; thresholds fixed on discovery) | Regime track |
| Sector membership | `companies.sector` is **empty**; latest `daily_fundamentals.sector` (overwritten daily) | **Modern membership projected back: biased** | Sector track labelled exploratory; no causal claims |
| Observed RS / sector snapshots | 7–8 sessions | Too short | Not used |

**Correction to Notebook 01.** The backtest dataset's `r_single` is the **3R-target** exit (`ml_training/features/price_features.py`, `plan_outcomes`). The live ledger exits at the **first** +1R target. With gap fills off, the simulator reproduces `r_single` **exactly (100%)**; with gap fills on it agrees 92.9% of the time, and the difference is entirely the gap fills. It also replays the live evaluator exactly on all 1,408 resolved ledger rows (outcome and holding period). The engines are consistent; the rules differ, so backtest and live numbers must not be compared directly. Full detail: [EXECUTION_RULE_PARITY_AUDIT.md](EXECUTION_RULE_PARITY_AUDIT.md).

## 3. Method

- **Primary outcome = the executable live plan**: entry at the next open, stop 2×ATR (= 1R), first target +1R, gap-aware fills (losses can exceed 1R), 20-bar expiry, 10 bps round trip. A secondary outcome uses a 3R target. Price returns are shown separately and are never called trade outcomes.
- **Window**: 2021-09 → 2026-09, 380,783 breakouts. Discovery = 2021-09 → 2023-12 (172,936). Out-of-sample (OOS) = 2024-01 → 2026-09 (207,847).
- **Uncertainty**: day-cluster bootstrap with 2,000 resamples.
- **Hypothesis testing**: one declared family of 20 tests (10 hypotheses × 2 directions), Holm-adjusted on discovery. **SUPPORTED** = Holm p < 0.05 in discovery **and** the same sign with p < 0.05 in OOS.
- **Live ledger check**: the censoring-corrected "locked-in R by session k" method from Notebook 01.

## 4. Results

### Baseline (unfiltered, executable 1R plan, net)

| | Discovery | OOS |
|---|---|---|
| All | −0.027 R [−0.064, +0.012] | **−0.063 R [−0.089, −0.036]** |
| Long | −0.015 | −0.004 |
| Short | −0.040 | **−0.137** |

Execution (next-open entry, gaps and costs) costs about 0.04–0.07 R against the non-executable dataset figure. That gap is enough to erase the "+0.05 R bullish" backtest edge.

### Track 1 — Earnings

| Earnings timing (retro) | Share | Mean R | Loses ≥ 1.5R | 5% ES | Gap through stop |
|---|---|---|---|---|---|
| No report within 20 sessions | 58% | −0.051 | 0.35% | −1.20R | 4.8% |
| Report in 1–5 sessions | 8% | −0.051 | **5.7%** | **−2.33R** | **13.3%** |
| Report in 6–10 sessions | 8% | −0.044 | 3.5% | −1.99R | 10.5% |
| Report on signal day / 1–3 sessions before | 10% | −0.03 / −0.02 | 0.4% | −1.18R | 4% |

- **Tail risk**: trades losing more than 2R are 2.3% when exposed against 0.14% when not, about 16×.
- **Average return**: H1–H3 (report within 5/10/20 sessions) and H4 (post-earnings breakout) are not supported. The mean doesn't move because upside gaps through the target offset downside gaps.
- **PIT projection**: H5 is significant in discovery only and fails OOS.

### Track 2 — Market regime

- **H7, the one SUPPORTED result**: shorts taken in weak breadth do worse. −0.217 R in discovery (Holm p = 0.01) and −0.094 R OOS (p = 0.04).
- **H6, trend alignment for shorts**: the same direction (Holm p = 0.051).
- **Shorts overall**: no regime split makes shorts positive OOS.
- **Longs**: no regime filter helps longs.
- **Exploratory only**:
  - High VIX: shorts −0.33 R, longs +0.09 R (1 year of data).
  - High realised volatility, longs OOS: +0.165 R [+0.03, +0.28] (H8, OOS only, rests on few days).

### Track 3 — Sector strength (modern-membership biased)

- **H9, sector alignment**: no effect in either direction.
- **H10, stock-vs-sector strength**: significant in different directions in each period, so it is inconsistent. Not supported.

### Track 4 — Combined filters (OOS)

| Filter | Trades kept | Mean R | Loses ≥ 1.5R | 5% ES |
|---|---|---|---|---|
| Baseline | 100% | −0.064 | 1.50% | −1.52R |
| E (no report in 1–10) | 85% | −0.060 | **0.74%** | **−1.31R** |
| T (trend aligned) | 58% | −0.016 | 1.37% | −1.48R |
| E & T | 49% | −0.011 | 0.64% | −1.27R |
| E & T & S | 30% | −0.011 | 0.65% | −1.29R |
| Long only & E | 47% | −0.002 | 0.65% | −1.28R |
| Long & Ep & T & S (all PIT-safer) | 28% | −0.004 | 1.03% | −1.39R |

No combination has a confidence interval above zero, in discovery or OOS. Filters that cut most trades only move the mean toward zero.

### Live ledger (14 sessions)

- Trades with a report in 1–10 sessions locked +0.19 R by session 5, against −0.03 R for the rest.
- This is one October earnings season with 55% of signals exposed. It is anecdotal and does not contradict the 5-year result.

## 5. Practical implications

| Area | Implication |
|---|---|
| **Screener** | Show an "earnings in N sessions" risk tag on every breakout, which the data supports. Don't rank or filter by sector or regime. De-emphasise or suspend short breakouts, which is the most consistent finding across both notebooks. |
| **Watchlist** | Earnings-ahead warning with the measured tail: about 1 in 18 trades lose ≥ 1.5R when a report falls in the first 5 sessions, against about 1 in 290 without. |
| **Earnings page/data** | Its value is risk context, not return prediction. A first-seen schedule log would be the only way to make a known-at-entry filter testable. That is a future collector/migration decision and is not requested here. |
| **ML** | Earnings proximity is a strong candidate feature for **tail/gap-risk** targets (P(loss ≥ 1.5R)), not for mean R. Don't train for return with regime or sector features. Any future training must use the executable outcome, not `r_single`. |
| **Risk/sizing** | Halve the size, or skip, when a report falls inside the first 10 sessions. This halves the ≥ 1.5R tail at little coverage cost (15% of trades). |

## 6. GO / NO-GO

| Proposed feature | Verdict | Reason |
|---|---|---|
| Earnings-in-window **risk tag** (screener, watchlist, bot) | **GO** | Large, stable tail effect; uses data already in the database; no return claim needed |
| Earnings-based **sizing/skip rule** for any future paper or live book | **GO (to paper-test first)** | Halves ≥ 1.5R losses at 15% coverage cost; mean unchanged |
| Earnings as a **return filter** / ranking | **NO-GO** | H1–H5 not supported |
| Post-earnings-breakout boost | **NO-GO** | H4 not supported |
| First-seen earnings date log (PIT schedule) | **HOLD** (separate decision) | Needed to validate known-at-entry claims; it is a collector/migration, and new datasets are on hold |
| EW trend / breadth filter for longs | **NO-GO** | No effect for longs |
| Suspend or de-emphasise **short** breakouts | **GO (recommendation; product decision)** | OOS −0.137 R with CI below zero; worst in weak breadth (H7 supported) |
| VIX / realised-vol regime filter | **HOLD** | Exploratory (1 year of VIX, OOS-only for realised vol); monitor forward |
| Sector strength filter / ranking | **NO-GO** | No effect; data not point-in-time |
| Combined E & T & S filter | **NO-GO** as a profit filter | No combination positive; keep E only as risk control |
| ML features: days to next report, days since report | **GO for a future tail-risk model** | Strong tail signal; training is out of scope now |
| ML for mean R using regime/sector | **NO-GO** | No signal |

## 7. Limits

- **Survivorship**: delisted names are absent.
- **Costs**: a flat 10 bps, with no liquidity-dependent spread.
- **Release time**: not stored.
- **Sector**: membership is modern.
- **VIX**: 1 year of history.
- **Overlap**: overlapping breakouts of one symbol are counted separately.
- **Windows**: the 2021+ window is set by earnings coverage; earlier years only appear in Notebook 01.
- **Testing**: 20 tests were run; one is supported after correction, and it is unfavourable to shorts.

Stopping here for owner review.
