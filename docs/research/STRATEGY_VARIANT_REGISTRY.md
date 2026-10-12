# Strategy variant registry (proposed migration 35; isolated research databases only)

Status (2026-10-12): implemented, tested, and **promoted into the normal migration path** as migration 35 (`mechanism/add_strategy_variant_registry.sql`, mounted in `docker-compose.yml`, CI-validated, role scripts extended). **Not applied to production; automatic production registry writes are not enabled.** Procedure, gates and rollback: `docs/operations/MIGRATION_35_STRATEGY_VARIANT.md`.

**What it is.** A versioned, append-only identity for a *variant* of the live strategy: today, `donchian_breakout v1` exited by one of the five lab exit policies
(`research/exit_policies.py`). Table `strategy_variant` (`mechanism/add_strategy_variant_registry.sql`), writer and audit
`mechanism/research/strategy_variants.py`.

**Why a separate table.** `strategy_analytics` lists every row of `strategies`, so a variant row there would appear on the production dashboards as a strategy with
no signals. The variant table only *references* its parent (`parent_strategy_id`, read-only foreign key). The module reads `strategies` and inserts only into
`strategy_variant`; a static test pins that, and a database test proves `strategies` and `signal_ledger` are byte-for-byte unchanged by a registration.

**Rules** (enforced in the module and again by the database):
- Idempotent: same definition again = `unchanged`, same ids; four concurrent writers converge on five rows.
- Never edited: a changed definition under an existing key is refused unless a new version is explicitly requested; then `v2` supersedes `v1` (straight lineage,
  version exactly +1, each row superseded once). An older definition cannot be silently re-registered; one definition cannot live under two names.
- Lineage is self-describing: the definition echoes the parent key/version (checked against the parent row), the kind, the exit parameters and the engine constants;
  `definition_hash` = sha256 of the canonical JSON, re-verified by `verify`.
- Immutable: UPDATE / DELETE / TRUNCATE raise (ENABLE ALWAYS triggers); `created_at` is DB-stamped. Rollback while empty: `deploy/db/rollback_35.sql`.

**Tests.** `test_strategy_variants.py` (7 pure: pinned hashes of the five identities, key shape, engine-constant sensitivity, write-surface) and
`test_strategy_variants_db.py` (10 on real Postgres: idempotency, untouched live identity and ledger, drift refused, new version, regression refused, duplicate
definition refused, missing parent, immutability, DB-enforced lineage/consistency/DB-stamped time, tamper detection, concurrency). CLI smoke on a scratch
database: dry run writes nothing; `--apply` requires `--confirm-database <name>`; second apply = all `unchanged`; `verify` OK.

**Production:** held until CI is green on the final integration commit, a production backup and the read-only preflight (`deploy/db/preflight_35.sql`) are done; then the exact steps in `docs/operations/MIGRATION_35_STRATEGY_VARIANT.md`. Registering the five variants is a separate, manual owner decision.
