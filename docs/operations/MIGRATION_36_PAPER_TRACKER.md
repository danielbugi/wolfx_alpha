# Migration 36: forward paper tracker storage (`paper_session`, `paper_observation`, `paper_event`)

Status 2026-10-13: merged to main (PR #14), **NOT applied to production**. Production read-only preflight on 2026-10-13: `PREFLIGHT_36 OK state=ABSENT` (strategy_variant 5 rows, none of the three tables exists,
research roles present). Applying it is a separate owner approval; so is everything after it (`docs/research/FORWARD_PAPER_TRACKER.md` section 6).

What it adds: three append-only tables (`paper_session`, `paper_observation`, `paper_event`), their guard/stamp trigger functions and `ENABLE ALWAYS` immutability triggers, marker comments. Read-only foreign keys to
`strategy_variant` and `candidate_observation`; it never writes `strategies`, `signal_ledger`, `candidate_observation` or any collector table. Nothing in the pipeline, the collector, the bot or the backend reads or writes them.

## What proves it (all on every PR; the DB tests also run in `db-bootstrap-integration`)

| Concern | Test |
|---|---|
| Fresh bootstrap (compose init list 1..36, real role script), permissions, nine ALWAYS triggers | `forward_collection/tests/test_paper_tracker_db.py::test_a_fresh_bootstrap_has_the_three_tables_with_least_privilege_and_nine_always_triggers` |
| Preflight ABSENT / EXACT / refusals; re-apply is a no-op | `test_the_preflight_reports_exact_and_the_reapply_is_a_no_op` |
| Immutability (UPDATE / DELETE / TRUNCATE refused even for a superuser; runtime role lacks the privileges), DB-enforced order, contiguity, chain linkage, hindsight flag | `test_nothing_can_be_updated...`, `test_the_database_enforces_event_order...`, `test_a_session_cannot_be_inserted_out_of_order...` |
| Role model against the whole schema | `research/tests/test_roles_full_schema.py` |
| Rollback refuses while evidence exists | `test_the_rollback_refuses_while_evidence_exists_and_removes_exactly_the_migration_when_approved` |
| Deterministic, interruption-safe, duplicate-free, independent variants, explicit absent context | `test_paper_tracker_validation_db.py` (two databases converge hash for hash; connection dropped before commit and crash mid-write leave nothing; one entered / closed per observation; each variant equals that variant simulated alone; absent vs broken vs empty context sections) |

## Production procedure (NOT performed; needs the owner's final schema-apply approval)

Apply in a quiet window: the foreign keys take a brief SHARE ROW EXCLUSIVE lock on `strategy_variant` (5 rows) and `candidate_observation` (written by the nightly capture). Check first that no pipeline, scan, collector
or backup unit is running (`systemctl list-units 'donchian-*' --state=running`); the pipeline fires 23:45 / 01:00, the collector 06:45 / 08:15 and the nightly backup around 02:30 (Asia/Jerusalem), so late morning to evening is clear.

```
D="ssh -i ~/.ssh/donchian_deploy -o IdentitiesOnly=yes -o BatchMode=yes deploy@116.203.220.219"      PG=donchian-screener-postgres-1
0. Pre-state record (read-only): pins/arming file/timers/containers snapshot (the same script used for every release) + row counts of strategies, signal_ledger, candidate_observation, strategy_variant.
1. Backup (restore-checked; secret globals stay on the box):
   $D "bash -s -- migration-36" < deploy/db/pre_activation_backup.sh          # then verify: sha256sum -c production.dump.sha256, rowcounts.txt present
2. Preflight (read-only session; must end with  PREFLIGHT_36 OK state=ABSENT):
   $D "docker exec -i -e PGOPTIONS='-c search_path=public -c default_transaction_read_only=on' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -c \"SET research.preflight_require_roles='yes'\" -f -" < deploy/db/preflight_36.sql
3. Check the file on the box equals the reviewed one: sha256 of mechanism/add_paper_tracker_tables.sql (LF) against the repo at the merge commit.
4. Apply atomically:
   $D "docker exec -i -e PGOPTIONS='-c search_path=public' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -1 -f -" < mechanism/add_paper_tracker_tables.sql
5. Roles in the SAME session (until then the runtime role holds full DML on the new tables; the immutability triggers still raise for everyone):
   $D "docker exec -i -e PGOPTIONS='-c search_path=public' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -1 -f -" < deploy/db/research_roles.sql
   $D "docker exec -i -e PGOPTIONS='-c search_path=public' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -f -" < deploy/db/research_roles_verify.sql     # prints OK lines only
6. Verify with a real query: preflight again (state=EXACT), and
   select to_regclass('paper_session'), to_regclass('paper_observation'), to_regclass('paper_event'), (select count(*) from paper_session), (select count(*) from paper_observation), (select count(*) from paper_event),
          (select count(*) from strategies), (select count(*) from signal_ledger), (select count(*) from candidate_observation), (select count(*) from strategy_variant);   -- three regclasses / 0 0 0 / counts unchanged
7. Confirm the pipeline / collector / bot / backend are healthy and nothing else changed (pin files, arming file, timers, containers) by diffing the step-0 snapshot.
```

**Rollback (only while the tables are empty, or with written approval of the loss):** `PGOPTIONS='-c search_path=public' psql -v ON_ERROR_STOP=1 -1 -f deploy/db/rollback_36.sql` (refuses when any row exists; touches only the
migration-36 objects; no CASCADE). Roles: re-run the role script (listing is conditional on `to_regclass`). Full restore from the step-1 backup is the last resort.

**Storage monitoring (retention is "preserve all"):** measured on a 946-session replay with five variants: about 140 MB (open-position snapshots ~72 KB per session dominate). A production session registers about 250 triggered
candidates x 5 variants, so expect order 20-50 MB per year of snapshots plus observations and events; the host has 91 GB free and the database is 4.1 GB. Add a size line for these three tables to the weekly storage check;
review at 1 GB.
