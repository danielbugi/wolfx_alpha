# Migration registry (canonical)

Status: written 2026-10-04 on `lab/first-light-algo` for First Light Lab slice 1. Audit only: no migration was applied or changed.

## 1. How migrations exist here (the facts)

- There is **no migration runner and no history table.** A migration is a `mechanism/*.sql` file that a person applies to production by hand.
  The only evidence that one was applied is that its objects exist. The preflight scripts
  (`mechanism/check_signal_ledger_migration_preflight.py`, `mechanism/check_research_migration_preflight.py`) check for that.
- The **only numbering** is the `docker-compose.yml` init-mount list (`/docker-entrypoint-initdb.d/NN_<file>`). It runs only when a database is
  created empty (a fresh dev/CI/restore target). It never touches production. The number is not in the file name.
- Files are additive and re-runnable (`IF NOT EXISTS`). 24 and 25 additionally refuse on a partial or unmarked prior state (collision guard) and run atomically (`psql -1`).
- Least-privilege roles are separate: `deploy/db/research_roles.sql` (+ `_verify`, `_rollback`). **A new table is invisible to `donchian_app` until that
  script is re-run, and a new *immutable* table is silently given full DML unless it is added to the script's exclusion list** (section 6 of
  `research_roles.sql`, the `NOT IN (...)` list, and the section 7 style grant block). There is deliberately no `ALTER DEFAULT PRIVILEGES`.

## 2. Registry

Production state: **verified** = observed by the S10/S11 read-only observers on the VPS; **inferred** = objects existed when the Windows database was
migrated and later preflights passed, but this audit did not re-query it.

| # | File | Creates | Production state |
|---|---|---|---|
| 1 | create_trading_schema.sql | base schema | inferred: present |
| 2 | add_multi_timeframe_tables.sql | multi-timeframe signal tables | inferred: present |
| 3 | add_market_data_tables.sql | market index + sector daily data | inferred: present |
| 4 | add_ml_dataset_tables.sql | ML dataset tables | inferred: present |
| 5 | add_alerts_tables.sql | alert tables | inferred: present |
| 6 | add_digest_tables.sql | digest tables | inferred: present |
| 7 | add_tracker_tables.sql | tracker tables | inferred: present |
| 8 | add_assistant_tables.sql | private assistant tables | inferred: present |
| 9 | add_access_flow_tables.sql | access flow | inferred: present |
| 10 | add_telegram_control_tables.sql | telegram control | inferred: present |
| 11 | add_telegram_control_phase2.sql | telegram control phase 2 | inferred: present |
| 12 | add_dashboard_auth_tables.sql | dashboard auth | inferred: present |
| 13 | add_earnings_calendar_table.sql | `earnings_calendar` (overwritten in place; no first-seen history) | inferred: present |
| 14 | add_morning_dm_tables.sql | morning DM | inferred: present |
| 15 | add_piotroski_score.sql | piotroski column | inferred: present |
| 16 | widen_ml_models_version.sql | widens `ml_models.version` | inferred: present |
| 17 | add_ml_prediction_tracking_tables.sql | ML prediction tracking | inferred: present |
| 18 | add_telegram_post_delivery_table.sql | `telegram_post_delivery` | verified: present (28 rows at S11 baseline) |
| 19 | add_signal_ledger_tables.sql | `signal_ledger` | verified: present (1022 rows) |
| 20 | add_strategy_identity_release_a.sql | `strategies`, ledger strategy identity | verified: present |
| 21 | add_signal_ledger_eval_flags.sql | `evaluation_flag`, `resolution_flag` | verified: present |
| 22 | add_research_observation_tables.sql | capture/observation/feature-snapshot/registry/activation tables | verified: applied, capture OFF (all tables 0 rows) |
| 23 | add_ml_models_registry_columns.sql | 3 `ml_models` columns | verified: applied |
| 24 | add_market_snapshot_tables.sql | `universe_snapshot`, `market_snapshot`, `sector_snapshot`, `research_market_guard()` | verified: **not applied** (S11 `SCHEMA` probe) |
| 25 | add_market_event_tables.sql | `market_event`, `market_event_revision`, `research_market_event_stamp()`; **requires 24** | verified: **not applied** |
| 26 | add_forward_return_label_table.sql | `forward_return_label`, `research_label_guard()`, `research_label_consistency()`; **requires 22** | not applied (lab slice 2; nothing writes to it until a separate owner-authorised activation) |
| 27 | add_source_observation_tables.sql | `source_observation` (hash-chained first-seen log), `source_poll`, `research_observation_guard()`, `research_observation_stamp()`, `research_poll_stamp()`; independent | not applied (lab slice 3; nothing writes to it until a separate owner-authorised activation) |
| 28 | add_catalyst_classification_table.sql | `catalyst_classification`, `research_classification_guard()`, `research_classification_consistency()`; **requires 25** | not applied (lab slice 3; dormant) |
| 29 | add_stock_relative_strength_table.sql | `stock_relative_strength`, `research_rs_guard()`, `research_rs_stamp()`; independent | not applied (lab slice 3; written only under the runner's opt-in `--with-stock-rs`) |
| 30 | add_dataset_experiment_registry_tables.sql | `dataset_manifest`, `experiment_registration`, `experiment_result`, `research_registry_guard()`/`_stamp()`/`_consistency()`; logically after 22/26, no FK | not applied (lab slice 3; dormant) |
| 31 | add_sector_history_tables.sql | `sector_observation` (hash-chained PIT sector history), `sector_poll`, `sector_reconstruction` (separate, never read by the forward path), `research_sector_guard()`/`_enc()`/`_row_hash()`/`_obs_stamp()`/`_poll_stamp()`/`_recon_stamp()`; independent | not applied (lab slice 10; the recorder is behind a default-OFF flag, nothing else writes to it) |

Next free number: **32**.

## 3. Drift that already happened (do not repeat)

The earlier design reserved 23/24/25 for `forward_return_label`, `candidate_plan_outcome` and ledger foreign keys. Reality used 23 for the
`ml_models` columns and 24/25 for Market Intelligence. `forward_return_label` and `candidate_plan_outcome` therefore have **no** number yet. Any
document that cites those numbers is stale. The `mechanism/shared/tests/test_migration_registry.py` guard now fails when the compose list, the
SQL files on disk and this table disagree (gap, duplicate, unmounted file, missing row).

## 4. Migrations 24 and 25 audit (read in full; nothing applied)

Both are safe to apply as written, but neither is activated by applying it: nothing in the runtime writes to them yet (no collector, no scheduled job).

| Property | 24 (snapshots) | 25 (events) |
|---|---|---|
| Atomic / re-runnable | yes (`psql -1`, `IF NOT EXISTS`) | yes |
| Collision guard | refuses on partial or unmarked objects (marker `market_intelligence_migration_24`) | same (marker `..._25`); refuses without 24 |
| Immutability | ENABLE ALWAYS BEFORE UPDATE/DELETE row triggers + BEFORE TRUNCATE statement triggers; `research_market_guard()` always raises; **no maintenance hatch**, a correction is a new `feature_set_version` | events immutable; revisions append-only; `ingested_at` stamped by a DB trigger, not caller-controlled |
| PIT / provenance | `provenance` observed or reconstructed in every UNIQUE key; `sector_pit_safe` TRUE only for observed rows | `known_at` is NULL iff basis is unknown; `known_at <= ingested_at`; pit_grade X iff unknown; actuals only on reported/revised |
| Roles | `research_roles.sql` section 7 covers all 5 tables (owner `donchian_owner`, `donchian_app` SELECT+INSERT, admin SELECT). Conditional, so it is valid before and after; **must be re-run and verified after 24/25 are applied** | same |
| Tests | `mechanism/market_intelligence/tests/test_migrations_24_25.py` (14 tests: re-apply, immutability incl. replication role, DB-stamped time, known_at rules, refusal on foreign relation, refusal without 24, weakened guard, unavailable-regime and sector-member checks, correction = new version) | same file |
| Depends on 22/23 | no | no (depends on 24 only) |

**Does 24 hold the raw regime/breadth/volatility/participation measurements?** Yes, in `regime_components` (JSONB: raw value + normalised score +
availability per component, `risk_regime.COMPONENTS` c1..c7: index trend, index momentum, % above SMA50, % above SMA200, net 52-week highs minus lows,
VIX level, RUT-vs-SPX). Equal-weight universe and S&P returns (5/20/60) and per-sector returns, relative strength vs S&P and vs universe, member
counts, valid/excluded counts and rank are typed columns. Consequence: **no extension migration is needed for B1.** The only gap is that the raw
breadth numbers are JSONB, not typed columns; that is a read-side concern (an index or a view), not a reason for a new table.

Caveats unchanged: the regime is a v1 heuristic (not a validated model); sector history is reconstructed and never PIT-safe; `earnings_calendar` is not
backfilled into `market_event`.

## 5. Recommendation: allocation rule and provisional order (nothing is assigned)

Numbers 32+ are **not** reserved (26-31 were assigned by lab slices 2-3 and 10). A number is assigned only in the commit that adds the SQL file, its init-mount line, its row in section 2 and its
roles/verify update together, and the registry test must pass in that commit. Two lanes can therefore never collide silently: the second to merge
sees a failing test and renumbers.

Provisional dependency order, for planning only. Items 1-4 are now assigned (26, 27, 28, 30; per-stock RS persistence is 29); the list is kept as the original plan:

1. `forward_return_label` (`fwd_v1`): depends on 22. Immutable, so it needs an exclusion-list entry and an append-only grant (SELECT+INSERT).
2. Earnings first-seen observation log and earnings facts: independent. Fixes `earnings_calendar`'s overwrite problem going forward only.
3. Catalyst classification (versioned, append-only, references an event revision; never rewrites the event): depends on 25. Source is SEC EDGAR; the
   revenue/guidance vendor is undecided and must not be encoded in a schema until the capability assessment is done.
4. Dataset manifest and experiment registry: depends on 22 and the label table.
5. Regime/sector extension: **dropped** by the 24 audit above unless a typed-breadth requirement appears.

Applying any of these to production is a separate, owner-authorised stage after S11. No lab migration is applied to production by this work.
