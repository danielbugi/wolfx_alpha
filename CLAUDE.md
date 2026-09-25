# CLAUDE.md — First Light / Donchian Breakout Screening Platform

> **Living document, rewritten 2026-09-25** after a full repository consolidation audit. Historical
> changelog entries were moved out of this file — see `docs/history/` (restructuring proposed, not
> yet executed — see the audit report) and `git log` for that detail. This file is kept intentionally
> concise: it describes what the system **is**, not the history of how it got there. Update it when
> architecture, data flow, or safety rules change — not as a running diary.

## 1. What this is

A daily quantitative screening platform that scans ~2,900-3,100 US equities for Donchian channel
breakouts, enriches signals with fundamentals and an XGBoost ML layer, stores everything in
PostgreSQL, and serves it through a FastAPI backend + Next.js dashboard. A companion system —
"First Light" — publishes a daily post-market summary to a public Telegram channel and runs a
private, invite-only Telegram assistant bot.

## 2. Current production architecture — READ THIS FIRST

```
User -> dashboard.first-light.finance (Vercel) -> api.first-light.finance (Caddy, VPS)
      -> FastAPI backend -> PostgreSQL trading_production (VPS-internal only)

Telegram channel  <- publish_post_market.py (post-market package) + send_earnings_today.py + send_channel_notices.py
Telegram bot      <- run_bot.py (private assistant, invite-only, long polling)
```

**The Hetzner VPS (116.203.220.219) is the sole, authoritative production environment.** It runs
Postgres, the FastAPI backend, the Telegram bot, and all scheduled jobs, all as Docker containers
managed by `docker-compose.yml` + `docker-compose.prod.yml`, scheduled by systemd timers (§7).

**The Windows PC that used to be production is now a frozen development/reference environment.**
Its copy of the database was migrated to the VPS and has NOT been written to since. Its 6 Task
Scheduler jobs and its `run_bot.py` process are deliberately `Disabled`/stopped, kept only as a
manual rollback path, not as a warm standby. **Do not re-enable any Windows writer while the VPS is
active — this would immediately create two writers to two diverged databases.**

`docs/devops/CUTOVER_PLAN.md` is the one actively-maintained infrastructure doc — treat it as the
source of truth for migration/cutover state ahead of any other file in `docs/devops/`, most of which
are pre-execution planning documents that were never updated after the work they describe was done.

## 3. Repository structure

| Path | What it is |
|---|---|
| `mechanism/` | The data pipeline: updaters, the screener, ML enhancement, and `mechanism/alerts/` (Telegram). |
| `mechanism/alerts/` | Everything Telegram: digest/board/gainers/health builders, the publisher, the bot, access control. |
| `ml_training/` | Model training pipeline, separate from `mechanism/ml_enhancement/` (which does live inference using the same feature module). |
| `backend/` | FastAPI app: `main.py` (DB pool, CORS, dashboard endpoints inline), `routers/` + `services/` (one pair per feature), `auth/` (JWT + email 2FA). |
| `frontend/` | Next.js App Router dashboard, deployed to Vercel. `services/api.ts` is the single API client. |
| `deploy/vps/` | Backend deploy/rollback scripts + the VPS firewall unit. |
| `deploy/db/` | Backup/restore tooling (production dump, nightly backup, restore-and-verify drill). |
| `.github/workflows/` | `ci.yml` (auto, every push/PR) and `cd.yml` (manual only, `workflow_dispatch`). |
| `docker-compose.yml` / `docker-compose.prod.yml` | Base stack + production overlay (`!override`/`!reset` directives — production never falls back to a placeholder secret and never auto-bootstraps the schema). |

**Known legacy/duplicate paths — do not build on these without checking first**: `mechanism/screeners/donchian_screener.py` and `ml_donchian_screener.py` (superseded by `multi_timeframe_screener.py`, the only screener the pipeline or backend actually uses); any `*_backup.py`/`*_old.py` file anywhere in `mechanism/`/`ml_training/` (all confirmed zero live imports); `backend/routers/dashboard.py` and `market_data.py` (both 0-byte, unimported — dashboard logic lives inline in `main.py`, the real market router is `market.py`); `ml_training/data_preparation/momentum_labeler.py`, `feature_builder.py`, `ml_training/deployment/ml_integration.py` (retired — the live chain is `features/price_features.py` -> `data_preparation/build_dataset.py` -> `models/momentum_predictor.py`); `mechanism/orchestrators/master_automation_runner.py` (not wired into the live pipeline); `mechanism/data/`, `frontend_data/`, `breakout_results/`, `reports/`, `logs/` (stale duplicates of the root-level directories of the same name, which are the ones the pipeline actually writes to).

`backups/` (repo root, ~21,000 files, untracked) and `SKILLS/` (repo root, tracked but inert generic templates) are not part of the working system — leave them alone.

## 4. Source-of-truth rules

- **VPS `trading_production` is authoritative.** Never assume the Windows database reflects current reality — it's frozen at the migration freeze point and diverges further every day the VPS runs.
- **`docs/devops/CUTOVER_PLAN.md`** is the current infrastructure state. Most other `docs/devops/*.md` files are historical planning documents, not current-state descriptions — check a file's own header/status line before trusting it.
- **Verify production state directly before acting on it** (`systemctl`, `docker ps`, a real query) rather than inferring it from any document, including this one. Documentation has been found stale multiple times in this project's history.

## 5. Database (`trading_production`, 18 migrations, `mechanism/*.sql`)

Bootstrap order is fixed by `docker-compose.yml`'s numbered `docker-entrypoint-initdb.d` mounts (01-18). **Production never auto-runs this bootstrap** (`docker-compose.prod.yml` overrides postgres `volumes:` to drop the mounts) — a production schema comes only from restoring a `pg_dump`.

Key tables by category:
- **Market data**: `stock_prices`, `technical_indicators`, `weekly_/monthly_technical_indicators`, `market_index_prices`, `sector_performance_daily`, `price_discontinuities`.
- **Fundamentals**: `daily_fundamentals`, `quarterly_fundamentals`, `earnings_calendar`. (`companies` exists but no migration comment shows anything writing to it or FK-ing to it — likely unused, unconfirmed.)
- **Screener/ML**: `ml_breakout_dataset_v2` (the live, versioned dataset — `breakouts`/`ml_training_data` view are the older, stale-basis predecessors, do not train from them), `ml_models`, `ml_predictions`/`ml_prediction_outcomes`/`ml_performance_metrics`.
- **Digest/channel content**: `digest_runs`, `digest_stocks` (the snapshot Momentum Board, the bot's "today's lists," and the scoreboard all read from — never recomputed live), `alerts`.
- **Telegram delivery**: `telegram_messages` (the ledger every real channel send passes through, written automatically by `TelegramClient`), `telegram_control_audit`, **`telegram_post_delivery`** (the post-market package's per-kind idempotency table — see §8).
- **Bot (private assistant)**: `bot_users`, `bot_tracked` (current watchlist/portfolio — `bot_watchlist` is a confirmed-dead predecessor, never dropped), `bot_access`/`bot_invites`/`bot_audit`, `bot_requests`/`funnel_events`.
- **Auth**: `dashboard_users`, `dashboard_login_challenges`, `dashboard_sessions`, `dashboard_auth_audit` — all secrets stored only as sha256 hashes, never plaintext.

**Two coexisting idempotency mechanisms exist by design, not oversight**: `data/session_state.json` (a flat file, one "last processed session" per job key — still used by the full pipeline and as a secondary safety net by the digest/posts senders) and `telegram_post_delivery` (a real DB table, per-(session, post kind, destination), the primary mechanism for the post-market package specifically). Don't assume they're redundant or safe to merge without checking both call sites.

## 6. Pipeline architecture

`automation_pipeline.sh` — 13 steps: market index + daily price update -> freshness check -> **post-market package publish** -> weekly/monthly/fundamentals/quarterly/screener -> 2x ML retrain -> session mark -> a retry of the post-market publish. Triggered by `donchian-pipeline.timer` on the VPS (23:45 + 01:00 Israel) — **the script's own header comments still describe Windows Task Scheduler; that's stale prose, the systemd timer is the real trigger, verify live state rather than trusting the comment.**

**The post-market package does NOT depend on the heavy steps.** Daily Digest, Momentum Board, Top Gainers, and Market Health each only need `stock_prices` (today) + `market_index_prices` + (for the board) prior sessions' `digest_stocks` — never `technical_indicators`, weekly/monthly tables, the full fundamentals refresh, the screener, or any ML step. This is why the lightweight publisher (§8) can run independently of the full pipeline.

**ML status**: the promotion gate has never let a model into production as of this writing — `ml_predict_momentum()` returns an explicit `no_model`/other reason code rather than a score. This is expected, working-as-designed behavior, not a bug to fix reflexively; check `docs/history/` (once restructured) or the latest candidate report under `ml_training/models/candidates/` before assuming otherwise. `ml_training/features/fundamentals_features.py` exists, is finished and unit-tested, but is **deliberately not yet wired into the live training chain** — don't wire it in without checking whether that's still intentional.

**Data provider**: `DATA_PROVIDER` env var selects yfinance/alpaca/tiingo for price data. Fundamentals stay yfinance-sourced for ~99% of the universe even when Tiingo is active for prices — Tiingo's Fundamentals API is Dow-30-only on the current plan.

## 7. Scheduler architecture (VPS systemd, verify live before trusting this table)

All timers use systemd's native per-timer `Asia/Jerusalem` calendar tag (DST-safe by construction) — except `donchian-nightly-backup.timer`, which predates that convention and hardcodes a UTC offset (a known, low-priority inconsistency).

| Timer | Israel schedule | Sends Telegram? | Updates data? |
|---|---|---|---|
| `donchian-pipeline` | 23:45, 01:00 | Yes (post-market package + retry) | Yes (full pipeline) |
| `donchian-postmarket-retry` | 23:45, then ~every 20 min through 06:00 | Yes (post-market package only) | Yes, but only index+price (never the heavy steps) |
| `donchian-firstlight1-prices` | 05:00 | No (`--snapshot-only`) | Yes (index+price safety net) |
| `donchian-earnings-today` | 10:00 | Yes (own kind) | No |
| `donchian-notice-midday` / `-evening` | 12:00 / 20:00 | Yes (disclaimer+promo, unrelated to market data) | No |
| `donchian-nightly-backup` | ~02:30 | No | No (read-only dump) |

**Unit files are tracked in `deploy/vps/donchian-*.{service,timer}`** — verbatim, SHA-256-verified copies of what's installed at `/etc/systemd/system/` on the VPS (pulled 2026-09-25; see `deploy/vps/README.md`'s "Scheduling" section for the full table and the diff-before-editing workflow). They are a **snapshot, not a live sync** — editing the repo copy does nothing until someone manually re-installs it on the VPS.

## 8. Telegram publishing architecture

```
market_index_updater.py -> daily_data_updater.py -> check_price_freshness.check()
  -> NOT FRESH: exit, publish nothing, the retry timer tries again later
  -> FRESH: publish_post_market.publish(session, target)
       for each of {daily_digest, momentum_board, top_gainers, market_health}:
         post_delivery.claim(db, session, kind, target)   <- one atomic SQL statement
           None -> skip (already sent, or another worker is actively sending it)
           Claim -> build + send -> mark_sent(message_id) OR mark_failed(error) -- never marks 'sent' on failure
```

- **`market_session` is always an explicit `DATE`**, derived from the trading calendar's "latest completed session," never from the Israel-local wall clock at send time — this matters because the retry timer runs past midnight Israel time for a session that closed the evening before.
- **Concurrency safety is a database `UNIQUE(market_session, post_kind, target)` constraint**, not anything enforced in Python — a single `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE <reclaimable>` statement. Never replace this with a check-then-insert pattern.
- **Market Health is one of the four standard daily posts, not part of the weekday rotation** (`channel_content.ROTATION`/`pick_kinds()` — removed 2026-09-25; a test asserts it can never be re-selected there). If you're editing the rotation system, remember Health is intentionally excluded.
- **Earnings-today and the twice-daily notices are fully independent** of the post-market package — separate scripts, separate timers, separate gates.
- `daily_digest` is invoked as a subprocess (`send_daily_digest.py`, unchanged proven code), not refactored in-process — its own internal multi-message send isn't itself sub-divided by the per-kind idempotency table, a small known limitation, not a bug.
- The single `TelegramClient` (`mechanism/alerts/telegram_client.py`) is the only thing allowed to call the real Telegram API — structural tests enforce this. It auto-records every channel send/edit/delete/pin into `telegram_messages`.

## 9. Backend/frontend architecture

Backend: FastAPI, `main.py` does DB pooling + CORS + dashboard endpoints inline, everything else is a `routers/`+`services/` pair, auth is JWT + emailed-2FA (`backend/auth/`). CORS fails closed (raises if `ALLOWED_ORIGINS` would ever include `*` with credentials enabled). Frontend: Next.js App Router on Vercel, single axios client in `services/api.ts` that fails closed (throws at build time if `NEXT_PUBLIC_API_BASE_URL` is missing outside dev).

Test coverage is genuinely thin on the backend (2 real test files; no router has a dedicated test) — do not assume behavior is tested just because the endpoint exists.

## 10. CI/CD workflow

```
push main -> ci.yml (auto): frontend-ci, backend-ci, mechanism-ci, db-bootstrap-integration, docker-validate
  -> [green] -> manual: gh workflow run cd.yml -f service=backend|mechanism -f confirm_ci_passed=yes
  -> build-and-push (GHCR, tag = 12-hex commit SHA)
  -> deploy job (backend: health-gated rollout with auto-rollback; mechanism: deliberate no-op, image-build-only)
```

`cd.yml` is `workflow_dispatch` only — **never** triggers on push. The `production` GitHub Environment currently has **no required reviewer** (only a branch policy) — the workflow's own design assumes one exists; it doesn't yet. Treat any CD dispatch as needing the same care a reviewer gate would otherwise enforce.

Mechanism image deploys are always manual after the build: pull the image on the VPS, pin `/opt/donchian/CURRENT_MECHANISM_SHA`, update the relevant compose/systemd references — there's no automated rollout for bot/pipeline/channel-sender by design (starting the bot needs careful, deliberate handling, never an automatic health-gated swap).

## 11. Environment/configuration rules

- Real secrets live only in `/opt/donchian/env/.env` on the VPS, never in git. `docker/.env.example` is dev-placeholder-only.
- **`docker compose config` run from the repo root with `-f docker-compose.prod.yml` will correctly show real secrets from the real `.env`** — this is `env_file: !override` working exactly as designed (production must never silently fall back to a committed placeholder), not a leak. Confirmed by a clean re-test 2026-09-25: the base compose file alone never resolves any real-`.env`-only variable; only adding the prod overlay does. Don't re-flag this as a vulnerability without re-confirming which files were actually passed to the command.
- `docker/.env.example` is missing ~30 variables that code actually reads (most default safely; `MARKET_SETTLE_MINUTES` is the one production-critical exception — real value is 30, code default is 120).
- Every safety-critical switch fails closed: `PROD_SENDING_ENABLED` unset = locked, `JWT_SECRET` unset = hard startup failure, `TELEGRAM_CONTROL_TOKEN` unset = 403 on every mutation.
- **Never print, log, commit, or expose any real secret value** — when transferring a credential between environments, use length/hash comparisons to verify, never echo the value itself.

## 12. Safety rules for production changes

- **Never enable a Windows writer while the VPS is active.** Check both sides (`systemctl`/`docker ps` on the VPS, `Get-ScheduledTask` on Windows) before touching either.
- **Never bypass `PROD_SENDING_ENABLED` casually** — flipping it to `1` is a real, user-facing production action requiring the same care as any other go-live step.
- **Telegram publishing must stay idempotent.** Any new send path needs to either use `post_delivery.py`'s claim mechanism or have an equally real per-item dedup — never "check if already sent" without an atomic database guard, and never assume a retry is safe without checking.
- **Market session must be explicit, never inferred from wall-clock date**, anywhere rollover ambiguity is possible (a job running after midnight Israel time for a session that closed hours earlier).
- **Heavy pipeline stages (weekly/monthly/fundamentals/quarterly/screener/ML) are not required for post-market publication** — don't add a dependency on them to the publishing path without a specific reason.
- **Database migrations must be forward-safe** (`CREATE TABLE IF NOT EXISTS`, additive `ALTER`s) and applied to production manually, verified by a real query afterward — there is no automatic migration-on-deploy.
- **Do not infer production state solely from documentation.** Verify directly (a real query, a real `systemctl status`, a real HTTP request) before making a change that depends on that state being true — this project's own history includes real cases of documentation being wrong in ways that would have caused real damage if trusted blindly.
- **Production changes need tests + CI green before deployment**, and a deploy is a distinct, later step from a commit — don't conflate "committed" with "deployed."

## 13. Testing requirements

```bash
# Backend (no DB needed)
python -m pytest backend/auth/tests backend/tests -q

# Mechanism + ML (some tests self-skip without a reachable Postgres)
python -m pytest mechanism/alerts/tests mechanism/data_updaters/tests ml_training/tests -q
python mechanism/alerts/tests/mutation_checks.py --check

# Frontend
cd frontend && npx tsc --noEmit && npx next lint && NEXT_DIST_DIR=.next-build npm run build
```

New Telegram-publishing tests must mock/stub the transport (`TelegramClient`) — never send real messages as part of a test run. Tests that need real Postgres (e.g. `post_delivery.py`'s claim/concurrency tests) follow the established pattern: skip cleanly if unreachable, use a unique tag/synthetic date so cleanup can never touch real rows.

## 14. Deployment / rollback rules

Backend: `deploy/vps/deploy.sh` (health-gated, auto-rollback to `PREVIOUS_GOOD_SHA` on failure) via the CD workflow's forced-command SSH path. Mechanism: build via CD, then always a manual pull + pin + reference-swap on the VPS — never automated. Database: no rollback path beyond restoring a `pg_dump`; the Windows database is available as a frozen reference but re-enabling it as a writer is a Gate-5-class decision requiring explicit approval, not a casual fallback.

## 15. Known intentional quirks

- `mechanism/Dockerfile` symlinks `.venv/Scripts/python.exe` to the container's real Python — a deliberate workaround so `automation_pipeline.sh` stays byte-identical between Windows dev and the container, not a mistake.
- `ml_training/models/` mixes trainer source with generated artifacts on disk; the pipeline container mounts it as a **named** Docker volume (auto-seeds from the image on first use), not a bind mount, specifically to avoid shadowing the source — don't "simplify" this back to a bind mount.
- `pipeline`'s compose service has `mem_limit: 5g` — raised from 3g after a real OOM kill during ML training; this is a measured, load-bearing value, not an arbitrary default.
- The frontend Dockerfile and the `frontend` compose service are real but deliberately not part of the default production path (`profiles: ["self-hosted-frontend-fallback"]`) — Vercel serves production traffic.

## 16. Known open risks / technical debt

See the latest repository consolidation audit (published as an artifact / recorded in `docs/history/` once the restructuring in §11 of that audit happens) for the full ranked list. Top items: no required reviewer on the `production` GitHub Environment; off-box backups aren't yet at a genuinely independent third location; `donchian-nightly-backup.timer` still hardcodes a UTC offset instead of the `Asia/Jerusalem` tag every other timer uses, and will drift an hour at the next DST change; ~14 `docs/devops/` planning documents describe a pre-execution state that's now been executed and aren't marked superseded; a confirmed-dead set of `*_backup.py` files and two empty backend router stubs are safe to remove whenever convenient.

## 17. Things an agent MUST NOT assume

- That the Windows PC is production, or that its database is current.
- That any `docs/devops/*.md` file other than `CUTOVER_PLAN.md` describes current infrastructure state without checking its own header/status first.
- That `docker/.env.example` is a complete environment template — it's missing real variables.
- That a `docker compose config` resolving real secrets from the repo root is automatically a leak — check which `-f` files were actually passed first (§11).
- That Market Health is sent via the weekday rotation system — it isn't, as of 2026-09-25.
- That `deploy/vps/donchian-*.{service,timer}` reflects the VPS's current state without re-diffing it first — these are a point-in-time snapshot (2026-09-25), not a live sync; the VPS is still the sole source of truth.
- That a router/service file having no test means its behavior is unverified in production — check for a live-verification report before assuming either way.
- That any historical incident report's "fixed" claim still holds without checking current code — this project has a real history of documentation drifting from reality.

## Commands quick reference

```bash
# Health/sanity
python mechanism/orchestrators/master_automation_runner.py health   # legacy, not wired into the live pipeline — spot-check only
python mechanism/shared/database.py                                  # validates DB connection

# Full pipeline (manual run)
./automation_pipeline.sh            # skips itself on weekends/holidays/an already-done session
./automation_pipeline.sh --force    # run anyway

# Post-market package (manual)
python mechanism/alerts/publish_post_market.py                        # dry run
python mechanism/alerts/publish_post_market.py --send --to prod       # real send, refreshes data first
python mechanism/alerts/publish_post_market.py --send --to prod --skip-update   # assumes data is already fresh

# Backend
cd backend && uvicorn main:app --reload --port 8000

# Frontend
cd frontend && npm run dev

# Bootstrap the first dashboard account
python backend/scripts/create_user.py --email you@example.com --role owner
```
