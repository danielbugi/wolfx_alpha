# Migration 35: `strategy_variant` (versioned strategy-variant identity registry)

Status 2026-10-12: promoted into the normal migration path (`mechanism/add_strategy_variant_registry.sql`, mounted as `35_` in `docker-compose.yml`, row 35 in
`docs/architecture/MIGRATION_REGISTRY.md`). **Not applied to production.** Automatic production registry writes are **not enabled**: nothing in the pipeline, the
bot, the backend or any timer calls `research.strategy_variants`; it is a CLI that writes only with `--apply --confirm-database <name>`.

What it adds: one append-only table `strategy_variant` and two trigger functions (`research_strategy_variant_guard/stamp`). Read-only foreign key to `strategies`;
it never writes `strategies` or `signal_ledger`. Design and rules: `docs/research/STRATEGY_VARIANT_REGISTRY.md`.

## What CI proves (all run on every PR)

| Concern | Test |
|---|---|
| Fresh bootstrap (compose init list 1..35, real role script) | `forward_collection/tests/test_migration_35_db.py::test_a_fresh_bootstrap_has_exactly_the_intended_objects_and_least_privilege` and `db-bootstrap-integration` |
| Permissions | same test: runtime role SELECT + INSERT only; no UPDATE / DELETE / TRUNCATE / REFERENCES / TRIGGER; PUBLIC none; sequence USAGE; no EXECUTE on the trigger functions; owner `donchian_owner` |
| Works as the least-privilege runtime role | `test_the_registry_works_as_the_least_privilege_runtime_role...` (FK check, DB-stamped time and lineage trigger under `donchian_app`) |
| Role model against the whole schema | `research/tests/test_roles_full_schema.py` (the verifier passes with `strategy_variant` listed append-only) |
| Existing-schema compatibility, idempotency | `test_reapplying_over_a_populated_table...` (applied twice over a populated table: same rows, same hashes, `strategies` / `signal_ledger` counts unchanged); `test_a_foreign_table_or_function...` (refuses a same-named object without the marker) |
| Registry rules | `research/tests/test_strategy_variants_db.py` (idempotency, drift refused, versioning, duplicate refused, immutability, DB-enforced lineage, concurrency) |
| Preflight | `test_the_preflight_*`: ABSENT / EXACT reported; stops on a missing parent, a foreign object, a trigger not ENABLE ALWAYS, and (with `research.preflight_require_roles=yes`) missing roles; it contains no write keyword and runs in a read-only session |
| Rollback | `test_a_row_of_identity_evidence_blocks_the_rollback...`, `test_an_empty_table_is_dropped...` |
| Frozen-artifact bookkeeping | `forward_collection/tests/test_activation_artifacts.py` (runbook digests of the three extended role scripts regenerated) |

The role scripts (`deploy/db/research_roles.sql`, `_verify.sql`, `_rollback.sql`) were extended to list `strategy_variant` as append-only, as their own header requires after any
migration that adds a table (otherwise the baseline section grants the runtime role full DML on it). Production still has the previous versions; they are re-run only in step 6 below.

## Production procedure (NOT performed; needs the owner's go at the moment of execution)

Preconditions: CI green on the final integration commit; the new `/api/lab` backend release already verified (migration 35 is independent of it: the backend does not read the table).

```
D="ssh -i ~/.ssh/donchian_deploy -o IdentitiesOnly=yes -o BatchMode=yes deploy@116.203.220.219"      PG=donchian-screener-postgres-1
1. Backup (restore-checked; secret globals stay on the box):
   $D "bash -s -- migration-35" < deploy/db/pre_activation_backup.sh          # then verify: sha256sum -c production.dump.sha256, rowcounts.txt present
2. Preflight (read-only session; must end with  PREFLIGHT_35 OK state=ABSENT):
   $D "docker exec -i -e PGOPTIONS='-c search_path=public -c default_transaction_read_only=on' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -c \"SET research.preflight_require_roles='yes'\" -f -" < deploy/db/preflight_35.sql
3. Check the file on the box equals the reviewed one: sha256 of mechanism/add_strategy_variant_registry.sql (LF) against the repo.
4. Apply atomically:
   $D "docker exec -i -e PGOPTIONS='-c search_path=public' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -1 -f -" < mechanism/add_strategy_variant_registry.sql
5. Verify with a real query: preflight again (state=EXACT), and
   select to_regclass('strategy_variant'), (select count(*) from strategy_variant), (select count(*) from strategies), (select count(*) from signal_ledger);   -- 0 / unchanged / unchanged
6. Roles: re-run deploy/db/research_roles.sql then deploy/db/research_roles_verify.sql exactly as in `docs/operations/FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md` (the role-script step).
   Until this is done the runtime role has full DML on strategy_variant (the immutability triggers still raise for everyone): do steps 4 and 6 in the same session.
7. Confirm the pipeline / collector / bot / backend are healthy (container health, timer states, a read query) and that nothing else changed (pin files, arming file).
8. Registry writes stay MANUAL and off: `python -m research.strategy_variants register ... --apply --confirm-database trading_production` is a separate owner decision.
```

**Rollback (only while the table is empty, or with written approval of the loss):** `PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback_35.sql`
(refuses when rows exist; touches only the migration-35 objects; never uses CASCADE). Roles: re-run the previous `research_roles.sql` (the table is gone; the listing is conditional on
`to_regclass`, so the extended script is also safe to leave in place). Full restore from the step-1 backup is the last resort.

**Reversibility:** applying is additive and reversible while empty. The migration takes no lock on any live table (it creates a new table and a foreign key to `strategies`, which needs a brief
SHARE ROW EXCLUSIVE lock on `strategies`: a 1-row table read by the screener once per run; apply when no pipeline, scan or collector unit is running: check `systemctl list-timers` and `systemctl list-units 'donchian-*' --state=running` first).
