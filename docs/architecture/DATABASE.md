# Database — `trading_production` (PostgreSQL)

> **Purpose:** the schema explained by functional domain, not a raw table dump. For the system-level
> picture, start at [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md).
> **Source of truth:** the 18 migration files at the repo root of `mechanism/` (`create_trading_schema.sql`
> + 17 `add_*`/`widen_*` files), in the exact order `docker-compose.yml`'s numbered
> `docker-entrypoint-initdb.d` mounts apply them. This document is derived from those files and from
> current code — it does not invent anything not present in either.
> **Last verified:** 2026-09-25 (18/18 migrations applied cleanly against a fresh ephemeral Postgres
> as part of the CI fix that same day).

## 1. PostgreSQL is the one authoritative datastore

Database name: `trading_production`. There is no secondary datastore, cache-of-record, or
event log that competes with it — `frontend_data/`/`breakout_results/` are generated JSON output
the screener writes and the backend reads, not a second source of truth; `data/session_state.json`
is a coarse job-completion flag, not application data (see §5).

**Production never auto-bootstraps this schema.** `docker-compose.prod.yml` overrides Postgres's
`volumes:` to drop the numbered init-mounts entirely — a production schema arrives only by
restoring a `pg_dump` (see [../operations/BACKUPS.md](../operations/BACKUPS.md)). The base
`docker-compose.yml` (local dev / CI) *does* auto-bootstrap from the 18 files, which is exactly how
CI's `db-bootstrap-integration` job validates them on every push.

## 2. Migration model

- **Discovery, not a hardcoded list.** `docker-compose.yml`'s `docker-entrypoint-initdb.d` mounts
  (`01_create_trading_schema.sql` … `18_add_telegram_post_delivery_table.sql`) are the single
  ordered source of truth. CI's `db-bootstrap-integration` job (`.github/workflows/ci.yml`) parses
  that same list with `sed` rather than hardcoding a second copy — a hardcoded second list is
  exactly what let this job silently run only 16 of 18 files for a period in 2026-09 until the
  2026-09-25 audit caught and fixed it.
- **Adding a migration**: create `mechanism/add_<thing>_tables.sql` (or `widen_<thing>.sql` for an
  `ALTER`), append it to `docker-compose.yml`'s init-mount list with the next number, and it is
  automatically picked up by CI. Every statement should be idempotent
  (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) so a re-run never fails.
- **Applying to production** is always a manual, deliberate step (`psql -f` against the live DB,
  verified by a real query afterward) — there is no automatic migration-on-deploy. See
  [../operations/DEPLOYMENT.md](../operations/DEPLOYMENT.md).
- **CI validation** goes beyond "did it apply": it asserts a table-count floor (41, as of the newest
  migration), the 3 expected views, a real INSERT/SELECT/DELETE round trip, and — for the two most
  recently added table groups — specific PK/FK/UNIQUE/index/grant assertions (a "captured-drift"
  regression guard for `ml_predictions`/`ml_prediction_outcomes`/`ml_performance_metrics`, and a
  claim/reclaim concurrency test for `telegram_post_delivery`, described in §4 below).

## 3. Schema by functional domain

### Market data
`stock_prices` (OHLCV, one row/symbol/date) · `technical_indicators`, `weekly_technical_indicators`,
`monthly_technical_indicators` (SMA/RSI/MACD/Bollinger/ATR/Donchian, at daily/weekly/monthly
granularity) · `market_index_prices` (S&P 500, Nasdaq, VIX, 10Y yield, gold, crude, DXY, BTC — never
equities) · `sector_performance_daily` (persisted daily sector aggregate) · `price_discontinuities`
(days where the stored series isn't a continuous path — a split-shape jump or a bad bar; ML
inference and the digest both refuse to score across one rather than "repairing" it).

### Fundamentals
`daily_fundamentals` (valuation ratios, sector, composite quality score/grade — refreshed every
trading day, no staleness filter, since these are price-derived) · `quarterly_fundamentals`
(detailed financials, event-driven staleness gate keyed off `earnings_calendar`) · `earnings_calendar`
(report dates, past + upcoming, batch-fetched) · `companies` (sector/industry/exchange master data —
**no migration shows anything writing to it and no FK references it**; likely unused, not confirmed
dead, do not assume it matters without checking a live row count first).

### Screener / ML
`breakouts`, `ml_training_data` (view) — **stale-basis, do not train from either**: the price series
was restated in place by a vendor migration without recomputing these, so their labels/entry prices
disagree with `stock_prices` by a material margin for 2023–2025. `ml_breakout_dataset_v2` is the
live, versioned replacement (built from `stock_prices` only, ~595k rows 2018→) — see
`ml_training/data_preparation/build_dataset.py`. `ml_models` (registry; only rows with a matching
`_meta.json` on disk are actually served — see `mechanism/ml_enhancement/`). `ml_predictions`,
`ml_prediction_outcomes`, `ml_performance_metrics` (live-prediction tracking; added by a migration
that specifically backfills a table set that existed only in production before being tracked — this
is why `ml_predictions` uses an `INTEGER` PK instead of every other table's `BIGSERIAL` convention;
a known, accepted inconsistency, not a bug to "fix" casually). `inactive_symbols` (delisted/removed
from the universe).

### Digest / channel content
`digest_runs` (one row per US session actually sent) · `digest_stocks` (**every** liquid stock's
facts for that session — close, 1-day %, volume ×, range × ATR, distance below the 20-day high,
group, and its rank in each of that day's fact lists — not just the ones that made a published
list). This is the snapshot every downstream consumer reads from instead of recomputing: Momentum
Board, the private assistant's "today's lists," the scoreboard, and the weekly recap. `alerts`
(ledger of the older `send_daily_alerts.py` cards, superseded by the digest for the public channel).

### Telegram delivery
`telegram_messages` — the ledger every real **channel** send/edit/delete/pin passes through,
written automatically by `TelegramClient` (never the bot's private DMs, never a dry run).
`telegram_control_audit` — what the Telegram Control Center dashboard page did (edit/delete,
outcome), never message bodies or tokens. `telegram_post_delivery` — see §4, its own section, since
this is the newest and most safety-critical table in the schema.

### Bot (private assistant)
`bot_users` (Telegram user id, acknowledgement time, watchlist symbols) · `bot_tracked` (current
watchlist/portfolio, one row per user+symbol — supersedes the dead `bot_watchlist`, kept in schema
but never read) · `bot_access`, `bot_invites`, `bot_audit` (who may use the bot, one-time invite
links, admin action audit) · `bot_requests`, `funnel_events` (the request-access flow) ·
`news_items`, `news_fetched`, `bot_chart_cache` (shared per-symbol caches, not per-user) ·
`bot_user_settings` (opt-in morning DM preferences).

### Authentication (dashboard)
`dashboard_users` (Owner + Collaborator roles) · `dashboard_login_challenges` (in-progress
password+2FA logins) · `dashboard_sessions` (revocable refresh tokens, stored hashed) ·
`dashboard_auth_audit`. Entirely separate from the bot's own `bot_access`/`bot_users` — this is who
may use the FastAPI backend and Next.js dashboard, not the Telegram bot.

### Operational / audit state (not financial data)
`data_updates` (a generic per-updater run log) · `telegram_control_audit`, `dashboard_auth_audit`,
`bot_audit` (each subsystem's own audit trail — deliberately not unified into one table, since each
records a different kind of action against a different authorization boundary).

## 4. `telegram_post_delivery` — why it exists

Added 2026-09-25, alongside a full redesign of Telegram publishing (see
[TELEGRAM_PUBLISHING.md](TELEGRAM_PUBLISHING.md) for the full flow). Before this table existed, the
only idempotency mechanism was `data/session_state.json` — a flat file holding one "last processed
session" flag per job key. That was too coarse for the post-market package: if the Daily Digest and
Momentum Board sent successfully but Top Gainers failed, the coarse flag had no way to express
"resend only Top Gainers" — a retry would either resend everything (risking duplicate channel posts)
or nothing (silently dropping the failed post forever).

```sql
CREATE TABLE telegram_post_delivery (
    id             BIGSERIAL    PRIMARY KEY,
    market_session DATE         NOT NULL,   -- the US trading session this post is FOR
    post_kind      VARCHAR(30)  NOT NULL,   -- 'daily_digest' | 'momentum_board' | 'top_gainers' | 'market_health'
    target         VARCHAR(8)   NOT NULL CHECK (target IN ('dev', 'prod')),
    status         VARCHAR(8)   NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'sent', 'failed')),
    message_id     BIGINT,
    claimed_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at        TIMESTAMPTZ,
    attempts       INTEGER      NOT NULL DEFAULT 1,
    last_error     VARCHAR(200),
    UNIQUE (market_session, post_kind, target)
);
```

- **`market_session` is explicit**, not inferred from wall-clock time — it's the US trading
  session's own date, set once by `market_calendar.latest_completed()`. This matters because the
  retry timer runs past midnight Israel time for a session that closed the evening before; deriving
  the "session" from the Israel-local date at send time would create a second, wrong row.
- **The `UNIQUE` constraint IS the concurrency guard.** `post_delivery.claim()` is a single atomic
  `INSERT ... ON CONFLICT (market_session, post_kind, target) DO UPDATE ... WHERE <reclaimable>
  RETURNING id`. It only returns a row (i.e. only succeeds) when: no row exists yet, or the existing
  row is `'failed'` (reclaimable immediately), or a `'reserved'` row has gone stale — its claimant
  crashed without ever calling `mark_sent`/`mark_failed` (reclaimable after `RECLAIM_AFTER_MINUTES`,
  currently 10). It never returns a row when another process's claim is still fresh, and never when
  the post was already delivered. This is verified by a real two-thread race in
  `mechanism/alerts/tests/test_post_delivery.py`, not just asserted.
- **A row is never moved to `'sent'`** except by the process that actually received a successful
  Telegram API response — `mark_failed()` cannot accidentally mark something sent.

## 5. Relationship to `session_state.json`

Two idempotency mechanisms coexist by design, not oversight:

| | `data/session_state.json` | `telegram_post_delivery` |
|---|---|---|
| Granularity | One flag per job key (`pipeline`, `digest:prod`, `posts:prod`, …) | One row per (session, post kind, destination) |
| What it protects | The full pipeline's own "have I processed this session at all" gate; a secondary safety net inside `send_daily_digest.py`/`send_channel_posts.py` | The post-market package's actual per-kind delivery state |
| Partial-failure recovery | No — it's a single boolean per job | Yes — exactly the missing kinds are retried |

This is real, acknowledged architectural overlap (tracked as a P1 cleanup item), not a bug — folding
the coarse flag into the fine-grained table, or documenting the two-tier design as permanent, is a
deliberate future decision, not something to "simplify" incidentally while doing other work.

## 6. Confirmed dead / unused (do not build on these)

- `bot_watchlist` — its own sibling migration's comment says it was superseded by `bot_tracked`;
  never dropped, never read by current code.
- `breakouts`, `ml_training_data` (view) — stale-basis, see §3.
- `companies`, `api_requests`, `data_quality_checks` — likely unused; `api_requests`/
  `data_quality_checks` are `DROP TABLE IF EXISTS`'d by the base schema but never `CREATE`'d by any
  tracked migration (either a genuine leftover from outside migration history, or a vestigial
  defensive drop). Investigate with a live row count before concluding either way.

## 7. Connection pooling (two independent pools — a known naming inconsistency)

`backend/main.py` runs its own `psycopg2.ThreadedConnectionPool` (`DB_POOL_MIN`/`DB_POOL_MAX`,
default 8/30). `mechanism/shared/database.py`'s `DatabaseManager` runs a separate one
(`DB_POOL_MIN_SIZE`/`DB_POOL_MAX_SIZE`, default 2/10) plus an optional `asyncpg` pool. Two different
subsystems, two different variable-name conventions, connecting to the *same* Postgres instance —
tuning one has zero effect on the other. See [ENVIRONMENT.md](../dev/ENVIRONMENT.md).
