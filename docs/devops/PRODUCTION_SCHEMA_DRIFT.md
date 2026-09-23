# Production Schema Drift Report

> Phase 4A.5 (investigation) — read-only findings only. Every fact below came from
> `information_schema`/`pg_catalog` metadata queries and `COUNT(*)` row counts against the live
> production database. **No actual row content (symbols, prices, user data) was read, printed, or
> copied anywhere in this report or in the session that produced it.** No production write occurred.
>
> **Phase 4A.6 update — the drift for the 4 active tables is now closed in the repository.**
> `mechanism/add_ml_prediction_tracking_tables.sql` (created and approved in Phase 4A.6) captures
> `inactive_symbols`, `ml_predictions`, `ml_prediction_outcomes`, and `ml_performance_metrics` as a
> tracked migration, verified against both a fresh empty database and a simulated pre-existing
> database (full results in `PRODUCTION_MIGRATION_RUNBOOK.md`). **This closes the *repository* gap
> only — it does not touch production.** The real production database has not been migrated,
> modified, or connected to by this work; see that file's explicit distinction between "historical
> drift now captured in the repository" and "actual production migration, still not executed."
> Important scope note carried over from the approval discussion: the new migration file's
> `CREATE TABLE IF NOT EXISTS` statements do **not** validate or reconcile structure against an
> already-existing table — on an existing relation they are a pure skip, nothing is compared. The
> file is safe to run against production specifically because its definitions were derived from
> *this document's* independent, read-only inspection, not because Postgres checks anything at
> apply time.

## Executive summary

Production has **6 tables that no file tracked in this repository's git history ever created**
(confirmed by searching all 8 commits in the repo's entire history — zero matches for `CREATE
TABLE` on any of these 6 names). Of the 6:

- **4 are genuinely active, load-bearing tables** used by currently-running application code:
  `inactive_symbols`, `ml_predictions`, `ml_prediction_outcomes`, `ml_performance_metrics`.
- **2 are confirmed dead** — referenced only by scripts `CLAUDE.md` already documents as retired
  (`ml_training/data_preparation/momentum_labeler.py`, `feature_builder.py`, `data_cleaner.py`):
  `momentum_scores`, `enhanced_ml_training_data`.

**The single most important fact in this report:** `mechanism/create_trading_schema.sql` line 15
reads `DROP TABLE IF EXISTS ml_predictions CASCADE;`. `ml_predictions` is one of the 4 active
tables, currently holding 1,537 real rows, and — via its foreign key — `CASCADE` would also drop
`ml_prediction_outcomes`. **If the fresh-bootstrap script were ever run against production, it
would destroy real ML prediction history**, not merely fail or be redundant. This is no longer a
theoretical caution (as stated in Phase 4A) — it is a proven, specific, named risk.

---

## Per-table detail

### 1. `inactive_symbols`

| | |
|---|---|
| Referenced by application code? | **Yes — actively, by the live pipeline and backend** |
| Referencing files | `backend/services/system_health_service.py` (2 references: an exclusion filter and a count for the health report), `mechanism/data_updaters/daily_data_updater.py`, `fundamentals_updater.py`, `quarterly_fundamentals_updater.py`, `earnings_calendar_updater.py` (all four: `WHERE symbol NOT IN (SELECT symbol FROM inactive_symbols)` — used every day to skip confirmed-delisted symbols) |
| Columns | `symbol` varchar(10) NOT NULL, `reason` varchar(200), `marked_inactive_date` date DEFAULT CURRENT_DATE |
| Primary key | `symbol` |
| Foreign keys | None |
| Indexes | `inactive_symbols_pkey` (unique btree on `symbol`) |
| Sequences | None (natural key, no surrogate ID) |
| Ownership | `trading_user` |
| Permissions | `trading_user`: DELETE, INSERT, REFERENCES, SELECT, TRIGGER, TRUNCATE, UPDATE (full — same grant set as every tracked table) |
| Row count | **39** |
| Contains production data? | Yes — real symbol exclusion list (delisted/dead tickers), actively maintained |
| Equivalent under another name in tracked schema? | **No.** No tracked table serves this "known-dead symbols to skip" purpose. |

### 2. `ml_predictions`

| | |
|---|---|
| Referenced by application code? | **Yes — actively written and read** |
| Referencing files | **Written by** `mechanism/screeners/multi_timeframe_screener.py` (the live, primary screener — CLAUDE.md's "main engine") via `ml_training/evaluation/performance_tracker.py`'s `MLPerformanceTracker.record_predictions()`, called at the end of every screener run. **Read by** `backend/services/ml_stats_service.py` (the `/api/ml-stats` endpoint) and `system_health_service.py` |
| Columns | `id` (PK, serial), `symbol` varchar(10), `prediction_date` date, `breakout_type` varchar(20), `entry_price` numeric(10,2), `ml_probability` numeric(5,3), `ml_confidence` varchar(20), `ml_recommendation` varchar(20), `ml_risk_score` integer, `model_version` varchar(50), `created_at` timestamp DEFAULT CURRENT_TIMESTAMP |
| Primary key | `id` |
| Foreign keys | None (is itself referenced by `ml_prediction_outcomes`) |
| Unique constraint | `(symbol, prediction_date)` |
| Indexes | PK + the unique constraint's index |
| Sequences | `ml_predictions_id_seq` (standard serial) |
| Ownership | `trading_user` |
| Permissions | Full grant set, same as above |
| Row count | **1,537** |
| Contains production data? | **Yes — real ML prediction history, actively growing** |
| Equivalent under another name in tracked schema? | No direct equivalent. `ml_models` (tracked) stores model *registry* metadata (versions, training runs) — a different purpose from per-symbol prediction records. |

### 3. `ml_prediction_outcomes`

| | |
|---|---|
| Referenced by application code? | **Yes** |
| Referencing files | **Written by** `ml_training/evaluation/performance_tracker.py` (`record_outcomes`-style logic, joins back to `ml_predictions`). **Read by** `backend/services/ml_stats_service.py`, `system_health_service.py` |
| Columns | `id` (PK, serial), `prediction_id` integer, `symbol` varchar(10), `prediction_date` date, `evaluation_date` date, `days_elapsed` integer, `actual_return_pct` numeric(8,2), `max_gain_pct` numeric(8,2), `max_loss_pct` numeric(8,2), `momentum_achieved` boolean, `momentum_score` integer, `created_at` timestamp DEFAULT CURRENT_TIMESTAMP |
| Primary key | `id` |
| Foreign keys | **`prediction_id` → `ml_predictions(id)`** — this is the FK that makes `ml_predictions`' `DROP ... CASCADE` also destroy this table |
| Indexes | PK only |
| Sequences | `ml_prediction_outcomes_id_seq` |
| Ownership | `trading_user` |
| Permissions | Full grant set |
| Row count | **0** |
| Contains production data? | No rows currently, but the table is structurally wired into live code (read by `ml_stats_service.py`) — the evaluation half of the ML tracking pipeline appears not to have completed a run yet, not that the table is unused |
| Equivalent under another name in tracked schema? | No |

### 4. `ml_performance_metrics`

| | |
|---|---|
| Referenced by application code? | **Yes, narrowly** |
| Referencing files | `ml_training/evaluation/performance_tracker.py` only (both an `INSERT` and a later `SELECT * FROM ml_performance_metrics`) — **not** currently read by any backend service (confirmed: `backend/services/ml_stats_service.py` references `ml_predictions` and `ml_prediction_outcomes` but not this table) |
| Columns | `id` (PK, serial), `metric_date` date, `model_version` varchar(50), `total_predictions` integer, `correct_predictions` integer, `accuracy` numeric(5,3), `precision_high_prob` numeric(5,3), `recall_high_prob` numeric(5,3), `avg_return_predicted_high` numeric(8,2), `avg_return_predicted_low` numeric(8,2), `sharpe_ratio` numeric(6,3), `max_drawdown` numeric(8,2), `created_at` timestamp DEFAULT CURRENT_TIMESTAMP |
| Primary key | `id` |
| Foreign keys | None |
| Indexes | PK only |
| Sequences | `ml_performance_metrics_id_seq` |
| Ownership | `trading_user` |
| Permissions | Full grant set |
| Row count | **0** |
| Contains production data? | No rows yet — same "evaluation half not yet completed" situation as `ml_prediction_outcomes` |
| Equivalent under another name in tracked schema? | No |

### 5. `momentum_scores` — **confirmed dead/legacy**

| | |
|---|---|
| Referenced by application code? | Only by scripts `CLAUDE.md` already explicitly documents as **retired**: `ml_training/data_preparation/momentum_labeler.py` (writes), `feature_builder.py` (reads), `data_cleaner.py` (a `COUNT(*)` check). None of these three run as part of the active pipeline. |
| Columns | `id` (PK, serial), `symbol` varchar(10), `date` date, `breakout_type` varchar(10), `entry_price` numeric(10,2), `original_success` boolean, `momentum_score` integer, `momentum_category` varchar(20), `total_return_pct` numeric(8,2), `analysis_days` integer, `created_at` timestamp DEFAULT CURRENT_TIMESTAMP |
| Primary key | `id` |
| Foreign keys | None |
| Unique constraint | `(symbol, date)` |
| Sequences | `momentum_scores_id_seq` |
| Ownership | `trading_user` |
| Permissions | Full grant set (same as every other table — permissions were never revoked even though the table fell out of use) |
| Row count | **66,191** |
| Contains production data? | Yes — real historical data, but per `CLAUDE.md`'s own documented finding, built on the pre-Tiingo-restatement "stale basis" — explicitly flagged as **do not train from** |
| Equivalent under another name in tracked schema? | The tracked *view* `ml_training_data` is the semantic successor — same broad purpose (a training dataset), but CLAUDE.md documents that this view is **also** stale-basis and superseded by `ml_breakout_dataset_v2` (a tracked table, rebuilt nightly from `stock_prices` only). Naming similarity between `momentum_scores`/`enhanced_ml_training_data` and `ml_training_data`/`ml_breakout_dataset_v2` is a real confusion risk worth flagging even though none of these four are being proposed for consolidation here. |

### 6. `enhanced_ml_training_data` — **confirmed dead/legacy**

| | |
|---|---|
| Referenced by application code? | Only by `ml_training/data_preparation/feature_builder.py` (write) — one of the three CLAUDE.md-documented retired scripts |
| Columns | `id` (PK, serial), `symbol` varchar(10), `date` date, `breakout_type` varchar(10), `features` jsonb, `target_momentum_score` integer, `target_momentum_category` varchar(20), `target_binary` integer, `target_return_pct` numeric(8,2), `created_at` timestamp DEFAULT CURRENT_TIMESTAMP |
| Primary key | `id` |
| Foreign keys | None |
| Unique constraint | `(symbol, date)` |
| Sequences | `enhanced_ml_training_data_id_seq` |
| Ownership | `trading_user` |
| Permissions | Full grant set |
| Row count | **65,846** |
| Contains production data? | Yes, real historical data — same stale-basis caveat as `momentum_scores` |
| Equivalent under another name in tracked schema? | Same successor relationship as `momentum_scores` above — `ml_breakout_dataset_v2` is the tracked, currently-maintained replacement for this table's purpose |

---

## Canonical schema determination: A, B, C, or D?

Evidence gathered: `git log --all --oneline` shows **8 total commits in this repository's entire
history**; `git log --all -p -- '*.sql'` searched every version of every `.sql` file ever committed
for a `CREATE TABLE` matching any of the 6 drifted table names — **zero matches**. These tables were
never created by any file this repository has ever tracked, at any point.

**Determination: D — a combination, and the combination is different per table:**

- **For the 4 active tables** (`inactive_symbols`, `ml_predictions`, `ml_prediction_outcomes`,
  `ml_performance_metrics`): this is **A + B together** — production is ahead of the repository
  (option A: the tables exist and are used, but no tracked file describes them), which is the same
  thing as saying the repository is missing the historical migration that created them (option B).
  These aren't two competing explanations; they're two descriptions of the same gap. The tables
  were almost certainly created by a one-off manual `CREATE TABLE` (run directly via `psql` or an
  ad hoc script, consistent with `create_trading_schema.sql`'s own header comment referencing a
  `psql -U trading_user -d trading_production -h localhost` workflow and this project's documented
  SQLite-to-PostgreSQL migration era) that was never saved as a committed `.sql` file.
- **For the 2 dead tables** (`momentum_scores`, `enhanced_ml_training_data`): this is **option C**
  — obsolete tables, superseded by `ml_breakout_dataset_v2` per `CLAUDE.md`'s own documented
  2026-09-20 ML rewrite, left in place rather than dropped (a reasonable, conservative choice at
  the time — dropping a table is much harder to undo than leaving an unused one).

## Recommendation: how the repository should represent the current production schema going forward

**Item 1 below is now DONE (Phase 4A.6), items 2-3 remain recommendations.**

1. ~~Capture the 4 active tables' real DDL as a new, properly-ordered tracked migration file~~ —
   **done**: `mechanism/add_ml_prediction_tracking_tables.sql`, created and approved in Phase 4A.6,
   its definitions derived from this document's own captured metadata (not a fresh `pg_dump` —
   the constraints/defaults/sequences documented in the per-table sections above were transcribed
   directly into the migration's inline `CREATE TABLE`/`CREATE SEQUENCE` statements). Integrated as
   file 17 in the fresh-bootstrap sequence (`docker-compose.yml`'s init mounts and the CI
   `db-bootstrap-integration` job). Verified two ways, both documented in full in
   `PRODUCTION_MIGRATION_RUNBOOK.md`: (a) a completely fresh, empty database now bootstraps to
   exactly 40 tables + 3 views with the 4 captured tables structurally matching production's real
   constraints/sequences/grants (not just existing — every PK, the one FK, the unique constraint,
   sequence ownership, and `trading_user` grants were individually re-verified); (b) applying the
   same migration file against a database that already has these 4 tables (simulating production)
   is a confirmed true no-op — bit-for-bit identical data checksums before/after, sequence values
   not reset. **Still not run against actual production** — that remains a Phase 4B decision.
2. **Do not add `momentum_scores` or `enhanced_ml_training_data` to any new tracked migration.**
   They're dead; capturing dead tables into version control would just be documenting cruft as if
   it were current design. If they're ever formally decommissioned (a separate decision, not this
   phase's concern), that would be a `DROP TABLE` migration at that time — not before.
3. **Going forward, adopt the practice (independent of whether a tool like Alembic is introduced —
   see `PRODUCTION_MIGRATION_RUNBOOK.md` §3) that no `CREATE TABLE`/`ALTER TABLE` is ever run
   directly against production without also being committed as a numbered `.sql` file in the same
   change.** This is a process discipline, not a tooling purchase — it's what would have prevented
   this exact drift from happening in the first place.

## Cross-cutting observations

- **Every one of the 6 tables is owned by `trading_user` with the identical, full grant set** every
  tracked table has (DELETE/INSERT/REFERENCES/SELECT/TRIGGER/TRUNCATE/UPDATE). There is no
  permissions anomaly — whatever process created these tables also granted `trading_user` access
  correctly and consistently, matching the tracked tables' pattern exactly. This makes a future
  migration-file capture (§ recommendation below) straightforward — no special-cased permissions
  logic would be needed.
- **All 6 use the same ID/sequence pattern as tracked tables** (`SERIAL`-style integer PK with a
  `_id_seq` sequence, `created_at timestamp DEFAULT CURRENT_TIMESTAMP`) — stylistically consistent
  with the rest of the schema, suggesting they were created by the same person/process using the
  same conventions, just never captured in a committed file.
- **One real foreign-key relationship exists among the drifted tables**
  (`ml_prediction_outcomes.prediction_id → ml_predictions.id`) — any future migration file
  capturing these tables must create `ml_predictions` before `ml_prediction_outcomes`, matching
  the dependency order already implicit in the live schema.
- **Total drifted-table data volume**: 65,846 + 66,191 + 39 + 1,537 + 0 + 0 = **133,613 rows**,
  none of it reproducible by re-running any tracked script (the two populated-and-active tables,
  `inactive_symbols` and `ml_predictions`, hold operationally curated state — a manually-maintained
  exclusion list and a running prediction log — not data that could be regenerated from
  `stock_prices` the way `ml_breakout_dataset_v2` can be).
