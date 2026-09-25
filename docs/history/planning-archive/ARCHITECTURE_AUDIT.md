> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** A pre-execution planning document
> from before the VPS migration/cutover was actually carried out. Preserved for reference/history only.
> For current architecture, see [docs/architecture/](../../architecture/), [docs/operations/](../../operations/),
> [CLAUDE.md](../../../CLAUDE.md), and [../../devops/CUTOVER_PLAN.md](../../devops/CUTOVER_PLAN.md) (the
> one actively-maintained infrastructure doc, kept in place — not archived — while Gate 5 remains
> outstanding). Moved here 2026-09-25; content below is unmodified except for this banner.

# Architecture Audit — Donchian Screening Platform

> Analysis only. No files, infrastructure, or resources were modified, deployed, created, or deleted
> to produce this document. Companion docs: [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md),
> [SERVICE_INVENTORY.md](SERVICE_INVENTORY.md), [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md),
> [CICD_STRATEGY.md](CICD_STRATEGY.md), [INFRASTRUCTURE_PLAN.md](INFRASTRUCTURE_PLAN.md),
> [MIGRATION_PLAN.md](MIGRATION_PLAN.md), [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md).
> Audited 2026-09-22 against the repository at `donchian_screener_0.1`.

## 1. What was actually found on disk

A repo-wide search turned up **no containerization, no CI/CD, and no reverse proxy** anywhere in the
project:

| Looked for | Found |
|---|---|
| `Dockerfile*` | None (only unrelated files inside `.venv`'s vendored packages) |
| `docker-compose*` | None |
| `.github/workflows/*` | None — no GitHub Actions at all |
| `nginx*` / reverse proxy config | None |
| `*.service` (systemd) / `ecosystem.config.js` (PM2) / `Procfile` | None |
| Deploy scripts (`*deploy*`) | None (only an unrelated `ml_training/deployment/` module — model
  *promotion* code, not infra deployment) |
| Scheduling | **Windows Task Scheduler** only, driven by 4 checked-in PowerShell wrapper scripts at
  the repo root (`run_first_light_morning.ps1`, `run_first_light_notice.ps1`,
  `run_earnings_today_post.ps1`, `replay_dev_channel.ps1`) + task names referenced in
  `RUNBOOK_FIRST_LIGHT.md` (`FirstLight-1-UpdatePrices` 05:00, `FirstLight-2-SendDigest` 06:00,
  `FirstLight-3-Notices-Midday` 12:00, `FirstLight-4-Notices-Evening` 20:00, `FirstLight-5-EarningsToday`
  11:00, and a separately-scheduled `DonchianScreenerDailyPipeline` at 02:00) |
| Environment config | One `.env` at repo root, read independently by `mechanism/shared/config.py` and
  `backend/main.py`; `frontend/.env.local` for the Next.js build |

**This means the project is not currently deployed anywhere in a conventional sense.** `.env` has
`DB_HOST=localhost`, `API_HOST=localhost`, `ENVIRONMENT=development`. The backend is started with
`uvicorn main:app --reload`, the frontend with `next dev`, and the Telegram bot with
`python mechanism/alerts/run_bot.py` — all three as manually-started (or Task-Scheduler-started)
foreground/background processes on a single Windows machine, with no process supervisor watching
any of them. `PLATFORM_ARCHITECTURE.md` (written earlier in this project) notes the user has a
separate AWS EC2 instance and discusses hosting *there*, but nothing in this repository currently
targets it — no deployment script, connection string, or CI step references it. **Treat "where this
actually runs today" as an open question to confirm with the user before the migration plan's first
step** (see MIGRATION_PLAN.md Phase 1).

## 2. Dependency map (as-built)

```
                                   ┌───────────────────────────┐
                                   │        End user             │
                                   │  (owner + one collaborator) │
                                   └──────────────┬──────────────┘
                                                  │ HTTPS (browser)
                                                  ▼
                                   ┌───────────────────────────┐
                                   │  Frontend (Next.js, :3001)  │
                                   │  `next dev` / `next build`  │
                                   │  single axios client        │
                                   └──────────────┬──────────────┘
                                                  │ HTTP + JWT bearer
                                                  ▼
                                   ┌───────────────────────────┐
                                   │  Backend API (FastAPI,:8000)│
                                   │  `uvicorn main:app`         │
                                   │  11 routers + inline routes │
                                   └───┬──────────┬─────────┬────┘
                          reads/writes │          │ reads   │ (defense-in-depth
                                       ▼          ▼ JSON     │  token, see below)
                        ┌───────────────────┐ ┌──────────┐  │
                        │ PostgreSQL          │ │frontend_ │  │
                        │ trading_production   │ │data/*.json│  │
                        │ (localhost:5432)    │ │(files)   │  │
                        └─────────┬──────────┘ └────┬─────┘  │
                                  ▲                  ▲        │
              ┌───────────────────┘                  │        │
              │ writes                                │writes  │
              │                                        │        │
┌─────────────┴───────────────────────┐    ┌──────────┴──────┐ │
│ Mechanism pipeline (batch, no server) │    │ ML training      │ │
│ automation_pipeline.sh, 12 steps,     │    │ (manual/nightly, │ │
│ Task Scheduler 02:00                  │    │ builds/scores    │ │
│ → data_updaters → screener → ML       │    │ XGBoost models)  │ │
│ steps 10-12 (retrain+gate check)      │    └──────────────────┘ │
└──────┬────────────────────┬──────────┘                         │
       │                    │                                    │
       ▼                    ▼                                    │
  yfinance /           Alpaca / Tiingo                            │
  Yahoo Finance        (market data vendors)                      │
  (unofficial API)                                                │
                                                                   │
┌──────────────────────────────────────┐   Bot API (long-poll)    │
│ Telegram bot (long-running process)    │◄──────────────────────┘ HTTP calls into
│ run_bot.py (aiogram), lock port 47831  │                         /api/telegram/*,
│ + one-shot channel senders             │   direct SQL            /api/bot-access/*
│ (send_daily_digest.py, etc.)           │───────────────► Postgres (RBAC-gated, Owner only)
└───────────────┬────────────────────────┘
                │ Telegram Bot API (HTTPS out)
                ▼
     Telegram channel(s) + private chats
     (dev chat == prod chat as of 2026-09-22)
```

No Redis, no message queue, no WebSocket layer, and no internal service-to-service RPC exists
anywhere in the code — confirmed by a full-text search of `mechanism/`, `backend/`, and
`frontend/src/` for `redis|celery|rabbitmq|kafka|websocket|socket.io` (zero matches). All
coordination between the batch pipeline, the API, and the Telegram bot happens **through
PostgreSQL and the filesystem** (`frontend_data/*.json`), never direct network calls between them,
except the backend's `/api/telegram/*` and `/api/bot-access/*` routers, which make outbound Telegram
Bot API HTTPS calls and read the bot's own tables under RBAC — the one place where "dashboard" and
"bot" processes are coupled today.

## 3. Biggest architectural risks (see INFRASTRUCTURE_PLAN.md and DISASTER_RECOVERY.md for detail)

1. **Single point of failure, twice over.** One Postgres instance, on `localhost`, is the system of
   record for the screener, the dashboard, the Telegram bot, and every access-control table. There is
   no documented backup job anywhere in the repo (`pg_dump`, WAL archiving, or otherwise). If the disk
   holding `trading_production` fails, every table in CLAUDE.md §5 — years of price history, the ML
   dataset, user accounts, Telegram access grants — is gone with no recovery path.
2. **No process supervision.** `uvicorn --reload`, `next dev`, and `run_bot.py` are all manually
   started (CLAUDE.md's own runbooks describe starting them "in its own terminal window" and
   restarting by hand). A machine reboot, a crashed process, or the documented `--reload` shutdown
   hang (CLAUDE.md §8, "If :8000 is wedged") takes the whole dashboard and/or bot offline until a
   human notices and restarts it.
3. **No CI, so nothing is verified before it reaches whatever is running.** There's a real, if manual,
   test suite (~1,700 pytest cases, `tsc`/`next lint`, mutation checks) but nothing runs it
   automatically on push/PR — a regression only surfaces after a human remembers to run the commands
   in CLAUDE.md §8 themselves, and only if they choose to.
4. **Local-only startup posture is inconsistent with "production."** `ENVIRONMENT=development`,
   `API_HOST=localhost`, `DB_HOST=localhost`, `uvicorn --reload` (auto-restarts on file change — a
   crash/wedge risk already logged in CLAUDE.md) — this is a dev-machine configuration serving what
   the user is now treating as a live product (a public Telegram channel with real subscribers,
   auth-gated dashboard with an Owner/Collaborator). CORS also hardcodes `localhost` origins in
   `backend/main.py`, meaning a real hosted frontend domain would be rejected until that's changed.
5. **Two independent Python environments that silently diverge** (system Python vs. `backend/.venv`)
   — already caused one real incident (`bcrypt`/`PyJWT`/`email-validator` missing on one side, dashboard
   auth broken) documented in CLAUDE.md. A packaging/deployment strategy that doesn't collapse this
   into one environment per service will keep re-triggering the same class of bug.
6. **Two secrets already caused silent security regressions from `load_dotenv()` path resolution** —
   documented twice in CLAUDE.md (`TELEGRAM_CONTROL_TOKEN` and, separately, `SMTP_HOST`) being
   silently shadowed by a stale `backend/.env` or an inherited empty env var. This is a symptom of
   "config file location" being implicit (upward directory search) rather than deployment-managed
   (explicit secret injection per environment) — exactly the class of bug a real CI/CD + secrets
   pipeline eliminates by construction.
7. **No universal secrets manager; `.env` and `frontend/.env.local` are the only source of truth,** and
   at least one stale copy (`backend/.env.stale-superseded-2026-09-22`) already existed on disk
   un-gitignored-by-name until noticed. Rotating a leaked secret today means manually finding and
   editing every place it's read from.
8. **No rollback mechanism for any service.** There are no versioned artifacts (no Docker image tags, no
   release commits, no `CHANGELOG` gating deploys) — "deploying" today means pulling the latest `git`
   state and restarting a process by hand. A bad commit is only undone by another manual `git`
   operation on the same machine that's serving traffic.
9. **The scheduled batch pipeline and the always-on Telegram bot compete for the same machine's CPU/
   network with no isolation.** `fundamentals_updater.py` alone runs for ~25 min against the full
   symbol universe; the nightly ML retrain (pipeline steps 10-12, wired in 2026-09-22, not yet verified
   end-to-end — see CLAUDE.md M1) adds a dataset rebuild + two XGBoost trainings on top. If any of this
   saturates CPU/disk I/O on the same box the API and bot run on, dashboard latency and bot
   responsiveness degrade during the run — there is no resource isolation today.
10. **Machine sleep already causes missed/late scheduled jobs** (documented in the user's own memory:
    "PC sleeps overnight, wake timers off on AC ⇒ 05:00/06:00 jobs fire as ~07:00-08:40 catch-ups").
    This is a direct, already-observed consequence of running production-facing scheduled jobs on a
    personal workstation rather than an always-on server.

## 4. What is already good and should be preserved

- **A real, fast, well-organized test suite** (~1,700 pytest cases across `mechanism/alerts/tests`,
  `ml_training/tests`, `backend/auth/tests`) plus mutation-testing checks — this is CI-ready almost
  as-is; the gap is *running it automatically*, not writing it.
- **Clean service boundaries already exist in the code**, even though nothing packages them
  separately: frontend / backend API / batch pipeline / Telegram bot are four genuinely independent
  Python/Node processes today, coupled only through Postgres and the filesystem (see §2). This is the
  hard part of a microservice-style split already done — see SERVICE_INVENTORY.md.
  `PLATFORM_ARCHITECTURE.md` §3 (written by an earlier pass on this project) reached the same
  conclusion independently.
- **A connection-pooling layer already exists** on both the `mechanism/` (`DatabaseManager`,
  `ThreadedConnectionPool` + `asyncpg`) and `backend/` (`_PooledConnection` wrapping the same
  psycopg2 pool pattern) sides — a real, tuned piece of infra work, not something to rebuild.
- **A defense-in-depth auth model already exists** for the highest-risk mutation surface (Telegram
  channel posts): RBAC (`Owner`/`Collaborator`) *and* an independent `TELEGRAM_CONTROL_TOKEN`. Any
  future secrets-manager migration should keep both, not collapse them into one.
- **A production lock already exists and is honored live**: `PROD_SENDING_ENABLED` gates the one
  genuinely irreversible action in the whole system (posting to the public Telegram channel), checked
  at call time, not just at startup. This is exactly the kind of "a bad deploy can't do the
  irreversible thing" pattern that should generalize into the CI/CD rollout gate.

## 5. Bottom line

There is no existing infrastructure to migrate *away from* in the DevOps sense — there's a
well-tested, well-separated codebase running as unmanaged local processes on a workstation, without a
network name, without TLS, and without repeatable deploys. The task is closer to **"stand up
infrastructure for the first time"** than **"replatform an existing production system."** That's the
good news: there's no live traffic-serving system to avoid breaking, no existing container/orchestrator
choice to unwind, and no legacy CI to migrate off of. See TARGET_ARCHITECTURE.md for the recommended
shape and MIGRATION_PLAN.md for the path from here to there.
