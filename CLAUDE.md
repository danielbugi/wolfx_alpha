# CLAUDE.md — First Light / Donchian Breakout Screening Platform

> **Navigation + critical context + safety invariants — not an encyclopedia.** Detailed architecture
> lives under `docs/`; this file tells you what exists, where to find it, and what must never break.
> Update this file when architecture, data flow, or safety rules genuinely change — not as a running
> diary. Historical/point-in-time content belongs in `docs/history/`, not here.

## What this is

A daily quantitative screening platform that scans ~2,900–3,100 US equities for Donchian channel
breakouts, enriches signals with fundamentals and an XGBoost ML layer, stores everything in
PostgreSQL, and serves it through a FastAPI backend + Next.js dashboard. A companion subsystem —
**First Light** — publishes a daily post-market summary to a public Telegram channel and runs a
private, invite-only Telegram assistant bot.

## Production architecture, at a glance

```
User -> dashboard.first-light.finance (Vercel) -> api.first-light.finance (Caddy, VPS)
      -> FastAPI backend -> PostgreSQL trading_production (VPS-internal only)
```

**The Hetzner VPS (`116.203.220.219`) is the sole, authoritative production environment** — Postgres,
backend, Telegram bot, and every scheduled job, all Docker containers, scheduled by systemd timers.

**The Windows PC that used to be production is now a frozen development/reference environment.** Its
database was migrated to the VPS and has not been written to since; its 6 Task Scheduler jobs and its
`run_bot.py` are deliberately `Disabled`/stopped. **Never re-enable a Windows writer while the VPS is
active** — see "Safety invariants" below.

Full picture, diagrams, and the 5 independent execution paths:
**[docs/architecture/SYSTEM_OVERVIEW.md](docs/architecture/SYSTEM_OVERVIEW.md)**.

## Documentation map — read this before searching for something yourself

| Doc | Covers |
|---|---|
| [docs/architecture/SYSTEM_OVERVIEW.md](docs/architecture/SYSTEM_OVERVIEW.md) | The primary technical map — production architecture, the 5 execution paths, component ownership, **"where should new work go."** Start here. |
| [docs/architecture/DATABASE.md](docs/architecture/DATABASE.md) | Schema by functional domain, migration model, `telegram_post_delivery` in depth. |
| [docs/architecture/TELEGRAM_PUBLISHING.md](docs/architecture/TELEGRAM_PUBLISHING.md) | The post-market package flow, idempotency contract, and how it differs from earnings/notices/the bot. |
| [docs/architecture/SCHEDULING.md](docs/architecture/SCHEDULING.md) | Every production systemd timer, verified against tracked unit files. |
| [docs/architecture/CI_CD.md](docs/architecture/CI_CD.md) | Commit → CI → manual CD → running container, for backend/mechanism/frontend. |
| [docs/architecture/CODEBASE_AUDIT.md](docs/architecture/CODEBASE_AUDIT.md) | Executable architecture map: entrypoints, module classification (ACTIVE/LEGACY/DEAD with evidence), DB ownership, test coverage gaps, AI-reachability findings, a ranked (unexecuted) cleanup plan. Read before assuming a file is dead or a feature is untested. |
| [docs/operations/DEPLOYMENT.md](docs/operations/DEPLOYMENT.md) | Practical deploy steps for each subsystem + migrations. |
| [docs/operations/BACKUPS.md](docs/operations/BACKUPS.md) | What's backed up automatically, what isn't yet (off-box redundancy — still open). |
| [docs/operations/ROLLBACK.md](docs/operations/ROLLBACK.md) | Failure-scenario decision framework; why Windows isn't a safe fallback anymore. |
| [docs/dev/LOCAL_SETUP.md](docs/dev/LOCAL_SETUP.md) | Running this locally — the Windows-vs-VPS boundary stated up front. |
| [docs/dev/TESTING.md](docs/dev/TESTING.md) | What each test suite covers, and what's genuinely undertested. |
| [docs/dev/ENVIRONMENT.md](docs/dev/ENVIRONMENT.md) | Every environment variable, classified, with real defaults. |
| [docs/devops/CUTOVER_PLAN.md](docs/devops/CUTOVER_PLAN.md) | The one actively-maintained migration/cutover doc — current until Gate 5. |
| `docs/history/` | Point-in-time reports and superseded planning docs — banner-marked, not current architecture. Still useful for "why," never for "how it works today." |

## Repository structure

| Path | What it is |
|---|---|
| `mechanism/` | The data pipeline: updaters, the screener, `mechanism/alerts/` (everything Telegram). |
| `ml_training/` | Model **training** — separate from `mechanism/ml_enhancement/` (live inference only). |
| `backend/` | FastAPI app: `main.py` + `routers/`/`services/` pairs + `auth/`. |
| `frontend/` | Next.js dashboard, deployed to Vercel. |
| `deploy/vps/` | Deploy/rollback scripts, the firewall unit, and the tracked systemd unit/timer files. |
| `deploy/db/` | Backup/restore tooling. |
| `.github/workflows/` | `ci.yml` (auto) and `cd.yml` (manual only). |

**Known legacy/dead paths — do not build on these**: `mechanism/screeners/donchian_screener.py` /
`ml_donchian_screener.py` (superseded by `multi_timeframe_screener.py`, the only screener actually
used); any `*_backup.py`/`*_old.py` file in `mechanism/`/`ml_training/`; `backend/routers/dashboard.py`
and `market_data.py` (both 0 bytes, unimported); `ml_training/data_preparation/momentum_labeler.py`,
`feature_builder.py`, `ml_training/deployment/ml_integration.py` (retired);
`mechanism/orchestrators/master_automation_runner.py` (unwired); `mechanism/data/`, `frontend_data/`,
`breakout_results/`, `reports/`, `logs/` under `mechanism/` (stale duplicates of the root-level dirs
the pipeline actually writes to). `backups/` and `SKILLS/` at the repo root are inert — leave alone.

## Safety invariants — these hold regardless of the task

- **VPS `trading_production` is the only authoritative database.** Never assume Windows reflects
  current reality; never enable a Windows writer while the VPS is active.
- **`PROD_SENDING_ENABLED` gates all real Telegram channel sends** — never bypass it casually.
- **Telegram publishing must stay idempotent** — use `post_delivery.py`'s atomic claim for any new
  send path; never check-then-send.
- **The post-market package never depends on the heavy pipeline stages** (weekly/monthly/
  fundamentals/quarterly/screener/ML) — don't add that dependency without a specific reason.
- **Market session must be explicit**, never inferred from wall-clock date, anywhere rollover
  ambiguity is possible.
- **Database migrations are additive/forward-safe only** (`IF NOT EXISTS`), applied to production
  manually, verified by a real query afterward.
- **Never print, log, commit, or expose a real secret value.**
- **Verify production state directly** (`systemctl`, `docker ps`, a real query) before acting on it —
  never infer it solely from documentation, including this one.
- **A deploy is a distinct, later step from a commit** — production changes need tests + CI green
  first, and "committed" never implies "deployed."

Full reasoning and scenario-by-scenario detail: [docs/operations/ROLLBACK.md](docs/operations/ROLLBACK.md).

## Where should new work go?

| Building… | Goes in… |
|---|---|
| A dashboard API endpoint | `backend/routers/` + `backend/services/` |
| A dashboard UI page/component | `frontend/src/app/` or `frontend/src/components/` |
| A price/fundamentals data source or updater | `mechanism/data_updaters/` |
| Breakout detection changes | `mechanism/screeners/multi_timeframe_screener.py` |
| A new Telegram channel post / post-market package change | `mechanism/alerts/` — read [TELEGRAM_PUBLISHING.md](docs/architecture/TELEGRAM_PUBLISHING.md) first |
| A new ML feature or training target | `ml_training/features/` + `ml_training/models/momentum_predictor.py` |
| Wiring a model into live scoring | `mechanism/ml_enhancement/ml_signal_enhancer.py` |
| A database schema change | A new migration in `mechanism/`, added to `docker-compose.yml`'s init-mount list — see [DATABASE.md](docs/architecture/DATABASE.md) |
| A new scheduled job | A systemd unit, installed on the VPS **and** committed to `deploy/vps/` — see [SCHEDULING.md](docs/architecture/SCHEDULING.md) |
| Deployment/infrastructure | `deploy/`, `docker-compose*.yml`, `.github/workflows/cd.yml` — see [CI_CD.md](docs/architecture/CI_CD.md) |

## Known open technical debt

Ranked list and full detail: the 2026-09-25 repository consolidation audit. Top items: no required
reviewer on the `production` GitHub Environment (settings-only fix); off-box backups aren't yet at a
genuinely independent third location; `donchian-nightly-backup.timer` hardcodes a UTC offset instead
of the `Asia/Jerusalem` tag every other timer uses; `session_state.json` and `telegram_post_delivery`
are two coexisting idempotency mechanisms (deliberate, not yet unified); a confirmed-dead set of
`*_backup.py` files and two empty backend router stubs are safe to remove on approval.

## Things an agent must not assume

- That the Windows PC is production, or that its database is current.
- That any `docs/devops/*.md` file other than `CUTOVER_PLAN.md`, or anything under `docs/history/`,
  describes current architecture without checking its own banner/status first.
- That `docker compose config` resolving real secrets with the prod overlay is a leak — it's
  `env_file: !override` working as designed; check which `-f` files were passed (see
  [ENVIRONMENT.md](docs/dev/ENVIRONMENT.md)).
- That Market Health is sent via a weekday rotation system — it isn't, as of 2026-09-25.
- That `deploy/vps/donchian-*.{service,timer}` reflects the VPS's current state without re-diffing
  first — it's a point-in-time snapshot, not a live sync.
- That a router/service file having no test means its behavior is unverified in production, or vice
  versa — check for a live-verification report before assuming either way.
- That any historical report's "fixed" claim still holds without checking current code.

## Commands quick reference

```bash
# Health/sanity
python mechanism/shared/database.py            # validates DB connection

# Full pipeline (manual run)
./automation_pipeline.sh            # skips itself on weekends/holidays/an already-done session
./automation_pipeline.sh --force    # run anyway

# Post-market package (manual)
python mechanism/alerts/publish_post_market.py                        # dry run
python mechanism/alerts/publish_post_market.py --send --to prod       # real send, refreshes data first

# Backend / frontend (local dev)
cd backend && uvicorn main:app --reload --port 8000
cd frontend && npm run dev

# Tests — see docs/dev/TESTING.md for the full map
python -m pytest backend/auth/tests backend/tests -q
python -m pytest mechanism/alerts/tests mechanism/data_updaters/tests ml_training/tests -q
cd frontend && npx tsc --noEmit && npx next lint

# Bootstrap the first dashboard account
python backend/scripts/create_user.py --email you@example.com --role owner
```

Deeper commands (deployment, backups, rollback, per-variable reference) live in the linked docs
above, not duplicated here.
