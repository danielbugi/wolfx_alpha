> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** Preserved for reference/history.
> For current architecture, see [docs/architecture/](../architecture/), [docs/operations/](../operations/),
> and [CLAUDE.md](../../CLAUDE.md). Moved here 2026-09-25 as part of the repository documentation
> consolidation — content below is unmodified from before the move except for this banner.

# System Health Report — 2026-09-19

> **Update, later same day:** added a `/api/performance/` tracking system per the
> user's request ("score and track window load speed for each endpoint and each
> route... performance is critical"), which surfaced and led to fixing two real
> bottlenecks. See **§7 Performance investigation and fixes** at the end of this
> file for the full story — the summary below is otherwise unchanged from the
> original report.

Generated during a QA session diagnosing frontend data-loading complaints. Snapshot taken via
`GET /api/system-health/` and `GET /api/ml-stats/` against a locally running backend
(`localhost:8000`) and frontend (`localhost:3000`).

## 1. What was actually wrong with the frontend

The reported "data not loading" issue was **not** a backend or database problem — every API
endpoint responded correctly. It was a **stale Next.js dev server**:

- The dev server process holding port 3000 was returning `404` on `/` and `500` on
  `/system-health`, while every backend endpoint it depends on returned `200`.
- A freshly started dev server (same code, same `.next` cache cleared) served every route
  correctly (`/`, `/system-health`, `/ml-stats`, `/screener`, `/strategy`, `/alerts` — all `200`).
- **Fix applied:** killed the stale process, deleted `frontend/.next`, restarted `npm run dev`
  clean on port 3000. Confirmed all routes now return `200`.
- **Takeaway:** a long-running `next dev` process can end up serving broken routes after enough
  file changes accumulate underneath it (webpack module-cache corruption — see the
  `invalid code lengths set` cache-restore warnings in the dev log, a symptom of a corrupted
  `.next` pack cache). When the frontend "stops loading data" again, restart the dev server
  (`Ctrl+C`, `rm -rf .next`, `npm run dev`) before assuming it's a backend/DB issue.

## 2. Real, current data-quality issue found

**Daily fundamentals coverage is critical: only 31.6% of active symbols (972 / 3,076)** have a
`daily_fundamentals` row from the last 35 days. This is visible on the dashboard right now — of
the top 10 gainers pulled from `/api/dashboard/top-gainers`, 9 showed `sector: "Unknown"` and
`market_cap: null`.

This matches a known, already-documented gap (CLAUDE.md §7.4): `fundamentals_updater.py`
(daily market cap / PE / PB / sector) has never been wired into `automation_pipeline.sh` — it's
still manual-only, unlike the quarterly fundamentals updater which was made staleness-aware and
automated on 2026-09-18. **Not fixed in this session** (wiring it in means adding a real daily
fetch step against ~3,000 symbols — flagging for a decision rather than running it unprompted).

## 3. Endpoint performance note

Two endpoints are notably slow (measured against local Postgres):

| Endpoint | Latency |
|---|---|
| `/api/system-health/` | ~10s |
| `/api/ml-stats/` | ~15–19s |

Both are well under the frontend's 30s axios timeout, so they don't currently fail — but they're
close enough to be worth watching, especially against a remote/production DB with higher latency
per round trip. Cause: each check/section opens its own DB connection sequentially rather than
batching, and `ml-stats` additionally does a `joblib.load()` of the full model file for feature
importance on every request (no caching, unlike the dashboard endpoints which cache for
15 min–1 hour).

## 4. Features added this session

- **Dev-only QA panel** (`frontend/src/components/dev/DevQAPanel.tsx`) — a floating widget
  (bottom-right, all pages) that pings all 14 endpoints the frontend depends on and reports
  status/latency/validation errors. Gated on `process.env.NODE_ENV === 'production'` (Next.js
  sets this automatically for `next build`), so it can never leak into a production build.
- **System Health page now has a 3-way view switch**: *Overview* (unchanged, everything),
  *Basic System Health* (pipeline freshness + data quality + universe coverage, no ML), and
  *ML Health & Accuracy* (the ML operational checks plus the full model/accuracy/feature-importance
  detail previously only on `/ml-stats`). The rich ML content was extracted into a shared
  `MLStatsPanel` component so `/ml-stats` and the new dropdown view render identically instead of
  drifting into two copies.

## 5. Full snapshot at generation time

### System Health (`/api/system-health/`) — overall: **critical**

**Pipeline Freshness — warning**
| Check | Latest | Days stale | Status |
|---|---|---|---|
| Daily prices | 2026-09-17 | 2 | warning |
| Daily technical indicators | 2026-09-17 | 2 | warning |
| Weekly technical indicators | 2026-09-18 | 1 | healthy |
| Monthly technical indicators | 2026-09-30 | 0 | healthy |
| Daily fundamentals | 2026-09-14 | 5 | warning |
| Quarterly fundamentals | 2026-08-31 | 19 | healthy |

*Daily prices/indicators crossing into "warning" here likely just reflects the time this snapshot
was taken (past midnight, before today's pipeline run) — worth re-checking after the next
scheduled run rather than treating as a new incident.*

**Data Quality — critical**
| Check | Detail | Status |
|---|---|---|
| Missing indicator values (30d) | 0.01% NULL across donchian/rsi/atr (60,953 rows checked) | healthy |
| Price anomalies (30d) | 0 rows with impossible OHLCV values | healthy |
| Fundamentals coverage | 972/3,076 active symbols (31.6%) | **critical** |

**ML Model Health — warning**
| Check | Detail | Status |
|---|---|---|
| Live model freshness | momentum_predictor_v20260918_2157.joblib (1 day since training) | healthy |
| ML predictions being recorded | 1,157 total, latest 2026-09-17 | healthy |
| Prediction outcomes evaluated | 0 — predictions never evaluated against actual results | warning |
| Model registry (ml_models table) | 1 registered model | healthy |

**Universe Coverage — healthy**
- Total symbols: 3,114 (3,076 active, 38 inactive)
- Quarterly fundamentals coverage: 96.3%
- 10-year price history coverage: 67.7%
- Sector breakdown: Technology 161, Industrials 149, Financial Services 135, Consumer Cyclical 134,
  Healthcare 103, Real Estate 65, Consumer Defensive 58, Communication Services 46,
  Basic Materials 41, Utilities 38, Energy 37

### ML Stats (`/api/ml-stats/`)

**Current model:** `momentum_predictor_v20260918_2157` — trained 2026-09-18 (0–1 days old),
41 features, 52,676 training samples, registered.

| Metric | Value |
|---|---|
| Accuracy | 0.676 |
| Precision | 0.603 |
| Recall | 0.190 |
| F1 | 0.289 |
| AUC | 0.702 |

*Recall is notably low relative to precision (the model rarely flags a positive, but is often
right when it does) — worth keeping an eye on once outcome evaluation (see above) is actually
running, since that's the only way to confirm this in live trading rather than backtest.*

**Top 5 features by importance:** `is_bullish` (0.090), `latest_net_income_positive` (0.088),
`volume_ratio` (0.079), `quality_rsi` (0.077), `quality_volume` (0.074).

**Training dataset:** 65,846 rows, 2,597 unique symbols, 2023-08-21 → 2026-09-11.
Class balance: 32.3% positive / 67.7% negative. Signal mix: 57.9% bullish / 42.1% bearish.

**Live prediction track record:** 1,157 predictions, latest 2026-09-17. Confidence distribution:
very_high 4, high 25, medium 24, low 64, very_low 1,040 — the vast majority of live predictions
are very-low confidence. **0 outcomes evaluated** — win rate and average return are both unknown
because `performance_tracker.py` has never run against this model's live predictions.

**Retraining history:** 4 model files on disk — this one (2026-09-18), then a large gap back to
2025-07-29, 2025-07-20, 2025-07-13. The 2026-09-18 retrain is new since CLAUDE.md's last note
that the live model was stale since 2025-07-29 — that finding is now resolved, but only this
newest run has accuracy/precision/recall recorded (`register_model()` wasn't wired in before).

## 6. Suggested next steps (not yet actioned)

1. Decide whether to wire `fundamentals_updater.py` into `automation_pipeline.sh` (mechanical,
   per CLAUDE.md §7.4) to fix the 31.6% fundamentals coverage gap driving "Unknown" sectors /
   null market caps on the dashboard.
2. Run `performance_tracker.py` (or otherwise populate `ml_prediction_outcomes`) so win rate /
   avg return stop reading as "never evaluated."
3. ~~Consider caching `/api/system-health/` and `/api/ml-stats/`~~ — partially addressed;
   see §7 below. `system_health_service.py`'s own queries are still uncached and still the
   slowest thing in the app (~6.8s) — candidate for the same kind of fix as §7.2 if it
   becomes a priority; not yet investigated query-by-query the way the dashboard was.

## 7. Performance investigation and fixes (2026-09-19, later same day)

Triggered by the user asking for real, tracked latency scoring across the database, every
backend endpoint, and every frontend route — "that is super critical the performance." Built
`/api/performance/` (backend middleware timing every request + a DB connection-vs-query
benchmark + a `usePagePerf` hook reporting real page-load time from the frontend) and a
`/performance` dashboard page. That tracking immediately surfaced two real, fixed problems:

### 7.1 No database connection pooling (fixed)

`backend/main.py`'s `get_database_connection()` called a bare `psycopg2.connect()` on every
single request, and `system_health_service.py` alone opened 9+ such connections per report.
Measured cost: **connection acquire ~43-53ms, pure query execution ~0-20ms** — nearly all of
that benchmark's time was pure connection tax.

**Fix:** added a real `ThreadedConnectionPool` in `main.py` (`_PooledConnection`, a
`psycopg2.extensions.connection` subclass whose `close()` returns the connection to the pool
instead of actually closing the socket — meaning every existing `conn.close()` call site across
every router/service kept working unmodified). Result: **connection acquire dropped from
~43-53ms to 0.0ms.**

### 7.2 Dashboard queries doing full-table joins instead of per-symbol lookups (fixed)

The real headline finding. `EXPLAIN ANALYZE` on the `top-gainers` query showed its
`previous_data` CTE (finding each symbol's previous closing price) doing a **merge join across
all 6,424,695 rows of `stock_prices`**, producing 6.3 million intermediate rows before filtering
down to ~3,000 — where the `technical_indicators`/`daily_fundamentals` joins two lines below it
did the equivalent lookup correctly (a per-symbol index seek, ~0.02ms × 1,622 rows). Same
anti-pattern, worse, in `unusual-volume`'s `previous_prices` CTE (a correlated `MAX(date)`
subquery over the same unfiltered join).

This one query pattern was ~7.5 of ~7.8 total seconds for a cold-cache `top-gainers` call — and
`/api/dashboard/main-page-data` calls the gainers/losers/unusual-volume queries in sequence, so
its cold-cache cost compounded to **2 minutes 27 seconds** in one measured request.

**Fix:** rewrote all three CTEs (`top-gainers`, `top-losers`, `unusual-volume` in
`backend/main.py`) to use `LEFT JOIN LATERAL ... ORDER BY date DESC LIMIT 1` — a per-symbol
index seek on `idx_stock_prices_symbol_date`, matching the pattern the correctly-fast joins
already used. Verified the result set is byte-for-byte identical to before the change (same
symbols, same percentages, same order) — this was a pure performance fix, not a behavior change.

**Measured impact (cold cache, i.e. worst case):**

| Endpoint | Before | After |
|---|---|---|
| `/api/dashboard/top-gainers` | ~6-8s (p95 5.7-6.3s, seen up to 6.3s) | **0.72s** |
| `/api/dashboard/main-page-data` | 139s (p95, 2 samples) / **147s** (worst single call) | **2.88s** |
| `/api/ml-stats/` | 8-19s | **2.3-2.4s** (pooling only — no query rewrite needed here) |
| `/api/system-health/` | ~10s | ~6.8-7s (pooling helped modestly; **not yet query-optimized** — see §6.3) |
| DB connection acquire | ~43-53ms | **0.0ms** |

### 7.3 What's still open

- `system_health_service.py`'s own queries (9+ per report) haven't been individually profiled
  the way the dashboard queries were — its remaining ~6.8s is likely a mix of several
  moderately-slow aggregate queries rather than one dominant anti-pattern. Worth an `EXPLAIN
  ANALYZE` pass if this page's speed becomes a priority.
- `pg_stat_activity` showed 3 concurrent identical `SELECT COUNT(*) FROM technical_indicators`
  queries during testing — plausibly just this session's own concurrent testing/polling
  (dashboard's 5-min auto-refresh, multiple pages open, the dev-only QA panel's 14 parallel
  checks), not a separate bug. Worth keeping an eye on via `/performance`'s endpoint table if
  it recurs under normal (non-testing) usage.
- `/performance`'s tracked stats are in-memory only and reset on backend restart — fine for a
  single dev process, but worth a real persistence layer (a Postgres table, sampled/aggregated)
  if this needs to survive restarts or aggregate across a longer window later.

## 8. Second performance pass — root causes fixed, not just symptoms (2026-09-19, later still)

Triggered by `/performance` showing `/api/deep-value/scan` and `/api/system-health/` at
**critical** and five more routes at **warning**, including two that do zero I/O
(`/api/performance/client-metric` POST and its `OPTIONS` preflight) — the tell that something
systemic, not per-endpoint, was going on. Full write-up of the diagnosis is in this session's
conversation history; summary of what was found and fixed below. All fixes were verified against
the real local Postgres instance (not mocked) — see §8.5.

### 8.1 Root cause: `async def` routes blocking the event loop (fixed)

Almost every router (`screener.py`, `alpha.py`, `strategy.py`, `deep_value.py`,
`system_health.py`, `ml_stats.py`, most of `stock.py`, the dashboard endpoints in `main.py`) was
declared `async def` but called blocking synchronous code inside — `psycopg2` queries,
`glob`/`open`/`json.load`, `joblib.load()`. In FastAPI, a blocking call inside `async def` freezes
the *entire* event loop, not just that request. This is exactly why a trivial in-memory operation
like `client-metric` (and its `OPTIONS` preflight) showed multi-second p99s: they were queued
behind whatever slow query another concurrent request was running. `stock.py`'s `get_earnings`
already used the correct pattern (plain `def`, so Starlette runs it in its threadpool) with a
comment explaining why — that fix just hadn't been applied anywhere else.

**Fix:** converted every route doing blocking I/O from `async def` to plain `def` across all the
files listed above.

### 8.2 Root cause: full-table scans instead of per-symbol lookups (fixed)

§7.2 above already found and fixed this exact anti-pattern in `main.py`'s dashboard queries
(`LEFT JOIN LATERAL ... LIMIT 1` instead of a correlated `MAX(date)` subquery or a bare
`DISTINCT ON` over the whole table) but it was never swept to the rest of the codebase:

- `services/market_service.py` (`get_market_overview` → `/api/screener/market-overview`,
  `get_filtered_stocks` → `/api/screener/search`): correlated scalar subquery per row, executed
  against a pre-dedup 5-day window (~5x more rows than symbols) instead of the deduped
  one-row-per-symbol set. Rewrote both to LATERAL, seeded from a `latest_prices` CTE.
- `services/deep_value_service.py` `scan()`: three `DISTINCT ON` queries over the **entire**
  `daily_fundamentals`, `monthly_technical_indicators`, and `stock_prices` tables, with no
  active-symbol filter and no caching at all. Rewrote to LATERAL, driven by `daily_fundamentals`
  narrowed to the last 40 days (it refreshes daily; older rows are stale anyway).

### 8.3 Missing caching on slow-changing data (fixed)

- `deep_value_service.py`: the raw SQL fetch (rows + the quarterly-fundamentals turnaround map)
  is now cached module-level for 15 minutes — this data only changes once/day via the pipeline.
  The cheap in-Python filtering by query params still runs fresh every call.
- `system_health_service.py` / `ml_stats_service.py`: `get_full_report()` cached for 2 minutes
  each (a production-readiness/model-accuracy dashboard doesn't need to be real-time); the
  duplicate `COUNT(DISTINCT symbol) FROM stock_prices` that both `get_data_quality()` and
  `get_universe_coverage()` ran independently is now computed once and shared (60s cache).
  `ml_stats_service.get_feature_importance()` (a `joblib.load()` of the model file) is now cached
  by model version instead of reloading on every request.
- `utils.py`: added `load_ml_enhanced_data_cached()` — a single shared 5-minute TTL cache for the
  screener's output JSON. Previously `alpha.py`, `strategy.py`, `stock.py`, and `main.py`'s
  `top-ai-picks` each independently re-globbed/re-read/re-parsed that file from disk, uncached
  (`stock.py` had its own local copy of this same cache; consolidated into `utils.py` so every
  caller shares one parse). `alpha.py`'s `_find_previous_day_signals()` (a second, separate file
  read for "yesterday's signals") is now cached too (30 min — it only changes once/day).

### 8.4 Sequential sub-fetches parallelized (fixed)

`main-page-data`, `system-health`'s full report, and `ml-stats`' full report each `await`ed (or
called) 4-5 independent DB round trips one after another. Now run concurrently via
`ThreadPoolExecutor` — a cache-miss pays for the slowest section, not the sum of all of them.
`main-page-data` additionally gets a background thread (started from `lifespan()`, stopped on
shutdown) that proactively rebuilds its cache ~2 minutes before the 15-minute TTL expires, so a
real user request should never hit the cold-build path at all.

### 8.5 Two real bugs found *while testing* these fixes (fixed)

Testing against the live local Postgres instance (not mocked) surfaced two bugs neither in the
original symptom list nor introduced by the changes above on their own — both are pre-existing,
just newly triggered:

1. **Connection-pool self-deadlock (critical, latent since the §7.1 pooling fix).**
   `_PooledConnection.close()` returns a connection to the pool via `db_pool.putconn(self)`. But
   psycopg2's `ThreadedConnectionPool._putconn()` itself calls `conn.close()` to discard a
   connection instead of pooling it whenever more than `minconn` idle connections are already
   parked (or the connection's transaction status comes back `UNKNOWN`). Without a reentrancy
   guard, that inner `close()` re-enters the same override and calls `putconn()` a *second* time
   on the same thread — but `putconn()` serializes on a plain, non-reentrant `threading.Lock`, so
   the second acquire blocks forever behind the first call's still-held lock. That one stuck
   thread permanently wedges the lock for the *entire pool* — every subsequent `getconn()` and
   `putconn()` from every other request hangs too, including completely unrelated ones (this is
   why `/api/health` itself stopped responding during testing). It was latent because the app
   rarely had more than `minconn` (2) connections checked out at once; §8.4's concurrent fetches
   made that routine, so it started reproducing on the very first cold `main-page-data` build.
   **Fixed** with a per-connection reentrancy flag (real `close()` on the nested call instead of
   bouncing back into `putconn()`). Verified with a stress test: 8 rounds × 12 concurrent
   connections against a `minconn=2` pool (deliberately forcing the surplus-discard path on
   almost every return) — 96 connection cycles, zero deadlocks, ~0.2-0.5s per round. Also bumped
   `DB_POOL_MIN` default 2 → 8 to reduce how often that discard/reconnect churn happens now that
   several endpoints routinely hold 4-5 connections at once (override via `DB_POOL_MIN` env var).
2. **SQL division-by-zero on a flat Donchian channel (pre-existing, explains the original
   `/api/screener/search` "100% error rate" reading).** `market_service.py`'s `donchian_position`
   calculation divided by `(donchian_high_20 - donchian_low_20)` guarded only by `> 0` checks on
   each side, not by the two being unequal. Any symbol in the filtered result set with a
   perfectly flat 20-day channel (not rare — it's literally what "coiling before a breakout"
   looks like) crashed the *entire* query with `psycopg2.errors.DivisionByZero`, taking down the
   whole response. Reproduced live (`POST /api/screener/search` → `500`,
   `"Error searching stocks: division by zero"`), fixed with `NULLIF(..., 0)` plus an explicit
   inequality check, and reverified returning real results.

### 8.6 Verified results (local Postgres, same environment as the original readings)

| Endpoint | Before (reported) | After (measured) |
|---|---|---|
| `/api/deep-value/scan` | 6.85-10.52s, **critical**, no cache | **0.38-0.41s** cold, **0.02s** warm (15-min cache) |
| `/api/system-health/` | 6.57s, **critical** | **~3.5-3.7s** cold (parallelized), **~0.003s** warm (2-min cache) |
| `/api/dashboard/main-page-data` | up to 5.42s p99, **critical** | **~0.003s** (background-prewarmed; essentially never cold in practice) |
| `/api/screener/market-overview` | 2.02-2.59s, **warning** | **0.31-0.33s** |
| `/api/alpha/finder` | up to 2.57s, **warning** | **0.045s** cold, **0.005s** warm |
| `/api/ml-stats/` | up to 2.11s, **warning** | **0.003s** warm (2-min cache); one observed cold outlier of 8-10s traced to Postgres's own page cache being cold for `enhanced_ml_training_data` (108MB / 65,846 rows against a 128MB `shared_buffers`) rather than app code — see §8.7 |
| `/api/stock/{symbol}/earnings` | up to 5.29s p99, **warning** | unchanged by design (real Yahoo Finance network call) — **5.3-5.7s** cold, **0.003s** warm (existing 1h/10min TTL cache was already correct) |
| `/api/performance/client-metric` POST/OPTIONS | up to 2.19s/1.92s, **warning** | **~0.002-0.02s** (was never actually slow — pure event-loop-starvation symptom of §8.1) |
| `/api/screener/search` | 1.48s, **warning**, 100% error (n=1) | **0.24-0.31s**, `200`, real results (bug in §8.5.2 was the actual cause of that error rate) |

Also load-tested: 18 concurrent requests spread across 6 different endpoints (deep-value,
system-health, main-page-data, market-overview, ml-stats, client-metric) all completed cleanly,
no hangs, no errors — confirms the pool deadlock fix holds under genuine concurrent load, not
just sequential requests.

### 8.7 Residual / not fixed here

- `enhanced_ml_training_data` (108MB, 65,846 rows) can still take 8-10s for `ml-stats`'
  `COUNT(*) FILTER (...)` aggregate when Postgres's `shared_buffers` (currently the 128MB
  default) doesn't happen to have it cached — easy to trigger by other queries evicting it under
  memory pressure. The 2-minute report cache means this is now paid rarely instead of on every
  request, but the query itself wasn't rewritten (there's no partial aggregate/index that would
  meaningfully help a `COUNT(*) FILTER` over the whole table). If this matters, the real fix is
  raising `shared_buffers` at the Postgres server level (a config change + restart, out of scope
  for this backend-code pass) — flagging as an infra decision for the user, same category as the
  open deployment/scheduling questions in CLAUDE.md §7.
- `/api/stock/{symbol}/earnings` was intentionally left as-is: it's already correctly isolated
  (plain `def`, own TTL cache) and its remaining latency is genuine external Yahoo Finance network
  time on cache misses, not something fixable in this codebase. A nightly cache-prewarm during
  `automation_pipeline.sh` would eliminate most user-facing cold hits but wasn't done here since
  it touches the pipeline, not the backend API surface this pass focused on.
