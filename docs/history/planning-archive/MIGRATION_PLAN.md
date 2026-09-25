> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** A pre-execution planning document
> from before the VPS migration/cutover was actually carried out. Preserved for reference/history only.
> For current architecture, see [docs/architecture/](../../architecture/), [docs/operations/](../../operations/),
> [CLAUDE.md](../../../CLAUDE.md), and [../../devops/CUTOVER_PLAN.md](../../devops/CUTOVER_PLAN.md) (the
> one actively-maintained infrastructure doc, kept in place — not archived — while Gate 5 remains
> outstanding). Moved here 2026-09-25; content below is unmodified except for this banner.

# Migration Plan — Current State → Target Architecture

> Companion to [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md), [CICD_STRATEGY.md](CICD_STRATEGY.md),
> [INFRASTRUCTURE_PLAN.md](INFRASTRUCTURE_PLAN.md). Because — per ARCHITECTURE_AUDIT.md — there is no
> live production traffic being served by this system today (everything runs on a local Windows
> machine, `ENVIRONMENT=development`), this migration carries **less downtime risk than a typical
> "replatform a live service" migration**. The real risk is the one-time database move and the first
> live send from the relocated Telegram bot, both called out explicitly below.
>
> **Nothing in this document has been executed.** It is a plan for the user to approve before any
> step begins, per the standing instruction for this analysis.

## Before starting: two things to confirm with the user

1. **Where does this actually go?** `PLATFORM_ARCHITECTURE.md` mentions an existing AWS EC2 instance
   the user already pays for, but nothing in this checkout targets it, and this repo's environment
   is Windows-only (Windows 10 Home, PowerShell scripts, Windows Task Scheduler) — the target VPS
   for this plan should be a fresh Linux instance (Ubuntu 22.04/24.04 LTS is the natural choice for
   Docker), either that existing EC2 instance reprovisioned as Linux, or a new one. Confirm before
   Phase 5.
2. **Is the public Telegram channel already live with real subscribers?** CLAUDE.md's own changelog
   says yes (`PROD_SENDING_ENABLED=1`, the former dev channel was promoted to the real public channel
   2026-09-22). This means Phase 5's bot cutover needs the explicit care described there — it is the
   one piece of this migration with a real, external, already-live audience.

## Phase 1 — Inventory and preparation

**What changes:** Nothing in the running system. Deliverables: this document set
(`docs/devops/*.md`), confirmation of the two open questions above, and a concrete resource sizing
decision (VPS tier) based on SERVICE_INVENTORY.md's resource-needs column.

**Risk level:** None — read-only.
**Rollback procedure:** N/A.
**Prerequisites:** None.
**Expected downtime:** None.

## Phase 2 — Containerization and standardization

**What changes:** Write `Dockerfile`s for the backend, the Telegram bot, and the pipeline (three
images — see CICD_STRATEGY.md §4), plus a `docker-compose.yml` that wires them to a Postgres
container and a reverse proxy, **built and tested entirely on the developer's own machine or a
throwaway VPS — never touching the current local-machine setup that's actively running today.**
Concretely:
1. Backend: a slim Python 3.11 image installing `backend/requirements.txt` (this finally forces the
   two-environment drift problem in ARCHITECTURE_AUDIT.md finding #5 to be resolved — one image, one
   dependency set, no more "system Python vs. `backend/.venv`" divergence).
2. Telegram bot: a Python 3.11 image installing `mechanism/requirements.txt`, entrypoint
   `python mechanism/alerts/run_bot.py`.
3. Pipeline: a Python 3.11 image installing `mechanism/requirements.txt` +
   `ml_training/requirements.txt`, entrypoint `automation_pipeline.sh` — built to be *run*, not to
   stay running (`docker compose run --rm pipeline`).
4. Frontend: **only needed if Vercel is rejected** (Option A fallback) — a standard Next.js
   multi-stage Dockerfile. Skip this step under the recommended Option C.
5. Fix the two known config bugs while touching this code anyway (cheap now, harder to remember
   later): wire `ALLOWED_ORIGINS` into `backend/main.py`'s CORS middleware; drop `--reload` from the
   production Uvicorn command.

**Risk level:** Low — all work happens in new files (`Dockerfile*`, `docker-compose.yml`) and is
validated locally; it does not touch `main.py` logic beyond the two config fixes above, both already
covered by the existing test suite.
**Rollback procedure:** These are new files; simply don't merge/use them if something doesn't work.
**Prerequisites:** Docker Desktop (or Docker Engine via WSL2) on the developer's machine for local
testing.
**Expected downtime:** None — the currently-running local processes are untouched throughout.

## Phase 3 — Continuous Integration

**What changes:** Add `.github/workflows/*.yml` implementing CICD_STRATEGY.md's test/lint jobs
(no build/deploy yet — CI only, deliberately split from CD so each half can be verified
independently). Wire the existing ~1,700-test suite, `tsc`, `next lint`, and the mutation checks to
run on every push/PR. Add a disposable Postgres service container to the workflow so DB-dependent
tests stop self-skipping (INFRASTRUCTURE_PLAN.md/CICD_STRATEGY.md §3 gap).

**Risk level:** Low — CI-only changes cannot affect any running service; a broken workflow file just
fails to run, it can't break the app.
**Rollback procedure:** Revert the workflow file commit.
**Prerequisites:** Phase 2's Dockerfiles (so CI can build-test them), a GitHub repo with Actions
enabled (already true for any GitHub-hosted repo).
**Expected downtime:** None.

## Phase 4 — Continuous Deployment

**What changes:** Extend the Phase 3 workflows with the build → push-to-ghcr.io → SSH-deploy →
health-check steps from CICD_STRATEGY.md, but **initially pointed at a throwaway/staging VPS, not the
final production target** — this de-risks the deploy mechanism itself (SSH auth, Compose file
correctness, health-check gating, rollback-on-failure logic) before it's ever used against a
real environment.

**Risk level:** Medium (staging only) — this is where the deploy mechanism is proven to actually
work, including deliberately breaking a health check once to confirm the automatic rollback fires.
**Rollback procedure:** Staging VPS can simply be torn down and rebuilt; no production impact by
design.
**Prerequisites:** Phase 2 + 3 complete; a staging VPS (can be the cheapest tier available, torn down
after this phase); GitHub Actions secrets configured (SSH key, registry auth).
**Expected downtime:** None (staging only).

## Phase 5 — Infrastructure migration (the one phase with real cutover risk)

**What changes, in this order, to minimize downtime and risk:**

1. **Provision the real target VPS** (per the answer to the "where does this go" question above):
   Docker + Docker Compose installed, firewall configured (only 22/SSH, 80/443 open — Postgres's 5432
   never exposed to the public internet), a non-root deploy user with the SSH key from Phase 4.
2. **Stand up Postgres on the VPS first, empty**, and take a `pg_dump` of the current local
   `trading_production` database. Restore it into the new instance. **Verify row counts and a sample
   of recent dates match the source** before proceeding — this is the migration's single highest-value
   checkpoint, since every other service is stateless and re-derivable, but this data (years of price
   history, the ML dataset, every Telegram user's access grant) is not.
3. **Point the pipeline at the new database from the *old* local machine first** (a low-risk dry run:
   run `automation_pipeline.sh` once by hand against the new remote Postgres, confirm it completes and
   writes correctly) before moving the pipeline itself.
4. **Deploy the backend + bot containers to the new VPS**, still with the *old* local processes also
   running in parallel and the *old* DNS/bookmarks still pointing at `localhost` for the developer's
   own testing — i.e., stand up the new stack fully before cutting anything over. Manually verify:
   login flow, `/api/health`, a few screener queries, the Telegram bot responding to `/start` in a
   private test chat (never the public channel at this step).
5. **Cut the frontend over**: point `NEXT_PUBLIC_API_BASE_URL` at the new VPS's HTTPS backend domain,
   deploy to Vercel (Option C) or the VPS (Option A fallback). This is the first user-facing change —
   test as the Owner and the Collaborator account before telling anyone else.
6. **Cut the Telegram bot over last, and only after Phase 5.4's private-chat verification**: stop the
   old local `run_bot.py` process (it holds the long-poll — Telegram only delivers updates to one
   poller at a time, so there is a natural, safe handoff point: stop the old one, start the new one,
   no dual-delivery risk). Re-point the scheduled channel-sender jobs (currently Windows Task
   Scheduler) to run as `cron` on the new VPS, **starting with `--dry-run`/preview mode for one full
   day cycle before the first real `--send --to prod`** against the live public channel, exactly the
   same caution CLAUDE.md's own "dev-first" workflow rule already establishes for this specific bot.
7. **Decommission the old local processes** only after 48-72h of the new VPS stack running correctly
   in parallel-verified mode (i.e., don't delete the fallback until the new setup has proven itself
   across at least one full daily pipeline cycle + one full channel-post cycle).

**Risk level:** **High** — this is the only phase touching real, irreplaceable data (the database
copy) and the only phase with a live, real-audience side effect (the Telegram channel). Sequenced
deliberately last-and-most-cautious for exactly that reason.
**Rollback procedure:** Because the old local stack is left running (not decommissioned) until step
7, rollback at any point in steps 1-6 is simply "stop using the new VPS, resume pointing at the local
stack" — no data was ever exclusively on the new side until step 7. For step 7 itself, the rollback is
"restart the local processes from source, they were never deleted, only stopped" plus (if the schema
diverged in the interim) a restore from the Phase 5.2 backup.
**Prerequisites:** Phases 1-4 complete and verified on staging.
**Expected downtime:** **Near-zero for the dashboard/API** (parallel cutover, DNS/URL change is the
only user-visible moment, instantaneous). **Near-zero for the Telegram bot** (long-poll handoff is a
stop/start, seconds of gap at most, users see no visible outage since it's not a webhook). The
**pipeline's nightly run** is the one piece where a short gap is acceptable and expected — skip one
scheduled run during the cutover window rather than trying to run it exactly once across two
environments simultaneously.

## Phase 6 — Monitoring and backups

**What changes:** Everything in INFRASTRUCTURE_PLAN.md Phase 7 (uptime monitor, disk/CPU monitoring,
Telegram-based alerting) and the backup job from DISASTER_RECOVERY.md, both stood up on the new VPS
**before** Phase 5.7's decommissioning, so the new environment is never left unmonitored even briefly.

**Risk level:** Low — purely additive (monitoring agents, a cron backup job); nothing here can break
the running application.
**Rollback procedure:** Remove the monitoring agent/cron job; no application impact either way.
**Prerequisites:** Phase 5 substantially complete (something real to monitor).
**Expected downtime:** None.

## Phase 7 — Cleanup of old infrastructure

**What changes:** Decommission the local Windows processes and Task Scheduler entries (Phase 5.7,
performed only after the 48-72h parallel-verification window), archive the local `.venv` and
`backend/.venv` (no longer authoritative once the containers are the source of truth), and update
CLAUDE.md/HANDOFF.md to point at the new runbooks (`docker compose logs`, `docker compose up -d`)
instead of the current "start `uvicorn --reload` in its own terminal" instructions — this project's
own documentation discipline (a living CLAUDE.md, updated every session) makes this an expected,
low-risk step, not an afterthought.

**Risk level:** Low, given the staged rollback safety net from Phase 5 means nothing here is
time-pressured — this phase should not start until the developer is genuinely confident, not on a
deadline.
**Rollback procedure:** N/A by this point — if something were still wrong, Phase 7 simply doesn't
start yet.
**Prerequisites:** A full confidence window post-Phase 5 (recommend at least one full trading week,
covering multiple pipeline runs and channel posts, not just the 48-72h minimum).
**Expected downtime:** None.
