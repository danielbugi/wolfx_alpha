# FIRST LIGHT LAB COMPLETION PLAN

Status: PROPOSAL for owner review. Written 2026-10-04 on branch `lab/first-light-algo` (worktree `donchian_screener_0.1-wt-lab`, off `main@d4eab95`).
Scope of this document: audit + plan only. No implementation slice has started. No production system was touched; the only production reads were the S11 read-only observers that were already running.

Naming: the platform is **First Light Algo**. Donchian Breakout v1 is one strategy (`strategy_id` in `strategies`) inside it. New modules use generic names. Existing production names (`donchian-screener-*` containers, `donchian-*.timer`, `donchian_app`/`donchian_owner` roles) are untouched.

Classification vocabulary: LIVE (running in production) / OFF (built, applied or deployable, switched off) / PARTIAL / PLANNED (design doc only) / MISSING.

---

## 1. What exists

| Component | Class | Evidence (files / tables / jobs) |
|---|---|---|
| Strategy identity + signal ledger + evaluator (Release A) | LIVE | `mechanism/add_strategy_identity_release_a.sql`, `add_signal_ledger_tables.sql`, `add_signal_ledger_eval_flags.sql`; `mechanism/screeners/signal_ledger_writer.py`; `donchian-signal-ledger-eval.timer` 00:30Z; `signal_ledger` (1022 rows at S11 baseline), `strategies` |
| Strategy analytics (win, R, held, stale, data health, MIN_SAMPLE_SIZE=5) | LIVE | `mechanism/strategy_analytics/{analytics,definitions,research}.py`; `backend/routers/strategy_intelligence.py`; `/api/strategies/*` |
| Strategies UI (overview / signals / data-health / research tabs) | LIVE | `frontend/src/app/strategies/**`, `frontend/src/components/strategies/*`, `frontend/src/services/strategyApi.ts`, `lib/strategyFormat.ts`, `lib/strategyExtensions.ts` |
| Market index + sector daily data | LIVE | `mechanism/data_updaters/{market_index_updater,sector_performance_snapshot}.py`; migration `add_market_data_tables.sql` |
| `earnings_calendar` (dates, EPS est/actual/surprise) | LIVE, not PIT-safe | `add_earnings_calendar_table.sql` (13), `earnings_calendar_updater.py` (yfinance, upsert-overwrites, no first-seen record); `backend/services/earnings_service.py`; `donchian-earnings-today.timer` |
| System health endpoints | LIVE | `backend/routers/system_health.py`, `backend/services/system_health_service.py` |
| Post-market Telegram package (+ idempotent delivery) | LIVE | `mechanism/alerts/publish_post_market.py`, `post_delivery.py`, `telegram_post_delivery` |
| Release B research capture (observer, snapshot builder, registry, immutability, least-privilege roles) | OFF | `mechanism/add_research_observation_tables.sql` (22, applied), `mechanism/research/{observer,registry,repository,snapshot_builder,schema_fingerprint}.py`, `validate_release_b.py`. `RESEARCH_CAPTURE_ENABLED` absent; activation row required; capture tables 0 rows |
| ML registry columns | OFF-ish | `add_ml_models_registry_columns.sql` (23, applied) |
| Market Intelligence (risk_regime_v1, rs_v1, provenance, event model, read-only API) | OFF | `mechanism/market_intelligence/*`, migrations 24 (`add_market_snapshot_tables.sql`) and 25 (`add_market_event_tables.sql`) NOT applied; `backend/routers/market_intelligence.py` degrades to `not_provisioned`; Telegram `market_environment` post not enabled |
| ML training (walk-forward, calibration) | PARTIAL | `ml_training/models/momentum_predictor.py` (EMBARGO_DAYS=30), `ml_training/features/{price_features,fundamentals_features}.py` |
| Research API | PARTIAL | candidates and snapshots served; forward outcomes are a stub (`strategy_analytics/research.py: FORWARD_OUTCOMES = not_available(...)`) |
| Event model | PARTIAL | `market_event` + `market_event_revision` schema, `events.py` validation, `ml_view` rules; no collector, no vendor, no backfill |
| News | PARTIAL | `mechanism/alerts/{news_service,channel_news}.py` (Alpaca, Telegram only, not stored as PIT events) |

## 2. What is partial

1. **ML training**: walk-forward and calibration exist but are wired to the legacy momentum labels, not `fwd_v1`, not regimes. The 30-day embargo is invalid for a 60-session horizon (needs embargo >= horizon plus purge). Honest AUC ~0.56 (see ML audit 2026-09-20): no proven edge, so nothing here goes to production.
2. **Research API / strategy intelligence**: serves captured candidates/snapshots; forward outcomes, conditional breakdowns and regime fields are `not_available`.
3. **Earnings**: dates and EPS only. No revenue estimate/actual, timing (BMO/AMC), guidance, reaction, abnormal volume. Overwritten in place, so history is not reconstructable.
4. **Event model**: correct append-only/PIT semantics, zero ingestion.
5. **Market Intelligence**: methodology exists, tables unapplied, no scheduled writer, sector history is reconstructed (never PIT-safe because `daily_fundamentals.sector` is overwritten).
6. **System status**: generic health exists; none of the lab fields (capture state, label maturity, feature coverage, active versions).

## 3. What is missing

- `forward_return_label` table + `mechanism/research/labels` package (`fwd_v1`). The older doc reserved migrations 23/24/25 for labels / `candidate_plan_outcome` / ledger FKs; that numbering drifted (22, 23 applied for other things; 24/25 are Market Intelligence). **Next free migration is 26.**
- `candidate_plan_outcome` / `daily_signal_observation` (deferred; not needed for fwd_v1).
- Scheduled writers for regime and sector/RS; observed (not reconstructed) snapshots.
- Earnings revenue/timing/guidance/reaction/abnormal-volume facts + append-only date observation log.
- Catalyst collectors and AI classification layer.
- Generic conditional-performance engine (by direction/regime/vol/sector/RS/grade/earnings/catalyst/calendar/feature bucket).
- Research dataset builder with leakage tests.
- ML evaluation harness (baselines, calibration, stability, regime/long-short, benchmark).
- Frontend: Market, Regimes, Sectors, Earnings, Catalysts, Models areas and a First Light shell.
- Data-quality / system-status extensions.
- **The `model_version` provenance fix** and the historical-normalization runbook.

## 4. Exact dependency graph

```
                         (all code in lab branch; activation gates in [brackets])

P1a model_version NULL fix ─────────────────────────────┐
  (screener + enhancer + ledger writer + tests)         │
                                                        ▼
M22 (applied) ──► P2 fwd_v1 labels (M26) ──► P7 performance engine ──► P10 UI (Performance/Strategies)
        │              │                         ▲                         ▲
        │              └──────────────┐          │                         │
        │                             ▼          │                         │
M24 (unapplied) ─► P3 regime writer (M24 + M27?) ┤                         │
        │            │                           │                         │
        └─► P4 sector+RS PIT-safe (M24 + M27?) ──┤                         │
                                                 ▼                         │
M13 earnings ──► P5 earnings facts + date log (M28) ─► P8 dataset builder ─► P9 ML research ─► P10 Models
M25 (unapplied) ─► P6 catalyst/events (M25 + M29) ───►      ▲
                                                            │
                         P11 status/data-quality ◄── reads every phase's tables (degrades to 'unavailable')
```

Hard edges:
- P2 needs only M22 (`candidate_observation`, `feature_snapshot`) and `stock_prices`/benchmark closes. It does not need M24/M25.
- P3/P4 need M24's tables (apply before activation, not before development; tests run against a scratch DB).
- P8 needs P2 + P3 + P4 + P5 + P6 *contracts* (column sets, versions) but each source degrades to explicit NULL + `*_status='unavailable'`; the builder can be developed against P2 alone, and join in the others as they land.
- P9 needs P8.
- P7 needs P2 (labels) for outcome-based metrics; the existing ledger-based analytics keep working without it.
- P10 pages are independent per area; each depends only on its own API contract.
- P11 is a read-only aggregator, last.

## 5. What can be implemented immediately (no activation dependency)

All of P1–P11 can be built and tested on the branch against a scratch/dev Postgres. Nothing waits on S11 to be *implemented*. Recommended first (highest value, lowest coupling): P1a, P2, P3/P4 contract review + writers, P5 date log, P7, P8 skeleton, P11 skeleton, UI shell with empty/unavailable states.

## 6. What needs S11 completion only for activation

- Merging lab code that touches the screener/enhancer into the production image path and bumping `CURRENT_MECHANISM_SHA` (CD is manual; merge to `main` alone does not deploy, but I propose we hold the merge until S11 decides, to keep the S11 baseline attributable).
- Applying migrations 24, 25, 26+ to `trading_production`.
- Creating the capture activation row / setting `RESEARCH_CAPTURE_ENABLED`.
- Adding any systemd timer/service for label maturity, regime, sector, earnings, catalyst jobs.
- Granting `donchian_app` access to any new table (least-privilege roles are part of each migration's activation runbook).
- Enabling Telegram `market_environment`.
- Any backfill.

## 7. Migrations already prepared

| # | File | State |
|---|---|---|
| 22 | `add_research_observation_tables.sql` | APPLIED, capture OFF |
| 23 | `add_ml_models_registry_columns.sql` | APPLIED |
| 24 | `add_market_snapshot_tables.sql` (market environment, risk regime, RS) | prepared, NOT applied |
| 25 | `add_market_event_tables.sql` (`market_event`, `market_event_revision`) | prepared, NOT applied |

## 8. Migrations still required (proposed numbering, all additive / `IF NOT EXISTS`, appended to the compose init list)

| # | Purpose | Notes |
|---|---|---|
| 26 | `forward_return_label` (fwd_v1) | FK to `candidate_observation`; immutable (same trigger pattern as M22); one row per (observation, horizon, label_version); horizons 1/3/5/10/20/60; raw/direction-adjusted/benchmark-relative return, MFE/MAE, sessions_observed, completeness, label_status, provenance |
| 27 | Regime/sector measurement extensions | ONLY if the P3/P4 audit shows M24 does not store raw trend/breadth/volatility/participation measurements and sector/RS measurements per session with methodology version. First step of P3 is a column-by-column check of M24; if sufficient, no M27 |
| 28 | `earnings_date_observation` (append-only first-seen log) + `earnings_fact` (revenue est/actual/surprise, timing, guidance, reaction gap, abnormal volume, each with `known_at` and `source`) | fixes the overwrite problem going forward; history marked `unavailable` / `reconstructed`, never invented |
| 29 | `market_event_classification` (append-only AI/rule classification and summary, referencing event revision) | factual fields never written by AI |
| 30 | `research_dataset_manifest`, `research_experiment` (+ registry link) | dataset hash, versions, split spec, metrics; reproducibility |
| (none) | P7 performance engine, P11 status | computed on read from existing tables; no table unless profiling demands one |

Numbers may shift if M27 is dropped; they are assigned at slice time and checked against the compose list so there is no collision with 24/25.

## 9. Backend / API work

- `backend/routers` + `backend/services` pairs only (CLAUDE.md rule); metrics stay in `mechanism/strategy_analytics`.
- New read-only endpoints under a generic prefix, e.g. `/api/lab/*`: `strategies/{id}/performance` (conditional breakdown), `labels/maturity`, `regime/current|history`, `sectors/rs`, `earnings/{window}`, `catalysts`, `research/datasets`, `models`, `status`.
- Every response carries `state` in {ok, preliminary, insufficient_sample, no_data, not_provisioned, unavailable, error}, `methodology_version`, `as_of_session`, `provenance`.
- Existing `/api/strategies/*` and `/api/market-intelligence/*` unchanged (backward compatible); `FORWARD_OUTCOMES` stub replaced by the real label summary once M26 exists, still `not_available` when the table is absent.

## 10. Mechanism / research work

- `mechanism/research/labels/` — fwd_v1 engine: C0/O1/C_H per the design spec §1A.15, window completeness and discontinuity checks (splits, halts, missing bars, delistings), label written only when the horizon completes, idempotent upsert-never-update, effective-N reporting. Pure functions + a thin repository; the scheduled runner is written but not scheduled.
- `mechanism/research/regime/`, `.../sector/` — reuse `market_intelligence/risk_regime.py` and `relative_strength.py` rather than duplicating; add the PIT/session-explicit runner and completeness fields.
- `mechanism/research/earnings/` — date-observation logger, fact builder, reaction/abnormal-volume computation from `stock_prices`.
- `mechanism/research/events/` — collector interface + one source (see data gaps), classification module with a hard "AI may not set factual fields" validator.
- `mechanism/research/dataset/` — builder + manifest + leakage guards.
- `mechanism/research/ml/` — evaluation harness (does not touch `mechanism/ml_enhancement`).
- P1a code touchpoints: `multi_timeframe_screener.py` (`'unknown'` default at ~481 and ~569), `ml_signal_enhancer.py` (`_unavailable()` omits the key), `signal_ledger_writer.py` (~226).

## 11. Frontend / UI work

- New `frontend/src/app/first-light/` area (Strategies, Performance, Regimes, Market, Sectors, Earnings, Catalysts, Research, Models) with shared components for the six states (loading / empty / unavailable / insufficient-sample / error / ok). Existing `/strategies` routes remain and are re-linked, not broken.
- Sample-size rule in one place (`lib/`), driven by the API `state`, so a tiny sample can never render as a metric without a label.
- Dev fixtures live under `__fixtures__/`, are loaded only behind an explicit dev flag and are rendered with a visible "FIXTURE DATA" banner.
- Frontend checks: `npx tsc --noEmit && npx next lint`, vitest/jest suites already in `lib/__tests__`.
- Deploy is manual Vercel; nothing deployed.

## 12. Test gaps

- No tests for forward labels, leakage, dataset joins, regime/RS writers under real Postgres (M24/M25 unapplied).
- DB-backed tests skip when Postgres is absent (commit cfd83f7). A local Postgres is reachable on :5432 on this PC (the frozen Windows dev DB). Plan: lab migration tests create a throwaway database/schema, never use that DB's data, and never touch the VPS.
- No test asserts the `model_version` NULL contract end-to-end (enhancer unavailable -> screener -> ledger writer -> observer).
- CI currently does not run migration-apply tests for 24+; add a migration-apply + immutability test job.
- Frontend: no tests for First Light pages; add state-matrix tests (each of the six states) per page.
- ML: no leakage/purge tests; embargo-vs-horizon assertion missing.

## 13. Data-source gaps

| Need | Today | Gap / proposal |
|---|---|---|
| Daily OHLCV, benchmark closes | `stock_prices`, market index tables | OK for labels; confirm adjusted vs raw handling and corporate actions for the discontinuity check |
| Sector/industry history | `daily_fundamentals.sector` overwritten | Never PIT-safe retroactively. Start an append-only sector map observation going forward; mark history `reconstructed` |
| Earnings dates | yfinance upsert | Add first-seen log (M28). History unavailable |
| Revenue estimate/actual, timing, guidance | none | Source decision needed (yfinance is not PIT; a paid/other feed or SEC filings). Until then fields are `unavailable` |
| Filing/catalyst events | none | SEC EDGAR (acceptance timestamps are PIT-grade, free) is the strongest candidate for a first observed event source; needs owner approval |
| News | Alpaca, Telegram-only, not stored | Needs a stored, timestamped table if used; classification only, never invented values |
| Analyst actions | none | out of scope until a PIT source exists |

## 14. PIT / leakage risks

1. Labels visible before horizon completion (guard: label rows only written for complete windows; dataset builder refuses unmatured labels).
2. Embargo shorter than horizon (current 30d vs 60 sessions) -> overlapping-label leakage in splits (guard: embargo >= max horizon + purge, asserted in tests).
3. Reconstructed sector maps and current-state fundamentals used as if known at T0 (guard: `provenance` observed vs reconstructed carried into the dataset; reconstructed excluded from ML view by default).
4. `earnings_calendar` overwrites: an earnings date that moved looks like it was always the final date.
5. yfinance estimates/actuals revised after the fact.
6. Survivorship: delisted symbols dropping out of `stock_prices` truncate labels; must be `incomplete`, never silently dropped or treated as zero.
7. Adjusted-price drift: split/dividend adjustments rewrite history after T0; labels must be computed from the same basis as the T0 snapshot or flagged.
8. Session ambiguity at rollover (CLAUDE.md invariant): every writer takes an explicit session.
9. Regime computed with data from after the session close.
10. AI classification leaking hindsight into event text; classification must be append-only and timestamped at its own time, and `known_at` of the event is not the classification time.
11. Strategy/direction contamination: `feature_snapshot` must stay strategy/direction-neutral T0; `candidate_observation` carries strategy/session/direction.
12. Overlapping signals counted as independent samples (report effective N).

## 15. Proposed implementation order

Each slice: audit -> contract -> implement -> tests -> run suites -> migration doc -> activation doc -> STOP for review. Slices 1 and 2 are small and can run concurrently with S11 observation.

1. **P0 deliverable**: `docs/research/FIRST_LIGHT_LAB_IMPLEMENTATION_STATUS.md` (component-by-component table with exact files/tables/jobs/tests/UI/dependencies). Docs only.
2. **P1**: `model_version` NULL fix + end-to-end contract tests; historical-normalization decision doc (do NOT rewrite the 165 'unknown' rows); Release B completion checklist (activation remains OFF).
3. **P2**: M26 + `mechanism/research/labels` + tests + runbook.
4. **P3 + P4 together** (shared PIT/session plumbing): M24 column audit, optional M27, runners, tests.
5. **P7** performance engine (needs slice 3 contract) in parallel with **P5** earnings log/facts (M28).
6. **P8** dataset builder + leakage tests.
7. **P6** catalyst architecture (collector interface + classification; source pending owner decision).
8. **P10** UI: shell + empty/unavailable states first (can start right after the API contracts of slice 3), then each area as its API lands.
9. **P9** ML research framework.
10. **P11** system status/data quality.
11. Consolidated activation runbook (ordering, grants, timers, validation queries, rollback).

## 16. Branch / worktree strategy

- Branch `lab/first-light-algo`, worktree `E:\מסמכים\pythonProjects\donchian_screener_0.1-wt-lab` (already created, off `main@d4eab95`).
- Untouched: main tree (uncommitted deep_value / alerts / qf-cleanup work), `-wt-qf-guard` (`fix/qf-yfinance-near-duplicate-guard`), `E:/dv_final`.
- Sub-branches per slice (`lab/p1-model-version`, `lab/p2-fwd-v1`, ...) cut from the lab branch if reviewing separately is preferred; otherwise one branch, one commit per slice.
- No push, no merge to `main`, no deploy without explicit owner approval. CI runs on the branch only if pushed; until then run suites locally.
- Tests run against a throwaway local database, never `trading_production`.
- Production reads: none planned. If one becomes necessary it goes through the existing pinned read-only S11 wrappers, outside S11 checkpoint windows.

## 17. Definition: LAB IMPLEMENTATION COMPLETE

All of the following, with nothing activated in production:
1. Migrations 26+ written, additive, in the compose init list, and applied/verified on a scratch DB, including immutability triggers and least-privilege grants.
2. `fwd_v1` label engine, regime, sector/RS, earnings, event/classification modules, dataset builder and ML harness implemented with unit + integration + leakage tests green locally and in CI.
3. `model_version` NULL fix merged-ready with a historical-normalization decision doc.
4. All `/api/lab/*` endpoints return the six explicit states; absent tables degrade to `not_provisioned`, never to zero or fabricated values.
5. First Light UI areas render every state; fixtures are isolated and labeled; `tsc` and `lint` clean.
6. P11 status page reports every lab field with explicit unavailable/stale states.
7. Per-phase docs: contract, migration notes, activation procedure, rollback.
8. `FIRST_LIGHT_LAB_IMPLEMENTATION_STATUS.md` shows every component as IMPLEMENTED BUT OFF (or an explicit, owner-accepted PLANNED item with the reason, e.g. a data source awaiting a decision).
9. Zero diffs to production pins, units, timers, roles, compose or the Telegram path.

## 18. Definition: PRODUCTION ACTIVATION COMPLETE

After S11 returns PASS and the owner approves, each step separately authorized, executed, validated by a real query, and rollback-ready:
1. Apply 24, 25, 26+ to `trading_production` (additive), verify with queries; grant `donchian_app` only what each job needs.
2. Build/pin a new mechanism image with the lab code; deploy via manual CD; verify the pin.
3. Register the capture activation row; enable research capture; verify that observations/snapshots are written under `donchian_app`, first-write-wins and hashes stable, ledger and delivery fingerprints unchanged.
4. Install and enable timers for label maturity, regime, sector/RS, earnings log, event collection (one at a time), each with a validation checkpoint.
5. Label engine runs idempotently on the first matured horizons; effective N reported; no backfill unless separately approved.
6. Enable `/api/lab/*` data (already deployable) and deploy the frontend (manual Vercel) with real data replacing `not_provisioned` states.
7. Decide separately: Telegram `market_environment`, any historical backfill, normalization of the 165 'unknown' rows, and any ML model promotion (none planned; needs OOS evidence).
8. Post-activation soak: a full session cycle observed with no permission/DDL errors, S11 invariants unchanged, status page green.

---

## Open decisions for the owner

1. Approve the worktree/branch layout and "no merge until S11 decides".
2. Event/catalyst source for P6 (SEC EDGAR recommended) and revenue/guidance source for P5.
3. Whether P1a (touches the screener) may be merged to `main` before S11 closes, or held on the branch (recommended: hold).
4. Whether the new UI area replaces or sits beside `/strategies` (recommended: beside, re-linked).

## Addendum 2026-10-04: owner decisions that supersede parts of this plan

- Three parallel lanes: A Research Foundation (model_version fix -> fwd_v1 -> dataset builder -> ML evaluation), B Market Intelligence (24 audit -> regime/breadth/sector/RS -> earnings -> catalysts), C Analytics/Product (generic performance engine -> APIs -> UI -> system/data-quality UI).
- The First Light UI sits above the existing /strategies concept; Strategy Intelligence stays, Donchian Breakout v1 is one strategy.
- First authoritative catalyst source: SEC EDGAR. Event ingestion is kept separate from catalyst classification; classifications are versioned and never rewrite the event. The revenue/guidance vendor is NOT chosen: a source-capability assessment comes first.
- Section 8 migration numbers are NOT assigned. See `docs/architecture/MIGRATION_REGISTRY.md` (M27 is dropped: the migration 24 audit found no gap).
- Historical `model_version='unknown'` production rows are not normalised (see `MODEL_VERSION_SEMANTICS_AND_HISTORICAL_ROWS.md`).
