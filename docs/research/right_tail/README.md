# Right-tail alpha discovery — Experiment 1: Asymmetric Breakout Study (2026-11-02)

Status: research only, EXPLORATORY. Nothing is deployed, scheduled, or imported by a service; no database was written. The six registered forward-only hypotheses, the strategy lab's
splits and every holdout boundary are untouched.
Code: `mechanism/research/right_tail/` (spec committed alone as `51376ea` **before** any result; engine `7e4fdf6`). Full tables: [tables.md](tables.md); every number: [result.json](result.json).
Reproducibility: two independent runs at `7e4fdf6` gave byte-identical `result.json`, sha256 `11d4a3bdc26b5e5c9dd29a9a78cd5b818c37b95b99f23b7899621bbe948aff4a` (~2 min each on a laptop). The extract (≈120 MB, not committed) regenerates read-only via
`ssh … 'docker exec -i donchian-screener-backend-1 python - --tar-stdout' < mechanism/research/right_tail/extract.py`. Prices sha256 `aefa1522c943654742c985825943cc2281393e3126a8ced21632f4762d9dcb74`; run with `PYTHONPATH=mechanism python -m research.right_tail run --data DIR --out OUT --code-ref 7e4fdf6`.

**Verdict (pre-declared rule, mechanical): REJECT.** Recommendation for the initiative: **REVISE**: drop technical-only entries as the source of the edge; condition on point-in-time fundamentals (section 10).

## 1. SEC/PIT production status (verified directly 2026-11-02 ~17:00Z, read-only)

| Item | State |
|---|---|
| Mechanism pin | `b4de88d790b1` (the inflection image; repinned 2026-11-01 19:58Z) |
| `SEC_USER_AGENT` | set in the prod env (value not read) |
| Scheduled run with steps 8b–8e | **has not run yet.** The only run since the repin was Sunday's weekend skip (exit 0, 274 ms CPU). First real run: `donchian-pipeline.timer` Mon 2026-11-02 22:45:20 UTC |
| SEC coverage | `sec_fundamentals_state` 20 rows / 20 ok; `sec_quarter` 12,408 rows for **20 CIKs** (0.66% of ~3,050), all from manual runs on 2026-11-01 |
| Inflection runs | `inflection_run_log` ids 5–10, all `ok`, 2026-10-31 (yfinance source, 3,001 symbols) |

Step 8d has a 1,500 s budget per night, so tonight can add a lot of coverage, but its outcome cannot be verified before ~23:30 UTC. **For this study SEC coverage is insufficient. No fundamental input is used, and nothing stands in for it.**

## 2. Data actually used

| Source | Rows | Notes |
|---|---|---|
| `stock_prices` | 6,519,681; 3,114 symbols; 2016-09-20 → 2026-10-30; sha256 `aefa1522…cb74` | **Vendor back-adjusted** (NVDA 10:1 2024-06-10 shows no jump; WDC's 2025-02-24 Sandisk separation is folded into history: no spin-off gap). Every price-derived value is *revised*, not point-in-time. 2,973 symbols had enough history |
| `price_discontinuities` | 1,044 (807 non-positive, 145 jump-up, 92 jump-down); sha256 `e39f633a…c15c` | used to drop signals whose lookback or trade path crosses a flagged break |
| Not used | `market_index_prices` (only from 2025-09), SEC tables (20 CIKs), earnings and yfinance fundamentals (not PIT) | regime = equal-weight median-return index of the liquid universe vs its 200-session mean; breadth = share of liquid names above their own 200-session mean |

**Limitations:** survivorship: only 38 symbols stop before 2026-09, so delisted companies are essentially absent. This biases long results **upward**: delisted names skew to large losses, and their absence also inflates the equal-weight regime index. No intraday data, so stop-vs-target order inside a bar and the real fill size are unknown (the stop is assumed first; gaps fill at the open). No benchmark index before 2025-09.

## 3. Exact definitions (frozen in `spec.py`, hash `a8ffcb7f…304a`)

- **Signal** at the close of bar *s*; **entry at the open of *s+1***; long only. Liquidity at *s*: close ≥ $5 and 20-day average dollar volume ≥ $5M.
- **D55**: fresh close above the prior 55-session high. Structural stop = lowest low of the last 20 sessions.
- **BASE**: fresh close above a 40-session base whose depth is ≤ 25% and whose top is ≥ 85% of the 52-week high. Structural stop = base low.
- **RS**: 126-session return in the top 20% of the liquid universe that day. **VOL**: volume ≥ 1.5× its 50-session mean. **REGIME**: equal-weight index above its 200-session mean.
- **Initial stop** = min(structural, entry − 1×ATR14); **1R = entry − stop** (actual risk). Skip if the risk is > 30% of the price. Median initial risk is 13–19% of the price, so 5R needs a move of about +65–95%.
- **Exits** (all capped at 250 bars): X1 initial stop only (≈1-year hold); X2 chandelier, highest high − 3×ATR, never below the initial stop; X3 low of the last 3 completed weeks; X4 next open after a close below the 50-day SMA (**primary**); X5 time exit at 60 bars.
- **Fills:** a stop fills at its level, or at the open on a gap through it. Costs are 20 bps round trip (10/40 as sensitivity). One open position per (set, exit, symbol). A position whose history ends before its exit is censored (excluded; marked-to-market as a sensitivity).
- **Split by signal date:** discovery 2017-10 → 2022-12, with discovery positions still open on 2023-01-01 purged; validation 2023-01 → 2026-10.
- **Controls:** a random session of the same symbol in the same segment, using D55's stop rule and the same exit; plus the unfiltered parents D55 and BASE.

## 4. Sample

10 entry sets × 5 exits × 2 segments. Primary cell (BASE+RS+VOL, X4): **2,736 discovery / 2,485 validation positions**; parents D55 X4 17,264 / 15,596. Signals per year for the primary: 228–772 (median ≈ 530). Capacity is not the constraint: at 20 slots the book rejects most signals.

## 5. R distribution and tail outcomes (20 bps)

| Cell | Segment | n | Mean net R [95% CI] | Median | Win | ≥3R / 5R / 10R / 20R | Avg win / loss |
|---|---|---|---|---|---|---|---|
| **BASE+RS+VOL X4 (primary)** | discovery | 2,736 | −0.034 [−0.093, +0.028] | −0.21 | 34.5% | 0.9 / 0.1 / 0.0 / 0.0% | +0.67 / −0.41 |
| | validation | 2,485 | +0.031 [−0.047, +0.111] | −0.21 | 36.4% | 1.5 / 0.4 / 0.1 / 0.0% | +0.80 / −0.41 |
| random-entry control | validation | 2,518 | +0.086 [+0.005, +0.168] | −0.16 | 37.5% | 3.0 / 1.4 / 0.4 / 0.1% | +1.09 / −0.52 |
| D55 X4 | validation | 15,596 | +0.042 [−0.047, +0.120] | | | – / 0.7 / 0.1 / 0.0% | |
| D55 X1 (1-yr hold) | discovery | 8,798 | +0.200 [−0.145, +0.623] | | | – / 4.4 / 0.8 / 0.1% | |
| D55 X1 | validation | 7,317 | +0.405 [−0.085, +0.931] | | | – / 6.7 / 1.4 / 0.3% | |
| D55+RS X1 | validation | 3,290 | +0.553 [+0.139, +0.992], Bonferroni [−0.093, +1.242] | | | – / 7.8 / 1.7 / 0.3% | |
| random entry, X1 | validation | 7,635 | +0.274 [−0.052, +0.622] | | | – / 7.3 / 3.6 / 1.5% | |

**MFE vs realisable (D55, X4):** the share that *reached* ≥5R within 250 bars is 12.0% (discovery) and 16.9% (validation); ≥10R 1.9% / 3.5%. The share that *realised* ≥5R is 0.6% / 0.7%. Mean theoretical max is +2.6 / +3.1 R, but the realised gross on the same positions is +0.03 / +0.08 R. MAE is mean 0.49 R, p90 0.87 R. The right tail is visible in hindsight; trailing and trend exits give almost all of it back. Only the stop-only, 1-year hold keeps a tail (4–8% ≥5R), and random entries keep the same tail.

**Execution:** 13–21% of stop exits gapped through the stop, for an average extra loss of +0.13 to +0.51 R when gapped (≤ 0.03 R per position overall). Entry open vs signal close is ≈ −0.01 R. Going from 10 to 40 bps moves expectancy by ≤ 0.03 R.

**Portfolio** (≤ 20 concurrent, 0.5% of initial equity risked each, 2017–2026): primary +55.8 R total, **max drawdown 41.8 R (20.9%)**, +2.8%/yr. D55 X1: +5.5%/yr with a 34.3% max drawdown. The best row (D55+RS+VOL X5) is +7.4%/yr with a 25.6% drawdown, and it was selected after looking. None of these beats its drawdown convincingly, and none is a validated result.

## 6. Market conditions (descriptive; no filter was selected from this)

Breakouts taken while the equal-weight index was **below** its 200-day mean did better in validation (D55 X1 +1.73 R and 13.8% ≥5R vs +0.22 R and 5.7% in risk-on; D55 X4 +0.34 vs +0.01). For X1 the sign repeats in discovery (+0.62 vs +0.08); for X4 it reverses (−0.08 vs +0.03). High breadth (> 60%) was the weakest bucket in validation for every cell. This agrees with the big-winner harness's adverse H2 ("strong market → fewer big winners"). It is concentrated in a few recovery episodes (2020, late 2023, spring 2025) and is **not independent evidence**. The REGIME filter itself added nothing measurable: primary with regime +0.010 R vs +0.031 R without, validation.

By year (D55 X1): +0.58, +0.03, −0.10, **+1.52 (2020)**, −0.33, −0.28, +0.74, +0.13, +0.89, −1.05 (2026, mostly censored). The results are regime-driven, not stable.

## 7. Expectancy evidence

- Primary: no edge. Discovery is negative, validation is indistinguishable from 0, and it is worse than random entry in both segments (−0.056 R, CIs [−0.120, +0.012] and [−0.139, +0.024]).
- Across all 50 cells, **no validation Bonferroni interval lies above 0**. Unadjusted "significant" cells are all X1 (1-year hold) in validation (2023–26, a strong market), and the random-entry X1 control is +0.27 R in the same period.
- Filters (RS, VOL, BASE) did not raise the realised ≥5R rate over unfiltered D55 or over random entries. Descriptively (discovery only), realised ≥5R winners had higher ATR% (X1 high tercile 6.5% vs 4.3% in the low tercile), deeper bases and lower dollar volume. That is more volatile names, which is a volatility tilt, not a signal.

## 8. Biases and reliability limits

1. **No untouched historical holdout.** 2017–2026 was already examined by the variant runner, the big-winner harness and Notebook 02; "validation" is only a chronological replication.
2. **Survivorship** (upward bias for longs, size unknown). It cannot rescue a result that is already ≤ random.
3. **Revised prices.** Back-adjustment changes historical price levels, the $5 filter and dollar volume.
4. **Correlated observations.** Signals cluster by date. Month-block intervals assume exchangeable months and probably under-cover (see `BIG_WINNER_HARNESS.md` §4a).
5. **Censoring.** 1,242 D55-X1 validation positions are still open; marking them raises the mean from +0.41 to +0.51 R. Outcome-dependent censoring biases recent long-hold cells downward.
6. **The random control under X4 exits within a median of 2 bars,** because a random day often closes below the 50-day mean. The control is weak for X4 specifically; the verdict also fails on discovery expectancy alone.
7. **Multiplicity:** 50 cells × 3 costs looked at, plus descriptive strata; only the primary decides.

## 9. Case studies (illustration only)

- **WDC:** 18 D55 entries under X4 (2017–2026). Fifteen were between −0.68 and +0.72 R; the others were +2.6 R (2020-11), +1.9 R (2026-04) and the big one. The 2025-05-19 breakout (risk 29%) became +14.2 R on X4 and +27.8 R on X1 (32 R reached within a year). The rules caught the big move, but one entry in 18 paid, and the stop-only exit kept twice what the trend exit kept.
- **SEZL:** the 2024-10-14 breakout reached 18.2 R within a year. X4 exited at +1.0 R on 2024-12-19; X1 kept +5.7 R. The 2023-09-14 bad print in the adjusted series is flagged and excluded.
- **DOCN:** 7 entries; the 2025-08-05 entry reached 15.2 R. X4 realised +1.7 R and X1 +9.3 R.

The pattern is the same as in the aggregate: the winners were reachable, the trend exits surrendered them, and nothing at entry separated them from the losers.

## 10. Recommendation and next experiment

**REJECT** the technical breakout as a standalone right-tail edge (primary cell, mechanical). **REVISE** the initiative. The data show right-tail moves exist (12–17% of breakouts reach ≥5R within a year), but price-and-volume conditions at entry do not separate them from random entries, and no tested exit beats a year-long hold. The distinguishing information, if it exists, has to come from outside the price series.

**Single highest-value next experiment: Fundamental-inflection × breakout, point-in-time.** Once SEC coverage passes the owner's eligibility gate (≥ 80% coverage plus valid PIT reconstruction), pre-register one hypothesis. It runs on the same frozen D55 entries, X1 exit and controls, using only the SEC figures first public before the signal date (`sec_quarter.filed_date`, first-filed values, no restatements). The hypothesis: breakouts in companies with a recent revenue-acceleration-plus-turn-to-profitability filing realise ≥5R more often than matched breakouts without one. Run it once, judged against a matched non-inflection control. EDGAR company facts go back years with filing dates, so this is a historical PIT test that becomes possible after step 8d covers the universe; tonight's run is the first measure of how fast that happens. Until then, nothing in this study supports a candidate list.
