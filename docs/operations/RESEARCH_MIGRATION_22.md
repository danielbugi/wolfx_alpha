# Migration 22 — research observation layer (Release B / B1): apply, verify, roll back

> **Status: PREPARED, NOT APPLIED to production.** Applying is a separate, human-approved step. This document is the
> procedure; it is not an instruction to run it. Roles: [RESEARCH_DB_ROLES.md](RESEARCH_DB_ROLES.md).

`mechanism/add_research_observation_tables.sql` creates 8 tables and 8 functions (all carrying the marker comment
`release_b_migration_22`), the immutability/append-only triggers (`ENABLE ALWAYS`), the activation boundary table, and four
**nullable** lineage constraints on `signal_ledger` (migration 21 added the columns). It writes no rows.

## What changed in the B1 hardening

* **`IF NOT EXISTS` no longer blesses an incompatible object.** A guard block at the top raises on an unmarked object of
  the same name, a partial set, an index on another relation, an orphan lineage constraint, or missing migration-21
  columns. Re-applying onto an exact copy is a no-op.
* **Preflight/verify compare real definitions** against the committed golden `mechanism/research/schema_fingerprint_22.json`
  (columns, types, nullability, defaults, PKs, UNIQUEs, CHECKs, FKs, indexes, trigger definitions **and enabled mode**,
  function signature / SECURITY DEFINER / pinned `search_path` / body hash / PUBLIC-execute). Result per target:
  `ABSENT` (apply is clean), `EXACT` (done / no-op), `INCOMPATIBLE` (stop, differences printed).
* **New CHECKs** from the real schema only: direction ∈ {1,-1}; `signal_type` ∈ {bullish_breakout, near_bullish,
  bearish_breakout, near_bearish} consistent with direction/triggered; capture-run status ∈ {running, complete, partial,
  failed} with `complete_is_accounted` / `partial_is_counted`; session source; `atr_source` ∈ {measured, fallback, missing},
  NOT NULL where a CHECK alone would admit NULL.
* **Ledger lineage FKs added now** (nullable, so Release A rows with NULL lineage pass): observation FK, snapshot FK,
  feature-set FK, and an all-or-none CHECK. Deployment order: **migration 22 before the first lineage write.** Adding them
  later would require validating existing lineage; adding them with the table creation costs nothing while there is none.
* **Role model, maintenance hatch, activation boundary** — see RESEARCH_DB_ROLES.md.

If the migration text is edited, regenerate the golden (from `mechanism/`, after a fresh apply into a scratch schema
`m22t`): `DB_… python research/schema_fingerprint.py m22t`, and commit it with the change.

> **Second strategy note.** The `signal_type` CHECK currently allows only the Donchian classes. A strategy with another
> signal class needs an **additive** widening migration (new number, not an edit of 22 once applied) plus a golden update.

## Apply (production — NOT done)

1. Fresh backup; confirm services idle (no pipeline running); confirm `/opt/donchian/CURRENT_MECHANISM_SHA` as expected.
2. `python mechanism/check_research_migration_preflight.py` → must print `Target state: ABSENT` and `RESULT: OK`.
3. Apply atomically:
   `PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f mechanism/add_research_observation_tables.sql`
   (`-1` = single transaction; any error rolls everything back. Add the file to `docker-compose.yml`'s init-mount list for
   fresh databases.)
4. `python mechanism/check_research_migration_preflight.py --verify` → `EXACT`, all row counts 0.
5. Verify with a real query, e.g. `SELECT count(*) FROM pg_trigger WHERE tgenabled='A' AND NOT tgisinternal;`.
6. Only then, separately: roles (RESEARCH_DB_ROLES.md §5), then — at the owner's decision — the activation boundary.
   Capture stays off (`RESEARCH_CAPTURE_ENABLED` unset) until the boundary is set.

## Roll back

`deploy/db/rollback22.sql` — **safe only before meaningful Release B production data exists.** It drops the research
tables and functions; captured observations, snapshots, capture-run history, activation boundaries and the maintenance
audit trail are **not recoverable** afterwards. It therefore:

* refuses unless every object to drop carries the migration-22 marker (never drops a look-alike);
* refuses a partial state or an absent set;
* refuses if any research table holds a row **or** any `signal_ledger` row carries lineage, unless the operator passes
  `-c research.rollback_data_loss_approved=yes` (export first: `pg_dump -t candidate_observation -t feature_snapshot …`);
* drops the four ledger lineage constraints first; with approval it NULLs the now-dangling lineage values on
  `signal_ledger` (no ledger row is ever deleted; no other column is written);
* uses no `CASCADE` (an unexpected dependent object fails the transaction instead of being dropped);
* leaves migrations 19/20/21 and every Release A row and column untouched; does not drop roles.

```
PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback22.sql
# with data present and the loss approved:
PGOPTIONS='-c search_path=public -c research.rollback_data_loss_approved=yes' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback22.sql
```

Order when undoing everything: services back to the old `DB_USER` → `research_roles_rollback.sql` → `rollback22.sql`.
Tested on a disposable database: apply → verify (`EXACT`) → rollback → verify (`ABSENT`, Release A rows and migration-21
columns intact) → reapply → verify (`EXACT`) — `mechanism/research/tests/test_rollback22.py`.
