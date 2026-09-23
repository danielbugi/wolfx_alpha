# Service Inventory & Classification

> Companion to [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md). "Deployment Risk" is about the
> *blast radius of a bad deploy or crash of that specific service today*, not a general security
> rating.

## 1. Classification table

| Service | Purpose | Runtime | Port | Dependencies | Stateful? | Resource Needs | Deployment Risk | Can Run Independently? |
|---|---|---|---|---|---|---|---|---|
| **Frontend** (Next.js) | Dashboard UI: screener, strategy, alerts, stock detail, system health, ML stats, Telegram control | Node.js 20+, Next.js 15 (App Router) | 3001 (dev) | Backend API only (HTTP) | Stateless (all state server-side; only per-viewer `localStorage` conveniences) | Low CPU, low memory (~100-300MB), bursty on build | Low — a bad frontend deploy only breaks the UI, not data or the bot | Yes, entirely — build artifact is static + edge-renderable |
| **Backend API** (FastAPI) | REST API for dashboard: screener, strategy calc, alpha finder, deep-value scan, system health, ML stats, performance, market data, auth, Telegram control | Python 3.11, Uvicorn/ASGI | 8000 | PostgreSQL (hard), `frontend_data/*.json` (hard — no fallback if missing/stale), SMTP (soft), Telegram Bot API (only for `/api/telegram/*`, `/api/bot-access/*`) | Stateless process (all real state in Postgres); has an in-memory TTL cache (2-15 min) that resets on restart — acceptable data loss, not correctness loss | Low-moderate CPU; connection pool (2-30 DB conns); memory grows with cached JSON + loaded XGBoost model for `/ml-stats` | **High** — every dashboard user and the Telegram Control Center panel depend on it; also the only consumer of `ALLOWED_ORIGINS`/CORS and JWT auth, so a bad deploy can lock everyone out | Yes — depends only on Postgres + the JSON files the pipeline last wrote (works on stale data if the pipeline is down) |
| **Telegram bot** (`run_bot.py`) | Private assistant: portfolio/watchlist tracking, stock cards, ATR levels, news, charts, access control (invite/approve/revoke) | Python 3.11, aiogram 3 (long-polling) | none inbound (outbound HTTPS only) + localhost lock port 47831 | PostgreSQL (hard — its own tables), Telegram Bot API (hard), Alpaca news API (soft, degrades to no news) | **Stateful in the sense of holding a live long-poll connection** — a restart drops in-flight `/command` conversations, but all durable state is in Postgres, so no data is lost, only session continuity | Low CPU most of the time; spikes rendering charts (`mplfinance`/Pillow) and on `/scan`/`/screen` queries | Medium — affects only the private assistant surface, not the public channel or the dashboard | Yes — fully independent of the backend API and frontend; only needs Postgres + Telegram |
| **Channel senders** (`send_daily_digest.py`, `send_channel_posts.py`, `send_daily_alerts.py`, `run_earnings_today_post.ps1`, notice senders) | One-shot scripts that post to the public Telegram channel (digest, board, notices, earnings) | Python 3.11, short-lived CLI | none | PostgreSQL (hard, reads), Telegram Bot API (hard), Pillow/matplotlib for chart images | Stateless invocations; coordinate *state* through `data/session_state.json`/`data/notice_state.json` files + the trading-day gate in Postgres-free file cache | Low, but real-time-sensitive (must run near their scheduled minute, and `PROD_SENDING_ENABLED` gates the one irreversible action) | **High per-run, but narrow blast radius** — a bad run posts wrong/duplicate content to a now-public, real-audience channel; cannot corrupt other services' data | Yes, but must run *after* the pipeline (depends on same-session price data being fresh) |
| **Mechanism pipeline** (`automation_pipeline.sh`, `data_updaters/*`, `screeners/*`) | Nightly batch: ingest prices/fundamentals/earnings from vendors, compute Donchian breakouts + multi-timeframe alignment, write `frontend_data/*.json` and Postgres | Python 3.11, Bash orchestrator, `ThreadPoolExecutor` for per-symbol fetch | none | Vendor APIs (Tiingo/Alpaca/yfinance, hard for that step only), PostgreSQL (hard) | Stateless as a process (idempotent day-keyed writes); its *output* (Postgres + JSON) is what's stateful | **Compute + I/O heavy**: ~1,000-2,500 symbols × 5 updaters, ~25-100+ min for the slowest step alone (`fundamentals_updater.py`); full run historically ~1-4h now that ML retrain steps are appended | High if it silently fails partway (stale/half-updated data feeds every downstream reader — screener, dashboard, digest — without any of them necessarily knowing); already has a trading-day gate + per-job "last completed session" state to make re-runs safe | Yes — entirely decoupled from the API/frontend/bot; they degrade gracefully (serve stale data) if this doesn't run |
| **ML training** (`ml_training/*`, folded into pipeline steps 10-12) | Rebuilds `ml_breakout_dataset_v2` (~595k rows) nightly, retrains XGBoost for two targets, evaluates against a promotion gate | Python 3.11, `xgboost`/`scikit-learn`/`pandas` | none | PostgreSQL (hard, reads `stock_prices` only — by design, avoids the corrupted derived tables) | Stateless process; writes versioned model files (`ml_training/models/*.joblib` + `_meta.json`) to local disk, not Postgres | **Compute + memory heavy**: full dataset rebuild + two chronological-holdout trainings with bootstrap CI and walk-forward folds — the single most CPU/RAM-intensive job in the system | Low-medium — a bad/failed training run never auto-promotes (gate-checked), so it cannot silently degrade production scores; worst case is "no model served," which the app already handles as a first-class state (`ml_confidence: "no_model"`) | Yes, but the model artifacts it produces are only useful to the Mechanism pipeline / backend's `/ml-stats` — not independently useful |
| **PostgreSQL** (`trading_production`) | System of record for all price/fundamentals/ML/auth/Telegram data | PostgreSQL (version not pinned in repo — confirm live version before migrating) | 5432 | none (leaf dependency) | **Fully stateful** — the single most critical piece of persistent state in the whole system | Grows with history (`stock_prices` alone spans 2018→ for ~1,000-2,500 symbols; `ml_breakout_dataset_v2` ~595k rows); disk I/O sensitive during the nightly pipeline write burst | **Critical — total system failure if lost**, and no backup strategy currently exists (see DISASTER_RECOVERY.md) | N/A — everything else depends on it; it depends on nothing else in this system |

## 2. Classification summary

**Stateless services** (safe to horizontally scale / blue-green deploy without special handling):
Frontend, Backend API, Channel senders (as invocations), Mechanism pipeline (as a process — its
output is stateful, the process itself is not).

**Stateful services:** PostgreSQL (the only true data store), the Telegram bot's long-poll session
(session continuity only — no unique data is held in-memory that isn't also in Postgres), and the
local model-artifact files ML training writes (`ml_training/models/*.joblib` — currently disk-only,
not backed by Postgres or object storage; **this is itself a small single-point-of-failure worth
fixing** — see TARGET_ARCHITECTURE.md).

**Compute-heavy:** Mechanism pipeline (threaded network I/O across thousands of symbols), ML training
(XGBoost training + dataset assembly over 595k rows).

**Memory-heavy:** Backend API when serving `/ml-stats` (loads a full XGBoost model + scaler into
memory via `joblib.load()`), ML training during dataset assembly (full DataFrame in memory).

**Low-resource / background:** Channel senders, the Telegram bot in steady state (idle between
messages), frontend (static/SSR rendering is cheap at this traffic scale — 2 authenticated users).

**Critical production services** (an outage is immediately user-visible or breaks the core product
promise): PostgreSQL, Backend API, Frontend, Telegram bot (for the private-assistant product), Channel
senders (for the public channel product — a missed post is a visible gap in a real audience's feed).

**Non-critical services** (an outage degrades gracefully or only affects internal tooling): ML
training (already has a "gate not passed → serve no model" fallback baked in), the Mechanism
pipeline on any *single* night (downstream consumers serve yesterday's data, which is stale but not
broken — though repeated misses compound into the trading-day-gate/staleness problems already
documented in CLAUDE.md).

## 3. What can safely share infrastructure vs. what should be isolated

**Can share one host/VPS safely:**
- Frontend + Backend API + reverse proxy — they're already coupled 1:1 (the frontend calls only this
  backend), low combined resource footprint, and co-locating removes a network hop that would
  otherwise add latency to every dashboard request.
- Channel senders + the Mechanism pipeline — both are short-lived/batch, both read the same Postgres
  data, both are already orchestrated by the same scheduling mechanism (Task Scheduler today; cron on
  Linux tomorrow) and already coordinate through file-based state (`data/session_state.json`) — no
  reason to split them across hosts.

**Should be isolated from the above (own host or at minimum strict resource limits):**
- **PostgreSQL** — the compute-heavy nightly pipeline write burst and the memory-heavy ML training
  reads should never compete with the database for the same host's I/O/memory that the live API also
  needs for request-serving latency. This is the single highest-value isolation boundary in the
  system.
- **ML training** — CPU/RAM spikes during the nightly retrain (two full XGBoost trainings +
  bootstrap CI + walk-forward folds over 595k rows) should not be able to starve the always-on
  Telegram bot or Backend API of CPU on the same box. If it must share a host for cost reasons, it
  needs a `nice`/cgroup resource cap, or must be scheduled for a window the bot/API can tolerate
  degraded latency in (already partially true — it's scheduled at 02:00 Israel time, off-peak for a
  US-market-hours audience).
- **The Telegram bot**, while low-resource in steady state, should not share a restart/deploy
  lifecycle with the Backend API — they're independently valuable (the private assistant can stay up
  through a backend deploy, and vice versa) and a shared process supervisor making them restart
  together would turn two small blast radii into one larger one for no benefit.
