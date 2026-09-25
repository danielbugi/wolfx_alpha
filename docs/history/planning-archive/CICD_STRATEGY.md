> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** A pre-execution planning document
> from before the VPS migration/cutover was actually carried out. Preserved for reference/history only.
> For current architecture, see [docs/architecture/](../../architecture/), [docs/operations/](../../operations/),
> [CLAUDE.md](../../../CLAUDE.md), and [../../devops/CUTOVER_PLAN.md](../../devops/CUTOVER_PLAN.md) (the
> one actively-maintained infrastructure doc, kept in place — not archived — while Gate 5 remains
> outstanding). Moved here 2026-09-25; content below is unmodified except for this banner.

# CI/CD Strategy

> Companion to [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md). Design only — no workflow files
> exist yet; see [MIGRATION_PLAN.md](MIGRATION_PLAN.md) for when/how to introduce them.

## 1. Pipeline shape

```
GitHub (push / PR to main)
        │
        ▼
GitHub Actions — "detect changed paths" job
        │
        ├── frontend/**            changed? ──► frontend CI job
        ├── backend/**              changed? ──► backend CI job
        ├── mechanism/**            changed? ──► mechanism/bot CI job
        ├── ml_training/**          changed? ──► ml_training CI job (unit tests only — never trains for real in CI)
        │
        ▼ (each relevant job, in parallel)
   Lint + type-check + unit tests (this repo already has ~1,700 pytest cases + tsc + next lint —
   CI's job is to run what already exists, not write new tests)
        │
        ▼ (on main branch only, after tests pass)
   Docker build (only the image(s) whose source changed)
        │
        ▼
   Push to GitHub Container Registry (ghcr.io), tagged with the git SHA + `latest`
        │
        ▼
   SSH deploy step → target VPS
        │
        ▼
   `docker compose pull <service>` + `docker compose up -d <service>` (only that service)
        │
        ▼
   Health check (`GET /api/health`, or the bot's own liveness signal)
        │
        ├── healthy → deployment complete, old image tag recorded for rollback
        └── unhealthy → auto-rollback to previous image tag, workflow fails loudly
```

## 2. Path-based selective build/deploy — mapped to this specific repo

GitHub Actions' `paths:` filters (or `dorny/paths-filter`) applied to this repo's actual layout:

| Path changed | What builds/deploys | What does NOT rebuild |
|---|---|---|
| `frontend/**` | Frontend only. On Option C (recommended), this is just "push to `main`" — Vercel's own GitHub integration handles build+deploy automatically, no Actions job needed at all beyond running `tsc`/`next lint`/`npm run build` as a required PR check. | Backend, bot, pipeline images |
| `backend/**` (+ shared root `.env`-schema changes) | Backend Docker image rebuilt, pushed, deployed | Frontend, bot, pipeline |
| `mechanism/alerts/**`, `mechanism/shared/**` (imported by the bot) | Telegram bot image rebuilt, pushed, deployed | Backend, frontend, pipeline (unless `mechanism/data_updaters`/`screeners` also changed) |
| `mechanism/data_updaters/**`, `mechanism/screeners/**`, `mechanism/orchestrators/**`, `automation_pipeline.sh` | Pipeline image rebuilt, pushed; **not "deployed" in the live-service sense** — the new image is just what the next scheduled `docker compose run` picks up | Backend, frontend, bot (unless they also changed) |
| `ml_training/**` | ML training image rebuilt (same reasoning as pipeline — picked up by the next scheduled retrain, not a live deploy) + its unit tests run in CI immediately regardless of a build | — |
| `mechanism/shared/**` alone (genuinely shared code — `database.py`, `config.py`, `utils.py`, `market_calendar.py`) | **Rebuild every image that imports it**: the pipeline, the bot, and (via `mechanism/ml_enhancement`) the backend's `/ml-stats` inference path. This is the one case that needs an explicit "shared library changed → rebuild N services" rule rather than a clean 1:1 path mapping — implement as a small `paths:` filter that lists `mechanism/shared/**` under *every* affected job, not just one. |
| Root `docker-compose.yml`, `Dockerfile.*` | Rebuild whatever image(s) that specific Dockerfile produces; if `docker-compose.yml` itself changes (new service, new volume), flag for manual review — don't auto-deploy infrastructure topology changes | — |
| `docs/**`, `*.md` only | Nothing builds or deploys | Everything |

This repo's existing structure already makes the mapping mostly mechanical because — per
ARCHITECTURE_AUDIT.md's finding — the four real services (`frontend/`, `backend/`, the bot + pipeline
under `mechanism/`, and `ml_training/`) already live in separate top-level directories with almost no
cross-imports, except the one documented shared-library case above.

## 3. What CI actually runs (mapped to commands that already exist — see CLAUDE.md §8)

| Job | Commands |
|---|---|
| Backend | `python -m pytest backend/auth/tests mechanism/alerts/tests ml_training/tests -q` (already the documented full suite); a lightweight `python -c "import main"`-style import smoke test against the built image |
| Frontend | `npx tsc --noEmit && npx next lint`; `NEXT_DIST_DIR=.next-build npm run build` (isolated build dir, already designed for exactly this in `next.config.ts`) |
| Mechanism/bot | `python -m pytest mechanism/alerts/tests -q`; `python mechanism/alerts/tests/mutation_checks.py` (54 mutants — already a CI-shaped regression gate, just never wired to CI) |
| ML training | `python -m pytest ml_training/tests -q` — **never** run `build_dataset.py --replace` or an actual `momentum_predictor.py` training in CI; that's a scheduled production job, not a test |

**A real gap to close, not just automate:** none of this runs against a live Postgres today except
manually. CI needs its own disposable Postgres (a `services: postgres:` container in the Actions job,
schema loaded from `mechanism/create_trading_schema.sql` + the `add_*.sql` migration files already in
the repo) so the DB-dependent tests (the train/serve parity test, the real-Postgres access round trip
mentioned in CLAUDE.md) actually run in CI instead of self-skipping ("the DB parity test skips itself
if Postgres is unreachable" — today that means it silently never runs in an environment without a
local DB, which is CI's default state unless explicitly provisioned).

## 4. Registry and image versioning

- **GitHub Container Registry (ghcr.io)** — free for a private repo at this scale, already
  authenticated via the same GitHub identity that hosts the code, no separate vendor account needed.
- Tag every image with **both** the git SHA (immutable, used for the actual deploy + rollback) and a
  floating `latest` (convenience only, never used for deploys). The SHA tag is what makes rollback a
  one-line `docker compose up -d` with the previous tag rather than a rebuild.
- Four images total: `ghcr.io/<owner>/donchian-backend`, `donchian-bot`, `donchian-pipeline`, and
  (only if Option A/self-hosted-frontend is ever chosen over Vercel) `donchian-frontend`.

## 5. Deploy mechanism (SSH-based, matching the "keep it simple" priority)

- A single deploy user on the VPS with an SSH key registered as a GitHub Actions secret
  (`DEPLOY_SSH_KEY`), scoped to only what it needs (`docker compose` access in one directory —
  no root, no sudo).
- The deploy step is `ssh deploy@vps "cd /opt/donchian && docker compose pull <service> &&
  docker compose up -d <service>"` — deliberately not a generic `pull && up -d` for *every* service
  on every deploy, so an unrelated bug in one service's image can never accidentally restart (and
  briefly interrupt) a different, unaffected service.
- No Ansible/Terraform/Pulumi — one VPS, one Compose file, doesn't justify a provisioning-as-code tool
  at this scale; the VPS's one-time setup (Docker install, firewall, Compose file, `.env`) is a short,
  documented manual runbook instead (see MIGRATION_PLAN.md Phase 5).

## 6. Health checks as the deploy gate

Each container gets a Compose `healthcheck:`:
- Backend: `GET /api/health` (already exists, already documented as open/no-auth in CLAUDE.md §8).
- Telegram bot: no HTTP endpoint exists today; add a trivial liveness signal (e.g., the bot writes a
  heartbeat timestamp file, or the lock-port `47831` being held and accepting a probe connection is
  itself evidence the process is alive) rather than inventing a full HTTP server for a long-polling
  process.
- Postgres: `pg_isready` (Docker's official Postgres image ships this).
- Pipeline/channel senders: not applicable — they're not long-running services, their "health" is
  their exit code, which the scheduling `cron`/Compose `run` invocation already surfaces.

The GitHub Actions deploy job polls the backend's `/api/health` (and the bot's liveness signal) for
up to N seconds after `docker compose up -d`; a non-200/non-alive result triggers the rollback in
INFRASTRUCTURE_PLAN.md §2 automatically, and the workflow run is marked failed — a human is never
required to notice a bad deploy in real time for it to be caught and reverted.
