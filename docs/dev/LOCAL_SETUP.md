# Local Development Setup

> **Purpose:** how to run this system on your own machine for development. Read the boundary below
> before running anything — this document describes a **development/reference environment**, never
> production.
> **Last verified:** 2026-09-25.

## The boundary — read this first

```
LOCAL WINDOWS MACHINE  =  development / reference environment.  NOT production. NOT a warm standby.
VPS (116.203.220.219)  =  the one and only production environment.
```

The Windows PC used to be production before the 2026-09 VPS migration. Its database is now frozen —
migrated to the VPS and never written to since. Its 6 Task Scheduler jobs (`DonchianScreenerDailyPipeline`,
`FirstLight-1` through `FirstLight-5`) and its `run_bot.py` process are deliberately `Disabled`/stopped.

**Do not enable any Windows scheduled job or start `run_bot.py` on Windows while the VPS is active.**
Doing so creates two independent writers against two already-diverged databases, or two Telegram bot
processes fighting over the same long-polling token. If you're working on this repo locally, your
local runs should go against your *own* local/dev database and the *dev* Telegram chat — never
against production, and never in a way that assumes Windows is a safe fallback (see
[../operations/ROLLBACK.md](../operations/ROLLBACK.md) for why that assumption is specifically wrong
today).

## Prerequisites

- Python (the project's own `.venv` — `python -m venv .venv && .venv/Scripts/python.exe -m pip
  install -r backend/requirements.txt -r mechanism/requirements.txt -r ml_training/requirements.txt`)
- Node.js (for `frontend/`)
- PostgreSQL, reachable locally (or via the Docker Compose validation stack — see below)
- Docker + Docker Compose, if you want to validate the containerized stack rather than run services
  directly

## Running the backend

```bash
cd backend
uvicorn main:app --reload --port 8000
curl http://localhost:8000/api/health          # open, no auth needed
```

## Running the frontend

```bash
cd frontend
npm install
npm run dev     # http://localhost:3000 (use `-- -p 3001` if 3000 is taken)
```
Run the dev server in its own terminal, not with stdout piped into something that later closes — a
closed pipe makes every Next worker throw `write EPIPE` and routes return 500. If routes 404/500
with a clean backend, stop the server, `rm -rf frontend/.next`, restart. Open via `localhost`, not
`127.0.0.1` — the backend's CORS list is keyed on `localhost`.

## Running the data pipeline / screener manually

```bash
python mechanism/data_updaters/daily_data_updater.py --test AAPL MSFT GOOGL   # isolated stage test
python mechanism/screeners/multi_timeframe_screener.py                        # full run, ~7 min observed
./automation_pipeline.sh            # full 13-step pipeline; skips itself on non-trading days
./automation_pipeline.sh --force    # run anyway
```

## Running Telegram alerts / the bot — dev chat only

```bash
python mechanism/alerts/send_daily_digest.py                  # dry run, prints only, sends nothing
python mechanism/alerts/send_daily_digest.py --send            # DEV channel only
python mechanism/alerts/publish_post_market.py                 # dry run
python mechanism/alerts/publish_post_market.py --send --to dev # DEV channel only -- never --to prod from a local session without explicit intent
python mechanism/alerts/run_bot.py                              # long polling; needs BOT_OWNER_ID in .env; Ctrl+C to stop
```
`--to prod` is gated by `PROD_SENDING_ENABLED` regardless of where it's run from — but treat that
gate as a last line of defense, not a reason to casually point local testing at production.

## Validating the Docker Compose stack locally

```bash
docker compose --env-file docker/.env.example up -d
docker compose -f docker-compose.yml --env-file docker/.env.example config   # dry validation, no containers
```
`docker/.env.example` is placeholder-only — see [ENVIRONMENT.md](ENVIRONMENT.md) for what every
variable means and which ones need a real value even for local use (none should, for this file).
This only exercises the base `docker-compose.yml`; the production overlay
(`docker-compose.prod.yml`) is not meant to be run locally at all.

## The root `.ps1` wrapper scripts — still useful, no longer production

`run_first_light_morning.ps1`, `run_first_light_notice.ps1`, `run_earnings_today_post.ps1`,
`replay_dev_channel.ps1` at the repo root are the scripts the old Windows Task Scheduler jobs used
to call. No workflow or systemd unit references any of them anymore — production scheduling is
entirely the systemd timers in [../architecture/SCHEDULING.md](../architecture/SCHEDULING.md). They
remain genuinely useful for manual/dev-channel testing on Windows; just don't mistake them for
production infrastructure.

## Where to go next

- [TESTING.md](TESTING.md) — what to run before committing.
- [ENVIRONMENT.md](ENVIRONMENT.md) — every environment variable, what it does, and its classification.
- [../architecture/SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md) — the big picture and
  "where should new work go."
