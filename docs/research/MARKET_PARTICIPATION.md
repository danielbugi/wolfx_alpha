# Market intelligence audit and `breakout_participation_v1`

Status: research only. Nothing here is deployed, scheduled, written to a database or imported by a service. No model, no tuning, no claim that market breadth, sector strength or breakout participation predicts returns (the Big Winner H2 result stands: not supported, reversed direction exploratory).
Audit date 2026-10-17. Production was read only (SELECT through a read-only session); no pin, migration, timer or write was touched.

## 1. Existing capability inventory

| Area | What exists | Where | State in production (read-only check, 2026-10-17) |
|---|---|---|---|
| Market breadth | `breadth_v1`: above SMA50 / SMA200, new 52-week high / low, positive 20-day return, advancing / declining, each as numerator / denominator / missing counts, market and per sector, pure | `mechanism/market_intelligence/breadth.py` | Observed rows written daily at 06:45 by `mi_runner_v2` since 2026-10-12 (5 sessions); e.g. 2026-10-16 above SMA50 = 861 of 3,053 eligible (28.2%) |
| Market regime | `risk_regime_v1`: seven weighted components, RISK_ON / NEUTRAL / RISK_OFF / UNAVAILABLE; documented as a heuristic with unfitted weights, not a predictive model | `risk_regime.py` | 5 observed snapshots, all NEUTRAL, scores -0.22 to +0.06 |
| Sector relative strength and rotation | `rs_v1`: stock returns 5/20/60, sector median (min 5 members), sector vs market, sector vs universe median, sector rank, strength state; `sector_v1` | `relative_strength.py`, `sector_intel.py` | `sector_snapshot` 55 rows (11 sectors x 5 sessions); `stock_relative_strength` 45,957 rows (5 sessions) |
| Point-in-time market context | Immutable snapshot tables with `observed` vs `reconstructed` provenance, DB-clock stamping, composite FKs, read-only API `/api/market-intelligence/*` | migrations 24/25, `store.py`, `provenance.py`, `backend/routers/market_intelligence.py` | Migration 24 applied (`universe_snapshot` 5, `market_snapshot` 5); `market_event` empty |
| Point-in-time sector | Sector observations with first-seen and change history, polls | `sector_observation` (3,084 rows, 3,054 symbols, from 2026-10-10), `sector_poll` (18,645), `sector_performance_daily` (1,230 rows from 2026-05-22) | Live; historical sector is still never PIT-safe (`daily_fundamentals` is rewritten in place) |
| Index context | Index and macro closes | `market_index_prices` (^GSPC, ^RUT, ^VIX, ^TNX, ...), ~270 sessions from 2025-09 | Live |
| Candidate-time features | Per-candidate feature snapshot captured at decision time | `feature_snapshot` (9,789 rows since 2026-10-09) | Live (Big Winner forward evidence) |
| Historical reconstruction for research | The Big Winner harness rebuilds breadth_50, index above 200, relative strength, sector rank and same-sector breakout counts as RECONSTRUCTED / REVISED_PRICE_DERIVED columns for each signal | `ml_training/experiments/bw_harness/features.py` | Merged (PR #27), dormant |

Provenance rule already in force: forward snapshots are `observed`; anything recomputed later from stored history is `reconstructed` and never presented as what was known then.

## 2. Gap analysis

| Candidate gap | Already covered? | Verdict |
|---|---|---|
| Per-session market breadth history | Pure functions exist and the runner can reconstruct one explicit session; forward capture is running | Not a gap for forward use. A vectorised historical series would duplicate `breadth_v1` and need an equivalence pin; deferred |
| Regime classification | `risk_regime_v1` exists; improving or validating it is a model-selection exercise on already-examined history | Out of scope (would be optimisation) |
| Sector history that is point in time | Impossible retroactively (source overwritten); forward capture via `sector_observation` already runs | Cannot be fixed with existing data |
| **Market-wide breakout participation** | **No.** `breadth_v1` counts stocks above averages or at highs; none of the modules counts stocks that broke their Donchian channel. The only related quantity is the harness's per-signal same-sector count, which exists only for signals and is reconstructed | **Genuine gap** |
| Point-in-time version of the signal set | The research signal set (`ml_breakout_dataset_v2`, `detect_signals`) excludes signals when a price discontinuity falls in the next 20 bars and requires 80+ total bars, both of which use the future | Real but small (see 4); fixed inside the selected component |

Why the participation gap matters for the next research: the Big Winner discovery found that signals cluster heavily (up to 1,720 signals on one date, 67% repeats within 10 bars), and the date-level exposure is exactly what made H2 impossible to test within a date. A clean, point-in-time, denominator-explicit count of how many stocks broke out each day (and how many started a new episode) is the missing context variable for Big Winner and for catalyst studies (an earnings reaction on a day when 30% of the universe broke out is a different observation from one on a quiet day). It reuses existing price data, needs no schema, no collector and no new source.

## 3. Selected opportunity: `breakout_participation_v1`

`ml_training/experiments/market_participation/participation.py` (pure) and `run.py` (read-only CLI). One row per session:

`session_date, n_eligible, n_long, n_short, n_long_new_episode, n_short_new_episode, pct_long, pct_short, n_*_dropped_by_research_rule, state, version, provenance`

Definitions (fixed and versioned, nothing fitted): eligible = has a bar, 60+ bars of history, 20-day average dollar volume >= 1,000,000, and no price discontinuity in the previous 253 bars; long / short = the live screener's channel rule; new episode = at least 120 bars after the previous kept signal of that symbol and direction (the harness's rule, reused); percentages withheld below 1,000 eligible stocks. A stock that cannot be evaluated leaves the denominator; it is never counted as "no breakout". Provenance is REVISED_PRICE_DERIVED on every row, with the surviving-universe caveat stated in the code and manifest.

Properties and the tests that pin them (`ml_training/tests/test_market_participation.py`, 11 tests, all pass):

- a hand-built breakout is counted once with the right denominator;
- counts equal the harness signal detector's per-date counts on clean data;
- **causality**: truncating the data at a date leaves every earlier row identical; a price jump injected after a signal does not change that day's point-in-time count while the research detector drops the signal;
- a missing bar reduces the denominator by one and is not treated as a negative;
- percentages are withheld below the minimum, counts kept;
- new-episode counts never exceed signal counts and collapse repeats;
- deterministic and independent of symbol order; the hash changes if any value changes;
- the pure module has no I/O, clock or SQL; the runner only reads (read-only session) and writes files; nothing in `mechanism/`, `backend/` or `deploy/` references it.


### 3a. Point-in-time contract (owner review)

The causal columns are `session_date, n_eligible, n_long, n_short, n_long_new_episode, n_short_new_episode, pct_long, pct_short, state, version, provenance` (`CAUSAL_COLUMNS`). Every value for session t is a function of bars up to and including t: the indicators are causal, eligibility looks back 253 bars for discontinuities and never forward, there is no total-series-length condition, and episodes are chained greedily in bar order (a signal's episode status depends only on earlier signals). Pinned by test: truncating all data at a date, or injecting a price jump after it, leaves every causal column up to that date identical.

The existing research dataset (`ml_breakout_dataset_v2`, `bw_harness.features.detect_signals`) is **not** point in time: it drops a signal when a price discontinuity falls in the NEXT 20 bars and requires the symbol to have 80+ bars in total. The two columns `n_long_dropped_by_research_rule` and `n_short_dropped_by_research_rule` (`AUDIT_COLUMNS`) deliberately use that future information, only to size the difference (194 of 603,156 signals); they are audit columns, never context, and a test pins that they are the only columns that can change when the future changes. The frozen Big Winner hypotheses were not modified or rerun.

Validity: only `state == "ok"` sessions are statistics. `insufficient_eligible` rows (the only one in the real series is the 2026-02-16 holiday with one stray bar) keep their counts for audit, have no percentage, and are excluded by `valid()` and by `summary()` (every figure in section 4 is from valid sessions, 2,210 of 2,211); a test with a stray Saturday bar pins this.

### 3b. Isolation boundary (owner review)

Structure: a pure core (`participation.py`: no I/O, clock, SQL, adapter import or model code) and an isolated read-only adapter (`run.py`: the harness's read-only session, which refuses a writable server; writes files only to `--out`).

The one change to an existing test was an exemption in the harness's "nothing else imports the harness" scan. It is safe because that scan only protects against production-side imports of the harness, and the exemption was first a directory name and is now **only the exact path `ml_training/experiments/market_participation`**, so a same-named directory under `mechanism/`, `backend/` or `deploy/` is still scanned. The protections against database writes, production imports and model execution are not carried by that scan: the harness package keeps its own read-only / no-write / no-model tests, and the new package has equivalents (no write SQL, no model code, no adapter import in the core, not referenced from `mechanism/`, `backend/`, `deploy/`, and no unit or timer). The exempt package is therefore checked by its own tests rather than left unchecked.

## 4. Real-data evidence (read-only, 2026-10-17)

Two independent runs (separate containers, mechanism image `5c511899505a` used only as a Python runtime) produced the identical table:

- rows 2,211 (2018-01-02 to 2026-10-16); table sha256 `8cc3f1624cfbe5c14d100b31b010d1628d262641129291275a2190bdfd7b9204`; code_ref `43bf679559c0` (a local pre-squash commit; the `market_participation` source files are byte-identical to PR commit `6c0ff0e`);
- **reconciliation with the research dataset is exact**: 333,808 long and 269,348 short signals; minus the 64 long and 130 short that the research rule drops equals 333,744 and 269,218, the direction totals of the Big Winner dataset. So the point-in-time rule keeps every research signal and adds 194 (0.03%); the lookahead in the research set is real but negligible;
- median daily participation: long 5.0% of eligible stocks, short 2.6%; 90th percentile 14.9% / 13.6%; long and short participation are negatively related (correlation -0.37); only about 10% of long and 12% of short signals start a new episode;
- extremes are market-wide events: most long breakouts 56.2% (2020-06-05), most short 89.0% (2020-03-12); yearly medians are stable (long 2.8% in 2022 to 6.8% in 2019);
- last five sessions: long 3.7% to 11.2%, short 5.2% to 18.2% (2026-10-14).
- Known quirk: 2026-02-16 (a holiday) appears with 1 eligible stock because a single symbol has a stray bar; it carries `insufficient_eligible` and no percentage. Consumers must filter on `state == "ok"`.

Evidence files: `docs/research/evidence/market_participation_2026-10-17/` (`breakout_participation.csv`, `manifest.json`).

## 5. What this does not claim

- No claim that participation, breadth or regime predicts returns or Big Winner frequency. This is a measurement, to be used as context or as a stratification variable in pre-registered work.
- The history is revised-price derived and survivor-only; it is not an observed record. The forward `observed` equivalents exist only for breadth, regime and sector, not for participation (see 6).
- No sector breakdown yet: the only historical sector map is reconstructed, so a sector split would need an explicit provenance label per row and is left to a later version.

## 6. Remaining dependencies requiring owner approval

1. **Forward observed capture of participation** would mean adding it to the `mi_runner_v2` snapshot path (a production code change and a new column or JSON field in `market_snapshot.coverage`); not done. The historical series can be regenerated on demand until then.
2. Any use of the series as a feature, filter or score in the tracker or strategies (not proposed).
3. A sector-split version needs a decision on which sector provenance to admit.
4. Whether to pre-register a participation-conditioned Big Winner analysis (a new pre-registration, not a retune of H1 to H5).

Nothing is merged or integrated. Stopping for owner review.
