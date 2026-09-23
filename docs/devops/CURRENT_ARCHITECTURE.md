# Current Architecture (As-Built)

> Detailed companion to [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md). Everything here was confirmed
> by reading the actual repository (env keys, requirements files, `main.py`, `next.config.ts`,
> scheduler scripts) on 2026-09-22, not inferred from documentation alone.

## 1. Applications and processes

| # | Process | Language/Runtime | How it's started today | Long-running? |
|---|---|---|---|---|
| 1 | Frontend (Next.js dashboard) | Node.js / TypeScript, Next.js 15 App Router | `npm run dev` (`next dev`) manually, in its own terminal | Yes |
| 2 | Backend API | Python 3.11, FastAPI + Uvicorn | `cd backend && uvicorn main:app --reload --port 8000` manually | Yes |
| 3 | Telegram bot (private assistant) | Python 3.11, aiogram 3 (long polling) | `python mechanism/alerts/run_bot.py` manually / background | Yes (guarded by a localhost lock port, 47831, against a second instance) |
| 4 | Mechanism pipeline | Python 3.11, bash orchestrator | `automation_pipeline.sh`, invoked by Windows Task Scheduler (`DonchianScreenerDailyPipeline`, 02:00 Israel time) | No — batch job, runs ~4h incl. ML steps, then exits |
| 5 | Channel/notice senders (First Light) | Python 3.11, standalone CLIs | 5 separate Windows Task Scheduler jobs (`FirstLight-1..5`) invoking `.ps1` wrappers around Python scripts | No — each is a short-lived CLI invocation |
| 6 | ML training (manual) | Python 3.11 | Manual CLI (`ml_training/scripts/ml_pipeline_runner.py`); also now folded into steps 10-12 of the pipeline | No |

No Docker, no VM/container orchestration, no process manager (systemd/PM2/supervisor) manages any of
these — they are plain OS processes. All run on a single Windows 10 Home machine as observed in this
session's environment; a separate AWS EC2 instance is mentioned in `PLATFORM_ARCHITECTURE.md` as
under consideration for hosting, but `.env`'s `DB_HOST=localhost` / `API_HOST=localhost` /
`ENVIRONMENT=development` confirm nothing in this checkout currently targets it.

## 2. Ports

| Port | Bound by | Purpose |
|---|---|---|
| 3000 | *(not this project — confirmed an unrelated local Ollama app)* | — |
| 3001 | Next.js dev server | Dashboard UI |
| 8000 | Uvicorn (FastAPI) | Backend API (`API_HOST`/`API_PORT` from `.env`) |
| 5432 | PostgreSQL | `trading_production` database |
| 47831 | Telegram bot's own lock socket | Prevents a second `run_bot.py` instance (not a real service port) |

Telegram bot connectivity is **outbound-only** (long-polling to Telegram's servers) — it does not
listen on any inbound port for bot traffic.

## 3. Database

Single PostgreSQL instance, database `trading_production`, `localhost:5432`. Every process above
connects to the same instance directly (no read replica, no PgBouncer/connection-proxy layer beyond
each process's own in-process pool). Two independent connection-pool implementations exist:

- `mechanism/shared/database.py` — `DatabaseManager`, sync `psycopg2.ThreadedConnectionPool` +
  optional async `asyncpg` pool, used by the pipeline, screener, and Telegram bot's own DB access.
- `backend/main.py` — a separate `ThreadedConnectionPool` wrapped by `_PooledConnection`
  (`DB_POOL_MIN`/`DB_POOL_MAX`, default 2/30), used only by the FastAPI process.

~30+ tables (full list in `CLAUDE.md` §5), spanning: price/technical history (`stock_prices`,
`technical_indicators`, weekly/monthly variants), fundamentals, the ML dataset
(`ml_breakout_dataset_v2`, ~595k rows), dashboard auth (`dashboard_users`, `dashboard_sessions`, …),
and the entire Telegram product surface (bot access/invites/audit, digest snapshots, watchlists,
portfolios, message ledger). **No caching layer (Redis or otherwise) sits in front of Postgres
anywhere** — the backend's only caching is an in-process, in-memory TTL cache inside `main.py`/
various `services/*.py` (documented 2-15 min TTLs), which is lost on every restart and cannot be
shared across multiple backend instances.

## 4. Data flow

```
Yahoo Finance / Alpaca / Tiingo (external market-data vendors)
        │  mechanism/data_updaters/*.py  (daily/weekly/monthly price, fundamentals, earnings calendar,
        │  index/sector snapshots — 12 pipeline steps total)
        ▼
PostgreSQL (trading_production)
        │
        │  mechanism/screeners/multi_timeframe_screener.py
        │  + mechanism/ml_enhancement/ml_signal_enhancer.py (XGBoost inference)
        ▼
frontend_data/*.json  (files, written by the pipeline)         PostgreSQL (same instance)
        │                                                              │
        └──────────────────────┬───────────────────────────────────────┘
                                ▼
                  backend/main.py (FastAPI, :8000)
                  — reads the JSON files directly off disk
                  — reads/writes Postgres directly (psycopg2)
                  — 11 routers + inline /api/dashboard/* routes
                  — JWT auth (Owner/Collaborator) on every route except /api/health
                                │  HTTP + JWT bearer token
                                ▼
                  frontend (Next.js, :3001) → browser
```

In parallel, entirely decoupled from the request/response path above:

```
Telegram bot (run_bot.py, long polling)  ◄──── owns its own Postgres tables directly
        │
        ▼
Telegram Bot API (outbound HTTPS) → private chats + the public channel
```

The backend and the bot are coupled in exactly one direction: `backend/routers/telegram_control.py`
and `backend/routers/bot_access.py` make outbound Telegram Bot API calls and read/write the bot's
Postgres tables (message ledger, access requests) so the dashboard can manage the channel/bot — the
bot process itself never calls the backend.

## 5. External dependencies

| Dependency | Used by | Criticality | Notes |
|---|---|---|---|
| PostgreSQL | everything | Hard — nothing functions without it | Single instance, no HA |
| Tiingo API | `daily_data_updater.py`, `fundamentals_updater.py` (Dow 30 only) | Hard for daily price ingestion (`DATA_PROVIDER=tiingo` is active) | Paid, $30/mo Power plan |
| Alpaca Markets API | optional price provider, market calendar gate | Soft (yfinance/Tiingo fallback exists) | Free tier |
| Yahoo Finance (`yfinance`) | fallback price provider, all fundamentals outside Dow 30, earnings calendar | Hard for fundamentals/earnings; soft for prices | Unofficial API, known to break without notice (caused a ~10-month outage once — see CLAUDE.md) |
| Telegram Bot API | bot, channel sends, dashboard's Telegram Control Center | Hard for the Telegram product surface only | Single bot token controls dev + prod (same chat as of 2026-09-22) |
| SMTP server | 2FA login codes (`backend/auth/email_service.py`) | Soft — falls back to a server-side log line if `SMTP_HOST` unset | No vendor lock-in (stdlib `smtplib`) |

## 6. Environment variables (names only — see `.env`, never committed with real values)

Grouped by the service that reads them:

- **Database:** `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- **Backend/API:** `API_HOST`, `API_PORT`, `ALLOWED_ORIGINS` (present but *not yet wired into*
  `backend/main.py`'s CORS middleware — it's still hardcoded, see §7), `DEBUG`, `ENVIRONMENT`
- **Auth:** `JWT_SECRET`, `ACCESS_TOKEN_TTL_MINUTES`, `REFRESH_TOKEN_TTL_DAYS`,
  `LOGIN_CODE_TTL_MINUTES`, `MAX_LOGIN_CODE_ATTEMPTS`
- **Email (2FA):** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
- **Data providers:** `DATA_PROVIDER`, `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `TIINGO_API_KEY`,
  `YAHOO_DELAY`, `FUNDAMENTALS_DELAY`, `MAX_RETRIES`
- **Screener tuning:** `DONCHIAN_PERIOD`, `LOOKFORWARD_DAYS`, `MIN_PRICE`, `MAX_PRICE`, `MIN_VOLUME`,
  `MIN_MARKET_CAP`, `MIN_QUALITY_SCORE`, `VOLUME_SPIKE_THRESHOLD`
- **Telegram:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_DEV_CHAT_ID`,
  `TELEGRAM_CONTROL_TOKEN`, `BOT_OWNER_ID`, `PROD_SENDING_ENABLED`
- **Alerts/digest:** `ALERTS_TIMEZONE`, `ALERTS_SEND_LOCAL_TIME`, `ALERTS_TOP_N`,
  `ALERTS_MAX_NEWS_LINKS`, `ALERTS_PLAN`, `ALERTS_SKIP_WEEKDAYS`
- **Misc:** `DATA_DIR`

Frontend has its own separate file, `frontend/.env.local` — at minimum `NEXT_PUBLIC_API_BASE_URL`
(required for any production build; `next.config.ts` throws at build time if unset).

Three `.env`-family files exist on disk: the real root `.env`, a stale, gitignored
`backend/.env.stale-superseded-2026-09-22` (superseded, kept only for reference — see
CLAUDE.md's 2026-09-22 changelog entry on the `load_dotenv()` bug it caused), and
`frontend/.env.local`. **There is no secrets manager, no per-environment `.env` convention
(`.env.production`/`.env.staging`), and no encryption at rest for any of these files** — they are
plain text on the local filesystem, excluded from git only by `.gitignore`.

## 7. Known configuration issues relevant to a hosting move

- `backend/main.py`'s CORS middleware **hardcodes** `allow_origins` to four `localhost` ports; the
  `ALLOWED_ORIGINS` env var exists but is unused. A hosted frontend domain will be rejected by CORS
  until this is fixed.
- `uvicorn main:app --reload` is the documented startup command — `--reload` is a *development*
  flag (file-watcher, auto-restart, single worker) and is explicitly linked in CLAUDE.md to a
  reproducible shutdown-hang bug (`--reload`'s reloader-parent + worker model). Not appropriate for
  a stable long-running service.
- `ENVIRONMENT=development` everywhere; nothing in the codebase currently branches on this value for
  security-relevant behavior (e.g., there's no evidence debug endpoints or verbose error responses
  are gated by it) — worth auditing before exposing the API publicly.
- Two independent Python environments (system `python` vs. `backend/.venv`) must be kept in sync by
  hand today (`backend/requirements.txt` must be installed into both — CLAUDE.md flags this as
  already having caused one real bug). A containerized/CI build removes this class of drift by
  construction (one image, one dependency install, one environment).

## 8. Scheduling detail (what actually runs when, per the checked-in scripts)

| Task | Trigger (Israel time) | What it does |
|---|---|---|
| `DonchianScreenerDailyPipeline` | 02:00 | `automation_pipeline.sh` — 12 steps: market index update → daily/weekly/monthly price update → daily fundamentals → sector snapshot → quarterly fundamentals → earnings calendar → screener → ML dataset rebuild + retrain×2 (gate-checked, not auto-promoted) |
| `FirstLight-1-UpdatePrices` | 05:00 | Price safety-net update ahead of the digest |
| `FirstLight-2-SendDigest` | 06:00 | Sends the daily channel digest (photo + text lists) |
| `FirstLight-3-Notices-Midday` | 12:00 | Disclaimer + assistant promo post |
| `FirstLight-4-Notices-Evening` | 20:00 | Same, evening slot |
| `FirstLight-5-EarningsToday` | 11:00 | "N companies report today" post |

All six are independent Windows Task Scheduler entries with no shared orchestration layer and no
retry/alerting if one is missed — a known, already-observed failure mode (machine sleep pushes
morning jobs to fire 1-2.5 hours late; see the user's own project notes referenced in
ARCHITECTURE_AUDIT.md §3.10).
