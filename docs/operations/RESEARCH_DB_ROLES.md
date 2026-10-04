# Research layer — database roles, immutability and the maintenance hatch

> **Status: PREPARED, NOT APPLIED.** Nothing here has been run against production. Production services still connect as
> the docker `POSTGRES_USER`, which is a **superuser**, so until the transition below is done the research tables are
> protected only against `session_replication_role = replica` and accidental writes, not against a deliberate one.
> Applying any of this is a distinct, later, human-approved step (commit ≠ deploy).

Files: [`deploy/db/research_roles.sql`](../../deploy/db/research_roles.sql) (grants) ·
[`research_roles_verify.sql`](../../deploy/db/research_roles_verify.sql) (read-only verification) ·
[`research_roles_rollback.sql`](../../deploy/db/research_roles_rollback.sql) · tests:
`mechanism/research/tests/test_roles.py`, `test_maintenance_hatch.py`.

## 1. What actually protects the data

| Layer | What it does | What it does **not** stop |
|---|---|---|
| **Privileges (the real boundary)** | The runtime role has `INSERT`/`SELECT` only on `candidate_observation`, `feature_snapshot`, `feature_set_registry`; column-limited `UPDATE` on `candidate_capture_run`; nothing on the maintenance tables; no `EXECUTE` on any maintenance function; no `TRUNCATE`/`TRIGGER`/`REFERENCES`; not the owner. | A superuser or the table owner. |
| **Triggers, `ENABLE ALWAYS`** (`tgenabled='A'`) | Row triggers refuse `UPDATE`/`DELETE`; statement triggers refuse `TRUNCATE`; they fire even under `session_replication_role = replica`. | A **superuser** or the **table owner** can `ALTER TABLE … DISABLE TRIGGER`, `DROP TRIGGER`, or `CREATE OR REPLACE` the function. `ENABLE ALWAYS` is **not** a defence against them. |
| **Maintenance hatch** | The only sanctioned way to change an immutable row (§3). | Anyone who can already bypass triggers. |

**Bypass statement.** Superusers bypass every privilege check and can disable/drop triggers or replace functions. The
table owner (`donchian_owner`) can do the same to its own tables and functions, which is why it is `NOLOGIN`: nobody
connects as it; a person `SET ROLE donchian_owner` only while applying a migration. Any role holding
`pg_write_all_data`, `pg_execute_server_program`, or membership in the owner is equally trusted. The guarantee is therefore
"the *runtime* role cannot", never "nobody can".

## 2. Role model

| Role | Login | Holds |
|---|---|---|
| `donchian_owner` | no | Owns the 8 research tables and 8 functions (SECURITY DEFINER functions run with its small privileges, not a superuser's). Applies migrations via `SET ROLE`. |
| `donchian_app` | yes | The one role every runtime service (pipeline, screener, backend, bot) connects as. Privileges as in §1. Password set out of band with `\password`; **never in the repo**. |
| `donchian_research_admin` | no (group) | Maintenance/activation. Each person gets their **own** login role granted this group, so `session_user` differs per person (an approver must differ from the opener). Can reach `UPDATE`/`DELETE`/`TRUNCATE`, but the triggers still demand an approved, begun ticket. |

## 3. The maintenance hatch (transaction-local, two-person, audited)

Flow, all `SECURITY DEFINER` with a pinned `search_path` and `EXECUTE` revoked from `PUBLIC`:

1. `research_maintenance_open(reason, target_table, ttl_minutes)` — opener creates a ticket.
2. `research_maintenance_approve(ticket)` — a **different `session_user`** approves. Self-approval is refused.
3. `research_maintenance_begin(ticket)` — inside **the transaction that will make the change**: records
   `(txid_current(), pg_backend_pid(), session_user)` in `research_maintenance_session`. Opener only; ticket must be
   approved, not closed, not expired.
4. The guard trigger authorises a write only if that session row exists for the current transaction, the ticket is still
   valid and was opened by the same `session_user`, and `ticket.target_table = TG_TABLE_NAME`. A GUC set by the caller is
   informational and authorises nothing.
5. `research_maintenance_close(ticket)` — closes the ticket; every use is in `research_maintenance_audit` (append-only).

A ticket never authorises a different table, an expired ticket authorises nothing, and authorisation ends with the
transaction. Application code has no reason to call any of this; `test_import_separation.py` scans the whole repo
(runtime packages, scripts, backend, ml_training) and fails if anything outside the DB catalog tooling references the
hatch functions or tables.

## 4. Activation boundary (not a role, but the same trust model)

`research_capture_activation` is written only through `research_capture_set_state(strategy_id, 'enabled'|'disabled',
effective_from, note)` (admin only; the runtime role cannot). It is intentionally **not** called by any code; the date is
chosen by a person at the eventual production activation. See `docs/architecture/STRATEGY_INTELLIGENCE.md`.

## 5. Transition procedure (production — each step needs explicit approval; none done)

Precondition: migration 22 applied and `check_research_migration_preflight.py --verify` EXACT
([RESEARCH_MIGRATION_22.md](RESEARCH_MIGRATION_22.md)); fresh backup.

1. Create the roles + grants (atomic):
   `PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/research_roles.sql`
2. Set `donchian_app`'s password out of band (`\password donchian_app`), store it only in the VPS env file.
3. Verify: `PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -f deploy/db/research_roles_verify.sql`
   (reads real ACLs, then proves behaviour with `SET LOCAL ROLE donchian_app` in a rolled-back transaction).
4. **Runtime coverage of the non-research objects.** The services currently run as a superuser; `research_roles.sql`
   §6 grants `donchian_app` what they need on every pre-existing object (tables `SELECT/INSERT/UPDATE/DELETE`, views
   `SELECT`, sequences `USAGE/SELECT/UPDATE`, functions `EXECUTE`; no DDL) and `research_roles_verify.sql` proves it, including
   a block of refused prohibited operations (rehearsed on a full production-shaped schema by
   `mechanism/research/tests/test_roles_full_schema.py`). **There are no default privileges: re-run the role script and the
   verify script after every later migration** (a new table is not covered until you do), and add any new immutable table
   to the §6 exclusion list. The former `ALTER TABLE ml_models` runtime DDL is gone (migration 23,
   `add_ml_models_registry_columns.sql`; the runtime now only checks the columns exist), so the app no longer needs owner rights.
5. Switch services one at a time by setting **`APP_DB_USER`/`APP_DB_PASSWORD`** (or `<SVC>_DB_USER`/`_PASSWORD` for one
   service) in the VPS env file — **never** by editing `DB_USER`, which is also the postgres bootstrap identity and the
   migration/admin identity (see [../dev/ENVIRONMENT.md](../dev/ENVIRONMENT.md)). Watch `systemctl`/logs; keep the old
   credentials available until a full scheduled run passes. Deploy scripts take the compose files from
   `/opt/donchian/compose` and the release bundle under `/opt/donchian/releases/<tag>/`: sync the new compose bundle first or
   the variable chain does not exist on the host. The read-only checks for this stage are
   `mechanism/validate_release_b.py roles` and `connections` ([RELEASE_B_ACTIVATION.md](RELEASE_B_ACTIVATION.md)).

The immutability is **real only after step 5**. Until then the verification in step 3 passes for `donchian_app` while
the services keep running as a superuser.

## 6. Rollback

`deploy/db/research_roles_rollback.sql` restores the 8 tables and 8 functions to the owner recorded **before** the role
script ran and revokes the grants. Record that owner first
(`SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class WHERE relname IN ('candidate_observation','feature_snapshot');`)
and pass it as `-c research_roles.original_owner=<role>` — there is deliberately no default. It does **not** drop the three
roles (dropping a role services still use is an outage); drop them by hand afterwards. Point services back at their previous
`DB_USER` **first**. Removing the research tables themselves is a separate script, `deploy/db/rollback22.sql`
(data-loss gated).

## 7. Notes

* `TRUNCATE … CASCADE` on a table referenced by another would cascade; the immutable tables are FK-referenced by
  `signal_ledger`'s lineage constraints, so a plain `TRUNCATE` of them is refused by Postgres, and `TRUNCATE … CASCADE`
  could reach `signal_ledger`. Only the owner/superuser can issue it; the statement triggers block it for everyone else
  and `donchian_app` has no `TRUNCATE` privilege.
* Backups: `pg_dump` as the owner/superuser is unaffected by any of this.
* **Append-only tables added after migration 22 (24, 25, 26).** `universe_snapshot`, `market_snapshot`, `sector_snapshot`,
  `market_event`, `market_event_revision` and `forward_return_label` have **no maintenance hatch**: `donchian_app` gets
  `SELECT` + `INSERT` only (and sequence `USAGE`/`SELECT`), the admin group gets `SELECT`, the owner is `donchian_owner`,
  and a correction is a new version row. They are conditional in `research_roles.sql` section 7 (valid before and after
  the migration), **excluded from the section 6 full-DML baseline** (the `NOT IN (...)` list there — a new immutable
  table that is not named in that list silently receives full DML) and covered by the verifier and the rollback script.
  After applying any of these migrations, re-run `research_roles.sql` and `research_roles_verify.sql`.
  Minimum runtime privileges for the fwd_v1 label generator (migration 26): `SELECT` on `candidate_observation`,
  `stock_prices`, `market_index_prices` (the section 6 baseline already grants these), and `SELECT` + `INSERT` on
  `forward_return_label`. It never needs `UPDATE`, `DELETE`, `TRUNCATE` or any DDL. `mechanism/research/tests/
  test_roles_full_schema.py` proves this on a bootstrapped full schema and must run in CI (it needs a superuser).
