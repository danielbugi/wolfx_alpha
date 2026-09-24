# Production Migration Runbook

> Phase 4A, updated in Phase 4A.5 and Phase 4A.6 — **this runbook is written, not executed against
> production.** No migration step below has been performed against the real production database. It
> is the chronological procedure for Phase 4B (or a later, separately-approved phase) to follow.
> Companion to [PRODUCTION_INFRASTRUCTURE.md](PRODUCTION_INFRASTRUCTURE.md),
> [SECRETS_STRATEGY.md](SECRETS_STRATEGY.md), [CD_DESIGN.md](CD_DESIGN.md),
> [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md), [MIGRATION_PLAN.md](MIGRATION_PLAN.md),
> [PRODUCTION_SCHEMA_DRIFT.md](PRODUCTION_SCHEMA_DRIFT.md) (the full per-table drift report — this
> document assumes its findings rather than repeating them in full).
>
> **Read this distinction carefully — it is the one this whole document is organized around:**
> - **Historical production schema drift → now captured in the repository (Phase 4A.6, done).** The
>   4 active tables that existed in production with no tracked migration
>   (`inactive_symbols`, `ml_predictions`, `ml_prediction_outcomes`, `ml_performance_metrics`) are
>   now representable from a fresh install via `mechanism/add_ml_prediction_tracking_tables.sql`.
>   This is a **repository-only** change — files added to version control, verified against
>   disposable test databases, nothing more.
> - **Actual production migration → still not executed (unchanged, still Phase 4B+).** The real
>   production database has not been backed up for migration purposes, not been connected to by any
>   new infrastructure, not been modified in any way. Everything in the DEPLOYMENT/CUTOVER sections
>   below remains a specification, not a completed action.
>
> **What Phase 4A.5 did:** the compatibility test in §2 below was performed and passed — the
> Dockerized backend, bot, pipeline, and channel-sender were all verified against a disposable,
> schema-metadata-only replica of production's real schema (40 active tables + 3 views). The CORS
> blocker flagged in the original Phase 4A version of this document was resolved and tested.
>
> **What Phase 4A.6 added:** the 4 active drifted tables are now a tracked migration (file 17 in the
> bootstrap sequence), verified two ways — a completely fresh database now produces the full 40-table
> schema with every constraint/sequence/grant matching production's real structure, **and** applying
> the same migration to a database that already has these tables (simulating production) was proven
> to be a true no-op (bit-for-bit identical data, sequence values unchanged). A CI regression guard
> now specifically asserts these 4 tables' structure, not just their existence.

## 0. The most important fact this runbook is built around

A **read-only** inspection of the real production database (`trading_production`, live, unmodified)
performed in Phase 4A.5 found:

- Production has **42 tables and 3 views**. At the time, the 16 tracked SQL migration files
  produced only **36 tables and 3 views** against an empty database. **As of Phase 4A.6, there are
  17 tracked files, producing 40 tables + 3 views** — the 4 active drifted tables are now included;
  the count is verified, not estimated (§4 below).
- The remaining difference is **2 confirmed-dead tables** (`enhanced_ml_training_data`,
  `momentum_scores` — documented in `CLAUDE.md` as stale/legacy, referenced only by already-retired
  scripts) that Phase 4A.6 deliberately did **not** add to any tracked migration (see
  `PRODUCTION_SCHEMA_DRIFT.md`'s recommendation §2 for why).
- **Zero tables exist in the tracked SQL files that are missing from production** — the tracked
  migrations remain a strict subset of what's really there (now a much smaller gap: 2 dead tables,
  not 6 tables of mixed importance).

**Consequence, stated plainly, and still true after Phase 4A.6: the fresh-bootstrap mechanism (now
17 SQL files, used for the empty CI/local-validation database) must never be run against
production.** It would not "set up" or "repair" anything — production already has everything those
files create. Running the bootstrap against production is unnecessary at best (every
`CREATE TABLE IF NOT EXISTS` on an already-existing table is a genuine no-op — verified explicitly
in Phase 4A.6, see §2) or, for the *original* `create_trading_schema.sql` file specifically
(which — unlike the new file 17 — still contains `DROP TABLE IF EXISTS` statements for some tables,
per Phase 2's observed log output), **could destroy real data** at worst. This is not a theoretical
caution; it is the literal, demonstrated behavior of `create_trading_schema.sql`'s first lines. The
new file (17) was written specifically to never behave this way (no `DROP` anywhere in it) — but the
*bootstrap sequence as a whole* still includes the older file, so the sequence as a whole remains
production-unsafe to run.

## 1. Fresh installation vs. existing production — two entirely different procedures

### Fresh installation (validated — Phase 2/3 for the original 16 files, Phase 4A.6 for the addition)
```
17 bootstrap/migration SQL files  →  empty PostgreSQL database  →  40 tables, 3 views
```
Used for: local development, CI's `db-bootstrap-integration` job, any future disposable/staging
environment. **Never used for production.**

### Existing production (this runbook's actual subject)
```
existing production database (42 tables, real data, years of history)
        │
        ▼
   full backup (pg_dump) + off-box copy + verified restore  ←  MUST pass before anything else (§8)
        │
        ▼
   the NEW Dockerized backend/bot/pipeline CONNECT DIRECTLY to the EXISTING database
   (same host initially, or migrated host — see §DEPLOYMENT) — no bootstrap script runs
        │
        ▼
   compatibility proven by connecting the real, validated application code against a
   RESTORED COPY of production (never production itself) and exercising real endpoints (§2)
```

## 2. Proving compatibility without touching production — DONE (Phase 4A.5), result: PASS

The Dockerized backend (Phase 2, fully validated) expects exactly the 36 tables + 3 views the
tracked SQL files create. Phase 4A.5's per-table drift investigation
(`PRODUCTION_SCHEMA_DRIFT.md`) found production actually has 42 tables, and narrowed the
compatibility question precisely: 2 of the 6 untracked tables are confirmed dead
(`momentum_scores`, `enhanced_ml_training_data` — referenced only by already-retired scripts), so
the real question was **does the Dockerized application correctly read/write against the 4 active
untracked tables** (`inactive_symbols`, `ml_predictions`, `ml_prediction_outcomes`,
`ml_performance_metrics`)?

**Test actually performed (read-only against production, no data copied):**
1. Extracted the 4 active tables' real DDL via `pg_dump --schema-only --no-owner --no-privileges
   --table=inactive_symbols --table=ml_predictions --table=ml_prediction_outcomes
   --table=ml_performance_metrics` — schema only, zero rows, zero business data.
2. Applied the 16 tracked SQL files + this extracted DDL, in dependency order (`ml_predictions`
   before `ml_prediction_outcomes`, matching the real foreign key), to a **fresh, disposable**
   `postgres:16-alpine` container — never production, removed after the test.
3. Granted `trading_user` the same full privilege set on the 4 new tables that production's real
   grants already show (`PRODUCTION_SCHEMA_DRIFT.md`'s per-table permissions section) — result: 40
   tables + 3 views, representing the *active* production schema exactly.
4. Pointed the already-built Phase 2 backend/mechanism Docker images at this disposable database
   (dummy credentials throughout, `PROD_SENDING_ENABLED=0`, no vendor keys) and exercised the real
   code paths:
   - **Backend**: all 14 routers loaded cleanly (confirming the Phase 2 Dockerfile fix still holds
     against a schema with the extra tables present); `SystemHealthService._query()` and
     `MLStatsService` — the exact classes backing `/api/system-health` and `/api/ml-stats` —
     successfully queried `inactive_symbols`, `ml_predictions`, `ml_prediction_outcomes`, and
     `ml_performance_metrics` directly, with zero errors.
   - **Bot**: DB connection pool initialized successfully against the new schema; correctly failed
     only at the (deliberately invalid) Telegram token step, exactly matching Phase 2's pattern —
     zero real Telegram interaction.
   - **Pipeline**: `daily_data_updater.py --test AAPL` completed successfully end-to-end against the
     new schema (1 real, public, read-only yfinance fetch — same bounded pattern as Phase 2).
   - **Channel-sender**: `send_daily_digest.py` (no `--send`) produced a correct dry-run digest
     against the new schema and explicitly confirmed nothing was sent.
5. **Result: PASS.** The Dockerized application is confirmed compatible with production's real,
   active schema — not just the 36 tracked tables. Test containers removed afterward; nothing
   persisted.

**What Phase 4B's real migration still needs to do that this test didn't** (this test proved
*code* compatibility with the *schema*, not a full data migration): a real `pg_dump`/restore of
production's actual data volume (§7 below), performed only as part of the approved PRE-MIGRATION
checklist, never as a casual test.

### Phase 4A.6 addendum: the 4 tables are now a tracked migration, verified two more ways

Phase 4A.5's test above used an ad hoc `pg_dump --schema-only` extraction, applied once, outside
version control. Phase 4A.6 turned that into `mechanism/add_ml_prediction_tracking_tables.sql` (file
17 in the bootstrap sequence) and re-verified compatibility from that tracked file specifically,
plus two new checks Phase 4A.5 didn't cover:

1. **Fresh-bootstrap result**: a completely empty `postgres:16-alpine` container, all 17 tracked
   files applied in order — result **40 tables + 3 views**, and every structural fact re-verified
   individually against the values captured in `PRODUCTION_SCHEMA_DRIFT.md` (not just "the table
   exists"): all 4 primary keys present, the `ml_prediction_outcomes → ml_predictions` foreign key
   present, the `ml_predictions (symbol, prediction_date)` unique constraint present, all 3
   sequences present and correctly `OWNED BY` their table's `id` column, `trading_user` ownership
   and the full grant set on all 4 tables, and every column default (`nextval(...)`,
   `CURRENT_DATE`, `CURRENT_TIMESTAMP`) matching production exactly.
2. **Idempotency against an already-populated database (simulating production)**: a separate
   disposable database was built with the 4 tables already present (the old Phase 4A.5-style
   extraction), then seeded with real test rows and an advanced sequence value, snapshotted
   (row counts + an MD5 checksum of every row's content), then the new migration file was applied
   against it. Result: every `CREATE TABLE`/`CREATE SEQUENCE` statement logged Postgres's own
   `NOTICE: relation "..." already exists, skipping` — **confirming a true no-op, not merely "ran
   without error."** The post-migration snapshot was **bit-for-bit identical** to the pre-migration
   one (identical MD5 checksums, identical row counts, sequence value unchanged — not reset to 1).
3. **Re-ran the full application compatibility battery** (backend router loading + `pytest
   backend/auth/tests` + direct `SystemHealthService` queries against the *seeded* pre-existing
   data, bot DB init, pipeline `--test` fetch, channel-sender dry run) against this now-reconciled,
   already-populated database — all passed, including reading back the seeded rows through the real
   service classes, proving the reconciliation doesn't just add empty tables but genuinely
   interoperates with data that was already there.

**Explicitly restating the scope of what this proves, per the clarification given during approval:**
none of this means `CREATE TABLE IF NOT EXISTS` validates or reconciles structure against an
existing table — it doesn't, and step 2 above deliberately tested the *actual* no-op behavior
directly rather than assuming it. The reason this migration is trusted to be safe against the real
production database is that its definitions were transcribed from Phase 4A.5's independent,
read-only inspection of production's real structures — the testing above verifies the *migration
file's own mechanics* (idempotency, no data loss, correct fresh-install structure), not that it
somehow "checks" production at apply time.

## 3. Should this project adopt a migration tool (Alembic)?

**Recommendation: yes, eventually — not introduced in this phase.**

The schema-drift finding in §0 is itself the argument: 4 real, load-bearing tables exist in
production with no corresponding tracked migration file. This happened because schema changes have
been applied ad hoc (direct `CREATE TABLE` statements, presumably run once by hand and never
captured as a committed `.sql` file) rather than through a tool that enforces "every schema change
is a tracked, ordered, applied-exactly-once migration." Alembic (or a lighter alternative like
`golang-migrate` or a hand-rolled `schema_migrations` tracking table — already suggested in
`INFRASTRUCTURE_PLAN.md` §6.4) would have caught this drift as it happened, not months later during
a Docker migration.

**Not introduced now because:** adopting a migration tool mid-migration adds a second major change
to an already-careful, staged process, and this project's existing convention (`mechanism/*.sql`
files applied by hand/via `docker-entrypoint-initdb.d`) is functional for what's actually needed
here (connecting to an already-correct production schema, not rebuilding it). **Concrete follow-up
recommendation for after the migration stabilizes:** capture the 6 drifted tables' real DDL via
`pg_dump --schema-only --table=inactive_symbols ...` (read-only, safe to run any time) into a new,
properly-numbered tracked migration file, so the tracked SQL history finally matches reality — then
consider Alembic (or the lighter `schema_migrations` table) for everything going forward, so this
specific class of drift can't recur silently.

## 4. Backup and disaster recovery — the exact gate before migration

Directly extending `DISASTER_RECOVERY.md` §2 (unrevised) into a concrete pre-migration gate. **All
three of the following must pass before migration proceeds — this is a hard gate, not a
recommendation:**

1. **Fresh production backup**: `pg_dump` of the full, live `trading_production` database, taken
   immediately before migration begins (not a stale prior backup).
2. **Off-box copy**: the dump synced to object storage (S3/B2, per `DISASTER_RECOVERY.md` §2),
   confirmed present in the bucket, not just assumed sent.
3. **Verified restore**: the dump restored into a disposable Postgres container (never back into
   production), with the exact table count (42), view count (3), and row counts for a sample of the
   largest tables (`stock_prices`, `technical_indicators` — ~6.4M rows each, per Phase 1's
   measurement) checked against the source. This restore is also, conveniently, the same restore
   §2's compatibility test uses — one restored copy, two purposes.

**Retention going forward** (post-migration, ongoing): 7 daily + 4 weekly local, synced off-box
nightly, encrypted at rest — unchanged from `DISASTER_RECOVERY.md` §2.

**Failure alerting**: the backup job's own success/failure is reported via the same Telegram
owner-DM channel used for other monitoring (§ MONITORING below) — a failed backup must be as loud
as any other production incident, not a silent cron-job failure discovered only when needed.

## 5. Production scheduling — cron vs. systemd timers

**Recommendation: host `cron`.** Evaluated against systemd timers:

| | cron | systemd timers |
|---|---|---|
| Simplicity | A handful of one-line crontab entries, directly matching the six Windows Task Scheduler jobs this replaces (`CURRENT_ARCHITECTURE.md` §8) | Requires a `.service` + `.timer` unit pair per job (12 files for 6 jobs) — more moving parts for identical behavior |
| Reliability at this scale | Sufficient — no missed-run catch-up logic is needed beyond what the application already has (`mechanism/shared/market_calendar.py`'s trading-day gate, unrevised, already handles "did a session complete that this job hasn't processed yet") | Better native logging (`journalctl`) and missed-run handling (`Persistent=true`), but this project's own gate already solves the problem systemd's `Persistent=true` would otherwise be needed for |
| Matches existing project conventions | Yes — the six jobs are already thought of as "Task Scheduler entries," cron is the direct Linux equivalent | A bigger conceptual jump for a solo operator maintaining this |

systemd timers would be worth revisiting only if per-job resource limits (cgroup-based CPU/memory
caps) become necessary — `docker compose run`'s own container-level limits already cover that need
today (per `RESOURCE_REQUIREMENTS.md` §2's resource-cap recommendation for the pipeline), so there's
no forcing function for systemd yet.

### Exact intended schedules (mirroring the real, currently-running Windows Task Scheduler jobs)

| Job | Schedule (Israel time) | Command |
|---|---|---|
| Pipeline (`DonchianScreenerDailyPipeline`) | 02:00 daily | `docker compose -f ... run --rm pipeline` |
| First price safety-net (`FirstLight-1`) | 05:00 daily | Part of the pipeline/channel-sender image, specific update script |
| Digest send (`FirstLight-2`) | 06:00 daily | `docker compose -f ... run --rm channel-sender python mechanism/alerts/send_daily_digest.py --send --to prod` |
| Midday notice (`FirstLight-3`) | 12:00 daily | `docker compose -f ... run --rm channel-sender python mechanism/alerts/send_channel_notices.py ...` |
| Evening notice (`FirstLight-4`) | 20:00 daily | Same script, evening slot |
| Earnings-today post (`FirstLight-5`) | 11:00 daily | `docker compose -f ... run --rm channel-sender python mechanism/alerts/send_earnings_today.py` |

**Not activated in this phase** — this table specifies the crontab entries Phase 4B would install,
not entries that exist anywhere yet.

### Preventing duplicate execution during the migration window

This is the one scheduling risk unique to a migration (not present in steady-state operation): for
some period, the **old environment (the current local/Windows machine) and the new environment (the
VPS) could both be configured to run the same scheduled jobs**, which would mean the digest gets
sent twice, or the pipeline runs twice concurrently against the same database.

**Concrete prevention plan:**
1. The new VPS's cron entries are **installed but commented out / disabled** during the parallel
   verification window (matching `MIGRATION_PLAN.md` Phase 5's own "stand up the new stack fully
   before cutting anything over" principle).
2. The existing `mechanism/shared/market_calendar.py` gate (unrevised — "has a completed session
   been processed by *this job* already," tracked in `data/session_state.json`) is **per-environment
   state**, keyed by file path, not shared between the old and new environments by default. This
   means simply having both environments "on" would NOT be caught by the existing gate — each
   environment would think it's the first to process a given session. **This is the actual
   mechanism that must be respected during cutover, not assumed safe.**
3. Therefore: only ONE environment's scheduler is ever active at a time. The cutover moment (§CUTOVER
   below) is exactly the point where the old Windows Task Scheduler jobs are disabled and the new
   VPS cron entries are enabled — not a gradual overlap.
4. `PROD_SENDING_ENABLED` (the existing production lock, `CURRENT_ARCHITECTURE.md` §6, unrevised)
   provides a second, independent safety net specifically for the one truly irreversible action
   (posting to the real Telegram channel) — kept at `0` on the new environment until the cutover
   moment, exactly mirroring how this same flag already protected the dev→prod channel promotion
   documented in this project's own history.

## 6. Monitoring — the minimum needed before cutover

Directly extending `INFRASTRUCTURE_PLAN.md` §Phase 7 (unrevised) into a pre-cutover checklist:

| Check | Mechanism | Alert channel |
|---|---|---|
| External `/api/health` | An external uptime monitor (UptimeRobot/Better Uptime free tier), polling from *outside* the VPS | Telegram owner DM |
| VPS CPU/RAM/disk | Netdata free tier or the provider's built-in dashboard | Telegram owner DM (disk especially — see `RESOURCE_REQUIREMENTS.md` §0's already-found unbounded `logs/`/`breakout_results/` growth risk) |
| PostgreSQL health | `pg_isready` (already a Compose healthcheck, Phase 2-validated) + a daily scheduled query reusing `mechanism/diagnostic_tools/test_db_connection.py`'s logic | Telegram owner DM |
| Docker container health | Compose healthchecks (Phase 2-validated) + `restart: unless-stopped` | Telegram owner DM only if a container is crash-looping (repeated restarts within a short window), not on every individual restart |
| Backup failure | The backup cron job's own exit code | Telegram owner DM |
| Scheduled pipeline/channel-sender failure | Each job's own exit code, checked by the cron wrapper | Telegram owner DM |

**Avoiding recursive failure dependencies (your explicit concern):** every alert above uses the
Telegram bot's **owner DM** send path (`telegram_client.py`'s `from_env("owner")` target, already
structurally forbidden from ever touching the channel — `CURRENT_ARCHITECTURE.md`'s Telegram Control
Center entry). The risk you're flagging is real: if the bot process itself is down, or the database
the bot reads from is down, "use Telegram to alert about Telegram/DB being down" fails silently.
**Mitigation:** the *external* uptime monitor (first row) is deliberately the one check that does
**not** depend on anything in this system being healthy — it's a third-party service polling a
public HTTPS endpoint from outside, so it's the one alert path guaranteed to still work when
everything else (including the bot's own ability to send a Telegram message) is down. It should be
configured with its own **independent** notification channel (e-mail, or the monitoring service's
own app) as a fallback, precisely so a total-system-down scenario still reaches you through at least
one path that doesn't depend on the system being at least partially alive.

## 7. Production database migration strategy — the exact approach

**Confirmed: `pg_dump` → new Docker PostgreSQL → `pg_restore`, schema preserved exactly, is the
right approach** — not the fresh-bootstrap sequence, per §0/§1/§2 above and
`PRODUCTION_SCHEMA_DRIFT.md`'s findings. Spelled out precisely:

```
pg_dump --format=custom --no-owner --no-privileges trading_production > backup.dump
        │  (custom format: supports parallel restore and selective table/schema restore;
        │   --no-owner/--no-privileges: the NEW database will have its own role setup —
        │   see below — rather than assuming the dump's roles exist identically)
        ▼
pg_restore --create --clean-if-exists (or into a pre-created empty DB) < backup.dump
        │  onto the NEW Postgres instance (VPS-hosted, in Docker, named volume)
        ▼
verify: table count (42), view count (3), row counts for the largest tables
(stock_prices, technical_indicators — ~6.4M rows each) match the source exactly
```

**Why `--no-owner --no-privileges` plus a separate, explicit grant step, not a literal
owner-preserving restore:** the dump's objects are all currently owned by `trading_user` on the
source instance. Rather than assuming the target instance's `trading_user` role was created
identically (same exact `CREATE ROLE` flags, same password-hash algorithm compatibility across
Postgres versions), the safer, more explicit sequence is:
1. On the new instance, `CREATE ROLE trading_user` explicitly (matching the exact role name every
   tracked SQL file and the application's own `DB_USER` default already assume — confirmed to
   already exist in production per `PRODUCTION_SCHEMA_DRIFT.md`'s live check).
2. Restore the schema+data with `--no-owner --no-privileges` (objects land owned by whatever role
   runs the restore — typically the instance's own superuser/`postgres` role).
3. Run an explicit `ALTER TABLE ... OWNER TO trading_user` / `GRANT ALL PRIVILEGES ...` pass across
   every table, sequence, and view — mechanically generated from the exact grant list already
   captured in this session (`PRODUCTION_SCHEMA_DRIFT.md`'s "Cross-cutting observations": every
   table has the identical full grant set, which makes this pass a single, uniform script, not 42
   special cases).

This is more verbose than a straight owner-preserving restore, but it means the target's role setup
is never *assumed* to match the source's — it's asserted and verified as its own explicit step,
consistent with this whole project's "verify, don't assume" pattern established since Phase 1.

**Controlled future migrations, separately:** once the existing database is running in the new
environment, any *new* schema change (including, eventually, the recommended
`add_ml_prediction_tracking_tables.sql` capture from `PRODUCTION_SCHEMA_DRIFT.md`) follows the
project's existing numbered-`.sql`-file convention, applied explicitly — never bundled into the
restore itself.

## 8. Full-scale pipeline resource test plan

**Design only — not run in this phase.** What to measure, and how, before finalizing the VPS spec:

| Metric | How to measure |
|---|---|
| Peak RAM | `docker stats` (or `/usr/bin/time -v` if run outside Docker) sampled continuously through a full pipeline run |
| Peak CPU | Same |
| Execution time | Wall-clock, per pipeline step (the pipeline already logs step boundaries) |
| Database growth | `pg_size_pretty(pg_database_size(...))` before/after |
| Temporary disk usage | `du -sh` on `logs/`, `breakout_results/`, `frontend_data/` before/after (already known to be an unbounded-growth risk — this test also quantifies it) |
| XGBoost training resource usage | Same `docker stats` sampling, isolated to just the ML dataset-rebuild + retrain steps (pipeline steps 10-12) |

**Can this be benchmarked locally before provisioning the VPS? Yes — with one caveat.** The
pipeline's resource consumption (CPU/RAM/disk) is a function of the code and data volume, not the
host it runs on, so measuring it on this local dev machine (or in a local Docker container with
`docker stats` attached, same technique already used throughout Phase 2-4A.5) is valid and safe.
**The caveat:** a full-scale run means real API calls to Tiingo/Alpaca/yfinance for the *entire*
symbol universe (~1,000-2,500 symbols), which is a real cost/rate-limit event, not a bounded 1-3
symbol smoke test like every test performed so far in this project's Docker work. **Recommendation:
don't manufacture a new full-scale run purely for benchmarking — instrument the next few already-
scheduled real 02:00 pipeline runs** (the ones already happening daily on the current environment,
per `CLAUDE.md`'s own documented schedule) with resource monitoring attached, rather than creating
additional, redundant full-universe API load. This gets real numbers with zero marginal cost beyond
attaching a monitor to work that's already happening. **Not performed in this phase** — flagged as
the concrete first action for whoever picks up VPS sizing confirmation, using data from operations
that continue regardless of this migration's timeline.

## CORS — resolved (Phase 4A.5)

The CORS blocker flagged throughout Phase 4A (`backend/main.py` hardcoding four `localhost` origins,
with an already-present-but-unused `ALLOWED_ORIGINS` env var) is now fixed, tested, and merged —
see the Phase 4A.5 report for the full diff, test results (5/5 passing, including an explicit
fail-closed check on `ALLOWED_ORIGINS=*`), and end-to-end Docker stack re-verification. **No longer
a blocker for cutover** — the remaining action is simply *setting* `ALLOWED_ORIGINS` to the real
production frontend domain on the VPS once DNS/Vercel are actually cut over (Phase 4B) — see
"Domain (approved, not yet live)" immediately below for the exact value.

## Domain (approved 2026-09-24, not yet live)

The owner has purchased `first-light.finance` and approved the following production hostnames.
**Nothing below has been acted on** — no DNS record has been created, no Vercel project/domain has
been configured, no Caddy config references a public domain, and no cutover has happened. This is
recorded here so the exact values are ready when PRE-MIGRATION/CUTOVER actually run.

| Component | Hostname | Target |
|---|---|---|
| Frontend/dashboard | `https://dashboard.first-light.finance` | Vercel |
| Backend API | `https://api.first-light.finance` | Hetzner VPS → Caddy → FastAPI (`backend` container) |

At cutover, this means:
- Frontend production env var: `NEXT_PUBLIC_API_BASE_URL=https://api.first-light.finance`
- Backend production `.env`: `ALLOWED_ORIGINS=https://dashboard.first-light.finance`
- Caddy's public-domain config (`docker/Caddyfile.prod`, not yet written — see `docker-compose.prod.yml`'s
  comment) will request a cert for `api.first-light.finance`
- DNS: an A/AAAA record for `api.first-light.finance` → the VPS's public IP; `dashboard.first-light.finance`
  is configured on Vercel's side (CNAME per Vercel's own instructions), not on the VPS

---

## PRE-MIGRATION

- [x] **Repository schema completeness**: the 4 active drifted tables are now a tracked migration
      (`mechanism/add_ml_prediction_tracking_tables.sql`) — **DONE in Phase 4A.6**. A fresh install
      now reproduces production's full active schema (40 tables + 3 views); the 2 confirmed-dead
      tables (`momentum_scores`, `enhanced_ml_training_data`) remain deliberately uncaptured.
- [x] **Compatibility test (schema-only)**: Dockerized backend/bot/pipeline/channel-sender exercised
      against a disposable, schema-metadata-only replica of production, both freshly bootstrapped
      and pre-populated-then-migrated — **DONE in Phase 4A.5 and re-verified in Phase 4A.6, PASS**.
      Still open: the same test against a *real data* restore (§2, §7) — schema compatibility and
      migration-file idempotency are proven, full-data-volume behavior is not yet.
- [ ] **Backup**: fresh `pg_dump` of production taken (§4.1) — not yet performed
- [ ] **Off-box copy**: dump confirmed present in object storage (§4.2)
- [ ] **Restore verification**: dump restored into a disposable Postgres, table/view/row counts
      match source (§4.3)
- [ ] **VPS readiness**: provisioned per `PRODUCTION_INFRASTRUCTURE.md` (OS, Docker, firewall, SSH,
      directory layout) — confirmed reachable, Docker functional, nothing else running on it yet
- [ ] **Images ready**: backend/mechanism/frontend(if self-hosted)/reverse-proxy images built and
      pushed to GHCR with immutable SHA tags (`CD_DESIGN.md` §4) — not yet pulled to the VPS
- [ ] **Secrets ready**: production `.env` manually provisioned on the VPS per `SECRETS_STRATEGY.md`
      §5, `chmod 600`, never committed
- [x] **CORS**: `ALLOWED_ORIGINS` now read from environment (Phase 4A.5) — remaining action is
      setting its value to `https://dashboard.first-light.finance` (approved 2026-09-24, see "Domain"
      above), not a code change
- [ ] **Domain + TLS prerequisite**: `api.first-light.finance` points at the VPS, Caddy can obtain a
      certificate (needed before Vercel's `dashboard.first-light.finance` frontend can call the
      backend without a mixed-content block, per `TARGET_ARCHITECTURE.md` §2's Option C prerequisite)
      — **not yet done**, no DNS record exists yet

## DEPLOYMENT

- [ ] Start infrastructure on the VPS: `postgres`, `reverse-proxy` containers up (Postgres here is
      still **empty** at this point — nothing has connected it to real data yet)
- [ ] **Restore/connect database**: this is the pivotal step, and it has two sub-options depending
      on whether Postgres itself is migrating hosts or staying where it is:
      - **If Postgres stays on its current host** (simplest, matches `RESOURCE_REQUIREMENTS.md` §4's
        recommendation to keep Postgres self-hosted, co-located with the app): the new VPS's backend
        container simply points `DB_HOST` at that existing Postgres instance's real, reachable
        address. No restore happens here at all — this is the "connect directly to the existing
        database" path from §1.
      - **If Postgres itself is moving to the new VPS** (a bigger, separate decision not assumed by
        this runbook): restore the §4 backup into the new VPS's Postgres container, then re-verify
        table/view/row counts one more time on the actual target before anything connects to it.
      Either way: **the 16-file bootstrap script never runs here.**
- [ ] **Backend health verification**: start the backend container pointed at the (now-connected)
      real database; confirm `/api/health` returns 200 with `"status": "healthy"` (not
      `"degraded"`) for every component, specifically re-checking the routers that touch the 6
      untracked tables
- [ ] **Bot verification**: start the bot container with the **real** `TELEGRAM_BOT_TOKEN`; confirm
      it authenticates successfully (`getMe()` succeeds, unlike Phase 2's deliberate dummy-token
      test) and responds correctly to a private test message from the owner account — never posts
      to any channel at this step
- [ ] **Pipeline verification**: run the pipeline manually once (`docker compose run --rm pipeline`)
      against the real database, confirm it completes and the data it writes is sane (spot-check a
      few symbols' latest prices against a known-good source) — do this with `PROD_SENDING_ENABLED`
      still `0`

## CUTOVER

- [ ] **Prevent duplicate schedulers** (§5's plan): disable the old Windows Task Scheduler jobs
      *before* enabling the new VPS cron entries — never both active simultaneously, even briefly
- [ ] **DNS/frontend/API configuration changes**: point `api.first-light.finance`'s DNS at the VPS
      (if not already done during provisioning); deploy the frontend to Vercel with
      `NEXT_PUBLIC_API_BASE_URL=https://api.first-light.finance`; confirm CORS
      (`ALLOWED_ORIGINS=https://dashboard.first-light.finance`, per `CURRENT_ARCHITECTURE.md` §7's
      known gap — must be wired in before this step, not discovered as broken during it)
- [ ] **Flip `PROD_SENDING_ENABLED` to `1`** on the new environment only once every above check has
      passed — this is the actual moment the new environment becomes "live" for the one truly
      irreversible action
- [ ] **Final health checks**: full stack (frontend via Vercel, backend, bot, Postgres) confirmed
      healthy end-to-end from a real browser session, not just curl

## POST-CUTOVER

- [ ] **Monitoring**: confirm every check in the MONITORING section above is actually firing
      (deliberately trigger one test alert per channel to prove the path works, not just that it's
      configured)
- [ ] **Logs**: confirm `logs/` (pipeline) and Docker's own container logs are being written and
      rotated, not silently filling the disk (the already-known unbounded-growth risk from
      `RESOURCE_REQUIREMENTS.md` §0)
- [ ] **Database checks**: re-run the same table/view/row-count verification from PRE-MIGRATION
      against the now-live production connection, confirming nothing changed unexpectedly during
      cutover
- [ ] **Old environment kept available, not decommissioned**: per `MIGRATION_PLAN.md` Phase 5's
      original 48-72h-minimum parallel-verification principle (unrevised) — the old local/Windows
      processes stay installed and stoppable, not deleted, for at least one full trading week
      covering multiple pipeline runs and channel posts before Phase 7 cleanup begins

## ROLLBACK

**Exact conditions that trigger a rollback decision** (not automatic — a human judgment call, per
`CD_DESIGN.md` §6's "no automatic rollback in this first version"):
- The backend's `/api/health` reports `unhealthy` for the database component for longer than the
  connection-pool self-healing window observed in Phase 2 (~2-3 requests, a few seconds) — a
  persistent failure past that point is a real problem, not the known transient blip.
- The bot fails to authenticate with the real token, or posts anything unexpected to a real channel.
- The pipeline's manual verification run (DEPLOYMENT step) produces data that doesn't match a
  known-good spot-check.
- Any data-loss or data-corruption signal at all, however small — this is the one category where
  the bias should be "roll back first, investigate after," never the reverse.

**Exact rollback steps:**
1. **Application-only issue** (backend/bot/pipeline image problem, database itself fine): re-point
   the affected service at `PREVIOUS_GOOD_SHA` (`CD_DESIGN.md` §6's one-line command) — fast,
   low-risk, no data implications.
2. **Cutover-level issue** (something wrong with the new environment as a whole, not just one
   image): revert DNS/`NEXT_PUBLIC_API_BASE_URL` to point back at the old environment, **re-enable**
   the old Windows Task Scheduler jobs, **disable** the new VPS cron entries (the exact inverse of
   the CUTOVER section, performed in the same careful non-overlapping order) — this is why the old
   environment is deliberately kept running and untouched through POST-CUTOVER, not decommissioned
   early.
3. **Database-level issue** (data corruption, an unexpected schema change, anything touching
   correctness of the data itself): **stop.** Do not attempt to "fix forward" on live data. Restore
   the PRE-MIGRATION backup into a fresh instance, verify it, and only then decide — with the
   architecture at the time, not assumed in advance — how to resume. This category is explicitly
   **not** a one-command rollback, because a database rollback is fundamentally different from an
   application rollback (§ CD_DESIGN.md's core caution): the exact recovery path depends on what
   actually went wrong, which can't be scripted in advance.

**What must never happen during a rollback:** running the 16-file bootstrap script "to reset things"
— per §0's finding, this would destroy the 6 untracked-but-real tables' relationship to the rest of
the schema and does not represent a safe reset of anything.

---

## This runbook's migration has not been executed

The three checked boxes above reflect real, completed repository-level work — Phase 4A.5's
schema-only compatibility test and CORS fix, and Phase 4A.6's schema reconciliation (a new tracked
migration file, verified fresh-install and idempotent, application compatibility re-confirmed) —
none of which touched production data or required a VPS. Every other checkbox is still unchecked.
No backup was taken for migration purposes (Phase 4A.5's read-only schema inspection queried
table/view/role metadata and row *counts* only; Phase 4A.6's migration content was derived from
that same metadata, not from any new production read). No VPS exists. Nothing was deployed or
migrated against the real production database. This document remains the specification for Phase
4B, pending your separate approval.
