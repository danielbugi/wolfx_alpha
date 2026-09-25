# Environment Variables

> **Purpose:** what every environment variable actually does, built from a repo-wide grep of
> `os.environ`/`os.getenv`/`process.env` cross-checked against `mechanism/shared/config.py`'s field
> defaults — not copied from an older document. `docker/.env.example` is the runnable version of
> this reference (placeholder values, local Compose validation only); this document is the prose
> explanation.
> **Last verified:** 2026-09-25.

## Classification

- **[secret]** — a real credential. Never committed, never logged, never printed. Lives only in the
  real, untracked `.env` (repo root, host dev) or `/opt/donchian/env/.env` (VPS, chmod 600).
- **[required]** — non-secret but load-bearing: the app behaves differently, often unsafely or
  incorrectly, if unset.
- **[optional]** — has a safe in-code fallback; only needs setting to tune behavior.
- **[dev-only]** — meaningful only for local/build tooling, never read inside a running container.
- **[legacy]** — read by zero current application code; kept out of `docker/.env.example` as of the
  2026-09-25 audit; listed here only so a grep hit for one of these names doesn't cause confusion.

## Database

| Variable | Class | Default | Notes |
|---|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | [required] | `localhost`/`5432`/`trading_production`/`trading_user` | `DB_USER` must be `trading_user` in any fresh bootstrap — `create_trading_schema.sql` ends with a `GRANT ... TO trading_user` assuming that role exists |
| `DB_PASSWORD` | [secret] | `''` | |
| `DB_POOL_MIN`, `DB_POOL_MAX` | [optional] | `8`/`30` | Read only by `backend/main.py`'s own connection pool |
| `DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE` | [optional] | `2`/`10` | Read only by `mechanism/shared/config.py`'s `DatabaseManager` — **a separate pool from the two above**, tuning one has no effect on the other (see [../architecture/DATABASE.md](../architecture/DATABASE.md) §7) |
| `DB_TIMEOUT` | [optional] | `60` (seconds) | `mechanism/shared/config.py` |

## Backend / API

| Variable | Class | Default | Notes |
|---|---|---|---|
| `API_HOST`, `API_PORT` | [required] | `0.0.0.0`/`8000` | |
| `ALLOWED_ORIGINS` | [required] | — | `backend/main.py` fails closed (raises at startup) if this would ever resolve to `*` combined with `allow_credentials=True` |
| `API_TIMEOUT`, `API_RATE_LIMIT` | [optional] | `30`/`100` | `mechanism/shared/config.py` |
| `NEXT_PUBLIC_API_BASE_URL` | [required] for a production frontend build | — | `frontend/next.config.ts` throws in the production build phase if unset; dev falls back to `http://127.0.0.1:8000` (the IPv4 literal deliberately — avoids a measured ~216ms IPv6-then-fallback stall on Windows) |
| `API_SITE_ADDRESS` | [required], production-overlay only | `:80` | Read only by `docker-compose.prod.yml` (Caddy's own site address) — irrelevant to the base `docker-compose.yml` / `docker/.env.example` |

## Auth (`backend/auth/`)

| Variable | Class | Default | Notes |
|---|---|---|---|
| `JWT_SECRET` | [secret] | — | Backend fails to start if unset. The `docker/.env.example` placeholder is explicitly a dev value production never falls back to. |
| `ACCESS_TOKEN_TTL_MINUTES`, `REFRESH_TOKEN_TTL_DAYS`, `LOGIN_CODE_TTL_MINUTES`, `MAX_LOGIN_CODE_ATTEMPTS` | [optional] | `30`/`30`/`10`/`5` | |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | [secret] when set | — | Unset `SMTP_HOST` makes `email_service.py` fall back to logging the 2FA code server-side instead of emailing it — the deliberate local-dev/validation behavior, not a bug |

## Data providers

| Variable | Class | Default | Notes |
|---|---|---|---|
| `DATA_PROVIDER` | [required] | `yfinance` | `yfinance` \| `alpaca` \| `tiingo` — see [../../CLAUDE.md](../../CLAUDE.md) for the coverage/reliability tradeoffs of each |
| `ALPACA_API_KEY`, `ALPACA_API_SECRET` | [secret] when set | — | |
| `TIINGO_API_KEY` | [secret] when set | — | Fundamentals stay yfinance-sourced for ~99% of the universe even with Tiingo active for prices — Tiingo's Fundamentals API is Dow-30-only on the current plan |

## Data update / screening / ML tuning

All [optional], `mechanism/shared/config.py`, sane hardcoded fallbacks:

| Variable | Default |
|---|---|
| `DATA_BATCH_SIZE` | `1000` |
| `MAX_CONCURRENT_UPDATES` | `5` |
| `RATE_LIMIT_DELAY` | `1.0` |
| `MIN_PRICE`, `MAX_PRICE` | `5.0`/`500.0` |
| `MIN_VOLUME` | `100000` |
| `MIN_MARKET_CAP` | `100000000` |
| `MIN_QUALITY_SCORE` | `60.0` |
| `VOLUME_SPIKE_THRESHOLD` | `1.5` |
| `ML_LOOKFORWARD_DAYS` | `10` — **note the real name**: a bare `LOOKFORWARD_DAYS` is not read by any code; that exact mismatch was a real bug in `docker/.env.example` until the 2026-09-25 audit fixed it |
| `ML_MIN_DATA_POINTS` | `50` |

## Trading-day / freshness gate

| Variable | Class | Default | Notes |
|---|---|---|---|
| `MARKET_SETTLE_MINUTES` | **[required]** in spirit, [optional] in code | `120` | Production actually runs on **`30`** — an unset value here silently changes when the pipeline/post-market publisher considers a session "complete." This was the one genuinely production-critical variable missing from `docker/.env.example` before the 2026-09-25 audit fixed it. See [../architecture/SCHEDULING.md](../architecture/SCHEDULING.md). |

## Logging / paths / performance

All [optional], `mechanism/shared/config.py`:

| Variable | Default |
|---|---|
| `LOG_LEVEL` | `INFO` |
| `LOG_FILE_MAX_SIZE` | `10485760` (10MB) |
| `LOG_BACKUP_COUNT` | `5` |
| `DATA_DIR`, `LOGS_DIR`, `REPORTS_DIR`, `FRONTEND_DATA_DIR` | `data`/`logs`/`reports`/`frontend_data` |
| `PERFORMANCE_MONITORING` | `true` |
| `METRIC_COLLECTION_INTERVAL` | `60` |

## Telegram

| Variable | Class | Default | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | [secret] | — | Never logged, never printed |
| `TELEGRAM_CHAT_ID`, `TELEGRAM_DEV_CHAT_ID` | [secret] | — | Prod and dev channel ids |
| `TELEGRAM_BOT_USERNAME` | [optional] | — | Falls back to a live `getMe()` call when blank |
| `TELEGRAM_CONTROL_TOKEN` | [secret] | — | Unset or a placeholder means every Telegram Control Center dashboard mutation is refused — fails closed, by design |
| `TELEGRAM_READ_REQUIRES_TOKEN` | [optional] | off | Opt-in toggle; only relevant once the dashboard API is hosted somewhere other than localhost |
| `BOT_OWNER_ID` | **[required]** for the bot | `0` | Unset means no new user can ever be approved |
| `PROD_SENDING_ENABLED` | **[required]** — THE production-posting safety gate | unset = locked | Read via `mechanism/alerts/telegram_client.py`'s `PROD_SWITCH` constant — see [../architecture/TELEGRAM_PUBLISHING.md](../architecture/TELEGRAM_PUBLISHING.md) §5. Fails closed: unset or anything other than exactly `"1"` means locked. |

## Bot access / rate limits

All [optional], sane fallbacks, read by `mechanism/alerts/run_bot.py` / `backend/services/bot_access_service.py`:

| Variable | Default |
|---|---|
| `BOT_ACCESS_MODE` | `approve` (also: `auto`, `closed`) |
| `BOT_MAX_MEMBERS` | `25` |
| `BOT_MAX_PENDING` | `100` |
| `BOT_MSG_LIMIT` | `120` (messages/hour/user) |
| `BOT_POPUP_LIMIT` | `300` (popup taps/hour/user) |
| `BOT_GROUP_LIMIT` | `5` (messages/minute/group) |
| `BOT_HEAVY_LIMIT` | `30` (news/chart calls/hour/user) |
| `BOT_LOCK_PORT` | `47831` (localhost port refusing a second `run_bot.py` instance) |

## Channel content feature flags

All [optional], read by `mechanism/alerts/send_channel_posts.py` / `channel_control.py`:

| Variable | Default | Notes |
|---|---|---|
| `CHANNEL_BOARD_ENABLED` | on (`1`) | Momentum board post |
| `CHANNEL_NEWS_ENABLED` | off | News-provider licence terms not yet checked |
| `CHANNEL_SCOREBOARD_ENABLED` | off | An unflattering first result — see [../../CLAUDE.md](../../CLAUDE.md) history |

## Alerts / digest

[optional], `mechanism/alerts/`: `ALERTS_TIMEZONE` (`Asia/Jerusalem`), `ALERTS_TOP_N` (`15`),
`ALERTS_MAX_NEWS_LINKS` (`2`), `ALERTS_PLAN` (`balanced`).

## CI/CD

| Variable | Class | Notes |
|---|---|---|
| `IMAGE_TAG` | **[required]**, production-overlay only | `docker-compose.prod.yml` hard-fails (`${IMAGE_TAG:?...}`) if unset — never `:latest` in production. Read by `deploy.sh`'s own `$T`-scoped shell export for backend; by `CURRENT_MECHANISM_SHA` / `mechanism_image_tag.env` for mechanism services (see [../operations/DEPLOYMENT.md](../operations/DEPLOYMENT.md) §2) |

## Frontend / dev-only

| Variable | Class | Notes |
|---|---|---|
| `NODE_ENV` | [dev-only] | Set by Next.js itself for `next build`/production start — not something to hand-set |
| `NEXT_DIST_DIR` | [dev-only] | Only for an isolated local verification build (`NEXT_DIST_DIR=.next-build npm run build`), never read inside a running container |

## Legacy — read by zero current code

Removed from `docker/.env.example` by the 2026-09-25 audit; listed here for completeness only, in
case an older doc or a grep hit references one:

`ALERTS_SEND_LOCAL_TIME`, `DEBUG`, `DONCHIAN_PERIOD`, `ENVIRONMENT`, `FUNDAMENTALS_DELAY`,
`MAX_RETRIES`, `YAHOO_DELAY`, a bare `LOOKFORWARD_DAYS`. `DEBUG`/`ENVIRONMENT` are Flask-era
leftovers this project already removed once from the real `.env` (2026-09-14) that had crept back
into the newer `docker/.env.example` file.

Also confirmed real but **out of the live app's configuration path**: `POSTGRES_DB`,
`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — fallback names read only by
`mechanism/diagnostic_tools/check_database_schema.py` and an archived `ml_training/scripts/backup/`
script when the real `DB_*` vars are unset. Use `DB_*`.

## A note on the `.env.example` / Compose "secret leak" non-issue

`docker compose config` run with `-f docker-compose.prod.yml` will correctly show real secrets from
the real `.env` on the machine it's run on. This is `env_file: !override` working exactly as
designed (production must never silently fall back to a committed placeholder) — **not a leak**.
Confirmed by a clean re-test: the base `docker-compose.yml` alone never resolves any real-`.env`-only
variable; only adding the prod overlay does. Don't re-flag this as a vulnerability without
re-confirming which `-f` files were actually passed to the command that raised the concern.
