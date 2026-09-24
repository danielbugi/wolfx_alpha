# DATA_ML_MILESTONES.md — execution tracker: scheduling, earnings, fundamentals, ML

> Source of truth for *what* and *why*: the 2026-09-22 conversation (fundamentals-usage audit, ML feature audit,
> scheduling audit). This file tracks **status**. Update it at the end of every milestone (status, what changed,
> what was verified, what is left) — same convention as [CHANNEL_CONTENT_MILESTONES.md](CHANNEL_CONTENT_MILESTONES.md).
> Binding rules carried over from CLAUDE.md: root cause not band-aids, no fabricated defaults, a number that
> cannot be trusted is `n/a`/`None` never invented, features are point-in-time (no lookahead), every new module
> ships with tests, dev-tested before anything touches the production channel.

## Status legend
`[ ]` not started · `[~]` in progress · `[x]` done and verified · `[!]` blocked (needs the user / an outside decision)

## Decisions made this session (so later work doesn't re-litigate them)
1. **Schedule the heavy pipeline after the US close, not at 06:00 Israel time.** Removes the overnight-sleep /
   wake-timer race entirely (the PC is reliably on in the evening); 05:00/06:00 FirstLight jobs stay as-is,
   unchanged, as a safety net and the actual send.
2. **Retrain the ML model every night** (dataset rebuild + both targets, gate-checked, auto-promote on pass).
   Cheap (~10-15 min total), and means any feature/label improvement gets evaluated the very next cycle instead
   of waiting for a manual run.
3. **Fundamentals refresh becomes earnings-date-driven**, not a blind 25-day timer — cheaper (fewer wasted calls)
   and correct (catches new data exactly when it exists, no later, no earlier).
4. **A daily "who reports today" channel post** — built from the same earnings-date table, gated on "is there a
   US trading session today" (skip entirely on weekends/holidays — nobody reports and nobody trades).
5. **Valuation ("cheap vs. expensive") becomes a per-sector relative feature, not an absolute threshold**, fed to
   the model alongside existing distance-from-52-week-high/low features, so the model — not a hand-written rule —
   finds whether "cheap + near lows" (value/turnaround) and "expensive + near highs" (quality-momentum
   continuation) are both real, separate setups. No hand-coded "cheap=buy" rule.
6. **Multi-timeframe alignment (monthly candle shape + ATR anomaly + weekly strength) becomes a real ML feature**,
   not just the dashboard's existing hand-tuned display score. Built as its own module on the same
   single-source-of-truth-from-`stock_prices` philosophy as `price_features.py`, added to the *same* model first
   (simplest path through the existing eval/gate harness); a dedicated second model is a later option once this is
   proven to add signal, not the starting design.
7. **Hosting stays on the user's machine** until the planned microservices split (mechanism / backend / frontend /
   telegram / database). No work item here — noted so future sessions don't re-propose a VPS migration as the fix
   for reliability; M1's reschedule is the interim reliability fix instead.

---

## M1 — Reschedule the pipeline + nightly ML retrain
- [x] M1.1 `automation_pipeline.sh`: added steps 9-11 (`build_dataset.py --replace` full rebuild, then
  `momentum_predictor.py --target momentum`, then `--target plan_profit`, each auto-promoting on the honest gate).
  `TOTAL_STEPS` 8 → 11. Verified: `bash -n automation_pipeline.sh` (syntax clean). A gate miss is not a pipeline
  failure — `momentum_predictor.py` exits 0 whether or not a model clears the gate (confirmed by reading `main()`/
  `run()`: the only `SystemExit` paths are genuine data problems — empty dataset, missing features — which
  *should* fail the pipeline loudly, so these three steps deliberately use the same `run_step()` wrapper as
  everything else, no special soft-fail path).
- [x] M1.2 `DonchianScreenerDailyPipeline` Task Scheduler entry: trigger moved 06:00 → **02:00** Israel time,
  `ExecutionTimeLimit` 3h → 4h (headroom for the added ~10-15 min of ML steps on top of the existing ~2-2.5h data
  pipeline). Verified live via `Get-ScheduledTask`: trigger and settings applied, next run 2026-09-23 02:00.
  **Why 02:00, not right after the ~23:00 close:** the market-calendar gate requires close + `MARKET_SETTLE_MINUTES`
  (120) to pass before it says "run" (worst case ~01:00-02:00 across the small DST-misalignment window between
  Israel and US daylight-saving transitions); 02:00 gives an hour of margin so the trigger essentially never fires
  into a "not ready yet, skip" no-op, which would otherwise cost a full day (the *heavy* pipeline only has this one
  daily trigger — unlike the digest, nothing else re-attempts it same-day). FirstLight-1 (05:00) and FirstLight-2
  (06:00) are **unchanged** on purpose: FirstLight-1 becomes a near-zero-cost safety net (re-checks `stock_prices`
  is current; skips fast if the 02:00 run already did it) in case the heavy run fails or the PC was asleep at 02:00.
- [ ] M1.3 **Not yet verified end-to-end**: the new 02:00 trigger and the 3 new ML steps have not run yet (next
  scheduled fire is 2026-09-23 02:00). Check `logs/pipeline_<date>.log` the morning after for: all 11 steps
  present, ML steps' exit codes, whether either target cleared the promotion gate, and total duration vs. the 4h
  limit. Also confirm the 05:00/06:00 FirstLight jobs still behave correctly against data that is now already
  fresh from 02:00 (should be a fast no-op update + normal digest).

## M2 — Earnings-date table + earnings-driven fundamentals refresh — DONE, verified live
- [x] M2.1 New table **`earnings_calendar`** (symbol, report_date, eps_estimate, eps_actual, surprise_pct,
  fetched_at) — `mechanism/add_earnings_calendar_table.sql`, applied to the live DB. **Design changed from the
  original spec**: one row per (symbol, report_date) — mirroring exactly what yfinance's `get_earnings_dates()`
  returns (past actuals + upcoming estimates) — instead of one denormalized summary row per symbol. This keeps
  history (useful later for post-earnings-drift features) and means ingestion never has to decide "which one is
  next," only query time does. New updater `mechanism/data_updaters/earnings_calendar_updater.py`
  (`EarningsCalendarUpdater`) — deliberately duplicates `earnings_service.py`'s small yfinance-fetch function
  rather than importing it, since `backend/` and `mechanism/` are headed toward separate deployable services (M6).
  **Verified live**: `--test AAPL MSFT INVALIDXYZ` → AAPL/MSFT each upserted 25 rows correctly (AAPL next
  report 2026-10-29 est. $1.98, MSFT 2026-10-28 est. $4.72), the invalid symbol failed gracefully without
  aborting the batch.
- [x] M2.2 `quarterly_fundamentals_updater.get_symbols_to_update()`: now earnings-date-driven where
  `earnings_calendar` has coverage for a symbol (re-check only once a known report date is on/after the last
  check), falling back to the old `recheck_after_days=25` timer only for symbols with no calendar coverage yet —
  so a coverage gap (new listing, a failed calendar fetch) never silently stops refreshing. **Verified live**
  against the real DB: query runs cleanly, returns due symbols correctly (AAPL/MSFT — just refreshed, future
  known date — correctly excluded from the due list immediately after M2.1's test run).
- [x] M2.3 Wired into `automation_pipeline.sh` as its own **step 7**, immediately before quarterly fundamentals
  (now step 8, which depends on it) — pipeline is now 12 steps total, see M1. `bash -n` syntax-clean.
- [x] M2.4 Tests: `mechanism/data_updaters/tests/test_earnings_calendar_updater.py` — 10 tests (pure `_safe_float`
  edge cases + the staleness predicate against the real DB: no-row, future+fresh, past-known-date, future-but-
  stale-fetch, upsert idempotency). Auto-cleanup fixture (`ZZTEST_*` symbols, never real tickers). Full suite
  re-run after this change: **1,635 passed, 1 skipped** (`mechanism/alerts/tests ml_training/tests
  mechanism/data_updaters/tests`) — nothing broken by the `quarterly_fundamentals_updater.py` query change.

## M3 — Pre-market "who reports today" channel post — DONE, verified live, registered on DEV
- [x] M3.1 New post kind `earnings_today` in `channel_content.py` (`Ctx.earnings_today`, `post_earnings_today()`,
  registered in `BUILDERS`/`KINDS`) + a dedicated sender `mechanism/alerts/send_earnings_today.py` (own CLI, not
  folded into `send_channel_posts.py`'s one-extra-post rotation, since it runs on its own 11:00 schedule).
  Queries `earnings_calendar` for `report_date = today`, restricted to the covered/liquid universe (symbols the
  digest classified in its most recent stored session — a `digest_stocks` join), with sector
  (`daily_fundamentals`) and last close (`stock_prices`) as context via `LEFT JOIN LATERAL`. Rows beyond 25 fold
  into an expandable Telegram quote (`<blockquote expandable>`), matching the momentum board's existing pattern.
  **No before/after-market timing** — `earnings_calendar` only stores the report *date* (see M2.1's schema
  note); the post honestly says nothing about BMO/AMC rather than guessing. **Verified live** against real data:
  `--date 2026-10-29` correctly shows "1 company... reports today" for AAPL (Technology, $338.98, EPS est.
  1.98); a day with no known reporters in the (still-thin, M2-just-built) calendar correctly produces no post.
- [x] M3.2 **Trading-day gate**: new `market_calendar.is_trading_day(day, sessions=None)` — a plain "is this
  calendar date an actual US session" check, deliberately distinct from `check_new_session`'s "has a session
  COMPLETED" (this post is about *today*, sent same-day before the close, not about a finished session). Also
  exposed as `market_calendar.py trading-day [--date]` (exit 0/3, matching the `gate` subcommand's convention).
  **Verified live**: `--date 2026-09-20` (a Saturday) correctly skipped with "the market is closed... nobody
  reports, nobody trades"; the CLI returned exit 3 for the Saturday and exit 0 for a real trading day.
- [x] M3.3 Schedule: **11:00 Israel time** (confirmed by the user, not the original 16:30-17:30 suggestion) — a
  few hours before the US pre-market. `run_earnings_today_post.ps1` (dry-run default, `-Send`, `-To`, `-Force`,
  the same production-lock pre-check pattern as the other wrappers) + Task Scheduler `FirstLight-5-EarningsToday`
  registered, **dev only** (`-To dev`), daily at 11:00, 15-min execution limit. Verified live via the wrapper
  (dry run, real output) and `Get-ScheduledTask` (trigger and action confirmed).
- [x] M3.4 Tests: `mechanism/alerts/tests/test_channel_content.py` (missing-ingredient → None, singular/plural
  wording, the 25-row expandable-quote split, added to the `ALL`/HTML-validity/banned-words parametrized
  coverage) + `mechanism/alerts/tests/test_market_calendar.py` (`is_trading_day` for a real session, a weekend,
  a holiday, and the default-today path). **Found and fixed by the existing test suite, not by inspection**: the
  pinned Start-here post's `test_every_kind_of_channel_post_has_a_note_in_the_pinned_post` correctly failed —
  the new kind had no disclaimer note, which would have shipped with zero caveat coverage anywhere. Added a
  `("earnings_today", "Who reports today", ...)` entry to `channel_posts.SERVICE_NOTES`/`NOTE_FOR_KIND`; the
  pinned post was already within 89 chars of Telegram's parsed-length budget, so also trimmed a redundant clause
  from the existing "gaps" note to make room — final pinned-post length 3,989 parsed chars (budget ≤ 4,000).
  Full suite after all of M2+M3: **1,645 passed, 1 skipped**; `mutation_checks.py --check` — all 77 patterns
  still present. Not yet done: a dry-run review in the owner's private chat before this reaches the dev channel
  for real (the same review step every other channel-content addition has gone through) — the first scheduled
  fire is today, 2026-09-22 11:00, to the dev channel directly; recommend the owner watch that first live send.

## M4 — Fundamentals as real ML features (valuation, cheap-vs-expensive)
- [x] M4.1 **Groundwork done, unit-tested, NOT yet wired in.** New module
  `ml_training/features/fundamentals_features.py` (`fundamentals_v1`, sibling to `price_features.py`'s
  `price_v1`): `sector_relative_percentile()` (percentile within the stock's own GICS sector on the same date,
  computed over the full cross-section — never an absolute P/E threshold; a group under `MIN_SECTOR_GROUP=5`
  members is never ranked; non-positive P/E/P/B is excluded as not comparable), `earnings_turnaround_flag()`
  (same definition `deep_value_service.py` already uses: latest quarter profitable, the one before a loss),
  `join_asof_fundamentals()` (a `pandas.merge_asof` point-in-time join — never a future fundamentals row).
  Deliberately does **not** do the quarter-pairing ("which two quarters count as latest/previous as of a date")
  — left as a query-shaped problem for the DB-integration step, M4.3. 18 unit tests
  (`ml_training/tests/test_fundamentals_features.py`): cross-sector isolation, small-group and non-positive-value
  exclusion, per-date independence, the turnaround truth table (incl. the `net_income == 0` boundary), no-future-
  leakage and no-cross-symbol-leakage in the as-of join, missing-column tolerance. **All pass.**
- [x] M4.2 Deliberately do **not** hand-code "cheap = buy near lows, expensive = buy near highs" as a rule — the
  module only computes descriptive features (enforced by design: no threshold/rule logic anywhere in it). Once
  wired in (M4.3), add alongside the existing `pct_from_52w_high`/`pct_from_52w_low` features already in
  `price_features.FEATURE_NAMES` and let the tree model find the interaction; confirm post-hoc via
  feature-importance / partial-dependence whether the model learned the two-regime split, as a sanity check, not
  as the feature design.
- [ ] M4.3 **Not started — waits on M1's first scheduled run (2026-09-23 02:00) proving the new 12-step nightly
  pipeline stable.** Wire `fundamentals_features.py` into `build_dataset.py`: load `daily_fundamentals` history,
  compute `sector_relative_percentile()` over the full cross-section, solve the quarter-pairing query against
  `quarterly_fundamentals`, `join_asof_fundamentals()` onto each breakout row, add the 4 new columns to
  `ml_breakout_dataset_v2` (or a joined view), bump `FEATURE_SET_VERSION`, rebuild, retrain both targets, compare
  against the current baseline honest-eval reports (`ml_training/models/candidates/report_*.json`).
- [x] M4.4 Decision recorded above (2026-09-22): accept missing data, never fabricate a backfill. Implemented in
  the module itself — a symbol/date with no coverage gets `NaN` throughout, by construction, not by a follow-up
  fix. Coverage caveat carries forward to M4.3's eventual report: `daily_fundamentals` only has full-universe
  daily coverage since ~2026-09-19, so expect thin/no signal from these features until more history accumulates
  — that is the honest result, not a bug to chase.

## M5 — Multi-timeframe alignment as a real momentum signal
- [ ] M5.1 **Root-cause fix first**: `weekly_technical_indicators`/`monthly_technical_indicators` carry the same
  restated-price corruption as daily `technical_indicators` did (CLAUDE.md §5) — going-forward rows are computed
  fresh each run, but rows from before the Tiingo migration were never recomputed. One-time backfill needed before
  the dashboard's *existing* weekly/monthly alignment score can be trusted for any historical date. Confirm this
  diagnosis by spot-checking a known pre-migration date against current `stock_prices` (same method already used
  to find the daily-table corruption) before writing the backfill.
- [ ] M5.2 **Build the new signal on `stock_prices` directly, not the stored weekly/monthly tables** — resample
  daily bars to weekly/monthly inside a new module (same causal, single-source-of-truth approach as
  `price_features.py`), and derive: monthly candle pattern (engulfing / strong close), an ATR-anomaly flag
  (today's range vs. prior ATR — the exact ratio the digest already computes daily), and weekly trend strength.
  This sidesteps M5.1's historical corruption entirely for anything new; M5.1 remains necessary separately for the
  dashboard's own existing display to be trustworthy.
- [ ] M5.3 Add the combined "multi-timeframe alignment" feature(s) to the **same** model first (reuses the
  existing eval/gate harness with the least new infrastructure); only spin out a dedicated second model /
  standalone alignment score later if the combined-model experiment shows it's worth a dedicated signal (per the
  decision above — this is a sequencing choice, not a rejection of the two-model idea).
- [ ] M5.4 Same digest/channel exposure as any other descriptive tag: a "strong continuation setup" flag on
  qualifying rows, worded as a fact ("aligned on daily/weekly/monthly, ATR expansion today"), never as advice.
- [ ] M5.5 Tests: causal-correctness test analogous to `tests/test_price_features.py` (features computed on full
  history must equal features computed on history truncated at that date — no lookahead via the weekly/monthly
  resampling).

## M7 — Dashboard screener correctness fixes (2026-09-22, same-day root-cause fixes, verified live)
Triggered by comparing the dashboard's breakout count to the digest's for the same session and refusing to accept
"cause unconfirmed" — traced to source, fixed, and verified against real production data (not simulated), not just
described. `mechanism/screeners/multi_timeframe_screener.py`.

- [x] M7.1 **Liquidity + data-integrity guard parity.** Root cause: this screener detected breakouts straight from
  `technical_indicators`/`stock_prices` SQL and never applied either guard the digest already has (a $1M/day,
  prior-20-session dollar-volume floor; no price discontinuity in the last 253 bars; a minimum bar-count floor).
  New `_apply_universe_guards()` reuses the exact same functions the digest and ML training already trust
  (`ml_training/features/price_features.py`'s `dollar_vol_20`/`find_discontinuities`, `digest_builder.MIN_BARS`) —
  not a second, drift-prone reimplementation. **Verified on real data (session 2026-09-21):** re-ran the screener
  end to end; the guard dropped 107/1748 symbols; **the dashboard's bullish-breakout count went from 112 → 104,
  exactly matching the digest's 104**, and near-bullish 291 exactly matches the digest's near_breakout 291 — zero
  symbols differ in either direction, confirmed by a direct set diff. The 8 previously-extra symbols (AFGE, DMAC,
  EVI, KMPB, RXT, SBC, SECZ, USDE) are gone; each was independently confirmed to fail a real guard (liquidity,
  discontinuity, or too little history) before the fix landed, including DMAC, which an earlier manual approximate
  check had missed.
- [x] M7.2 **Null combined_score / unranked "best stocks to watch" bug.** Root cause: `_create_enhanced_results`
  built the dashboard's main `signals.bullish_breakout`/`bearish_breakout`/`near_bullish`/`near_bearish` lists
  straight from `all_signals`, which never had `combined_score` set on it — only a SEPARATE `ml_enhanced_signals`
  copy did, and only for Grade A/B/C signals (`high_quality_signals`). Every signal in the main lists therefore
  carried `combined_score: None`, so "sort by combined score" was a no-op sorting on an all-None key, silently
  leaving the list in the SQL query's original (alphabetical) order — the opposite of "best stocks to watch",
  while `ai_insights.top_ai_picks` (which read from the correctly-scored `ml_enhanced_signals`) looked fine.
  New `_merge_ml_scores()` merges the enhanced copies back by `(symbol, signal_type)` and gives every OTHER
  signal (Grade D/F, or any signal ML enhancement wasn't attempted for) the identical no-ML-contribution formula
  (`alignment_score * 0.6`) rather than leaving it null. A second fix, found while verifying the first: even with
  correct scores, the main lists still weren't globally re-sorted (only `enhance_signals_with_ml`'s own internal,
  separate list was) — added an explicit sort by `combined_score` descending after the merge.
  **Verified on real data**: zero `None` combined_scores across all four signal categories (104 + 231 + 291 +
  1015 = 1,641 signals); every list is confirmed non-increasing by score (`scores == sorted(scores,
  reverse=True)`); today's real score tiers are `{30.0, 36.0, 42.0, 48.0, 60.0}` (alignment-grade-driven, since
  no ML model is currently promoted — `ml_part` is honestly 0 for everyone, not invented).
- [ ] M7.3 Not done: the "best stocks to watch" ranking today is alignment-grade-only (no ML contribution, since
  no model is promoted) — ties within a grade tier break alphabetically, which is honest but coarse (only 5
  distinct scores across 1,641 signals). M4/M5 (fundamentals + multi-timeframe alignment as real ML features)
  are the real fix for finer-grained ranking, not a further patch here.
- [ ] M7.4 Not done: `multi_timeframe_screener.py` still runs its own independent SQL-based breakout DETECTION
  (joined against `technical_indicators`) rather than `price_features.py`'s `detect_breakouts()` — M7.1's guard
  filters out bad results after the fact, which is verified correct today, but the two-implementation split
  itself remains and could drift again on a future change to either side. A full consolidation onto
  `price_features.py` (discussed and deferred as higher-risk than the guard-based fix) is still the more durable
  long-term fix; not attempted here.

## M8 — Data completeness audit + cleanup (2026-09-22, same session as M7)
- [x] M8.1 **Database completeness audit, verified live.** Universe: 3,114 symbols with price history, 38 marked
  `inactive_symbols`, 3,076 fresh (priced in the last 5 days). Fundamentals: 3,074/3,076 fresh symbols covered as
  of today (3,050 with sector, 3,051 with market_cap, 3,074 with a quality score) — the 31.6%-coverage gap
  CLAUDE.md documented from before the fundamentals step was wired into the daily pipeline (2026-09-19) is gone;
  only 2 symbols (BRK/A, BRK/B) have never had a fundamentals row, both explained by M8.2. `technical_indicators`:
  3,049/3,076 (99.1%) of today's priced symbols have a matching row — a small (27-symbol) residual gap, not
  investigated further this session. `quarterly_fundamentals`: 3,000 symbols covered; the periodic ~25-day
  recheck cycle (now event-driven per M2) means "most recently checked" trails by design, not a bug.
- [x] M8.2 **Found and fixed a real duplicate-ticker bug.** `BRK/B` (Yahoo slash notation) and `BRK.B` (dot
  notation) are the exact same security under two spellings — confirmed by comparing every field of their most
  recent price rows: identical close, identical volume, every session checked. This was silently double-counting
  Berkshire Hathaway B shares in every breakout/sector/universe count on both the dashboard and (before M7's
  guard fix) inconsistently on the digest, and burning a wasted daily fundamentals API call ending in a 404 (seen
  directly in the 2026-09-22 pipeline log). **Fixed**: `BRK/B` added to `inactive_symbols` (excludes it from
  price, daily-fundamentals, quarterly-fundamentals, and the new earnings-calendar updater alike). `BRK/A` was
  checked and is **not** a duplicate — it's Berkshire Class A, a real, distinct, liquid security (~$750K/share,
  ~$213M/day dollar volume despite only ~283 shares/day) — left active. Its own daily fundamentals 404 (ticker
  format Yahoo/Tiingo can't resolve) is a known, low-cost residual inefficiency (one wasted call/day for one
  symbol), not fixed further this session — a finer-grained "skip fundamentals only, keep price" list would be
  the proper fix if this is worth revisiting.
- [x] M8.3 **Removed confirmed-dead files and directories**, each checked for zero references anywhere in the
  codebase (`grep` across `*.py`/`*.sh`/`*.ps1`) before removal: `mechanism/data_updaters/backups/`,
  `mechanism/screeners/backups/`, `mechanism/ml_enhancement/ml_signal_enhancer_backup.py`,
  `mechanism/screeners/ml_donchian_screener_backup.py`, `mechanism/screeners/multi_timeframe_screener_backup.py`,
  `ml_training/data_preparation/feature_builder_backup.py`, `ml_training/data_preparation/momentum_labeler_backup.py`,
  `ml_training/scripts/backup/` (10 files), `ml_training/models/backup/` (~70MB on disk, mostly untracked
  `.joblib`/text dumps) — all via `git rm` (were git-tracked). Also removed the stale, already-gitignored,
  untracked duplicate output directories `mechanism/frontend_data/` (last written 2025-08-26),
  `mechanism/breakout_results/` (2025-08-26), `mechanism/reports/` (2025-07-11), `mechanism/data/` (empty).
  **Left alone, deliberately**: `mechanism/screeners/donchian_screener.py` (non-backup) — still referenced by
  `master_automation_runner.py`'s component map, confirmed by grep, so not dead despite being legacy;
  `mechanism/logs/` — NOT stale (a file from 4 days ago, 2026-09-18), left in place since something still
  occasionally writes there via a relative path when a script is run with `mechanism/` as the working directory
  instead of the repo root (a real but low-stakes quirk, not fixed this session); the root-level `backups/`
  directory (~21,000 files incl. an old `venv/`/`node_modules/`) — already gitignored and therefore not a repo
  hygiene problem, but a large judgment call on disk cleanup that needs the user's explicit go-ahead before
  deleting, not assumed. **Verified nothing broke**: full test suite re-run after every removal, 1,660 passed, 1
  skipped, unchanged from before the cleanup.

## M6 — Hosting
- [x] Acknowledged: stays on the user's machine until the planned mechanism/backend/frontend/telegram/DB
  microservices split. No work item; M1's reschedule is the interim reliability fix. Revisit only when the split
  is actually being planned.

---

## Decisions confirmed 2026-09-22 (second round)
- **M3.3**: **11:00 Israel time** — a few hours before the US pre-market, not right before it. Confirmed by the
  user over the original 16:30-17:30 suggestion.
- **M4.4**: **accept missing data, do not backfill.** Reasoning: a trustworthy point-in-time backfill of P/E/P/B
  back to 2018 isn't actually available — yfinance's fundamentals-statement depth is shallow (a handful of
  trailing quarters, not years), and Tiingo's deeper statements are Dow-30-only on the current plan (§6a). Any
  attempt to reconstruct it further back would mean approximating historical EPS from incomplete data and
  presenting it as real, which is exactly what the project's "no fabricated defaults" rule (and the honest-eval
  culture that caught the original stale-basis corruption) exists to prevent — the backfill would itself become
  a second, harder-to-detect version of the same mistake. Middle ground kept from the original M4.1 spec: compute
  the valuation feature from *real* stored data wherever it already exists (any date `daily_fundamentals` actually
  covers, not just post-2026-09-19), and leave it `NaN` everywhere else — not a binary "only recent data" vs.
  "backfill everything" choice, just: never invent what isn't there. Practical consequence to track once M4 is
  built: the feature's usable sample size grows slowly (full-universe coverage only since ~2026-09-19), so don't
  expect it to show real signal in the honest-eval report for a while — that's the honest result, not a bug.

## Open decisions still needed from the user
- **M5.1**: confirm before building — is the backfill scope "recompute weekly/monthly indicators for the full
  history" (matches the daily-table fix's scope) or a narrower window?
