# MILESTONES.md — Donchian Breakout Platform Roadmap

> Living tracker toward the standing goal: **check everything is fine → test
> everything → deploy.** Update this file every session — check items off,
> add new findings under the relevant milestone, don't let it drift from
> reality. Architecture/mechanism detail lives in [CLAUDE.md](CLAUDE.md); this
> file is the status/roadmap view on top of it.
>
> **Frontend + data-accuracy fixes** (audit of 2026-09-19) are tracked separately in
> [FRONTEND_FIX_MILESTONES.md](FRONTEND_FIX_MILESTONES.md) (FM0–FM8) so this file
> stays a roadmap, not a bug list. Its rules apply to all work on that plan.

Status legend: ✅ done · 🔄 in progress · ⬜ not started · ⚠️ blocked/needs decision

---

## Milestone 0 — Full Codebase Audit ✅ DONE (2026-09-14)

Examined `mechanism/`, `ml_training/`, `backend/`, `frontend/`/`frontend_1/`,
`.env`, `.gitignore`, and all output/log directories. Full writeup in
[CLAUDE.md](CLAUDE.md). Raw findings, for the record:

**Architecture confirmed:**
- Pipeline: `yfinance` → Postgres (`stock_prices`, `technical_indicators`,
  `daily_fundamentals`, `breakouts`, `ml_training_data`, …) →
  `mechanism/screeners/multi_timeframe_screener.py` (daily+weekly+monthly
  Donchian breakout detection + alignment scoring) → ML enhancement via
  `mechanism/ml_enhancement/ml_signal_enhancer.py` → JSON output
  (`frontend_data/`) → FastAPI (`backend/main.py`, `:8000`) → Next.js
  dashboard (`frontend/`, `:3000`).
- `ml_training/` and `mechanism/ml_enhancement/` are **one connected system**:
  the trainer saves `momentum_predictor_v<timestamp>.joblib` into
  `ml_training/models/`, and the live screener auto-loads the newest one by
  filename timestamp at runtime. No manual promotion gate actually enforced.
- Last confirmed successful full pipeline run: **2025-11-07**, 7 min runtime,
  588 signals, 343 ML-enhanced, 41 high-confidence picks. Works when run.

**Issues found:**
- ⚠️ **Stale ML model** — newest model is `momentum_predictor_v20250729_1545.joblib`
  (2025-07-29). Not retrained since. Performance unverified.
- ⚠️ **No scheduler wired in anywhere in-repo** — `automation_pipeline.sh` has
  no cron/Task Scheduler entry checked into the repo. Unknown if/how it runs
  daily in production.
- 🐛 **Bug, fixed this session**: `mechanism/shared/database.py`'s
  `DatabaseManager.get_daily_data_for_weekly_calc()` called
  `self.get_connection()`, which doesn't exist (real method is
  `get_sync_connection()`) — would have raised `AttributeError` if ever
  called. Turned out to be **dead code**: `weekly_data_updater.py` has its
  own independent, correctly-implemented method of the same name on a
  different class, and that's the one actually used. Fixed anyway since it's
  a live landmine for whoever wires the shared version up next.
- 🐛 **`.gitignore` landmine (fixed this session)** — blanket `*.json` /
  `*.csv` / `stock_lists/` rules would have silently dropped `package.json`,
  `package-lock.json`, `tsconfig.json`, and the entire symbol universe
  (`mechanism/stock_lists/*`) the moment anything got `git add`-ed.
- 🧟 **Duplicate frontends (fixed this session)** — `frontend/` (bare scaffold)
  vs `frontend_1/` (real dashboard with dashboard components + API service).
- 🧟 **Dead backend stubs** — `backend/services/screener_service.py`,
  `ai_service.py`, `backend/database.py` are 0 bytes, unused by anything
  (`routers/screener.py` actually uses `services/market_service.py`).
- 🧟 **Stale duplicate output dirs** — `mechanism/data/`,
  `mechanism/frontend_data/` (latest file: 2025-08-26, vs. the live one at
  repo-root `frontend_data/` updated 2025-11-07), `mechanism/breakout_results/`,
  `mechanism/reports/`, `mechanism/logs/` — leftovers from a different CWD.
- 🧟 **`backups/` folder** — ~21,000 files including a full old `venv/` and an
  old `node_modules/`, untracked. Kept on disk per your call, now gitignored.
- 🧟 **Manual `*_backup.py` files** committed alongside their real counterparts
  in `mechanism/screeners/`, `ml_training/*/`, `ml_training/scripts/backup/` —
  redundant with git history.
- 🧟 **`ml_training/models/backup/*.joblib`** — old differently-named model
  experiments (`breakout_ai_*`, `hybrid_ai_*`, `six_month_classifier_*`) that
  don't match the `momentum_predictor_v*` pattern the loader looks for —
  inert, safe to archive elsewhere.
- 📦 **No root/`mechanism/` `requirements.txt`** — only `backend/` and
  `ml_training/` have one. `mechanism/` (the actual pipeline) has none.
- 📦 **Duplicate/inconsistent `.env` keys** — `API_HOST`/`API_PORT` defined
  twice, both `CORS_ORIGINS` and `ALLOWED_ORIGINS` present.
- 🧪 **No automated test suite** — only manual print-and-eyeball scripts named
  `test_*.py` (not pytest-discoverable assertions).
- 🚀 **No deploy config at all** — no Dockerfile, no CI workflow, no hosting
  target defined anywhere in the repo.
- 📊 **Scale gap vs. stated goal** — universe is ~1,000–1,012 symbols
  (S&P 500 + Russell 1000 + Nasdaq 100, deduped), not the ~2,000 target.
  Current `yfinance`-based fetch (`ThreadPoolExecutor`, `max_concurrent=3`,
  per-symbol rate-limit delay) is the real ceiling — a data-vendor problem,
  not a code-architecture one.

**Actions taken this session:**
- ✅ Created `CLAUDE.md` (architecture reference, kept in sync going forward).
- ✅ Deleted bare `frontend/` scaffold, promoted `frontend_1/` → `frontend/`.
- ✅ Rewrote `.gitignore` — scoped to real generated output, fixed the
  `*.json`/`*.csv` landmine, added `backups/`.
- ✅ Confirmed fix: `mechanism/stock_lists/*`, `frontend/package.json`,
  `frontend/tsconfig.json` are no longer accidentally ignored.
- ⬜ ML model performance check **not run** — this environment has neither
  `psycopg2`/`asyncpg` installed nor a reachable Postgres instance. Needs to
  run locally (command below, Milestone 2).

---

## Milestone 1 — Repo Hygiene 🔄 IN PROGRESS

- [ ] Commit `mechanism/stock_lists/*` (now untracked and visible — currently
      a fresh clone has no symbol universe to run against)
- [x] Delete `backend/services/screener_service.py`, `ai_service.py`,
      `backend/database.py` — confirmed 0 bytes, confirmed unused, deleted.
- [x] Fix `get_daily_data_for_weekly_calc()` bug in `mechanism/shared/database.py`
      (`self.get_connection()` → `self.get_sync_connection()`). Turned out to
      be dead code (see Milestone 0 note) but fixed anyway.
- [x] Add `mechanism/requirements.txt`.
- [x] Consolidate duplicate `.env` keys — removed dead `FLAST_ENV`/`FLAST_DEBUG`
      (Flask leftover typos, this is FastAPI), dead `AUTOMATION_DIR` (pointed
      at a path that no longer exists), dead duplicate `CORS_ORIGINS`/`API_HOST`/
      `API_PORT` block. Confirmed via grep that `ALLOWED_ORIGINS`/`CORS_ORIGINS`
      are **not currently read anywhere** — `backend/main.py` hardcodes CORS
      origins in the middleware instead. Kept one `ALLOWED_ORIGINS` with a note
      to wire it up. Kept the effective `API_HOST=localhost`/`API_PORT=8000`
      (dotenv resolves duplicate keys to the last-defined value, so these were
      already the ones in effect).
- [ ] Decide fate of `mechanism/data/`, `mechanism/frontend_data/`,
      `mechanism/breakout_results/`, `mechanism/reports/`, `mechanism/logs/`
      (stale duplicates — archive or delete). **Not yet deleted — these plus
      the `*_backup.py`/`.joblib` items below are all untracked-and-uncommitted,
      meaning deleting them now would be unrecoverable (no git history to fall
      back on). Recommend committing the current cleaned-up state first.**
- [ ] Decide fate of `*_backup.py` files and `ml_training/models/backup/*.joblib`
      — same "commit first" caveat as above.
- [ ] Prune or adapt `SKILLS/` — currently generic templates referencing a
      stack (Neon/Bun/Playwright) this project doesn't use

## Milestone 2 — Verify System Health 🔄 IN PROGRESS — going well

- [x] Installed Python deps into a fresh project-local `.venv/` (none existed
      before — the project had been running against whatever was globally
      installed, unpinned and unreproducible). Merged `mechanism/` +
      `backend/` + `ml_training/` requirements.
- [x] Ran `master_automation_runner.py health` — **system is fundamentally
      healthy**: DB connects, 582.6K price records, 569.6K technical
      indicator records, 503.9K fundamentals, 37.98K breakout/ML training
      records, 1,005 unique symbols, data through 2025-11-06.
      `SYSTEM STATUS: PRODUCTION READY`.
  - 🐛 **Bug found + fixed**: this command couldn't even run —
    `mechanism/shared/__init__.py` never exported `get_system_stats` (defined
    in `utils.py` but missing from the package's imports/`__all__`), so
    `master_automation_runner.py`'s import crashed immediately. Also fixed a
    stale `'performance_utils'` entry in `__all__` that should have read
    `'performance_monitor'`. Confirmed working after the fix.
- [x] **Root cause of the 10-month outage found and fixed**: `daily_data_updater.py
      --test AAPL MSFT GOOGL` returned `"possibly delisted; No price data
      found"` for all three (obviously not actually delisted). This is
      `yfinance==0.2.40` (the version pinned in the original requirements)
      being incompatible with Yahoo Finance's current unofficial API — Yahoo
      changes it often and yfinance ships fixes as point releases. Upgraded to
      `yfinance>=1.7.0` in `mechanism/requirements.txt` (deliberately
      unpinned with an explanatory comment) → **data fetching works again**,
      confirmed with real inserts (211 price + 86 indicator records/symbol).
- [x] Verified `daily_data_updater.py`, `weekly_data_updater.py`, and
      `monthly_data_updater.py` all work end-to-end against the live DB with
      the upgraded `yfinance` (tested on AAPL/MSFT/GOOGL).
- [x] Full daily-universe catch-up run: **1005/1005 symbols, 100% success,
      4m22s** (`mechanism/data_updaters/daily_data_updater.py`, full run, no
      limit). 37 symbols (3.7%) returned genuinely no data from Yahoo (e.g.
      ANSS/Ansys — acquired by Synopsys in 2025) — real delistings/M&A against
      a symbol list last generated 2025-07-06, not a bug. DB now current
      through 2026-09-14.
- [x] Made `automation_pipeline.sh` use the project's own `.venv/` explicitly
      (`$SCRIPT_DIR/.venv/Scripts/python.exe`) instead of bare `python` on
      PATH — the ambiguity of "whatever python happens to be on PATH" is
      part of what let this drift out of sync with pinned deps in the first
      place.
- [x] Ran the **full `automation_pipeline.sh`** end-to-end for real: daily
      update → weekly (correctly skipped, "too early in week") → monthly
      (correctly skipped, "too early in month") → screener. **3m52s total.**
      547 signals, 220 high-quality, 220 ML-enhanced, 12 high-confidence, top
      pick ARW at 75.9% ML probability. **The system is fully relaunched.**
- [x] Ran `ml_training/evaluation/performance_tracker.py` — hit two more bugs
      on the way, both fixed:
  - 🐛 File had corrupted non-UTF-8 bytes (mangled/double-encoded emoji) in
    12 `print()` statements that made the file fail to parse *at all*
    (`SyntaxError: Non-UTF-8 code`). Cleaned to plain ASCII text.
  - 🐛 Script read `DB_PASSWORD` etc. via bare `os.getenv()` but never called
    `load_dotenv()`, so run standalone it silently fell back to the literal
    placeholder `'your_password'` and failed to authenticate. Added
    `load_dotenv()`. (`ml_training/setup_ml_environment.py` has the same gap,
    not yet fixed — lower priority, one-off setup script.)
  - **Finding, not a bug**: once it could actually run, it reported *"No
    predictions to evaluate"* — because `mechanism/ml_enhancement/
    ml_signal_enhancer.py` generates ML predictions live but never calls
    `record_predictions()` to log them anywhere. The feedback loop
    (predict → log → evaluate outcome → retrain) was built in
    `ml_training/evaluation/performance_tracker.py` but never connected to
    the live screener. This is very likely *why* the model sat stale for a
    year — nothing was ever in a position to tell anyone it needed retraining.
- [x] **Wired up prediction logging**: `multi_timeframe_screener.py` now
      imports `MLPerformanceTracker` and calls `record_predictions()` at the
      end of `enhance_signals_with_ml()` for every ML-enhanced signal, tagged
      with the model version. Wrapped in try/except so a logging failure can
      never break an actual screening run. Going forward, every daily run
      leaves a trail `performance_tracker.py` can evaluate.
- [x] **Two more real, significant bugs found and fixed while regenerating
      historical breakout labels** — first attempt found **0 breakouts across
      all 953 symbols**, which is what triggered digging into these:
  - 🐛 **`technical_indicators` had a permanent 6-month hole** (2025-11-06 to
    2026-05-11) that the daily catch-up run did *not* close, even though
    `stock_prices` was fully backfilled. Root cause:
    `get_full_price_data_for_indicators()` in `daily_data_updater.py`
    hard-capped its fetch at `ORDER BY date DESC LIMIT 100` — so the older
    portion of any gap bigger than ~100 trading days is never even fetched,
    regardless of what the downstream "backfill newer than last row" logic
    intends. Fixed by fetching full available price history per symbol
    instead (cheap — a few hundred rows/symbol).
  - 🐛 **The backfill-detection logic itself couldn't repair a hole in the
    middle of the table**, only extend forward: it compared new data against
    `MAX(date)`, so once *anything* writes a row at the current date (which
    the very first catch-up run did, using the old 100-row-limited fetch),
    `MAX(date)` stops pointing at the edge of the hole and the gap becomes
    permanently invisible to that check. Fixed by comparing against the
    actual set of existing dates per symbol instead of just `MAX(date)` —
    self-healing against a gap of any size or position, not just this one.
    Ran a one-off backfill after both fixes: **1005/1005 symbols, 96s**,
    gap closed and verified (continuous daily rows, no gaps, confirmed on
    AAPL/MSFT/JPM).
  - 🐛 **`identify_breakouts()` compared price against the wrong day's
    channel — a structural, not just gap-related, bug.** It checked
    `current['close'] > current['donchian_high_20']`, but
    `donchian_high_20` is `high.rolling(20).max()` computed *inclusive* of
    the current row, so a day's own close can never exceed its own
    same-day Donchian high by construction — the condition was
    mathematically unsatisfiable. This means **this function could never
    have found a breakout, on any data, at any time** — not a
    regression from the outage. (The live screener's own
    `get_daily_breakout_signals()` in `multi_timeframe_screener.py` does
    this correctly, comparing against `prev_donchian_high`/`prev_donchian_low`
    — used as the reference for the fix.) Fixed by comparing `current['close']`
    against `previous['donchian_high_20']`/`previous['donchian_low_20']`
    (yesterday's channel, built without today's price action) instead.
    Verified: AAPL alone now correctly shows 16 real historical breakouts.
    **Open question, partially answered below**: since this function could
    never have produced a breakout, the existing 37,981 rows in `breakouts`/
    `ml_training_data` were *not* generated by this code path as it stands —
    the sequence-desync finding just below is strong evidence they came from
    a bulk/manual import rather than this generator, not the live screener's
    detection logic run separately.
  - 🐛 **`save_to_ml_training_table()` was calling `INSERT INTO
    ml_training_data`, but `ml_training_data` is a VIEW**, not a table —
    `SELECT b.*, df.overall_quality_score, df.quality_grade, df.sector,
    ti.rsi_14, ti.volume_ratio AS tech_volume_ratio FROM breakouts b LEFT
    JOIN daily_fundamentals df ... LEFT JOIN technical_indicators ti ...`.
    It has no storage of its own; every row in it is derived live from
    `breakouts` (+ fundamentals/technical context). The INSERT was broken
    two ways over: (1) it referenced 8 columns — `final_return_10d`,
    `volatility_10d`, `volume_confirmation`, `breakout_strength`,
    `price_vs_ma20`, `price_vs_ma50`, `macd_value`, `price_position` — that
    don't exist on the underlying `breakouts` table (confirmed by
    `ALTER TABLE ml_training_data ADD COLUMN` itself failing with "ALTER
    action ADD COLUMN cannot be performed on relation... not supported for
    views" — that's what surfaced this), and (2) even with correct column
    names, Postgres doesn't allow INSERT into a view built on a multi-table
    JOIN without an `INSTEAD OF` trigger, which doesn't exist here. **Removed
    the call** rather than fixed it — there's nothing for it to correctly
    do, since the view already reflects `breakouts` automatically the
    moment `save_to_breakouts_table()` (a real table, columns verified
    against the live schema) succeeds.
  - 🐛 **`breakouts_id_seq` was desynced from the table by ~38,000** — the
    sequence's `last_value` was 1452 while `MAX(id)` in the table was
    39418, causing `duplicate key value violates unique constraint
    "breakouts_pkey"` on essentially every insert attempt once the fixes
    above let real breakouts start flowing again. This is strong evidence
    the original 37,981-row dataset was bulk/manually loaded with explicit
    `id` values, never through this generator's own insert path, without
    the sequence being advanced to match — a classic bulk-import footgun.
    Fixed with `SELECT setval('breakouts_id_seq', (SELECT MAX(id) FROM
    breakouts))`. Checked the other four `id`-sequenced tables
    (`stock_prices`, `technical_indicators`, `daily_fundamentals`,
    `quarterly_fundamentals`) for the same issue — all in sync, only
    `breakouts` was affected.
  - Full regeneration re-run across all ~1,000 symbols with every fix
    applied: **10,090 new breakout training samples, 953/953 symbols,
    100% success, 0 errors, 57 seconds.** `breakouts` table: 37,980 → 48,070
    rows. `ml_training_data` view count matches exactly (confirms the view
    is correctly reflecting the real table). New-data success rate 57.1%
    (sane, not degenerate). **The ML training data pipeline is fully fixed
    and current.**
- [x] **Ran `ml_training/scripts/ml_pipeline_runner.py test` first** (50
      samples) before committing to a full run — clean end-to-end success:
      momentum labeling → feature engineering (30 features) → XGBoost
      training (AUC 0.714, accuracy 0.80) → model save. But the saved model
      was nowhere to be found in `ml_training/models/`...
  - 🐛 **Trained models were being saved *outside the entire project
    directory*.** `MomentumBreakoutPredictor.save_model()` defaulted to
    `model_dir="../models"` — a relative path that only resolves correctly
    if the process's CWD happens to be `ml_training/scripts/`. Running it
    the way the project's own README Quick Start instructs
    (`python ml_training/scripts/ml_pipeline_runner.py test`, from the
    project root) resolves `../models` relative to the *project root's
    parent* instead — i.e. `E:\...\pythonProjects\models\`, a folder
    sitting next to this project, not inside it. Confirmed: the test
    model landed there. Worse, `ml_signal_enhancer.py`'s model
    auto-discovery only globs `ml_training/models/`, `models/`, or
    `mechanism/models/` (paths anchored to the detected *project* root) —
    it would **never** find a model saved here. Training silently
    "succeeds" and the live screener just keeps using the old model
    forever, with no error anywhere pointing at why.
    **This has happened before**: the stray directory already contained
    orphaned `momentum_predictor_v20250713_1725`, `_1729`,
    `v20250720_1023`, and `_1047` models — matching dates just before and
    around the 2025-07-29 model that *did* land correctly and became
    "production." Strong circumstantial evidence this exact mistake is why
    two full days of earlier training attempts (7/13, 7/20) never actually
    updated the live model.
    Fixed by anchoring the default to the module's own file location
    (`os.path.dirname(os.path.abspath(__file__))` — i.e. `ml_training/models/`
    itself) instead of a CWD-relative string. Cleaned up the stray test
    output; left the older orphaned files where they were (outside the
    repo, not this project's to clean up).
- [x] **Retrained the model on the fresh, corrected dataset** — full
      pipeline (`run_full_pipeline`, expanded limits: momentum_limit=6000,
      feature_limit=4000, training_limit=10000, using the newly-fixed
      48,070-row `breakouts` table). Ran in ~11.5 minutes.
  - **⚠️ Result: the retrained model was rejected, and correctly so.** On
    the full 4,542-sample dataset (vs. the earlier 50-sample smoke test's
    deceptively good AUC 0.714), the real model scored **AUC 0.572**
    (barely above the 0.5 random baseline) with **0.00 recall on the "High
    Momentum" class** — it degenerated into always predicting the majority
    class. `MomentumBreakoutPredictor`'s own quality gate correctly refused
    to save it (`ERROR: Model performance too low (AUC: 0.572)`), so
    **nothing was overwritten — the 2025-07-29 model is still the active
    production model**, just as stale as before. This is not a bug I
    introduced; it's the real, previously-unmeasured performance of this
    modeling approach once given enough data to evaluate honestly (the
    50-sample test's 0.714 was noise — too small a sample to mean anything,
    which is itself worth remembering before trusting any small-sample
    result in this pipeline going forward).
  - **Not yet investigated — worth a real look before retraining again**:
    is this a label problem (the momentum-score threshold defining "high"
    vs "low" in `momentum_labeler.py`/`ml_config.py`'s
    `STRONG_MOMENTUM_THRESHOLD`), a feature problem (30 features, several
    sector one-hots dominating importance ahead of anything price-action
    related), a class-imbalance problem (31% positive rate, no
    class-weighting visible in the training call), or a fundamentally
    weak signal (momentum/breakout follow-through may just be hard to
    predict from these inputs, which would itself be a useful thing to
    know rather than assume the pipeline will yield an "alpha" once
    plumbing is fixed). Flagging rather than guessing further under time
    pressure — recommend treating this as its own follow-up investigation.
- [x] **Daily automation scheduled**: registered a Windows Scheduled Task
      (`DonchianScreenerDailyPipeline`, daily at 06:00, runs
      `automation_pipeline.sh` via Git Bash) — state `Ready`. Uses
      `Interactive` logon (runs when the account is logged in, even
      screen-locked; will **not** run if fully logged out/shut down — true
      run-when-logged-off needs stored credentials, not set up).
  - [x] **Manually triggered and verified end-to-end** (2026-09-14,
        `Start-ScheduledTask`): ran the real 4-step pipeline via Task
        Scheduler → Git Bash → venv python, exactly as the 06:00 job will.
        Completed in 7m22s, full screener run (547 signals → 220
        high-quality → 220 ML-enhanced), sensible sector breakdown and top
        picks. Confirms the scheduling mechanism itself works, not just the
        scripts run manually.

## Data correctness audit (2026-09-14) — 3 more real bugs found and fixed

Prompted by "let's check the data is correct." Full DB sanity pass:
`stock_prices`/`technical_indicators` clean (0 duplicates, 0 negative
prices/volume, 0 impossible RSI/Donchian values across 787K+/774K+ rows;
264 pre-existing bad-OHLC rows found, all dated `2025-11-07` or earlier —
**zero from anything written today**, confirmed via `updated_at`, left
alone as low-priority historical noise). Three real, previously-latent
bugs surfaced once real volume flowed through post-relaunch:

- 🐛 **`ml_predictions.breakout_type` was `varchar(10)`**, but real signal
  types run up to 16 chars (`bullish_breakout`, `near_bullish`,
  `near_bearish`). One bad row (ARW) hit the limit, which aborted the
  Postgres transaction — and because `record_predictions()` caught the
  per-row exception without ever calling `rollback()`, every subsequent
  `execute()` in that same poisoned transaction failed too
  (`current transaction is aborted...`), silently dropping an entire
  217-signal batch down to 0 recorded predictions. Fixed both: widened the
  column to `varchar(20)`, and changed `record_predictions()` to commit
  per-row with a per-row rollback on failure, so one bad row can never
  cascade again. Re-ran the screener: **217/217 predictions now correctly
  recorded.**
- 🐛 **`predict_ml_momentum()` never actually returned `ml_model_version`**
  in its result dict, despite the attribute existing on the class — every
  logged prediction showed `model_version: 'unknown'`, which would have
  made it impossible to ever tell old-model vs. retrained-model predictions
  apart in `performance_tracker.py`'s evaluation. Added the field to the
  return dict. Verified: predictions now correctly tagged
  `momentum_predictor_v20250729_1545`.
- 🐛 **5 quality-score columns in `daily_fundamentals` were `NUMERIC(4,2)`**
  (max 99.99) for scores that are supposed to reach 100 — a legitimate
  score of exactly 100.00 already doesn't fit that precision. Any symbol
  hitting 100 (or a formula edge case pushing slightly over) silently lost
  its *entire* fundamentals row on insert (found via CNA Financial: `numeric
  field overflow`). Widened all 5 to `NUMERIC(5,2)`; had to `DROP`/recreate
  2 dependent views (`latest_fundamentals`, `ml_training_data`) to do it,
  since Postgres won't let you alter a column type a view depends on —
  verified both intact afterward (1,005 / 48,070 rows respectively,
  matching pre-change counts). Retried the 5 affected symbols
  (AIG/AMCR/C/CCL/CNA) — all 5 now succeed, all scoring exactly 100,
  confirming the fix precisely targeted the real cause.

**Fourth finding — not a data bug, a reliability bug, and the most
consequential one**: the full fundamentals run reported "8:23:04.194599"
total duration where steady-state pace predicted ~40 minutes. Root cause:
`fetch_company_info()`'s `ticker.info` call has **no timeout at all**, and
one symbol (IRM) hit a transient network/DNS blip (`curl: (6)`) and hung
for **7.6 hours** before finally erroring, after which the remaining ~700
symbols raced through normally in ~24 minutes. yfinance 1.x uses
`curl_cffi` internally, whose session/timeout isn't straightforward to
configure from the caller, so fixed with a hard wall-clock timeout via a
single-worker `ThreadPoolExecutor` + `future.result(timeout=20)` instead —
guaranteed to work regardless of what's hanging underneath. **Caught a
self-inflicted bug while building this fix**: an initial version used
`with ThreadPoolExecutor() as executor:`, whose `__exit__` calls
`shutdown(wait=True)` by default — which would have blocked on the still-
hung worker thread anyway, completely defeating the point. Fixed by not
using the context manager and calling `shutdown(wait=False)` explicitly,
leaving the orphaned thread to finish (or stay stuck) in the background —
an acceptable trade for "never block the whole run again." Verified with a
forced artificial hang (monkeypatched `yf.Ticker.info` to sleep 30s against
a 2s test timeout): fails fast at 2s as intended, correctly flows into the
existing `@retry_on_failure` backoff (2s→4s→8s) instead of hanging.
**This was silently capable of stalling the unattended 6am scheduled run
for hours, indefinitely, with nothing downstream ever running** — arguably
the highest-value fix of this whole session.

Final state, verified: all 1,005 symbols current (`stock_prices`/
`technical_indicators` through 2026-09-11, `daily_fundamentals` through
today for 972/1,005 — the other 33 are the same known-dead tickers, see
Milestone 4). All 5 `id` sequences healthy. Quality scores now range
5.00–100.00 (avg 76.7) with zero overflow errors.

## Milestone 3 — Testing ⬜ NOT STARTED

- [ ] Add a real `pytest` suite: unit tests for Donchian/RSI/MACD/ATR
      calculations, breakout detection logic, alignment scoring
      *(started 2026-09-20: `ml_training/tests` — feature module, plan outcomes, integrity rules, DB train/serve
      parity — and `mechanism/alerts/tests`, 25 tests total; run `python -m pytest ml_training/tests
      mechanism/alerts/tests -q`. Screener, updaters and backend still have none.)*
- [ ] Add an integration test for the daily pipeline against a test/staging DB
- [ ] Add basic backend API tests (`/api/health`, `/api/screener/*`)
- [ ] Wire a CI workflow (GitHub Actions or similar) to run the suite on push
- [ ] Replace/retire the ad hoc `test_*.py` debug scripts once real coverage
      exists

## Milestone 4 — Scale to ~2,000 Symbols 🔄 IN PROGRESS

Vendor decision (2026-09-14, after checking current pricing — see chat):
staged approach, starting free. Verified current pricing/capabilities
(training data can go stale on this, so this was checked live, not assumed):
**Alpaca** free tier ($0, unlimited historical daily bars, 200 req/min,
IEX feed) for price data now; **Financial Modeling Prep** Starter ($29/mo,
native fundamentals — sector/PE/PB/financials, not yfinance-scraped) as a
later addition once fundamentals quality actually needs it. Note: Polygon.io
rebranded to **Massive.com** in 2025/2026 — same product, new name, if it
comes up again.

- [x] **Built the Alpaca integration, behind a feature flag — not yet
      activated** (no API keys entered yet, this is on the user):
  - Added `data_provider` / `alpaca_api_key` / `alpaca_api_secret` to
    `shared/config.py`, defaulting to `yfinance` — completely inert until
    `.env` sets `DATA_PROVIDER=alpaca` + real keys. Verified: with the flag
    unset, `daily_data_updater.py --test AAPL` still runs the exact same
    yfinance path as before.
  - New `mechanism/shared/alpaca_client.py`: `get_daily_bars(symbol, period)`
    returns the identical DataFrame shape the yfinance path already
    produces (`date, open, high, low, close, adj_close, volume`), so it's a
    true drop-in — nothing downstream needed to change. Uses
    `alpaca-py==0.44.0` (verified against the actually-installed SDK's real
    class signatures, not assumed from memory — `StockHistoricalDataClient`,
    `StockBarsRequest`, `Adjustment.SPLIT` for split-adjusted prices,
    `DataFeed.IEX` for the free tier).
  - Wired into `daily_data_updater.py`'s `get_efficient_stock_data()` as an
    `if config.data_provider == 'alpaca': ... else: <existing yfinance
    code, unchanged>` branch.
  - Verified the "not configured yet" path fails with a clear, actionable
    `RuntimeError` (which env vars are missing + signup link) rather than
    an opaque SDK auth error.
  - Added `ALPACA_API_KEY=` / `ALPACA_API_SECRET=` (empty) and a commented
    `# DATA_PROVIDER=alpaca` to `.env` with instructions.
- [x] **Activated and verified working (2026-09-14).** First attempt used
      the wrong credential pair (Alpaca's OAuth "Client ID/Client Secret" —
      for building apps other users log into — instead of the personal
      "API Key ID/Secret Key" needed for direct access), caught immediately
      via a clean `401 Authorization Required`. Second attempt (`PK...` key
      — correct paper-trading-key format) worked:
  - Raw client: `get_daily_bars('AAPL')` → 274 real rows, latest close
    $332.245 on 2026-09-11 (vs. yfinance's $332.27 for the same day —
    expected small difference, IEX feed vs. Yahoo's consolidated data).
  - Full class integration verified too, not just the raw client:
    forced `EnhancedDailyDataUpdater.get_efficient_stock_data('MSFT')`
    through its "stale, needs incremental update" branch (monkeypatched
    `get_latest_date_for_symbol` rather than touching real DB data) — it
    correctly routed through Alpaca, fetched, and filtered to just the new
    rows. Read-only test, no DB writes.
  - `.env` now has `DATA_PROVIDER=alpaca` live and real keys filled in.
- [x] **Full-universe Alpaca run — done (2026-09-14), superseding the "not
      yet done" note above.** Forced all 1,005 symbols through the real
      `EnhancedDailyDataUpdater`/`run_enhanced_update` code path (not a
      hand-rolled loop) via a monkeypatched staleness check: **1005/1005,
      5m23s, 100% reported success.** ~38 symbols (BK, MMC, FI, EA, HES,
      WBA, K, AZEK, SKX, etc.) returned genuinely empty data from Alpaca's
      free IEX feed — confirmed via isolated re-test (no exception, just an
      empty result, not transient) that this is IEX's real per-symbol
      coverage limit, not a bug. **Same ~38 symbols fail on yfinance too**
      (matches the known-dead-ticker list below almost exactly) — so this
      turned out to be a symbol-list-hygiene problem, not a vendor
      weakness. Built a fallback anyway (Alpaca first, yfinance for
      whatever Alpaca can't cover) since it's a legitimate real gap:
      `daily_data_updater.py`'s `get_efficient_stock_data()` now falls
      back automatically, verified working on BK/MMC/AAPL in isolation.
      Manually triggered the real Task Scheduler job on top of this (not
      just running scripts by hand) — 7m22s, real signals, confirmed the
      whole chain (Task Scheduler → Git Bash → venv python →
      `automation_pipeline.sh`) works end to end with Alpaca active.
- [ ] Not yet done: same swap for `fundamentals_updater.py` (still
      yfinance-only; FMP is the planned target, not started — no API key
      yet either, and this wasn't the option chosen this round).
- [ ] Not yet done: bulk/batch endpoint investigation (Massive's "grouped
      daily" all-symbols-in-one-call endpoint, or FMP's batch endpoints) —
      flagged as a possible real architectural simplification over the
      current concurrent-loop-with-rate-limiting design, but not confirmed
      or built.
- [x] **Symbol list refresh & dead-ticker exclusion — done (2026-09-15).**
      Started as "just run `symbol_scraper.py`," turned into real debugging:
  - 🐛 **Nasdaq-100 scraper returned 0 symbols with no error.** Wikipedia
    split the constituents table off the main "Nasdaq-100" article onto its
    own "List of NASDAQ-100 companies" page at some point; the old URL's
    page no longer has any Ticker/Company table at all. Confirmed by
    fetching both pages' raw HTML directly rather than guessing. Fixed:
    one-line URL change to the new page, which has the exact
    `id="constituents"` table and column order (`Ticker` first) the
    existing parsing code already assumed — nothing else needed to change.
  - 🐛 **Russell 1000 scraper was broken beyond the `403` rate-limit it hit** —
    the Wikipedia "Russell 1000 Index" page doesn't contain a full
    1,000-company table at all (confirmed: only 71 `<tr>` total across the
    *entire* page). This wasn't a today-only rate-limit problem; the
    approach was never going to work. Checked the fallback the code's own
    comment pointed at (iShares' IWB ETF holdings CSV, the standard source
    for this) — blocked by iShares' CDN (`403 Access Denied` from Akamai)
    even with a browser-like User-Agent. Free, reliably-scrapable Russell
    1000 constituent data is a known hard problem (proprietary FTSE Russell
    data); **decision (user, given three options): stop chasing external
    sources, validate the existing ~1,005 DB symbols directly against
    live data instead** — solves the actual underlying problem (stop
    wasting time on dead tickers) without depending on fragile scraping.
    Added retry/backoff for 403/429 to `symbol_scraper.py` regardless,
    since it's a real resilience gap independent of the Russell 1000 issue.
  - **Caught a false-positive risk before it caused damage**: first
    validation pass (period='5d', single check) flagged AVB (AvalonBay
    Communities — a large, actively-traded S&P 500 REIT, no known
    delisting) as dead alongside genuine casualties like AL/AMED — failed
    on both Alpaca and yfinance in isolation, not just under batch load, so
    not obviously a fluke at first glance. Widened to period='1mo' and
    added a second pass with a real 30-second gap before finalizing
    anything as dead. Result: AVB (and everything else) passed clean on
    the wider window — zero false positives recovered on the retry pass,
    giving real confidence in the final list.
  - **Final validated result**: 967 alive / 38 dead, matching the earlier
    Alpaca-stress-test findings almost exactly (AL, AMED, ANSS, APLS, AZEK,
    BK, BLD, CCCS, CFLT, CIVI, CMA, CTRA, DAY, DNB, EA, EXAS, FI, FYBR,
    HES, HOLX, IAC, INFA, IPG, JHG, JNPR, K, LNW, MASI, MMC, MPW, NSA,
    PARAA, PSTG, SEE, SKX, SNV, SPR, WBA).
  - **Built the actual fix, not just a report**: creating
    `mechanism/stock_lists/*` files wouldn't have helped on its own — both
    `daily_data_updater.py` and `fundamentals_updater.py` source their
    working symbol set from `SELECT DISTINCT symbol FROM stock_prices`
    (whatever's already in the DB), never from the `stock_lists/` files at
    all, confirmed by reading the code. Refreshing the reference files
    alone would have left tomorrow's 6am run still retrying the same 38
    dead tickers forever. Instead: added a new `inactive_symbols` table
    (`symbol`, `reason`, `marked_inactive_date`), populated with the 38
    confirmed-dead symbols, and added `AND symbol NOT IN (SELECT symbol
    FROM inactive_symbols)` to both updaters' symbol-selection queries.
    Historical data for these 38 symbols is untouched — only future update
    attempts are skipped. Verified directly: both updaters now return
    exactly 967 symbols (was 1,005), confirmed `AL`/`BK` excluded from both.

## Milestone 5 — Deploy ⬜ NOT STARTED

- [ ] Containerize `backend/` + Postgres (Dockerfile + docker-compose)
- [ ] Pick a host for the scheduled daily pipeline (must run unattended —
      not dependent on a laptop being on)
- [ ] Deploy `frontend/` (Vercel is the natural fit for Next.js)
- [ ] Wire real scheduling (cron on the host, or a managed scheduler) — includes the 06:00 Jerusalem
      Telegram alert step (Milestone 6B), which must run after the price pipeline finishes
- [ ] Add basic monitoring/alerting for pipeline failures

## Milestone 6 — ML integrity & momentum alerts 🔄 IN PROGRESS (started 2026-09-20)

Origin: a full ML/strategy audit (chat, 2026-09-20) found the "ML momentum probability" had no measurable
edge — its reported AUC 0.70 came from a reversed train/test split plus labels corrupted by a price-basis
mismatch. Details, numbers and rationale: [CLAUDE.md](CLAUDE.md) changelog entries dated 2026-09-20.

**6A — ML audit fixes**
- [x] **Fix 1 · data integrity** — one price-only module (`ml_training/features/price_features.py`); dataset
      `ml_breakout_dataset_v2` rebuilt from `stock_prices` only (594,888 samples, 2018→); `price_discontinuities`
      registry (1,039 rows / 121 symbols); $1M/day liquidity floor.
- [x] **Fix 2 · honest evaluation** — `momentum_predictor.py` rewritten (chronological holdout + 30-day embargo,
      inner-validation early stopping, block-bootstrap CI, walk-forward, baselines, promotion gate).
      **Result: no model passes** → ML scores are unavailable in the app until one does.
- [x] **Fix 3 · train/serve parity** — enhancer calls the same feature module; explicit "unavailable" reasons
      instead of defaults; DB parity test (150 real rows, < 1e-6).
- [x] Consequences fixed: unscored signals no longer rank above scored ones; strategy score renormalises instead
      of scoring a missing ML value as 0; `/top-ai-picks` returns `null` not `0`; frontend types/renderers
      null-safe; `/ml-stats` + `/system-health` report the *served* model and the latest honest evaluation;
      screener null-safe, refuses to overwrite `latest_*.json` with an empty result, exits non-zero on failure.
- [x] Retired (deprecation banners): `momentum_labeler.py`, `feature_builder.py`; `ml_pipeline_runner.py`
      rewritten as a thin runner (`test` / `build` / `train` / `full`).
- [ ] **Fix 4** — confidence tiers are uncalibrated vs the base rate; `ml_risk_score` / `ml_trade_recommendation` /
      `ml_predicted_momentum_days` are constants in the live path (legacy standalone screener computes them by rule).
- [ ] **Fix 5** — automate build/train/outcome tracking in `automation_pipeline.sh` (nothing ML is scheduled).
- [ ] **Fix 6** — new predictive features or a redefined label (tail-focused ranking model: target = volatility-
      adjusted runner R, judged on top-decile tail lift). EDGAR fundamentals need a contact e-mail for the SEC
      User-Agent (SEC returns 403 without one).
- [ ] Recompute stale historical `technical_indicators` (+ weekly/monthly) from `stock_prices` (~24% of
      2023-Q3..2025-Q3 rows drift > 0.5%); add an adjustment-event guard to the daily updater (vendor restates
      history after every ex-dividend/split).
- [ ] Known limitations to keep in mind: survivorship (universe = current constituents), overlapping
      consecutive-day breakouts (samples not independent), bull-market-heavy 2018-2026 sample.
- [ ] Decisions pending with the user: what the UI shows while ML is off (dashboard "AI picks" is now just
      alignment ties); long-only default (shorts −0.16R in backtest); see also FRONTEND_FIX_MILESTONES §11 FM-N10..N14.

**6B — Telegram momentum alerts** (replaces the Finviz top-15 channel; 06:00 Asia/Jerusalem, liquid universe only)
- [x] `.env` fields (`TELEGRAM_*`, `ALERTS_*`); `mechanism/alerts/` package (candidates, enrichment, weekly/monthly
      false-breakdown facts, plan templates, short-title news links, Telegram client); `alerts` ledger table;
      18 unit tests; dry run verified on live data (session 2026-09-18, 3,070-symbol universe).
- [x] Connectivity verified 2026-09-20: a labelled test message was delivered to both the dev and the prod channel.
- [x] Evidence behind the design (studies in CLAUDE.md changelog): no proven selection edge from price/volume
      anomalies or the weekly/monthly false-breakdown filter; top gainers carry a fat right tail; wide-trail runner
      exit beats fixed targets → default plan template `balanced` (`runner` / `ladder` selectable).
- [ ] Review card format on the DEV channel (`send_daily_alerts.py --send`), then add theme grouping (7 of the 15
      alerts on 2026-09-18 were crypto-linked) and a compact-digest option (currently 16 messages/day).
- [ ] Outcome tracker for the ledger (reached +100% / +300% within 3/6/12 months, R multiples per alert type).
- [ ] Chart images (own daily/weekly/monthly panels with stop/TP drawn) and inline Traded/Watch/Skip buttons.
- [ ] Scheduler / pipeline step at 06:00 Jerusalem (the price pipeline must finish first; the alert aborts safely
      on stale data). Open together with Milestone 5 scheduling.
- [ ] EDGAR: net-income turnaround / >100% growth features and 8-K event dates (needs the contact e-mail).

**6C — "First Light" digest** (long side only: Breakout and Near breakout × Top gainers / Top ATR / Top volume, top 5 each; facts only)
- [x] Slice 1 (2026-09-20): `mechanism/alerts/digest_builder.py` / `digest_format.py` / `send_daily_digest.py`, 10 tests in
      `test_digest.py` (28 alerts tests total). Groups reuse the screener's rules. Whole universe analysed in ~36 s
      (18 s DB load). Counts cross-checked against an independent SQL query. Sent to the DEV channel.
- [x] Redesign 2026-09-20 after review ("emojis / Ignition / Thunder not understandable", "charts and news too heavy",
      "long only"): plain-word labels, no emojis, no news, no charts, short side dropped, 3 messages (header + one per
      group; only the header notifies), bold ticker = in 2+ lists, "was near breakout yesterday" on breakouts, NEW on
      near breakouts only. Removed: Pulse / Trap Watch checklists, Thunder / Shockwave names. `MIN_BARS` 210 → 60 (no
      SMA200 needed any more, so recent listings are now included).
- [x] Phone review 2026-09-21 (screenshot: ~38 chars/line, most rows wrapped) → **two-line rows** (name/price/change, then
      details), data-first headline line, legend collapsed in an expandable quote, header pushed to ≤ ~1.6k chars.
- [x] **Snapshot tables** (`mechanism/add_digest_tables.sql`, applied to the live DB): `digest_runs`, `digest_stocks`
      (all 2,866 analysed stocks for 2026-09-18), `bot_users`, `bot_watchlist`. A real send saves the snapshot.
- [x] **Interactive bot, phase 1 + watchlist (pull mode)** built and unit-tested (57 tests with the ML suite, incl. the
      aiogram wiring driven by fake updates and a Postgres round trip): `bot_service.py`, `run_bot.py`, `texts.py`,
      `snapshot.py`. Acknowledgement button before any reply, `/watch` `/unwatch` `/mylist`, popup definitions,
      per-user rate limits, optional channel-member gate, wording guard test. **Not verified live in Telegram yet** —
      nothing has pressed the buttons: run `python mechanism/alerts/run_bot.py`, open the bot, `/start`, then
      `send_daily_digest.py --send --buttons` to test the popups (they only respond while the bot runs).
- [x] **Visual layer (2026-09-21):** market-performance card (`market_card.py`, `market_context.py`, `--image` flag, off by
      default until approved), text polish (bold tickers, star for multi-list, arrows, quote blocks), `send_photo` in the
      client. Card previewed from real data (`reports/first_light/preview_market_card.png`) in the deep-blue **Midnight Dawn** palette (aqua up / coral down, validated, contrast test). 87 tests incl. ML, green on both the .venv (Pillow 12) and global (Pillow 10) interpreters.
      NOT yet sent to Telegram (waiting for the user's look at the PNG); animation (GIF/MP4) deliberately not built.
- [x] **`/levels SYMBOL` (2026-09-21):** ATR risk framework on request (risk level 2xATR below, reference levels 1R/2R/3R at +2/+4/+6 ATR,
      reward-to-risk of the distances, facts: ATR %, volume x, range x, distance to the 20-day high, avg $ volume), read from the
      snapshot (new `digest_stocks.atr` column, refreshed for 2026-09-18), deep link `?start=lv_SYM`, key caveat always visible +
      "roughly break-even" history note collapsed. Numbers equal the dashboard's Strategy page (test). 106 tests. Not yet tried live.
- [ ] **Decisions before this goes to the PUBLIC channel:** (1) wording "risk level / reference levels" vs the UI's SL/TP names (the
      latter reads as a trade instruction; the wording guard bans it); (2) legal review of publishing per-stock levels to a public
      audience (operator's + audience jurisdictions) — impersonal formula + hypothetical framing helps but is not a legal opinion;
      (3) keep the history note ("roughly break-even", from the 2026-09-20 study) — recommended; (4) optional `/risk SYMBOL AMOUNT`
      calculator using the USER's own number instead of any suggested % of portfolio.
- [ ] **Found 2026-09-21:** the dashboard's screener sees 7 breakouts / 46 near-breakouts on 2026-09-18 vs the digest's 51 / 282
      (45 of 46 and 7 of 7 overlap, so they agree where both look). Probably a narrower screener universe (index constituents) —
      cause NOT confirmed; the 12 sampled missing names all have technical_indicators rows. So the UI has no plan for e.g. GEMI/COIN/PS.
      Also `strategy_calc.py` derives reward % from already-rounded prices (36.45 vs exact 36.46): cosmetic.
- [x] **Bot QA (2026-09-21, user: "/levels MSTR in the channel does not work"):** root causes = (1) commands cannot be typed in a channel
      at all and the bot ignored groups; (2) the user never acknowledged (the button tap was lost while the bot was off, and a stale tap
      crashed the handler); (3) the running process was stale code. Fixed: group support + deep links, `/agree`, stale-tap tolerance,
      error handler, single-instance lock, `<TICKER> levels` buttons. 860 tests incl. 4 mutation checks (re-introduced bugs are caught);
      live: 14 real replies posted to dev, Telegram accepted all (1 QA-tool label bug found + fixed + regression test).
- [ ] After restarting `run_bot.py`: run `python mechanism/alerts/qa_live.py` (expects all PASS) — see HANDOFF.md §8.
- ⚠ **SUPERSEDED IN PART by Milestone 7 (2026-09-21):** the group-chat replies, the `<TICKER> levels` channel buttons and the open-to-anyone
      mode above are to be removed / replaced by the invite-only private assistant (PRIVATE_ASSISTANT_PLAN.md §2, §11). Everything else in
      6C (digest, market card, snapshot, QA tooling, `levels.py`) is the base the assistant is built on.
- [ ] Bot reliability: it only works while a `run_bot.py` process runs (was found stopped when buttons 'did not work').
      Options: launcher with auto-restart on the PC, or a VPS (public audience needs the VPS). Popups = optional until then.
- [ ] Watchlist push mode (opt-in morning DM, only when something changed) — deliberately not built; decide after pull mode is used.
- [ ] Hosting for `run_bot.py` (PC while it is on, VPS later); a second test *channel* mirroring prod (callbacks on channel posts unverified); pinned "Start here" post + channel description; `.env.example` and a gitleaks pre-commit hook (bot skill checklist).
- [ ] **Public-audience constraints (decided 2026-09-21, audience ~30K):** educational / not-advice framing everywhere
      (pinned "Start here", channel description, footer, acknowledgement button in the bot, wording guard test); economic
      bot design (answers only from the daily snapshot in Postgres, no per-request provider calls, outbound links for
      charts/news, per-user rate limits); before going public: legal review of the framing in the operator's and main
      audience's jurisdictions, and check that the data vendor terms (Tiingo / Yahoo) allow public display of derived data.
      The older shortlist cards with stop/TP "Plan" text must NOT go to the public channel.
- [ ] Interactive bot, phases: (1) popup definitions, "full list" + facts card from the snapshot; (2) watchlists (pull
      first, opt-in morning DM later); (3) feedback buttons / owner commands. Needs the snapshot tables + an always-on
      process (polling first). Nothing built yet.
- Shelved ideas (still valid, not in the message): chart-sheet images, news links, trend-health checklist column,
  staged build → validate → send pipeline with dev preview / hold flag, pinned "Start here" guide, header jump buttons.
- [ ] Slice 0: pre-declared backtest of "top-5 by volume / by range" vs "all members" (dataset `ml_breakout_dataset_v2`
      covers breakout/breakdown only; near-categories need a compute pass). Nothing goes to prod claiming an edge before it.
- [ ] Ledger rows (`source = 'digest:<category>:<list>'`) + forward-outcome scoreboard per list.
- [ ] Schedule at 06:00 Jerusalem as its own job (needs only `stock_prices`; Tue–Sat mornings), then `--to prod`.

---

## Milestone 7 — First Light channel + private assistant 🔄 BUILT AND TESTED ON DEV (2026-09-21); human QA, scheduling and launch pending

Start here in a new session: **[HANDOFF.md](HANDOFF.md)**. Plan and rules: [PRIVATE_ASSISTANT_PLAN.md](PRIVATE_ASSISTANT_PLAN.md) (§0 binding rules).
Product design: [BOT_DESIGN_REPORT.md](BOT_DESIGN_REPORT.md). Run/test steps: [RUNBOOK_FIRST_LIGHT.md](RUNBOOK_FIRST_LIGHT.md). Funnel and launch: [FUNNEL_PLAN.md](FUNNEL_PLAN.md).
Direction (user, 2026-09-21): the **channel** carries only data, promotion, news and information that catches traffic; the **private assistant**
(invite-only) provides everything personal in its own chat and **never posts in a channel**. Everything is built and tested on the DEV channel;
production ("Top Gainers - Daily") is locked (`PROD_SENDING_ENABLED=0`) until launch.

- [x] **7.0 Access layer** — invite-only (owner = `BOT_OWNER_ID` in `.env`, never in the DB), invitation codes stored as SHA-256 hashes only, revoke, purge after 30 days,
      groups get no data, fail-closed on a DB error (the owner is never locked out), no per-ticker buttons in the channel.
- [x] **Tracker core and assistant UX** — 4-step guide + persistent menu; Today's lists (the channel's lists, each stock once, tabs, paging); stock card with News (Alpaca,
      cached 6 h, shared), Chart (mplfinance, cached by Telegram file_id) and ATR levels; **Watchlist + Portfolio storing symbol, the user's price and the day added, showing the
      change since then** (value, weights, totals, `n/a` on a split/adjustment instead of a fake return); `/add /remove /export /deleteme /privacy /guide`.
- [x] **Request-access flow** — the channel button opens the bot; a stranger sees what it is and can tap *Request access*; only then Telegram id + time are stored; the owner gets
      Approve / Decline buttons; 7-day cool-down after a decline, 14-day purge, cap 100 waiting; `BOT_ACCESS_MODE` approve|auto|closed, `BOT_MAX_MEMBERS` 25; `/requests`, `/funnel`.
- [x] **Channel tooling** — `channel_posts.py` (pinned Start-here + promo), `promo_assets.py` (promo image, bot avatar, channel logo), `replay_dev_channel.ps1`,
      `run_first_light_morning.ps1` (gate → index → prices → digest), `dev_chat_reset.py` (dev groups only). A NEW dev channel "First Light - Dev" replaced the old flooded group.
- [x] **Safety net** — production lock in the only constructor of the prod sender; channel-isolation tests (the digest/channel modules must not import assistant screens, the tour goes only to
      the owner's private chat); wording guard; **1,364 tests pass; 39 mutation checks all caught** (`mechanism/alerts/tests/mutation_checks.py`).
- [ ] **Human QA** (RUNBOOK §2, 29 steps + §8 request-access with a second account) and a friend session; popup buttons inside a channel are still untested.
- [ ] **BotFather:** upload the bot avatar, turn **Allow Groups off** (rename to "First Light Assistant" is done). Upload the channel logo to the dev channel.
- [ ] **Channel content beyond the digest:** market-news post, rotating promo posts, weekly note (awaiting the user's yes; FUNNEL_PLAN §10).
- [ ] **Scheduling:** register the Task Scheduler jobs with `-To dev` (RUNBOOK §3.3) and watch 5 trading days.
- [ ] **7.2 Personal morning brief** (opt-in DM, only when something changed) - the retention loop; needs `bot_user_settings`.
- [ ] **7.4 Personal strategy profile, sizing and journal.**
- [ ] **7.6 Operations:** hosting (PC vs VPS), auto-restart, monitoring, encrypted backups.
- [ ] **Hard gates before any non-owner is invited:** legal review (personalised levels + portfolio tools; privacy-law duties) and a data-licensing check (Tiingo / Yahoo / news provider); decide field encryption (D5).
- [ ] **Launch (FUNNEL_PLAN §8):** new production channel "First Light - Stocks & Info", its id in `TELEGRAM_CHAT_ID`, pinned Start-here, promo, then `PROD_SENDING_ENABLED=1` (only on the user's explicit go-ahead).
- ⚠ **Nothing from 2026-09-21 is committed to git** (see HANDOFF.md §2).

---

## Changelog

- **2026-09-21 (end of day)** — Milestone 7 built and tested on DEV: assistant (tracker, news, chart, guide), request-access flow, production lock, new dev channel, promo assets, 1,364 tests + 39 mutation checks. Session handoff written (HANDOFF.md). Human QA, scheduling, hosting, legal review and launch are open.
- **2026-09-21 (later)** — Milestone 7 phase 7.0 built: invite-only access layer, group replies and per-ticker channel buttons removed,
  992 tests + 9 mutation checks, live-validated on the dev chat. Owner steps pending (see 7.0). Next: 7.1 portfolio core.
- **2026-09-21** — Milestone 6C bot/digest visual + QA work done; **direction change:** strategy/portfolio/news become a private,
  invite-only per-user assistant (Milestone 7, plan file added). The group-reply support and channel `levels` buttons built earlier the same
  day are superseded and scheduled for removal in 7.0.
- **2026-09-14** — Milestone 0 (audit) completed; file created. Milestones
  1–5 scoped out based on audit findings.

- **2026-09-20** — Milestone 6 opened. ML audit → fixes 1-3 done (data integrity, honest evaluation, train/serve
  parity); result: no ML model passes the promotion gate, ML scores are off in the app. Telegram alerts first slice
  built and connectivity-tested on the dev and prod channels. Also: `.env` gained Telegram/alert settings; a wedged
  dev backend on :8000 was diagnosed (CLOSE_WAIT pile-up) and the find-and-kill procedure documented in CLAUDE.md §8.
  First real pytest suites exist (`ml_training/tests`, `mechanism/alerts/tests`, 25 tests) — see Milestone 3.
