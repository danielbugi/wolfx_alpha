# CLAUDE.md — Donchian Breakout Screening Platform

> **Living document.** Update this file whenever architecture, data flow, or workflow
> changes — not just when asked to. Treat it as the single source of truth for what
> this project actually does today, since the codebase has accumulated multiple
> parallel/legacy implementations (see "Known duplication" below).
>
> For the status/roadmap view (what's fixed, what's next, toward "check → test →
> deploy"), see **[MILESTONES.md](MILESTONES.md)**.
>
> **Active fix plan (started 2026-09-19):** the frontend/data-accuracy fix work is
> tracked in **[FRONTEND_FIX_MILESTONES.md](FRONTEND_FIX_MILESTONES.md)** — it carries
> binding ground rules (root cause not band-aids, no fabricated defaults, financial-data
> accuracy, required skills). Read its §0 before changing frontend code or any number
> the UI displays.
>
> **Current work (2026-09-21): "First Light" channel + private assistant — BUILT AND TESTED ON DEV.** **Start with
> [HANDOFF.md](HANDOFF.md)** (state, module map, decisions, ordered next steps, traps, a two-minute verification). Then
> [PRIVATE_ASSISTANT_PLAN.md](PRIVATE_ASSISTANT_PLAN.md) §0 (binding rules), [RUNBOOK_FIRST_LIGHT.md](RUNBOOK_FIRST_LIGHT.md)
> (run the bot, 29-step manual test, morning routine, inviting a friend, fresh dev channel, request-access flow),
> [FUNNEL_PLAN.md](FUNNEL_PLAN.md) (channel -> request -> approve -> activate -> retain; launch plan §8; channel content policy §10),
> [BOT_DESIGN_REPORT.md](BOT_DESIGN_REPORT.md) (product/UX design), [REPORT_FIRST_LIGHT_2026-09-21.md](REPORT_FIRST_LIGHT_2026-09-21.md)
> (what was built, QA, path to launch). Status view: Milestone 7 in MILESTONES.md.
> **Standing rules:** (1) everything is built and tested on the DEV channel; the production channel is locked (`PROD_SENDING_ENABLED=0`, §6)
> until an explicit launch; (2) **the assistant never posts in a channel** — channels carry data, promotion, news and information, the
> assistant's screens exist only in the private chat; (3) facts and the user's own numbers only, no advice wording (wording guard).
> **Channel content + member features (2026-09-21, late): built and tested on DEV — see [CHANNEL_CONTENT_MILESTONES.md](CHANNEL_CONTENT_MILESTONES.md)** (one extra silent channel post per session via `send_channel_posts.py`, weekly recap, new assistant commands `/full /aligned /history /week /scan /screen /morning`, list scoreboard; DEV Task Scheduler jobs registered).
Commands: `python -m pytest mechanism/alerts/tests ml_training/tests -q` (1,494 pass); mutation checks `python mechanism/alerts/tests/mutation_checks.py`
> (39 mutants, `--check` / `--group access|tracker|flow|guards`); promo images `python mechanism/alerts/promo_assets.py` -> `reports/first_light/promo/`.

## 1. What this project is

A daily quantitative screening pipeline that scans ~1,000–2,500 US equities
(S&P 500 + Russell 1000 + Nasdaq 100, deduped) for **Donchian channel breakouts**
across daily/weekly/monthly timeframes, enriches signals with **fundamental quality
scores** and an **XGBoost ML momentum model**, stores everything in **PostgreSQL**,
and serves it through a **FastAPI** backend to a **Next.js** dashboard.

The stated goal (per the user) is to fetch and analyze ~2,000 stocks daily to find
alpha (breakout candidates with edge). Current symbol universe is closer to
~1,000–1,012 unique tickers (see `mechanism/stock_lists/`) — scaling to 2,000
is a real, unaddressed capacity question (see §7).

## 2. Tech stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL (`trading_production`), via `psycopg2` (sync) + `asyncpg` (async) |
| Data ingestion | `yfinance` (Yahoo Finance), threaded/rate-limited |
| Analysis engine | Python, `pandas`/`numpy`, custom Donchian/RSI/MACD/ATR/Bollinger calculations |
| ML | `xgboost`, `scikit-learn`, `joblib` model persistence |
| Backend API | FastAPI + `uvicorn`, Pydantic models |
| Frontend | Next.js (App Router) + TypeScript + `axios` |
| Orchestration | Bash pipeline script + Python orchestrator, no scheduler wired in yet (see §7) |

## 3. Directory map — what's live vs. legacy

This repo has **multiple generations of the same components** left in place side by
side. Before editing anything, confirm you're touching the *active* path.

### 3.1 Active / current

- **`mechanism/`** — the current pipeline implementation (post "Integrated multi
  timeframe system" commit). This is where daily work happens.
  - `shared/` — `config.py` (env-driven `TradingSystemConfig`), `database.py`
    (`DatabaseManager` with sync pool via `psycopg2.ThreadedConnectionPool` +
    async pool via `asyncpg`), `utils.py`, `alpaca_client.py` (optional
    Alpaca Markets price data provider — see §6a; inert unless
    `DATA_PROVIDER=alpaca` is set).
  - `data_updaters/` — `daily_data_updater.py` (1168 lines, threaded + rate-limited
    Yahoo Finance fetch), `weekly_data_updater.py`, `monthly_data_updater.py`,
    `fundamentals_updater.py`.
  - `screeners/multi_timeframe_screener.py` — **the main engine** (1173 lines,
    recently rewritten wholesale — commit message literally says "FULLY FIXED
    VERSION" / "ALL BUGS FIXED", a sign of heavy iterative live-debugging). Combines
    daily breakout detection + weekly/monthly trend alignment scoring + ML
    enhancement, writes results to `frontend_data/` and `breakout_results/`.
  - `screeners/donchian_screener.py` — simpler single-timeframe version, still
    referenced by `master_automation_runner.py`'s component map.
  - `screeners/ml_donchian_screener.py` — another screener variant with embedded
    ML logic (modified in current diff).
  - `ml_enhancement/ml_signal_enhancer.py` — loaded dynamically by the multi-
    timeframe screener (`CombinedDonchianScreener` or `MLMomentumEnhancer` class,
    detected via `hasattr` at import time).
  - `ml_generators/historical_breakouts_generator.py` — builds the labeled
    breakout dataset ML training consumes.
  - `orchestrators/master_automation_runner.py` — health check / report / workflow
    CLI (`health`, `report`, `breakouts`, `workflow`, `run-component`).
  - `stock_lists/` — CSV/JSON/TXT snapshots of index constituents (S&P 500,
    Russell 1000, Nasdaq 100), dated 2025-07-06.

- **`mechanism/alerts/`** (added 2026-09-20) — the daily **Telegram momentum shortlist** that replaces
  the Finviz-top-15 channel: `send_daily_alerts.py` (CLI; **dry-run by default**, `--send` → dev channel,
  `--send --to prod` → production), `alert_builder.py` (candidates = the session's top gainers in the liquid
  universe; enrichment via `ml_training/features/price_features.py`; six *descriptive* context flags; weekly /
  monthly false-breakdown facts built from daily bars; plan templates), `news_links.py` (yfinance news → short
  clickable titles, no URL-shortener service), `message_format.py` (Telegram HTML cards), `telegram_client.py`
  (token never logged, 429/5xx retry, 4096-char split). Settings in `.env` (`TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID` prod, `TELEGRAM_DEV_CHAT_ID`, `ALERTS_TIMEZONE=Asia/Jerusalem`, `ALERTS_SEND_LOCAL_TIME=06:00`,
  `ALERTS_TOP_N`, `ALERTS_MAX_NEWS_LINKS`, `ALERTS_PLAN`, `ALERTS_SKIP_WEEKDAYS`).
  **"First Light" digest** (added 2026-09-20, Milestone 6C): long side only. `digest_builder.py` puts every liquid symbol
  into breakout / near-breakout (same rules as `multi_timeframe_screener.py`; the short side is classified only to
  know yesterday's group) and ranks three lists per group — top gainers, top ATR (today's true range ÷ prior ATR14),
  top volume (today ÷ 50-day median); `digest_format.py` renders a header + one message per group in plain words (no
  emojis, no news, no charts; rows are two short lines for a phone, the legend is a collapsed expandable quote);
  `send_daily_digest.py` is the CLI (dry-run default, `--send` → dev, `--send --to prod`, `--buttons` adds the inline
  keyboard, `--snapshot-only`; only the header message notifies; no alert-ledger writes yet). Facts only — no stop/TP
  plan, disclaimer in the header; no validated edge (see the 2026-09-20 studies). Tests: `mechanism/alerts/tests/`.
  **Snapshot + interactive bot (added 2026-09-21):** a real send saves the day's snapshot to Postgres (`digest_runs`,
  `digest_stocks` — EVERY liquid stock's facts, not just the listed ones; §5). `run_bot.py` (aiogram 3, long polling, a
  SEPARATE process from the 06:00 broadcast) serves the private assistant described in the next paragraph (older commands: `/start` (must tap "I understand - continue":
  educational, not advice) is now the LAST step of the guide; `/watch` `/unwatch` `/mylist` are aliases of `/add` `/remove` `/watchlist`), `/levels SYMBOL` (the **ATR risk framework**: risk level = close - 2xATR, reference levels +2/+4/+6 ATR = 1R/2R/3R, plus facts; `levels.py`; the same arithmetic as `backend/services/strategy_calc.py`, asserted to the cent by `test_levels.py`; wording is deliberately "risk level / reference levels", never stop/target/plan, and the UI's hardcoded "2-3% of portfolio" is NOT published), `/help`, `/about`, plus the popup buttons under the header
  (`--buttons`: 3 educational popups + ONE neutral "Private assistant (invite only)" link; no per-ticker/strategy buttons).
  **THE ASSISTANT (built 2026-09-21, BOT_DESIGN_REPORT.md; running):** first contact = a 4-step guide (edited in place) ending in the educational
  notice, then a persistent menu (Today's lists / Portfolio / Watchlist / Help). **Today's lists** (`/today`) = the union of the channel's lists
  from `digest_stocks.list_ranks`, EACH SYMBOL ONCE, Breakout / Near breakout tabs, 15 per page. **Stock card** (tap a stock, `/stock AAPL`, or just
  type a ticker) = facts + the lists it is in + the user's own tracking + buttons News / Chart / ATR levels / Add. **Watchlist and Portfolio**
  (`bot_tracked`, one row per user+symbol) store the symbol, the price (the user's own or the last close) and the DAY added, and show the change
  SINCE that day; portfolio adds optional shares, value and weights and totals. `/add AAPL` (watchlist at last close), `/add AAPL 140.5`, `/add AAPL 140.5 10`
  (portfolio), guided prompt via the card, `/remove`, `/export` (CSV), `/deleteme`, `/privacy`, `/guide`. UI copy says "your price", never "entry"
  (the wording guard bans entry/buy/sell/target/stop/should/profit/pick/signal/alpha). Code: `performance.py` (Decimal maths, half-up, cent-exact
  tests; an untrustworthy number is None -> "n/a" with the reason), `tracker.py` (service + input parsing), `screens.py` (every screen as text +
  buttons, pure), `news_service.py` (Alpaca news, one provider call per symbol per 6 h shared by all users, back-off on failure, headline + link only),
  `chart.py` (mplfinance candles + volume + prior 20-day high + the user's price line, Midnight Dawn palette, rendered once per symbol per session and
  re-sent by Telegram file_id; a tracked stock's personal chart is not cached). **Price guard:** a close-to-close jump that looks like a split
  (beyond ~3x, or 1/2, 2/3, 1/3, 2, 3 within 3%) after the day added, or a restated reference close, makes every number measured from the reference
  "n/a - the price series was adjusted (a split?)" instead of a fake return (2 of the 23 list stocks on 2026-09-18 have such breaks). Performance
  reads `stock_prices` (NOT `digest_stocks`: only one snapshot day exists because snapshots are written when the digest is sent).
  **INVITE-ONLY since 2026-09-21 (phase 7.0, PRIVATE_ASSISTANT_PLAN.md):** `access.py` decides who may use the bot. The owner is
  `BOT_OWNER_ID` in `.env` (never in the DB; unset = nobody new can be let in); everyone else needs an active `bot_access` row, created by
  a one-time invitation link (`/invite [note]` → `t.me/<bot>?start=inv_<code>`, 72 h, only a SHA-256 hash stored, `bot_invites`) or the
  owner's `/approve <id>`; `/revoke <id>` blocks at once (a new link cannot re-admit, only `/approve`), saved data is purged after 30
  days by a daily task in `run_bot.py`; `/users` (counts only) and `/status` (snapshot age + counts); admin actions go to `bot_audit`
  (never holdings). A person who is not let in gets ONE refusal showing their own Telegram id and NOTHING is stored about them; a DB
  error fails closed (error reply, never data) except for the owner. **Where the bot answers:** private chat = authorised users only;
  GROUP/SUPERGROUP = one neutral pointer to the private chat, never data (turn "Allow Groups" off in BotFather); CHANNEL = never; the
  only public interaction is the three educational popups (`def:` callbacks: definitions, no personal/strategy content, rate-limited).
  Removed in 7.0: group `/levels`, the `<TICKER> levels` channel buttons, deep links `lv_`/`list`, and the channel-member gate
  (`BOT_GATE_CHAT_ID`/`MemberGate` - a public channel's members are not a private audience). **Cost rules:** every reply is a read of the
  snapshot (the bot never calls a price provider, so cost does not grow with users), per-user rate limits (`BOT_MSG_LIMIT`/
  `BOT_POPUP_LIMIT` per hour, `BOT_GROUP_LIMIT` per group per minute), watchlist cap 25 symbols validated against the snapshot. Typed
  `/agree` is a fallback for the acknowledgement button; a stale button tap no longer aborts the handler; an error handler replies
  instead of going silent; a localhost lock port (`BOT_LOCK_PORT`, 47831) refuses a second copy of run_bot.py. **QA tooling:**
  `tests/qa_harness.py` (real dispatcher, fake network; `drive()` enrols senders as members unless `enroll=False` - production code has
  no open mode; every reply checked by `tests/tg_html.py`, a strict Telegram-HTML validator), `tests/test_bot_qa.py` (~980 checks
  incl. section K = access/leak/privacy/log-scan, hostile input, DB outage, isolation), a real-Postgres access round trip in
  `test_bot.py`, and `alerts/qa_live.py` (read-only health check incl. `BOT_OWNER_ID`, Allow Groups and the access tables;
  `--send-to-owner [--only TEXT]` sends the distinct assistant screens to the OWNER'S PRIVATE CHAT (never a channel) so real Telegram judges the HTML/keyboards). A running bot
  must be RESTARTED to pick up code changes. **Owner steps to go live:** set `BOT_OWNER_ID`, BotFather → Allow Groups off, restart the
  bot, run `qa_live.py`.
  **Request-access flow, channel tooling, safety (added later 2026-09-21; map in HANDOFF.md §3):** a stranger sees "what is this" + *Request access*;
  Telegram id + time are stored only after the tap (`bot_requests`), the owner gets Approve/Decline buttons, `/requests`, `/funnel` (counts,
  `funnel_events`); 7-day decline cool-down, 14-day purge, caps `BOT_MAX_PENDING` (100) and `BOT_MAX_MEMBERS` (25); `BOT_ACCESS_MODE` =
  approve (default) | auto | closed. `channel_posts.py` builds the pinned Start-here post and the promo post; `promo_assets.py` draws the promo image,
  the bot avatar and the channel logo (`reports/first_light/promo/`); `dev_chat_reset.py` clears the last ~48 h of a DEV group only (bots cannot
  delete older messages — a fresh channel is the clean reset; it refuses prod and channels); `run_first_light_morning.ps1` (gate → index → prices
  ~21 min → digest ~1 min) and `replay_dev_channel.ps1` at the repo root. **Only** `TelegramClient.from_env(target)` can reach a chat: targets
  `dev`, `prod` (refused while `PROD_SENDING_ENABLED` ≠ 1) and `owner` (the private chat). Structural tests forbid other constructors and forbid the
  channel modules from importing assistant screens. Regression net: `tests/mutation_checks.py` (39 re-introduced bugs, all caught).
  Telegram user id, acknowledgement time and watchlist symbols are stored; `alerts/texts.py` holds the shared
  educational wording and a test scans every public string for advice-style words. Logic lives in `bot_service.py`
  (framework-free, unit-tested), wiring in `run_bot.py`. Not yet hosted anywhere (needs the machine on / a VPS). The
  **Visual layer (added 2026-09-21):** `market_card.py` renders ONE 1080x1440 PNG (Pillow only, bundled DejaVu font): six
  index tiles with sparklines (S&P 500, Nasdaq, Russell 2000, Dow, VIX, 10-year yield in bps), a stocks-up-vs-down bar
  and equal-weighted sector bars in the **"Midnight Dawn" palette** (deep-navy surface #0a1a3c, tiles #0f2452, aqua #1ea5c4 = up,
  coral #e5626b = down; validated with the dataviz skill's validator: colour-blind dE 14.5, contrast 5.9/5.1:1; text 6.5-16:1;
  a test re-checks the contrast) always paired with a triangle + number. `market_context.py` assembles its data from Postgres (missing = "n/a", never
  guessed; sectors are computed from the same analysed stocks as the breadth bar). `send_daily_digest.py --image` saves it
  under `reports/first_light/` and sends it first (its caption is the only notification); a render failure falls back to
  the text-only digest. Text: every ticker bold, star = in 2+ lists, up/down arrows, each list in a quote block.
  The bot's popup buttons only answer while `run_bot.py` is running (the process was found NOT running when the user
  reported them broken) — until it is hosted, treat popups as optional and never rely on them.
  older `send_daily_alerts.py` cards contain stop/TP "Plan" text and must not go to the public channel. It is **not yet wired into
  `automation_pipeline.sh`** or a scheduler (06:00 Jerusalem = after the US close, so the completed daily bar is
  used; still an open item with §7.3). Aborts on stale (> 4 days) or half-loaded price data. Tests:
  `python -m pytest mechanism/alerts/tests -q`.
- **`mechanism/shared/market_calendar.py`** (added 2026-09-21) — the **trading-day gate** shared by
  `automation_pipeline.sh`, `send_daily_digest.py` and `send_daily_alerts.py`. Question it answers: "has a US session
  COMPLETED (close + `MARKET_SETTLE_MINUTES`, default 120) that this job has not already processed?" — so weekends, NYSE
  holidays and repeat runs skip, and a missed day catches up. Calendar = Alpaca's `/v2/calendar` (holidays + early
  closes; uses the existing `ALPACA_API_KEY/SECRET`) → cache `data/market_calendar_cache.json` (weekly refresh) →
  Mon–Fri fallback that **fails open** (never skips on a broken calendar). Per-job memory lives in
  `data/session_state.json` (keys `pipeline`, `digest:<dev|prod>`, `alerts:<dev|prod>`); a key is marked only after
  the job SUCCEEDS (so a half-failed pipeline is retried), and never moves backwards. `--force` (senders and
  `./automation_pipeline.sh --force` / `FORCE=1`) bypasses it; a sender's `--date` also bypasses it; dry runs only print
  a note. Senders additionally ABORT if a newer session has completed than `stock_prices` holds (updater not caught up).
  CLI: `python mechanism/shared/market_calendar.py status|gate|mark`. `ALERTS_SKIP_WEEKDAYS` still works but is now redundant.
  Tests: `mechanism/alerts/tests/test_market_calendar.py`.
- **`automation_pipeline.sh`** (repo root) — the actual daily entrypoint in current
  use. First runs the trading-day gate above (skips the whole run, exit 0, on non-trading days), then 8 steps, in order: **market index updater** (added 2026-09-19 —
  see below) → daily updater → weekly updater → monthly updater →
  **daily fundamentals updater** (wired in 2026-09-19 — see below) →
  **sector performance snapshot** (added 2026-09-19 — see below) →
  quarterly fundamentals updater (added 2026-09-18 — see below) →
  `multi_timeframe_screener.py`. Logs to `logs/pipeline_<timestamp>.log`.
  Confirm it's still running on a schedule; no cron/Task Scheduler entry was
  found in-repo (still an open item — see §7.3).
  Each step's output now streams live to the terminal via `tee` (added
  2026-09-19 — see §9), not just into the log file, and the five
  per-symbol updaters (`daily_data_updater.py`, `weekly_data_updater.py`,
  `monthly_data_updater.py`, `fundamentals_updater.py`,
  `quarterly_fundamentals_updater.py`) each draw a live progress bar via
  the new `ProgressBar` helper in `shared/utils.py`, with a log line per
  symbol underneath it — so a run against the ~1,000-3,000 symbol universe
  is now visibly moving in the bash window, not silent until the step
  finishes.
  Quarterly fundamentals was previously never part of the automated pipeline
  at all (manual-only) — adding it as a daily step was only safe once
  `quarterly_fundamentals_updater.py`'s `get_symbols_to_update()` stopped
  unconditionally re-fetching every active symbol and started skipping
  anything checked in the last 25 days (see §4/§9): a company only files a
  new 10-Q/10-K ~4 times a year, so the old unconditional version would have
  meant ~3,000 wasted API calls/day once actually scheduled. The staleness
  signal is `updated_at` on each symbol's latest quarter row (a true
  last-checked timestamp — upsert_quarter refreshes it every run regardless
  of whether the financials changed), not the quarter's own end date, which
  was tried first and rejected: filings lag quarter-end by 30-45 days, so a
  "fresh" quarter is already most of the way through any reasonable
  threshold measured from quarter-end. `fundamentals_updater.py`
  (daily_fundamentals — market cap/PE/PB) deliberately does NOT get this
  same treatment — those fields are price-derived and genuinely change every
  trading day, so it has no staleness filter and re-fetches the full active
  universe (~3,000 symbols) every run; its own 2s/symbol rate-limit delay
  alone makes it the slowest step in the pipeline (~100+ min), which is
  expected, not a bug. It was wired into `automation_pipeline.sh` as its own
  step 2026-09-19, resolving the open item that used to be tracked here as
  §7.4 — see the Changelog.
  `mechanism/data_updaters/market_index_updater.py` (new 2026-09-19) fetches
  a small fixed list of real index/commodity/macro tickers (S&P 500, Nasdaq
  Composite, Russell 2000, Dow, VIX, 10Y Treasury yield, Gold, Crude WTI,
  Dollar Index, Bitcoin) into `market_index_prices` — independent of the
  stock universe, so it runs first and fast. Deliberately uses yfinance
  directly regardless of `DATA_PROVIDER`: these are index/futures/FX-index
  symbols, not tradable equities, so neither Tiingo's nor Alpaca's
  stock/ETF-quote endpoints can serve them at all (see
  `shared/tiingo_client.py`'s docstring) — yfinance's general fragility
  (§6a) is an acceptable tradeoff for a ~10-symbol daily fetch.
  `mechanism/data_updaters/sector_performance_snapshot.py` (new 2026-09-19)
  persists one row per (date, sector) into `sector_performance_daily`,
  using the same LATERAL-joined aggregate `backend/services/market_service.py`
  already computed live, per-request — previously there was no sector
  history stored anywhere, only "today". Runs after the daily fundamentals
  and daily price steps so sector tags and closes are same-day fresh.

- **`ml_training/`** — the ML system. **Rewritten 2026-09-20** (see the Changelog and
  "ML audit" notes): everything ML derives from prices now lives in ONE module,
  `features/price_features.py` (breakout detection, causal indicators, model features,
  price-integrity rules, trade-plan outcome labels), used by both training and live
  inference. Chain: `data_preparation/build_dataset.py` (stock_prices only →
  `ml_breakout_dataset_v2`) → `models/momentum_predictor.py` (chronological-holdout
  evaluation + promotion gate) → `mechanism/ml_enhancement/ml_signal_enhancer.py`
  (inference through the same feature module). `tests/` has unit tests and a DB
  train/serve parity test. `scripts/ml_pipeline_runner.py` is a thin runner
  (`test` / `build` / `train` / `full`). **Retired, do not run to build data:**
  `data_preparation/momentum_labeler.py`, `feature_builder.py`,
  `deployment/ml_integration.py` (they read the stale-basis `breakouts` /
  `technical_indicators` tables).

- **`backend/`** — FastAPI app. This section was badly stale until 2026-09-19 —
  `main.py` had grown six more routers than were ever documented here. Verify
  against `ls backend/routers backend/services` before trusting any file list,
  this one included, since it's clearly drifted before.
  - `main.py` (~750 lines) — the real, working entrypoint. Defines dashboard
    endpoints directly (`/api/dashboard/*`) reading JSON files produced by the
    screener, plus raw `psycopg2` queries, plus an in-memory 15-min cache.
    Also `include_router`s everything below, each independently try/excepted
    so one router failing to import doesn't take the whole API down.
  - `routers/screener.py` + `models/screener_models.py` +
    `services/market_service.py` (452 lines) — `/api/screener/*` (search,
    filters, presets, market overview). Real and functional.
  - `routers/stock.py` + `services/stock_service.py` + `services/earnings_service.py`
    — `/api/stock/*`: per-symbol detail, price history, earnings, signal lookup,
    symbol search (powers the top-nav search box).
  - `routers/alpha.py` + (logic inline) — `/api/alpha/finder` — ranked breakout
    signals by combined confidence/alignment/quality, backs the dashboard's
    "Alpha Finder" panel.
  - `routers/strategy.py` + `services/strategy_calc.py` — `/api/strategy/rank`
    — position-sizing/stop/target plan generation, backs `/strategy`.
  - `routers/deep_value.py` + `services/deep_value_service.py` — `/api/deep-value/scan`
    — turnaround-alert / deep-value-watch screen, backs `/alerts`.
  - `routers/system_health.py` + `services/system_health_service.py` (built
    2026-09-18) — `/api/system-health/*` — production-readiness checks across
    pipeline freshness, data quality, ML model health, and universe coverage.
    Distinct from the basic `/api/health` liveness check in `main.py`.
  - `routers/ml_stats.py` + `services/ml_stats_service.py` (built 2026-09-18)
    — `/api/ml-stats/*` — model accuracy/precision/recall/AUC, feature
    importance, training dataset composition, live prediction track record,
    retraining history. Depends on `momentum_predictor.py`'s `register_model()`
    populating the `ml_models` table — rows from before that existed won't
    show accuracy/precision/recall, just a filename and date. **Updated 2026-09-20:** "current model" now
    means the newest model that carries a `_meta.json` (i.e. passed the promotion gate); legacy files on
    disk are listed as "legacy · not served" with their old metrics hidden; the report also returns the
    latest honest-evaluation reports (`ml_training/models/candidates/report_*.json`) and reads the
    `ml_breakout_dataset_v2` / `price_discontinuities` tables. `/ml-stats` and `/system-health` render this
    via `MLStatsPanel`.
  - `routers/performance.py` + `services/performance_service.py` (built
    2026-09-19) — `/api/performance/*` — request-latency tracking (an ASGI
    middleware in `main.py` times every request) and a DB connection-vs-query
    benchmark. In-memory only, resets on restart. See §3.3 and
    SYSTEM_HEALTH_REPORT.md §7.
  - `services/screener_service.py`, `services/ai_service.py`, `database.py` —
    **empty (0-byte) stub files**. Nothing currently imports them; they're
    dead scaffolding from an incomplete refactor. Either finish them or delete
    them — don't assume they do anything.
  - `routers/market.py` + `services/market_data_service.py` (new 2026-09-19)
    — `/api/market/indices` and `/api/market/sectors/history` — real
    index/commodity/macro data and persisted sector-performance history (see
    the `automation_pipeline.sh` entry above for the ingestion side).
    Deliberately a separate file/class from `services/market_service.py`:
    that one's "market overview" is breadth stats over the internal stock
    universe (advance/decline, sector treemap), not actual index-level data
    — the naming collision risk is noted here on purpose so it doesn't trip
    up whoever touches this next.

- **`frontend/`** — **the real, developed dashboard** (promoted from `frontend_1/`
  on 2026-09-14; the old bare scaffold that used to live at `frontend/` was
  deleted — see Changelog). Next.js (App Router) + TypeScript, `src/` layout.
  Like `backend/` above, has grown well past what was last documented here —
  routes now include `/` (dashboard), `/screener`, `/strategy`, `/alerts`,
  `/stock/[symbol]`, `/system-health`, and `/ml-stats`, all linked from
  `components/layout/TopNav.tsx`. `services/api.ts` is the single axios client
  every page uses (base URL from `NEXT_PUBLIC_API_BASE_URL`, default
  `http://127.0.0.1:8000` in `next dev` only — the IPv4 literal on purpose, see
  the 2026-09-19 changelog entry; `localhost` costs ~200ms per connection on
  Windows. Production builds *require* the var and fail without it — see the FM1
  changelog entry). The
  dashboard's own `main-page-data` fetch in `app/page.tsx` uses `fetch()`
  directly rather than the axios client, but now builds its URL from the
  same exported `API_BASE_URL` (used to be a hardcoded `localhost:8000`).
  `components/health/MLStatsPanel.tsx` (added 2026-09-19) renders model
  accuracy/feature-importance/training-dataset/prediction-track-record content
  shared between `/ml-stats` and the "ML Health & Accuracy" view on
  `/system-health` (see §3.3), so the two can't drift into different
  renderings of the same report.
  `components/dev/DevQAPanel.tsx` (added 2026-09-19) is a floating,
  dev-only endpoint-health widget mounted in `app/layout.tsx` — see §3.3.
  `components/dashboard/MarketIndicesStrip.tsx` (added 2026-09-19) — the
  real macro strip (S&P 500/Nasdaq/Russell 2000/Dow/VIX/10Y yield/Gold/
  Crude/DXY/BTC, each with a sparkline; VIX also carries a calm/normal/
  elevated/fear regime chip), backed by `/api/market/indices`. Placed above
  the dashboard's header, not below it — deliberate: a trader orients
  top-down (macro regime → sector → single name), and the rest of the page
  is exactly that order already.
  `components/dashboard/SectorTrendChart.tsx` (added 2026-09-19) — the
  "Trend" alternative to the sector heatmap Treemap (a Heatmap/Trend toggle
  now sits in that card's header in `app/page.tsx`), backed by
  `/api/market/sectors/history`. Every sector has one fixed color, keyed by
  sector name in `components/dashboard/sectorPalette.ts` (changed 2026-09-19
  from muted-gray-by-default — see the Changelog): 8 solid lines use the
  validated 8-hue categorical palette, the 3 sectors past the 8th (Utilities,
  Consumer Defensive, Real Estate) reuse a hue with a *dashed* stroke
  (composite encoding — the palette must never be cycled/extended with
  generated hues), and "Unknown" is neutral gray dotted. The legend under the
  chart is a ranked list (swatch mirrors color + dash, plus each sector's
  return); hovering a row spotlights that line and dims the rest, clicking
  selects it (same `selectedSector` state the treemap and Alpha Finder
  share). Lines plot 90-day *cumulative* return (compounded from each
  day's `avg_performance`), not the raw daily deltas `sector_performance_daily`
  stores.

- **`frontend_data/`, `breakout_results/`, `logs/`, `reports/`** (repo root) —
  live output directories, actively written by the pipeline (confirmed via file
  timestamps, latest 2025-11-07). These are the ones the running system actually
  uses (`config.frontend_data_dir` defaults to `'frontend_data'`, resolved
  relative to CWD when the pipeline is run from repo root).

### 3.2 Legacy / duplicate — do not build on these without checking first

- ~~`frontend/` bare scaffold vs `frontend_1/` real dashboard~~ — **resolved
  2026-09-14**: old scaffold deleted, `frontend_1/` renamed to `frontend/`. No
  action needed here anymore.
- **`mechanism/data/`, `mechanism/frontend_data/`, `mechanism/breakout_results/`,
  `mechanism/reports/`, `mechanism/logs/`** — stale duplicates of the root-level
  output directories. `mechanism/frontend_data/latest_breakouts_ml_enhanced.json`
  is from **2025-08-26**; the live file people actually consume is
  `frontend_data/latest_multi_timeframe_ml_enhanced.json` at repo root. Likely
  left over from when `mechanism/` was run with a different CWD, or an artifact
  of copying the folder into the repo. Safe to archive/delete once confirmed unused.
- **`mechanism/screeners/*_backup.py`** (`multi_timeframe_screener_backup.py`,
  `ml_donchian_screener_backup.py`) and **`ml_training/*/​*_backup.py`,
  `ml_training/scripts/backup/*.py`** — manual backups committed as files instead
  of relying on git history. Noise; git already has this history.
- **`backups/`** (repo root, currently untracked, ~21,000 files) — contains an
  **entire old `venv/`** and an old frontend's `node_modules/`, plus early
  versions of `backend/main.py`. `.gitignore` currently only excludes `venv/` /
  `node_modules/` by *name*, which does match these nested ones — but the
  rest of `backups/` (debug scripts, old `main.py.backup`, etc.) is **not**
  ignored and would get swept into a `git add -A`. **Do not add this directory
  to git.** Recommend adding `backups/` to `.gitignore` explicitly, or deleting
  it if it's not needed for reference.
- **`SKILLS/`** — five `SKILL.md` files (`testing`, `backend-development`,
  `react-ui-patterns`, `create-progress-pr`, `avoid-feature-creep`). These look
  like generic templates pulled from a skill marketplace (the `testing` one
  references Neon/Drizzle/Bun/Playwright — a stack this project doesn't use).
  They're not wired into Claude Code's skill discovery (`.claude/skills/`), so
  they're currently inert. Worth pruning or adapting to this project's actual
  stack (PostgreSQL, not Neon; pytest, not Bun) if you want them usable.
- **Root-level loose files**: `ml_diagnostics.py`, `debug_ml_files_report.json`,
  `mechanism/quick_analyzer.py`, `mechanism/atr_database_fixer.py` — one-off
  debug/fixer scripts from past incidents. Fine to keep for reference but not
  part of the pipeline; consider moving to a `mechanism/diagnostic_tools/`
  (which already exists and already holds `test_db_connection.py`).

### 3.3 Dev-only QA tooling (frontend)

Added 2026-09-19 in response to "the frontend sometimes stops loading data" —
the actual cause that session was a stale `next dev` process with a corrupted
webpack cache (fix was `rm -rf frontend/.next` + restart, not a backend
change — see the Changelog entry below for the full diagnosis), but the
underlying ask ("give me a way to see endpoint health without guessing")
produced two lasting pieces:

- **`components/dev/DevQAPanel.tsx`** — floating widget (bottom-right, every
  page) that pings all ~14 endpoints the frontend depends on and reports
  status/latency/basic shape validation for each. Gated on
  `process.env.NODE_ENV === 'production'` (which Next.js sets automatically
  for `next build`/production start, not a custom env var), so there is no
  flag to forget — it cannot ship into a production build.
- **`/system-health` view switch** — the page now has three views (segmented
  buttons, not a `<select>`, to match the existing `alphaView` toggle pattern
  already used on the dashboard): *Overview* (unchanged — everything),
  *Basic System Health* (pipeline freshness + data quality + universe
  coverage, no ML section), and *ML Health & Accuracy* (the ML operational
  checks plus the same rich model/accuracy detail as `/ml-stats`, via the
  shared `MLStatsPanel`). `/ml-stats` still exists as its own route/nav link
  — this was additive, not a replacement.

## 4. Data flow (current, as-built)

```
Yahoo Finance (yfinance)
   │  daily_data_updater.py / weekly_data_updater.py / monthly_data_updater.py
   ▼
PostgreSQL: stock_prices, technical_indicators, daily_fundamentals,
            weekly_technical_indicators, monthly_technical_indicators
   │
   │  multi_timeframe_screener.py
   │    1. get_daily_breakout_signals()   — Donchian breakout detection
   │    2. get_multi_timeframe_signals()  — weekly/monthly context join
   │    3. calculate_timeframe_alignment_score() — cross-timeframe scoring
   │    4. enhance_signals_with_ml()      — via ml_enhancement/ml_signal_enhancer.py
   ▼
frontend_data/latest_multi_timeframe_ml_enhanced.json
breakout_results/multi_timeframe_ml_enhanced_<timestamp>.json
   │
   ▼
backend/main.py (FastAPI, :8000) — reads the JSON + queries Postgres directly,
   serves /api/dashboard/* and /api/screener/* (via routers/screener.py →
   services/market_service.py)
   │
   ▼
frontend (Next.js, :3000) — services/api.ts → dashboard components
```

**`ml_training/` and `mechanism/ml_enhancement/` are one connected pipeline, not
two competing ones** (confirmed by reading the code, not just the READMEs):
`ml_training/models/momentum_predictor.py` trains an XGBoost model and saves it
as `ml_training/models/momentum_predictor_v<timestamp>.joblib` (+ matching
`_scaler.joblib` and `_features.json`). At runtime,
`mechanism/ml_enhancement/ml_signal_enhancer.py._find_model_files()` walks up
from its own location to find the project root, then globs
`ml_training/models/momentum_predictor_v*.joblib` (falling back to `models/` or
`mechanism/models/`) and auto-loads the **most recent by filename timestamp** —
no manual promotion/registry step actually gates production use despite
`ml_training/deployment/model_registry.py` existing for that purpose.

**Model state (updated 2026-09-20):** `ml_signal_enhancer.py` now auto-loads only models
that carry a `_meta.json` contract (written by the new trainer *only after passing the
promotion gate*). The four older `momentum_predictor_v*.joblib` files (latest
`v20260918_2157`, registry AUC 0.702) are **legacy and ignored** — that AUC came from a
reversed train/test split (train = newest 80%, test = oldest 20%, early stopping on the
test set) plus labels corrupted by a price-basis mismatch. **No model currently passes
the gate, so ML scores are unavailable in the app** (`ml_confidence: "no_model"`, all
ML fields `null`). Honest holdout results (2025-02 → 2026-08, untouched): the legacy
momentum target reaches AUC 0.63 (0.59 without the volume features its label
mechanically contains) but ranks *actual trade-plan profit* no better than chance
within a direction (AUC 0.50); the target "trade plan ends R > 0" reaches AUC 0.53,
below the direction-only baseline 0.54. Latest reports:
`ml_training/models/candidates/report_*.json`, surfaced on `/ml-stats`.

## 5. Database (PostgreSQL, `trading_production`)

Core tables (per `mechanism/create_trading_schema.sql`,
`add_multi_timeframe_tables.sql`, and confirmed via `shared/database.py` /
`master_automation_runner.py` queries):

- `stock_prices` — OHLCV, one row per symbol/date
- `technical_indicators` — SMA/RSI/MACD/Bollinger/ATR/Donchian, daily
- `weekly_technical_indicators`, `monthly_technical_indicators` — same, coarser
- `daily_fundamentals` — valuation ratios, sector, composite quality score/grade
- `quarterly_fundamentals` — detailed financials
- `breakouts` — detected breakout events + 10-day forward outcome (`success`,
  `max_gain_10d`, `max_loss_10d`)
- `ml_training_data` — breakout rows joined with quality/sector features
- `latest_stock_data`, `latest_fundamentals` — cache tables for fast reads
- `market_index_prices` (added 2026-09-19, `mechanism/add_market_data_tables.sql`)
  — daily OHLCV for the fixed index/commodity/macro symbol list (^GSPC,
  ^IXIC, ^RUT, ^DJI, ^VIX, ^TNX, GC=F, CL=F, DX-Y.NYB, BTC-USD). Same shape
  as `stock_prices` but never holds equities — kept separate rather than
  adding rows to `stock_prices` since these symbols aren't part of the
  screened universe. Populated by `market_index_updater.py`.
- `sector_performance_daily` (added 2026-09-19, same migration file) — one
  row per (date, sector): `stock_count`, `avg_performance`. A persisted
  version of the aggregate `market_service.py` already computes live;
  populated by `sector_performance_snapshot.py`, feeds the dashboard's
  sector Trend chart.
- `ml_breakout_dataset_v2` (added 2026-09-20, `mechanism/add_ml_dataset_tables.sql`) —
  one row per Donchian breakout bar (2018→, ~595k rows), built from `stock_prices`
  only: JSONB `features` (feature set `price_v1`), the legacy momentum label
  (`momentum_score`/`target_binary`) and the outcome of the exact UI trade plan in R
  units (`plan_r`, `stopped`, `tp3_hit`, `mae_r`). NULL label = window not matured.
- `price_discontinuities` (same migration) — days where the stored price series is not a
  continuous path (|1-day close ratio| > 3× or < 1/3, non-positive or inconsistent bars;
  ~1,000 rows, 121 symbols). Samples whose 253-bar feature lookback or 20-bar label
  window cross one are excluded and ML inference refuses to score them; nothing is
  "repaired" (that would fabricate history). Also excluded: 20-day avg dollar volume
  < $1M (e.g. PARA — a consistent but astronomically back-adjusted series with ~0
  volume).
- `alerts` (added 2026-09-20, `mechanism/add_alerts_tables.sql`) — ledger with one row per alert actually
  SENT (dry runs are never recorded): session date, symbol, channel (dev/prod), entry reference, ATR, stop,
  TP1-3, trail %, the plan template and the descriptive flags/facts at alert time. Basis for the forward
  outcome tracker (next slice: did it reach +100% / +300%, R multiples, per alert type).
- `digest_runs`, `digest_stocks`, `bot_users`, `bot_watchlist` (added 2026-09-21, `mechanism/add_digest_tables.sql`) —
  the First Light snapshot (one `digest_runs` row per US session; `digest_stocks` = every liquid stock's close,
  1-day %, volume ×, range × ATR, ATR(14) value, distance below the 20-day high, group, yesterday's group and its rank in
  each list, ~2,866 rows/day) and the bot's only personal data (Telegram user id, acknowledgement time, watchlist symbols).
  The snapshot is also the base for the future list scoreboard.
- `bot_tracked`, `news_items`, `news_fetched`, `bot_chart_cache` (added 2026-09-21, `mechanism/add_tracker_tables.sql`) — the assistant's Watchlist +
  Portfolio (one row per user+symbol: kind watch/hold, ref_price, ref_source entered/close, ref_date, shares, first_added_at/first_price; cascades
  from `bot_users`, so the revoked-user purge and `/deleteme` need no extra code), the shared headline cache (UNIQUE symbol+url hash, headline + link
  only), and the Telegram file_id of each chart already uploaded per symbol and session. `bot_watchlist` is superseded and no longer read.
- `bot_access`, `bot_invites`, `bot_audit` (added 2026-09-21, `mechanism/add_assistant_tables.sql`) — the private assistant's access layer:
  who may use the bot (`active` / `revoked`; the owner is in `.env`, not here), one-time invitation links (only a SHA-256 hash of the
  code), and an audit trail of admin actions (never holdings). Planned next tables (portfolio, trades, news): PRIVATE_ASSISTANT_PLAN.md §5.
- **Stale-basis tables — do not train from them:** `breakouts`, `momentum_scores`,
  `enhanced_ml_training_data`, `ml_training_data`. `stock_prices` was overwritten in
  place by `repopulate_from_tiingo.py` (Tiingo split+dividend-adjusted series, restated
  by the vendor after every ex-dividend/split) while everything derived from it was
  never recomputed: ~24% of `technical_indicators` rows for 2023-Q3..2025-Q3 disagree
  with current prices by >0.5%, and ~42% of 2023-2025 `breakouts.entry_price` values
  disagree with the `stock_prices` close by >2%. **Open:** recompute historical
  `technical_indicators` (and weekly/monthly) from `stock_prices`, and add an
  adjustment-event guard to the daily updater (detect vendor restatement of the last
  N stored bars → refetch + recompute that symbol).

Connection management: `mechanism/shared/database.py`'s `DatabaseManager` runs a
`ThreadedConnectionPool` (sync, min/max from config) auto-initialized on import,
plus an optional `asyncpg` pool. **Known bug**: `get_daily_data_for_weekly_calc()`
calls `self.get_connection()`, which doesn't exist on `DatabaseManager` (the real
method is `get_sync_connection()`) — this method will raise `AttributeError` if
ever called. Confirm whether it's actually invoked anywhere before relying on
weekly-calc-from-daily-data functionality.

**`backend/` connection pooling (added 2026-09-19):** `backend/main.py` has its
own separate `ThreadedConnectionPool` (does not reuse `mechanism/`'s
`DatabaseManager` — different process, different import path) via
`_PooledConnection`, a `psycopg2.extensions.connection` subclass whose
`close()` returns the connection to the pool instead of closing the socket.
Every router/service still just calls `get_database_connection()` then
`conn.close()` in a `finally` exactly as before; pooling is transparent to
them. Sized via `DB_POOL_MIN`/`DB_POOL_MAX` env vars (default 2/30). Added
after `/api/performance/` measured every request paying a ~43-53ms bare
`psycopg2.connect()` cost — see SYSTEM_HEALTH_REPORT.md §7.1.

## 6. Environment / config

`.env` at repo root, loaded by `mechanism/shared/config.py` (searches several
candidate paths) and independently by `backend/main.py` (`load_dotenv()`).
Cleaned up 2026-09-14: removed dead duplicate `API_HOST`/`API_PORT`/`CORS_ORIGINS`
blocks and unused Flask-leftover keys (this is FastAPI); one `ALLOWED_ORIGINS`
remains, noted as not yet wired into `backend/main.py` (which still
hardcodes `allow_origins` in the CORS middleware instead of reading it).

`mechanism/requirements.txt` now exists (added 2026-09-14) alongside
`backend/requirements.txt` and `ml_training/requirements.txt` — no single
consolidated root file yet, but every subproject has its own.

**Production lock (added 2026-09-21):** `PROD_SENDING_ENABLED` in `.env` (0 = locked, 1 = launched). While it is not `1`, `TelegramClient.from_env("prod", dry_run=False)` raises and `run_first_light_morning.ps1 -Send -To prod` exits 4 — every feature is tested on the DEV group (`TELEGRAM_DEV_CHAT_ID`) first and the public channel (`TELEGRAM_CHAT_ID`, "Top Gainers - Daily", to be reset at launch) is untouched. A test checks that nothing else constructs a `TelegramClient`. Launch plan: FUNNEL_PLAN.md §8.

### 6a. Price data provider (yfinance / Alpaca / Tiingo)

`mechanism/shared/config.py`'s `data_provider` field (env: `DATA_PROVIDER`,
default `yfinance`) controls which API `daily_data_updater.py` (and, for
`tiingo` only, `fundamentals_updater.py`) uses to fetch data. Three options:

- **`yfinance`** (default) — scrapes Yahoo Finance's unofficial API. Free,
  no signup, but fragile: Yahoo changes its internal API without notice, and
  a pinned yfinance version going stale against it is what caused the
  ~10-month outage documented in §4/MILESTONES.md. Mitigated (not
  eliminated) by leaving the version unpinned in `mechanism/requirements.txt`.
- **`alpaca`** — `mechanism/shared/alpaca_client.py`, using Alpaca Markets'
  free-tier Market Data API (`alpaca-py`, official supported SDK, real SLA).
  Active in `.env` as of 2026-09-14. **Known gap, found via a full-universe
  test:** ~38/1005 symbols (BK, MMC, FI, EA, HES, WBA, ...) return a
  genuinely empty result from Alpaca's free IEX-only feed — not an error,
  not transient, just coverage the free tier doesn't carry. Some are real
  2025 delistings; others (BK, MMC, FI) are large, actively-traded names
  Alpaca's free feed simply doesn't have. `daily_data_updater.py` falls back
  to yfinance per-symbol when this happens, so it doesn't lose data, just
  reliability for that slice.
- **`tiingo`** — `mechanism/shared/tiingo_client.py`. Built 2026-09-17,
  **active in `.env`** as of the same day (real `TIINGO_API_KEY` supplied by
  the user). Migration target for scaling the universe to Russell 3000
  (~3,000 symbols): a paid ($30/mo Power plan), documented API with a
  **consolidated** feed (not Alpaca free tier's IEX-only gaps) and 10,000
  req/hour headroom. `get_daily_bars()` — **confirmed working for the full
  universe** (spot-tested AAPL, PLTR, RXRX, invalid-ticker handling) — is
  what actually solves the scaling problem; `daily_data_updater.py` uses it
  with a yfinance fallback per symbol, same pattern as the Alpaca path.

  **Fundamentals did NOT migrate the way originally planned.** The
  assumption going in — "Tiingo bundles fundamentals at the same $30/mo
  tier" — turned out to be wrong, and this isn't documented anywhere on
  Tiingo's docs/marketing pages; it only surfaced by calling the live API
  and getting HTTP 400 for non-AAPL/MSFT test symbols. **The Fundamentals
  API (`/daily` metrics, `/statements`, and `/meta`'s sector+industry
  fields) is restricted to the Dow 30 only on Free and Power plans** —
  every other symbol gets
  `{"detail": "Error: Free and Power plans are limited to the DOW 30..."}`,
  and `/meta` silently returns the literal string
  `"Field not available for free/evaluation"` for sector/industry instead
  of erroring. Real universe-wide fundamentals coverage needs Tiingo's
  separate paid Fundamental Data API add-on (price unknown — email
  support@tiingo.com) or a different vendor.

  **Practical effect (not a bug — the existing fallback design absorbs
  this cleanly):** `get_fundamentals()` and `get_quarterly_statements()`
  return `None`/`[]` for non-Dow-30 symbols, and
  `fundamentals_updater.py` / `quarterly_fundamentals_updater.py`'s
  Tiingo-then-yfinance-fallback logic means ~2,970 of ~3,000 symbols just
  keep using yfinance for fundamentals, exactly as before this migration —
  nothing broke, but "fundamentals also migrated to Tiingo" is false at
  this plan tier. The one real bonus, a Piotroski F-Score (0-9) pulled from
  Tiingo's `/statements` `overview` section — see
  `mechanism/add_piotroski_score.sql` and the new
  `quarterly_fundamentals.piotroski_f_score` column — only populates for
  Dow 30 names as a result.

  **Also-known field gap** (even for the Dow 30 names Tiingo does cover):
  its Fundamentals API doesn't expose `beta`, `dividendYield`,
  `sharesOutstanding`, or `floatShares` — those four come back `None`.
  `fundamentals_updater.py`'s quality-score functions already degrade
  gracefully on missing fields (additive scoring with fallback branches),
  so this reduces score precision rather than breaking anything.

  **Open decision for the user:** whether to pay for Tiingo's Fundamentals
  add-on, switch fundamentals specifically to Financial Modeling Prep (the
  original candidate per this section before Tiingo), or accept yfinance
  staying the de facto fundamentals provider for the ~3,000-symbol universe
  indefinitely.

Chosen 2026-09-17 over Polygon.io (~$2,000/mo — too expensive) and IEX Cloud
(shut down). See conversation history / MILESTONES.md for the fuller vendor
comparison including EODHD (cheaper, $19.99/mo bulk-EOD tier, but
fundamentals gated behind its $59.99/mo tier — would've meant a second
vendor for fundamentals).

## 7. Open questions to resolve with the user before big refactors

1. **Model retraining cadence / is there any ML edge** — superseded 2026-09-20: retraining
   is now honest but **nothing passes the promotion gate**, so there is no served model.
   Decisions pending with the user: what the UI should say when ML is unavailable (the
   dashboard "AI-Enhanced Picks" widget is now just alignment-ranked and mislabeled), and
   whether to invest in new predictive features (market regime from `market_index_prices`,
   relative strength, earnings proximity) or a redefined label (triple-barrier on the exact
   plan). No ML step is wired into `automation_pipeline.sh` yet (build/train/outcome
   tracking are manual: `python ml_training/scripts/ml_pipeline_runner.py full`).
2. **Scaling to Russell 3000 (~3,000 symbols)** — decided 2026-09-17: migrate
   to Tiingo (see §6a) to remove the yfinance/Alpaca rate-limit and coverage
   ceiling. Data-provider side is built but not yet activated (needs a real
   `TIINGO_API_KEY`). **Still open:** `mechanism/stock_lists/` currently holds
   S&P 500 + Russell 1000 + Nasdaq 100 (~1,000–1,012 tickers) — the actual
   Russell 2000/3000 constituent list still needs to be sourced and added
   before the universe itself grows; the data-provider migration alone
   doesn't add symbols, it just removes the ceiling that would have blocked
   adding them. Also still open: `max_concurrent=3` in the updater's
   `ThreadPoolExecutor` was tuned for yfinance's aggressive throttling —
   worth revisiting upward once Tiingo (10,000 req/hour) is active, since
   that concurrency ceiling will otherwise be the new bottleneck.
3. **Scheduling** — `automation_pipeline.sh` is not invoked by any cron/Task
   Scheduler entry found in this repo. Confirm how (or whether) it currently
   runs daily in production, since the most recent successful run in `logs/`
   predates today by a long stretch. Directly relevant to the VPS deployment
   the user wants next (2026-09-18 conversation) — this is one of the things
   that deployment needs to actually set up, not just inherit.
4. ~~**`fundamentals_updater.py` (daily_fundamentals) still not in the
   automated pipeline**~~ — **resolved 2026-09-19**: wired into
   `automation_pipeline.sh` as step 4/6, ahead of the quarterly updater and
   the screener. See §3.1 and the Changelog. This was also the fix for the
   31.6%-sector-coverage gap noted in the 2026-09-19 Changelog entry below
   (most stocks showing `sector: "Unknown"`/`market_cap: null` on the
   dashboard) — that gap was a direct symptom of this step never running
   automatically.

5. **Private assistant (decided 2026-09-21)** — access model (invite-only allowlist recommended), what the public channel says about
   the assistant, news provider (verify Alpaca free news first), protection level for portfolio data, hosting (VPS), legal review of
   personalised levels + portfolio tools and privacy-law duties. Full list with recommended defaults: PRIVATE_ASSISTANT_PLAN.md §10.

## 8. Workflow: check → test → deploy

The ML layer and the alerts package now have real pytest suites (25 tests, added 2026-09-20):
`python -m pytest ml_training/tests mechanism/alerts/tests -q` (the DB parity test skips itself if
Postgres is unreachable). The screener, the updaters and the backend still have **no automated
tests** — only ad hoc manual scripts named `test_*.py` in `backend/` and
`mechanism/diagnostic_tools/test_db_connection.py` (print-and-eyeball, not pytest-discoverable).
For those, use this manual sequence:

**Check (health/sanity):**
```bash
python mechanism/orchestrators/master_automation_runner.py health
python mechanism/shared/config.py        # validates env/config
python mechanism/shared/database.py      # validates DB connection + prints stats
python mechanism/diagnostic_tools/test_db_connection.py
```

**Test a pipeline stage in isolation before a full run:**
```bash
python mechanism/data_updaters/daily_data_updater.py --test AAPL MSFT GOOGL
python mechanism/screeners/multi_timeframe_screener.py   # full run, ~7 min observed
```

**ML (rebuild dataset → honest evaluation → promote only if the gate passes):**
```bash
python -m pytest ml_training/tests -q                       # unit tests + DB train/serve parity
python ml_training/data_preparation/build_dataset.py --replace   # ~5 min, all symbols, stock_prices only
python ml_training/models/momentum_predictor.py --target momentum --no-promote
python ml_training/models/momentum_predictor.py --target plan_profit
```
The gate (a-priori, in `momentum_predictor.py`): holdout AUC ≥ 0.55 with bootstrap CI
lower bound > 0.5, ≥ 0.02 over the direction-only baseline, ≥ 75% of walk-forward folds
above 0.5, top-decile lift ≥ 1.15, **and** AUC ≥ 0.52 for ranking actual plan profit
within each direction (that last criterion was added after the first run, once the legacy
label proved predictable only through its own volume term).

**Backend:**
```bash
cd backend
uvicorn main:app --reload --port 8000
curl http://localhost:8000/api/health
curl http://localhost:8000/api/screener/market-overview
```

**If :8000 is wedged** (requests hang, `/api/health` never answers, `netstat -ano | findstr :8000` shows a
pile of `CLOSE_WAIT` lines owned by the uvicorn PID): first, optionally `pip install py-spy` then
`py-spy dump --pid <pid>` to capture where the threads are stuck; then
`Get-NetTCPConnection -LocalPort 8000 -State Listen | Select OwningProcess` (PowerShell) →
`taskkill /PID <pid> /T /F` (`/T` matters: `--reload` runs a reloader parent + worker, killing only the worker
just respawns it) → confirm the port is free → restart in its own terminal window. Never kill by image name
(`/IM python.exe`).

**Frontend:**
```bash
cd frontend
npm install
npm run dev     # http://localhost:3000 (use `-- -p 3001` if 3000 is taken)
```
Run the dev server in its own terminal, **not** with stdout piped into something that
later closes — a closed pipe makes every Next worker throw `write EPIPE` and the routes
return 500 (FRONTEND_FIX_MILESTONES.md FM0.1). If routes 404/500 with a clean backend,
stop the server, `rm -rf frontend/.next`, restart. Open the app via `localhost`, not
`127.0.0.1` — the backend's CORS list is keyed on `localhost` (FM4.7).

**Verify a frontend change** (dev server + backend running):
```bash
npx tsc --noEmit && npx next lint                  # from frontend/
NEXT_DIST_DIR=.next-build npm run build            # isolated build; never touches the dev cache
bash frontend/scripts/screenshots.sh <out_dir> [/route ...]   # headless-Chrome shots at 1440/820/390
```

**Alerts (dry run first; nothing is sent without `--send`):**
```bash
python mechanism/alerts/send_daily_alerts.py                  # preview 15 cards + header
python mechanism/alerts/send_daily_alerts.py --send           # post to the DEV channel (needs TELEGRAM_* in .env)
python mechanism/alerts/send_daily_alerts.py --send --to prod # production channel
python mechanism/alerts/send_daily_alerts.py --plan runner --top 10 --news 2

# RUNBOOK_FIRST_LIGHT.md = how to run the bot, a 29-step manual test, and the morning notification.
# One command for the morning routine (preview by default; -Send [-To prod]; -UpdateOnly / -SkipUpdate for a 2-task schedule):
#   .\run_first_light_morning.ps1         (PowerShell, repo root; gate -> index -> prices ~21 min -> digest ~1 min)
# First Light digest (long side) + interactive bot
python mechanism/alerts/send_daily_digest.py                  # dry run, prints the 3 messages, writes nothing
python mechanism/alerts/send_daily_digest.py --send           # DEV channel + saves the snapshot for the bot
python mechanism/alerts/send_daily_digest.py --send --buttons # header with popup buttons (needs the bot running)
python mechanism/alerts/run_bot.py                            # the invite-only bot, long polling; needs BOT_OWNER_ID in .env; Ctrl+C to stop
python mechanism/alerts/qa_live.py                            # read-only live health check (add --send-to-owner to send the assistant screens to your PRIVATE chat with the bot)
# needs aiogram in the ACTIVE environment: pip install -r mechanism/requirements.txt  (the repo's .venv now has it,
# verified with pydantic 2.5.0 / fastapi 0.104.1 unchanged; the import alone takes ~3-7 s and prints "Loading...")
python -m pytest mechanism/alerts/tests -q
```

**Full daily pipeline:**
```bash
./automation_pipeline.sh            # skips itself on weekends / NYSE holidays / an already-done session
./automation_pipeline.sh --force    # run anyway (e.g. intraday, or to re-run a completed session)
python mechanism/shared/market_calendar.py status   # calendar source, latest completed session, stored state
```

**Deploy** — no deployment config (Dockerfile, CI workflow, hosting target) was
found in this repo. This needs to be defined explicitly: containerize backend +
Postgres, decide hosting for the scheduled pipeline (a box that must run daily
regardless of whether anyone's laptop is on — not something to run manually),
and pick a frontend host (Vercel is the natural fit for Next.js). Flag as a
priority once the duplication/cleanup above is settled — deploying the current
dual-frontend, half-stubbed-backend state as-is would just ship the confusion.

## 9. Changelog

- **2026-09-21 (channel content + member features built, DEV only)** — Executed CHANNEL_CONTENT_MILESTONES.md M0-M6 (all items that do not need a decision). New modules in `mechanism/alerts/`:
  `market_stats.py` (wide date x symbol market facts), `channel_cards.py` (health / sector / macro PNG cards; two-line chart colours validated `#4f86e8`/`#bf8514`), `channel_content.py` (pure post builders +
  weekday rotation), `send_channel_posts.py` (ONE extra silent post per session, Sunday recap, `--all` review, gate keys `posts:<target>` / `recap:<target>`), `channel_news.py` (OFF until
  `CHANNEL_NEWS_ENABLED=1`; licence unchecked), `scoreboard.py` (OFF until `CHANNEL_SCOREBOARD_ENABLED=1`; **its first result is unflattering: Breakout-list stocks lagged the whole liquid universe over
  25 Jun - 18 Sep**), `backfill_snapshots.py` (59 past sessions reconstructed), `price_guard.py` (the split-shape rule shared with `performance.py`), `insights.py` + `morning.py` (assistant: full lists, aligned list,
  past breakouts, `/week`, `/scan` CSV, `/screen` grammar, opt-in morning message; table `bot_user_settings`). The digest rows now carry a "small cap" tag (< $2B, from `daily_fundamentals`).
  Findings: the dashboard screener's 62 breakouts vs the digest's 51 = the screener has no liquidity floor / integrity guard (digest is the reference); a partial-date row silently emptied 200-day statistics
  (fixed); the ML dataset's 3x jump rule misses 2-for-1 splits (a stricter shared rule is used for the scoreboard); the universe has BRK.B and BRK/B; Bitcoin has no row for 18 Sep. DEV Task Scheduler jobs
  `FirstLight-1-UpdatePrices` (05:00) and `FirstLight-2-SendDigest` (06:00) registered; the bot was restarted on the new code. Local commits `1f9fc35`, `4d4df43` + a final one; nothing pushed. 1,494 tests, 39/39 mutation
  patterns present, 16/16 tracker mutants caught. Production untouched and locked.

- **2026-09-21 (channel content report)** — Wrote [CHANNEL_CONTENT_REPORT_2026-09-21.md](CHANNEL_CONTENT_REPORT_2026-09-21.md): verified data inventory, free-vs-paid split, ten
  extra channel posts buildable from data already stored (market health, sector rotation, gaps/volume, near-highs, aligned-timeframe teaser, news, weekly recap, base-rate card),
  backlog D1-D18, blockers B1-B5, decisions Q1-Q8. Nothing was built or sent. Findings worth knowing: the dashboard screener and the digest disagree on the breakout count
  (62 of 1,832 vs 51 of 2,915, cause unconfirmed); our own base rate for long 20-day breakouts 2018-2026 is 51% hitting the 2xATR risk level, mean +0.04R; the digest's top
  gainers skew to single-digit-dollar stocks (a market-cap tag is proposed); vendor licences for a paid tier are unchecked.

- **2026-09-21 (channel content rule)** — User rule: **a channel carries only data (the daily digest), promotion, news and information; the private assistant provides everything else in the private chat between the user and the bot - never in a channel.** I had posted the 35-screen assistant tour into the dev channel; removed it (ids 12+, kept Start-here / digest / promo). `qa_live.py --send-to-owner` (old `--send-to-dev` is an alias) now sends the tour to the owner's PRIVATE chat via a new `from_env("owner")` target; `replay_dev_channel.ps1` posts only Start-here + digest + promo (`-Tour` = private tour). Structural tests: the tour tool cannot use a channel target, the replay never sends the tour to the channel, and no channel-posting module may import the assistant's screens/tracker/access/bot or query its tables (3 mutation checks caught). 1,357 tests.

- **2026-09-21 (new dev channel live)** — `TELEGRAM_DEV_CHAT_ID` now points at the fresh private channel **"First Light - Dev"** (the old group's id is kept as a comment in `.env`). `replay_dev_channel.ps1` posted the whole experience into it: pinned Start-here post, the daily digest (photo + 3 messages, with buttons), the promo image, and the assistant tour (47/47 accepted by Telegram). Found on the way: `qa_live.py` shadowed a variable in the new funnel screens (fixed). The bot log showed 3 brief `TelegramConflictError`s (another getUpdates consumer, most likely a browser id lookup) - self-healed.

- **2026-09-21 (access flow + dev reset lesson)** — Built FUNNEL_PLAN.md §3-§4: request-access (`bot_requests`, `funnel_events`, `add_access_flow_tables.sql` applied), owner Approve/Decline buttons, `/requests`, `/funnel`, `BOT_ACCESS_MODE` (approve/auto/closed), 7-day decline cool-down, 14-day purge; the channel header button carries `?start=ch` (aggregate counter only). New bot avatar + channel logo (`promo_assets.py`), `channel_posts.py` (pinned Start-here + promo), `dev_chat_reset.py`, `replay_dev_channel.ps1`, owner forward-a-post -> chat id helper. 1,349 tests, 10 more mutation checks caught. **Finding:** Telegram refuses bot deletion of messages older than ~48 h (tested: 88 recent deleted, everything older refused, even #5), so a new channel is the only clean reset (dev now, production at launch). Found+fixed on the way: `delete_message` read "chat not found" as "message gone"; the owner-only fallback rejected malformed owner buttons.

- **2026-09-21 (dev-first workflow + production lock)** — User rule: build and test everything on the DEV group; when ready, reset the production channel and promote it with daily posts. Added the production launch lock (`PROD_SENDING_ENABLED`, `telegram_client.from_env`, wrapper pre-check, 3 tests + a mutation check), FUNNEL_PLAN.md (channel -> request -> approve -> activate -> retain; §8 launch checklist, reset options, promotion plan), RUNBOOK §5 (inviting a friend). Nothing was posted to or deleted from the production channel; read-only checks found the bot has admin rights there (post/edit/delete/invite/change info), the creator is the user, nothing is pinned, the description is the old top-15 text.

- **2026-09-21 (assistant tracker core built and running)** — Built BOT_DESIGN_REPORT.md with its recommended answers to Q1–Q8 (the user said "build
  that bot and run that" without answering them; tell them if any should change): browse = the channel's lists only, but ANY stock in the daily scan
  (2,866) can be added; watchlist defaults to the last close (editable), portfolio takes the user's own price + optional shares; one row per symbol
  (watch OR hold); "each symbol once" = one row per view + one news/chart fetch per symbol per day shared by everyone; English first; caps 25 + 25;
  no field encryption yet (D5 still open: restricted DB role only). New: `performance.py`, `tracker.py`, `screens.py`, `news_service.py`, `chart.py`,
  `add_tracker_tables.sql` (applied), `run_bot.py` rewritten (guide, persistent menu, edit-in-place navigation, typed-ticker shortcut, guided price
  prompt with a 10-minute expiry, export/erase), `qa_live.py` extended. **QA:** 1289 tests (was 992) incl. hand-computed maths, 16 mutation checks
  (all caught), a real-Postgres round trip of the tracker SQL, the whole bot driven against real Postgres data, and 37 live checks accepted by
  Telegram on the dev chat. The new tests found real defects, all fixed: the split guard missed common 2-for-1 / 3-for-2 splits (it only saw >3x
  moves), `$-0.00` for a rounded-away loss, a blank placeholder button in the paging row (Telegram can reject it), the chart crashed for stocks
  with < 22 bars (new listings), and "entry" in a prompt title (banned word). **Windows note:** `run_bot.py` now selects the selector event loop
  (`aiodns` 3.2 refuses the default Proactor loop; the old build started anyway for an unknown reason, this one did not). The old bot process
  (PID 4008) was stopped and the new one started in the background from this session — if it is not running, start it with
  `python mechanism/alerts/run_bot.py`. Still open: BotFather "Allow Groups" off (only the owner can do it), 7.2 morning DM, hosting/scheduler
  (the snapshot is 3 days old until the digest/pipeline run again), legal review before the first non-owner invitation, encryption (D5), Hebrew.

- **2026-09-21 (private assistant 7.0)** — Continued PRIVATE_ASSISTANT_PLAN.md: phase 7.0 built. Invite-only access layer (`alerts/access.py`,
  `mechanism/add_assistant_tables.sql` applied to the local DB), every private command + the acknowledgement button gated, owner commands
  (`/invite` `/approve` `/revoke` `/users` `/status`), group replies reduced to a neutral pointer, per-ticker channel buttons and the
  channel-member gate removed, deep links `lv_`/`list` retired, `send_daily_digest.py --buttons` now safe for prod. Built with the plan's
  recommended defaults for D1 and D2 (say so if you want them changed). 992 tests (985 alerts + 7 ML) and 9 mutation checks (auth bypass,
  group leak, owner commands exposed, invitation code logged, data stored for strangers, revoked re-entry, levels button back, purge too
  broad, fail-open on DB error) all caught; the new replies were posted to the DEV chat and Telegram accepted them. The new tests found two
  real bugs: `\d` accepted full-width digits (an id `９９` parsed as 99) and a `-1` "no owner" sentinel could match a user id. Live health
  check now reports `BOT_OWNER_ID` unset (FAIL) and Allow Groups on (WARN) — both are owner steps (see §3.1); the bot process was not running.
  Next: 7.1 portfolio core (needs decisions D3, D5).

- **2026-09-21 (private assistant — plan only)** — End of the long First Light / bot session. Built that day (see §3.1 and MILESTONES 6C):
  First Light digest (long side, plain words), phone-friendly two-line rows, "Midnight Dawn" market card (Pillow), snapshot tables,
  interactive aiogram bot (acknowledgement, watchlist, `/levels` ATR risk framework equal to the dashboard's Strategy page), bot QA
  harness + Telegram-HTML validator + `qa_live.py` (860 tests), trading-day gate. **Then the user decided the direction:** the channel is
  public and must not carry strategy content; strategies and portfolio management are not for everyone; the bot becomes each invited
  user's **private personal assistant** (portfolio, strategy, news for their stocks), connected to the channel but never answering in it.
  Nothing was implemented for that; the plan, rules, data model, phases and decisions are in **PRIVATE_ASSISTANT_PLAN.md**
  (Milestone 7). Known interim exposure until phase 7.0: open-mode bot, group replies, `can_join_groups` on (mitigations listed there,
  none applied). Also found: the dashboard's screener sees 7 breakouts vs the digest's 51 on 2026-09-18 (cause unconfirmed).

- **2026-09-21 (trading-day gate)** — Per user request: don't update the ~3,000 symbols or post to Telegram when the
  market was closed. Found first: `logs/` showed the pipeline starting on Sat 19th / Sun 20th / Mon 21st (nothing
  gated it), and both senders only skipped weekdays listed in `ALERTS_SKIP_WEEKDAYS` (empty in `.env`; cannot know
  holidays) and accepted price data up to 4 days old — so Sunday/Monday 06:00 would have re-posted Friday's digest.
  Added `mechanism/shared/market_calendar.py` (see §3.1): gate = "a completed US session newer than the one this job
  last finished". Chosen over (a) a weekday check — misses holidays; (b) an `exchange_calendars`/`pandas_market_calendars`
  dependency — offline but needs yearly upgrades; (c) inferring closure from the provider's newest ^GSPC/SPY bar —
  ground truth but a late vendor bar looks identical to a closure. Verified Alpaca's calendar live (2026-07-03 absent,
  2026-11-27 close 13:00). Wired into `automation_pipeline.sh` (gate before step 1, `mark` after the last step) and both
  senders. Behaviour notes: skipping also skips step 1's weekend BTC-USD fetch (caught up by the next run's `5d` window)
  and the daily fundamentals/quarterly steps; an intraday manual run needs `--force`. The first `--send`/pipeline run
  after this change has no state, so it processes the latest completed session once. 67 tests pass
  (`python -m pytest mechanism/alerts/tests -q`); skip paths verified end to end with test markers (removed afterwards).

- **2026-09-20 (Telegram alerts, first slice)** — Built `mechanism/alerts/` (see §3.1) after three studies
  (scratch scripts, not in the repo; universe = current liquid constituents, so survivor-biased and bull-market
  heavy; entry = next open; abnormal = minus same-date universe mean):
  - *Price-only "sleep → awakening candle"* (volume ≥ 3× + ≥ 2.5 ATR move + strong close, quiet ATR%, from the lows):
    no significant abnormal return (+1.2% at 20d, CI −1.1..+3.7 for the from-the-lows variant; regime dependent).
  - *Top-15 gainers vs a weekly/monthly false-breakdown filter* (week/month low sweeps the prior 4 weeks / 3 months
    and closes back in the top 30% of range): the plain gainers list carries a positive 60-day abnormal drift
    (+3.1%, CI +1.0..+5.0) but ~2/3 of alerts lose at 20 days; the false-breakdown filters did **not** improve it
    (weekly version worse). Fixed 2/4/6-ATR targets ≈ break-even.
  - *Tail economics* (250 bars, runner exit = 2×ATR initial stop then trail from highest close): top gainers reach
    +100% in 28.5% of cases vs 10.6% for random liquid stocks (+300%: 7.9% vs 1.5%), yet mean R is no better than
    random (+0.45R vs +0.48R with a 40% trail; the wider trail beat 25%): the edge is the wide-trail exit on a
    survivor universe, not stock selection. 100R monsters ≈ 1 in 1,000-3,000 trades; wins come from many mid-size
    runners. Scale-outs cost expectancy: pure runner +0.42R / P(R≥20) 1.0% vs "1/3 at +2R then breakeven" +0.27R /
    0.41% vs 25% ladder +0.23R / 0.07% → default template `balanced`, `runner` and `ladder` selectable.
  - Alert cards therefore show *facts* (volume, ATR, distance from 52-week high, weekly/monthly candle shape, news
    links, plan) rather than a claimed edge; the header says so. Cards sort by number of flags met.
  - Connectivity verified the same day: a labelled test message was delivered to both the dev and the prod
    channel (bot token and both chat ids are set in `.env`; neither is ever printed or committed).
  - Not done: outcome tracker for the `alerts` ledger, chart images (own daily/weekly/monthly panels with stop/TP
    drawn), inline "Traded / Watch / Skip" buttons, scheduler / pipeline step at 06:00 Jerusalem, a tail-focused
    ranking model (target = volatility-adjusted runner R, judged on top-decile tail lift), EDGAR-based earnings /
    net-income turnaround features (needs a contact e-mail in the SEC User-Agent).

- **2026-09-20 (ML integrity fixes 1-3)** — Executed fixes 1-3 of the ML audit (data
  integrity, honest evaluation, shared train/serve features). Summary of what changed and why:
  - **Root cause of the label/feature corruption:** the price series was re-based in place
    (Tiingo) but derived tables were not recomputed (see §5). Fixed at the ML layer by
    computing everything from `stock_prices` in one pass (`ml_training/features/price_features.py`,
    indicators verified against fresh `technical_indicators` rows: median rel. diff ~3e-5),
    plus the discontinuity registry and liquidity floor described in §5. Dataset rebuilt:
    594,888 samples 2018→2026 (was 65,846 on a corrupted basis).
  - **Train/serve parity by construction:** the enhancer calls the same feature module;
    `test_feature_parity_db.py` recomputes 150 stored rows through the live inference path
    (<1e-6). Removed the three legacy feature methods (bollinger/turnaround_volume/piotroski
    skew). `predict_ml_momentum()` now returns an explicit unavailable reason
    (`no_model`, `not_a_breakout`, `data_discontinuity`, `illiquid`, `feature_mismatch`, …)
    instead of a number; missing features raise instead of defaulting to 0; near-breakouts
    are no longer scored (the model never trained on them).
  - **Honest trainer** (chronological holdout + 30d embargo, early stopping on an inner
    slice, no scaler/imputation, block-bootstrap CI, walk-forward, baselines, calibration,
    promotion gate) — see §4/§8 for results: nothing is promoted.
  - **Consequences fixed in the same change:** `combined_score` no longer ranks unscored
    signals *above* scored ones (missing ML contributes 0, matching `strategy_calc`);
    `strategy_calc` renormalises over available components instead of scoring a missing
    ML value as 0 (strategy scores are therefore on a higher scale while ML is absent);
    `/api/dashboard/top-ai-picks` no longer turns null into `0`; frontend types/renderers
    for these fields are `number | null` ("—"); `/ml-stats` and `/system-health` report the
    *served* model and the latest honest evaluation instead of the legacy 0.70 AUC.
  - **Screener hardening:** `_create_enhanced_results` raised `TypeError` on a null ML
    probability, and the error path then *overwrote* `frontend_data/latest_*.json` with an
    empty result (happened once during this work; output was regenerated). It is now
    null-safe, refuses to save an empty result assembled after an error, and exits non-zero.
  - **Not done / next:** (4) fix confidence tiers and the constant `ml_risk_score` /
    `ml_trade_recommendation` / `ml_predicted_momentum_days` fields, (5) automate
    build/train/outcome tracking in the pipeline, (6) redefine the label / new features,
    recompute stale `technical_indicators`, adjustment-event guard, universe survivorship
    bias (current index constituents only), overlapping consecutive-day breakouts
    (samples are not independent), and the dashboard "AI picks" labeling. UI verified by
    typecheck/lint and the real API only — the dev backend on :8000 was wedged
    (`CloseWait` pile-up) so no browser render was done.

- **2026-09-19 (latest, FM1)** — FRONTEND_FIX_MILESTONES.md FM1 (build health) done:
  `tsc` clean, `next lint` 0 errors / 0 warnings with no `eslint-disable`/`as any`
  left in `src/`, and `NEXT_DIST_DIR=.next-build npm run build` exits 0 with lint on
  (it never had before). What changed in how the system works:
  - **`NEXT_PUBLIC_API_BASE_URL` is now required for production builds.**
    `next.config.ts` throws in the production-build phase if it is unset, and
    `services/api.ts` only falls back to `http://127.0.0.1:8000` when
    `NODE_ENV === 'development'` (an unset var used to silently point every
    visitor's browser at their own localhost). The unused `/api` rewrite and
    `images.domains` were removed — the app calls the API directly via
    `API_BASE_URL`, there is no same-origin proxy.
  - `/screener` is a `Suspense` shell around `ScreenerPageContent` (Next 15's
    prerender fails on a bare `useSearchParams()`; confirmed by running the build).
  - **API types are honest about "unknown".** `StockDetail` is a discriminated union
    (`ActiveSignalSnapshot` | `NoSignalSnapshot`) with `Maybe<T>` fields; the
    dashboard uses the existing `MainPageData`. Missing weekly data now gives a
    `null` trend instead of a fabricated "sideways". The wire types still describe
    what the backend sends today, including its fabricated defaults
    (`volume_ratio` 1.0, `price_change_pct` 0, screener `market_cap` 0 / `"$0B"`) —
    removing those at the backend/pipeline is FM3.4.
  - Found & logged, not fixed (FRONTEND_FIX_MILESTONES.md §11): the screener writes
    `"$0B"` for a missing market cap; the request-latency tracker keys 404s by raw
    URL; active-signal snapshots lack `peg_ratio`; dashboard `fetchAlpha` race.

- **2026-09-19 (FM0)** — Started FRONTEND_FIX_MILESTONES.md at FM0
  (verification harness); FM0.1–FM0.3 done. The `:3001` dev server had been
  returning 500 on every `/stock/*` route because it was started with a piped
  stdout that later closed (`write EPIPE`) — restarted clean, all routes render.
  `next.config.ts` now takes `NEXT_DIST_DIR` so a verification build can't
  corrupt the running dev cache (`.next-build/` gitignored); new
  `frontend/scripts/screenshots.sh` for reproducible headless-Chrome renders
  (procedure in §8). Note: port 3000 on the dev machine is an unrelated app
  (Ollama chat), not a stale copy of this frontend. Five new findings logged in
  FRONTEND_FIX_MILESTONES.md §11 (incl. a backend latency-tracker bug that keys
  404s by raw URL); none fixed yet. Next up: FM1 (build health).

- **2026-09-19 (latest, perf)** — Chart loading time, per user request. Measured
  first, then fixed what the numbers pointed at:
  - **`localhost` → `127.0.0.1` for the API (biggest win, affects every request
    the frontend makes).** On Windows `localhost` resolves to `::1` first;
    uvicorn listens on IPv4 only, so each new connection stalled ~215ms on the
    failed IPv6 attempt before falling back — against a 1.7ms server response.
    `127.0.0.1` connects in ~0.2ms (price-history: ~215ms → ~7ms per request).
    Changed the default in `services/api.ts` (now exports `API_BASE_URL`, also
    used by `app/page.tsx` and `DevQAPanel.tsx`) and `frontend/.env.local`
    (gitignored — a fresh checkout's `.env.local` needs the same value; CORS is
    unaffected since it keys off the *page* origin, `localhost:3001`).
  - **Hover chart no longer waits on the slowest call.** `useHoverStockSummary`
    used `Promise.allSettled` over price-history (~0.2s), signal (~0.2s) and
    earnings (~1.0–1.4s cold — yfinance), so the chart was gated on earnings
    markers. Rewritten to publish each piece as it lands (cached and
    de-duplicated per piece; failures aren't cached). Also staged: bars +
    signal start after 120ms of hover (`prefetch`), earnings only once the
    popover is actually open — a mouse sweep across rows never triggers the
    yfinance call. `StockHoverCard` renders bars/chip/markers independently.
  - **Stock page** `/stock/[symbol]`: earnings fetch was inside the same
    `Promise.all` as detail + price history, holding the whole page behind
    yfinance. Now fired separately; markers appear after the chart.
  - **Measured** (API-side, curl): hover time-to-chart 1443ms → 79ms. **Not
    measured:** in-browser paint time — no browser available in the session.
  - **Not done:** `earnings` is still ~1s the first time per symbol (yfinance);
    pre-warming that cache from `automation_pipeline.sh` remains the open item
    already noted in the 2026-09-19 performance entry. `lightweight-charts`
    is also still in the dashboard's initial bundle (could be lazy-loaded).
  - Also (same session, earlier): chart background tint (`#f1f5f9`) in
    `LightweightCandlestickChart.tsx`; `SymbolHoverLink` popover given an
    explicit white background (NextUI's `bg-content1` doesn't exist without
    its Tailwind plugin, which this app doesn't load); Alpha Finder's symbol
    links now use `SymbolHoverLink`.

- **2026-09-19 (latest, UI)** — Sector Trend chart: a distinct color per sector
  plus a legend explaining them, per user request (supersedes the earlier
  gray-by-default design — 12 identical gray lines gave no way to tell sectors
  apart). `sectorPalette.ts` holds the fixed sector→color/dash map (palette run
  through `validate_palette.js --mode light --surface #ffffff`: all hard checks
  PASS; aqua/yellow/magenta trip the sub-3:1 contrast WARN, which the always-
  visible legend-with-values satisfies as the required relief). `SectorTrendChart.tsx`
  rewritten: ranked legend, hover-spotlight, click-to-select, tooltip listing every
  sector at the hovered date, end-label on the spotlighted line. Tradeoff to know
  about: with 11 sectors, 3 share a hue with another and rely on dash style —
  fine with the legend/hover, but two same-hue lines can look alike at a glance.
  Verified by rendering the real component headlessly in Chrome against live API
  data (default + spotlight states); hover/tooltip interactions themselves were
  not exercised (headless screenshot can't hover). App has no dark theme, so
  only light-mode colors exist.

- **2026-09-19 (fix)** — Dashboard sector **Trend tab rendered a blank
  chart**. Root cause: `sector_performance_daily` had exactly one date (the
  table was created the same day) so every sector series was a single point,
  and `SectorTrendChart`'s `<Line dot={false}>` draws nothing for one point —
  the API/frontend wiring was fine. Also found: `sector_performance_snapshot.py`
  stamped rows with `datetime.now()` rather than the price bar's date, so the
  Saturday run wrote Friday's bars under 2026-09-19 (would have been
  double-counted by the chart's compounding). Fixes:
  - `sector_performance_snapshot.py` now computes one row per (trading date,
    sector) from `stock_prices` for a trailing window (`--days N`, default 7)
    and upserts them — idempotent, so a missed pipeline day self-heals, and
    dates are always real trading dates. `--days 120` backfills history
    (ran once against live DB: 984 rows, 82 trading dates, 2026-05-22 ..
    2026-09-18; the bogus 2026-09-19 rows were deleted).
  - `SectorTrendChart.tsx` shows an explanatory message when fewer than 2
    dates exist instead of an empty plot.
  - Caveat: backfilled history tags each symbol with its *current* sector, and
    ~2,100 symbols still lack a `daily_fundamentals` row (§7.4 gap), so the
    "Unknown" line stays large until the fundamentals step covers the universe.
  - Verified: service output (63 points/sector in the 90-day window), live
    `GET /api/market/sectors/history`, `tsc --noEmit`. Not verified: browser render.

- **2026-09-19 (earlier)** — Phase 2 of the dashboard data-representation
  review: real market-index/macro data and a sector Trend chart, closing
  the two gaps the review identified (no actual index-level data anywhere;
  sector heatmap had no time dimension). Built and verified end-to-end
  against live Postgres, not just written:
  - **New tables** (`mechanism/add_market_data_tables.sql`, applied to the
    live DB this session): `market_index_prices`, `sector_performance_daily`
    — see §5.
  - **New updaters**: `market_index_updater.py` (S&P 500/Nasdaq/Russell
    2000/Dow/VIX/10Y yield/Gold/Crude/DXY/BTC via yfinance directly, since
    Tiingo/Alpaca can't serve index/futures tickers at all — see §3.1) and
    `sector_performance_snapshot.py` (persists what `market_service.py`
    already computed live). Both wired into `automation_pipeline.sh`
    (now 8 steps — see §3.1). Test runs against production: index updater
    backfilled a full year for all 10 symbols in 9s and correctly wrote 0
    rows on a same-day re-run (incremental logic confirmed); sector
    snapshot wrote 12 rows (11 GICS sectors + Unknown) for today.
  - **Confirmed live** (not just documented) that the "Unknown" sector
    problem from §3.1/§7.4 is real and current: today's snapshot shows
    2,102 of ~2,700 active symbols still bucketed as Unknown, because the
    daily fundamentals backfill (this session's phase 1 fix) hasn't run a
    full universe pass yet — only manually tested on 2 symbols so far. This
    should clear on the next full `automation_pipeline.sh` run; worth
    spot-checking `sector_performance_daily` afterward to confirm.
  - **New backend**: `routers/market.py` + `services/market_data_service.py`
    — `/api/market/indices` (latest close/$/%change + sparkline, VIX gets a
    calm/normal/elevated/fear `regime` field) and
    `/api/market/sectors/history` (deliberately a separate class from
    `services/market_service.py` — see §3.1 for the naming-collision note
    left for whoever touches this next). Verified by calling
    `MarketDataService` directly against live Postgres (bypassing HTTP,
    since a live backend process couldn't be kept running across tool
    calls in this session's sandboxed shell — see the verification note
    below): both endpoints returned correct real data, e.g. `^GSPC` close
    7650.50 (+0.17%), `^VIX` 14.81 with `regime: "calm"`.
  - **New frontend**: `components/dashboard/MarketIndicesStrip.tsx` (macro
    strip, placed above the dashboard header — macro regime before
    sector before single-name, per how a trader actually reads a screen)
    and `components/dashboard/SectorTrendChart.tsx` (Heatmap/Trend toggle
    added to the sector card in `app/page.tsx`) — see §3.1 for the
    12-series-palette design reasoning (muted-gray-by-default with a
    single click-to-highlight accent, not a 12-hue categorical palette).
    Loaded the `dataviz` skill before writing either chart, per its trigger
    condition.
  - **Verification performed**: `npx tsc --noEmit` clean; `npm run build`
    produces zero new lint errors from this session's files (it does fail
    on **pre-existing** `@typescript-eslint/no-explicit-any` /
    `react/no-unescaped-entities` errors across several untouched files —
    confirmed pre-existing by reading the flagged lines before this
    session's edits; `next build` was apparently never run clean before
    now, since none of this is new). Backend service/SQL layer verified
    directly against live Postgres (see above). **Not verified**: an actual
    browser render of the new UI. This session's sandboxed shell killed
    backgrounded `uvicorn`/`next dev` processes shortly after each
    Bash-tool call returned (confirmed twice — once via an unrelated 8000
    bind conflict against a backend already running outside this session,
    once via the port going fully unreachable moments after a clean
    startup log), and no browser-automation tool was available in this
    session to drive one directly. Recommend running `npm run dev` +
    `uvicorn main:app --reload` locally (per §8) and eyeballing the
    dashboard before treating the UI half of this as done — the data layer
    is solid, the render is not yet human-confirmed.
  - **Not done**: the `/performance` route's endpoint-latency tracking
    doesn't yet know about `/api/market/*` (new routes since that system
    was built 2026-09-19 earlier the same day) — cosmetic gap, not
    incorrect, since `performance_service.py`'s middleware times every
    request by route template regardless of registration.

- **2026-09-19 (later)** — Wired `fundamentals_updater.py` (daily_fundamentals:
  market cap/PE/PB/sector/quality scores) into `automation_pipeline.sh` as a
  real daily step, resolving §7.4. Prompted by a dashboard-improvement
  discussion (professional market-overview/sector-heatmap review) that
  surfaced the sector treemap was being corrupted by the same root cause
  already logged in the 2026-09-19 QA entry below: with this step never
  running automatically, `daily_fundamentals.sector` was stale/missing for
  ~68% of active symbols, so most of the dashboard's sector heatmap and
  Market Overview breadth stats were lumping the majority of stocks into
  "Unknown" instead of their real sector. Placed as step 4/6, before the
  quarterly fundamentals step and the screener (so the screener/dashboard
  see same-day sector/quality data), after weekly/monthly updates.
  Deliberately did **not** add a staleness filter to
  `fundamentals_updater.py.get_symbols_to_update()` — per the existing
  design decision in §3.1/§7.4, these fields are price-derived and meant to
  refresh every trading day, unlike the quarterly updater, so a filter would
  mean stale valuation data on the dashboard, not just a faster pipeline.
  Instead dropped `rate_limit_delay` 2.0s → 0.5s: that delay predated the
  Tiingo migration (§6a) and was tuned defensively for yfinance-only
  throughput; Tiingo's the active provider now (10,000 req/hour headroom,
  yfinance only a per-symbol fallback), and 0.5s already matches what
  `daily_data_updater.py` runs safely against the same provider. Cuts a
  full-universe run (~3,000 symbols) from ~100+ min to ~25 min with no
  change to what gets fetched. This is step 1 of a larger dashboard data-representation plan
  discussed the same session (sector heatmap/trend toggle, a real
  market-indices/commodities strip) — the rest requires new tables/ingestion
  not yet built; see the conversation for the full plan.

- **2026-09-19 (earlier)** — Added live progress visibility to the daily
  pipeline, per user request ("we need to make a process bar in the daily
  update... to be sure everything is updated as needed"). Two changes:
  - **`mechanism/shared/utils.py`**: new `ProgressBar` class (exported from
    `shared/__init__.py`) — a dependency-free (no `tqdm`, which isn't in
    `mechanism/requirements.txt` or the project venv) in-place `[####----]`
    console bar with `ok=/fail=/eta=` counters. Its `log()` method
    clears-then-redraws the bar around any normal log line so per-symbol
    log output (still going through the existing `logger`, console +
    rotating file handlers both) doesn't get torn apart by the bar's `\r`
    redraws; `update()` ticks the bar once per symbol and pushes a
    milestone line through `logger` (console + file) every ~10% so the
    rotating file log shows the same movement, not just the terminal.
    Thread-safe (guarded by a lock) since `daily_data_updater.py` ticks it
    from its `ThreadPoolExecutor` workers.
  - Wired into all five per-symbol updaters: `daily_data_updater.py` (bar
    spans the whole run across all batches via a `self._progress` set in
    `run_enhanced_update()`; worker threads route their per-symbol
    ✅/⚠️/❌ lines through it via a new `_log_symbol_result()` helper),
    `weekly_data_updater.py`, `monthly_data_updater.py`,
    `fundamentals_updater.py`, `quarterly_fundamentals_updater.py` (the
    latter four are simple sequential `for symbol in symbols` loops, so
    just replaced their existing per-symbol `logger.info(f"Processing...")`
    + "every 50 symbols" progress lines with `progress.log()` /
    `progress.update()` calls in the same spots).
  - **`automation_pipeline.sh`**: each step's `"$PYTHON" script.py >>
    "$LOG_FILE" 2>&1` (output went straight into the log file, terminal
    showed nothing but "Step N/5: Running..." until the process exited)
    replaced with a `run_step()` helper that pipes through
    `2>&1 | tee -a "$LOG_FILE"` instead, so the bar and per-symbol lines
    above are now actually visible in the bash window during a live run,
    not just readable after the fact in `logs/pipeline_<timestamp>.log`.
    Added `set -o pipefail` alongside the existing `set -e` so a failing
    Python step still fails the pipeline correctly now that its exit code
    is behind a pipe into `tee`.

- **2026-09-19 (later still)** — Second performance pass on the routes
  `/api/performance/` (§7 below) flagged critical/warning: root-caused
  rather than just re-tuned. Full details, before/after measurements, and
  two bugs found while testing in **SYSTEM_HEALTH_REPORT.md §8**. Summary:
  - **Fixed the actual root causes**, not per-endpoint symptoms: (1) nearly
    every router was `async def` but called blocking `psycopg2`/file/
    `joblib` code directly, freezing the whole event loop per request —
    converted to plain `def` everywhere this applied (Starlette then runs
    them in its threadpool), matching the pattern `stock.py`'s
    `get_earnings` already used. (2) the full-table-scan /
    correlated-subquery anti-pattern §7.2 fixed in `main.py`'s dashboard
    queries was still present, unfixed, in `market_service.py`
    (market-overview, screener search) and `deep_value_service.py` (scan)
    — rewrote both to the same `LEFT JOIN LATERAL ... LIMIT 1` pattern.
  - **Added caching** where none existed: `deep_value_service.py`'s raw scan
    data (15 min — pipeline-driven, changes ~once/day), `system_health_
    service.py`/`ml_stats_service.py` full reports (2 min), `ml_stats_
    service.py`'s `joblib.load()` feature-importance result (by model
    version), and a new shared `utils.load_ml_enhanced_data_cached()` (5
    min) that `alpha.py`, `strategy.py`, `stock.py`, and `main.py` now all
    share instead of each independently re-parsing the screener's JSON
    output from disk on every request.
  - **Parallelized** independent sub-fetches (`main-page-data`,
    `system-health`'s and `ml-stats`' full reports) via `ThreadPoolExecutor`
    instead of awaiting them sequentially; `main-page-data` also gets a
    background thread that proactively rebuilds its cache ~2 minutes before
    the 15-minute TTL expires.
  - **Found and fixed two real bugs while testing against live Postgres**
    (not mocked), neither on the original symptom list: (1) a **connection-
    pool self-deadlock** in `main.py`'s `_PooledConnection.close()` —
    psycopg2's pool itself calls `.close()` again to discard a surplus
    connection, which re-entered the override and tried to reacquire the
    pool's own non-reentrant lock, permanently wedging it for every future
    request (this is why `/api/health` itself would hang during testing).
    Latent since §7.1's pooling fix, just never triggered until this
    session's parallelization made >`minconn` concurrent connections
    routine. Fixed with a reentrancy guard; verified with a 96-connection
    concurrent stress test. Also bumped `DB_POOL_MIN` default 2 → 8. (2) a
    **SQL division-by-zero** in `market_service.py`'s `donchian_position`
    calculation whenever a symbol's 20-day Donchian channel is perfectly
    flat — this was the real cause of `/api/screener/search`'s previously
    unexplained "100% error rate" reading, not a fluke; fixed with
    `NULLIF`.
  - Verified end-to-end against local Postgres: `/api/deep-value/scan`
    6.85-10.52s → 0.02-0.41s; `/api/system-health/` 6.57s → 0.003-3.7s;
    `/api/dashboard/main-page-data` up to 5.42s → ~0.003s; `/api/screener/
    market-overview` 2.02-2.59s → 0.31-0.33s; `/api/alpha/finder` up to
    2.57s → 0.005-0.045s; `/api/screener/search` 500/division-by-zero →
    200 with real results in 0.24-0.31s. Also load-tested 18 concurrent
    requests across 6 endpoints simultaneously with zero hangs/errors.
  - **Not fixed (flagged as infra/pipeline decisions, out of scope for a
    backend-code pass):** `enhanced_ml_training_data`'s ~8-10s cold-cache
    outlier when Postgres's 128MB `shared_buffers` doesn't have its 108MB
    evicted-and-reloaded (a server config change); pre-warming the
    earnings-date cache from `automation_pipeline.sh` (touches the
    pipeline, not the backend API surface this pass covered).

- **2026-09-19 (later same day)** — Performance tracking + two real fixes, per
  the user asking for scored/tracked latency across the database, every
  backend endpoint, and every frontend route ("that is super critical the
  performance"). Full investigation and before/after numbers in
  **SYSTEM_HEALTH_REPORT.md §7** — summary:
  - **Added `/api/performance/`** (`backend/services/performance_service.py`
    + `backend/routers/performance.py`): an ASGI middleware in `main.py`
    times every request (grouped by route template, e.g.
    `/api/stock/{symbol}`, not per-symbol) and scores it healthy/warning/
    critical by p95 latency; a `measure_database()` benchmark isolates
    connection-acquire cost from query-execution cost. Frontend:
    `hooks/usePagePerf.ts` (wired into `/`, `/system-health`, `/ml-stats`)
    reports real "time to data visible" per route, and
    `components/perf/RoutePerfCollector.tsx` reports browser Navigation
    Timing for hard loads. All surfaced on a new `/performance` page
    (added to `TopNav`). In-memory only — resets on backend restart.
  - **Fixed: no database connection pooling.** `main.py`'s
    `get_database_connection()` was a bare `psycopg2.connect()` on every
    request; the new tracking immediately measured ~43-53ms connection
    tax vs. ~0-20ms actual query time. Added a real `ThreadedConnectionPool`
    via `_PooledConnection` (a `psycopg2.extensions.connection` subclass
    whose `close()` returns the connection to the pool instead of closing
    the socket, so every existing `conn.close()` call site across every
    router/service kept working with zero changes). Connection acquire is
    now ~0.0ms.
  - **Fixed: full-table joins where a per-symbol lookup was meant.** The
    real headline finding — `EXPLAIN ANALYZE` showed the `top-gainers`/
    `top-losers`/`unusual-volume` queries' "previous close" CTEs doing a
    merge join across all 6.4M rows of `stock_prices` (6.3M intermediate
    rows) instead of a per-symbol index seek, right next to
    `technical_indicators`/`daily_fundamentals` joins in the same query that
    did the lookup correctly and ran in microseconds. Rewrote all three CTEs
    in `main.py` to `LEFT JOIN LATERAL ... ORDER BY date DESC LIMIT 1`
    against `idx_stock_prices_symbol_date`. Verified output is identical to
    before (same symbols/percentages/order) — pure perf fix, no behavior
    change. `/api/dashboard/main-page-data` went from a measured **147s**
    worst case down to **2.9s**; `/api/dashboard/top-gainers` from ~6-8s to
    **0.7s**.
  - **Not yet done:** `system_health_service.py`'s own ~9 queries per report
    (still ~6.8s total) haven't been profiled the same way — likely several
    moderately slow aggregates rather than one dominant anti-pattern like
    the dashboard had. Flagged in SYSTEM_HEALTH_REPORT.md §7.3 as the next
    candidate if that page's speed becomes a priority.

- **2026-09-19** — QA session: diagnosed a "frontend stops loading data"
  report, added dev-only endpoint-health tooling, and caught up this file
  after finding it had drifted well behind `backend/` and `frontend/` (six
  routers, a whole `/system-health` + `/ml-stats` subsystem built 2026-09-18,
  and several frontend routes/components existed with zero mention here).
  - **Root cause of the loading complaint:** not the backend — every
    endpoint returned `200`. The `next dev` process holding port 3000 had a
    corrupted webpack pack cache (`invalid code lengths set` in its log) and
    was serving `404` on `/` and `500` on `/system-health`. Fixed by killing
    it, deleting `frontend/.next`, and restarting clean. See
    **SYSTEM_HEALTH_REPORT.md** (repo root, generated same session) for the
    full diagnosis plus a point-in-time health/ML-stats snapshot.
  - **Real issue found in the process, not yet fixed:** `daily_fundamentals`
    coverage is at 31.6% of active symbols (972/3,076) — this is why most
    dashboard rows (top gainers/losers/unusual volume) show `sector: "Unknown"`
    and `market_cap: null`. Root cause matches the already-known §7.4 gap:
    `fundamentals_updater.py` still isn't wired into `automation_pipeline.sh`.
  - **Added:** `frontend/src/components/dev/DevQAPanel.tsx` (floating,
    `NODE_ENV`-gated endpoint-health widget, mounted in `app/layout.tsx`) and
    a 3-way view switch on `/system-health` (Overview / Basic System Health /
    ML Health & Accuracy), the last view sharing a new `MLStatsPanel`
    component with `/ml-stats` instead of duplicating that page's JSX. See
    §3.3 for details.
  - Also updated §3.1's `backend/` and `frontend/` entries to actually list
    what's there now (`routers/stock.py`, `alpha.py`, `strategy.py`,
    `deep_value.py`, `system_health.py`, `ml_stats.py` and their matching
    `services/`; frontend routes `/screener`, `/strategy`, `/alerts`,
    `/stock/[symbol]`, `/system-health`, `/ml-stats`) — none of this was
    new work this session, just previously undocumented.

- **2026-09-18 (later same day)** — Made `quarterly_fundamentals_updater.py`
  staleness-aware and added it to `automation_pipeline.sh` as a real daily
  step (previously manual-only — see §3.1/§4). `get_symbols_to_update()` now
  only calls the API for a symbol with no `quarterly_fundamentals` row yet,
  or whose latest row's `updated_at` is more than 25 days old — cut a live
  test from "all ~3,114 symbols" to 117 (the ones that genuinely had no data
  yet from the same-day backfill). First attempt used the quarter's own
  end-date as the staleness signal instead of `updated_at` and was wrong:
  filings lag quarter-end by 30-45 days, so a freshly-fetched quarter is
  already most of the way through any reasonable end-date-measured window.
  Deliberately did NOT apply the same treatment to `fundamentals_updater.py`
  (daily_fundamentals) — those fields are price-derived and change every
  trading day, so daily refresh is correct there, not waste; that script is
  still not wired into the pipeline at all (§7.4).
- **2026-09-17 (later same day)** — ML feature enrichment for the "growth
  stock turning profitable after years of losing" thesis, plus finishing
  the Tiingo quarterly-statements migration:
  - `mechanism/shared/tiingo_client.py`: added `get_quarterly_statements()`
    (income statement / balance sheet / cash flow, mapped from Tiingo's
    live dataCode fields — verified empirically since Tiingo's own docs
    don't enumerate them). Also surfaced the Dow-30-only Fundamentals API
    restriction documented in §6a.
  - `mechanism/data_updaters/quarterly_fundamentals_updater.py`: wired to
    use Tiingo (with yfinance fallback) via the same
    `fetch_yfinance_quarters` / provider-branch pattern as the other
    updaters; margin/ratio computation (`_compute_ratios`) was extracted
    into a provider-agnostic shared method so historical rows don't shift
    definitions depending on which provider produced them.
  - `mechanism/add_piotroski_score.sql` + `create_trading_schema.sql`:
    added `quarterly_fundamentals.piotroski_f_score` (populates for Dow 30
    names only, per the Tiingo restriction above).
  - **`ml_training/data_preparation/feature_builder.py`**: added
    `get_earnings_trajectory_features()` (reads `quarterly_fundamentals` —
    previously never fed into the ML pipeline at all, only
    `daily_fundamentals`' point-in-time ratios were) and
    `get_breakout_quality_features()` (channel-squeeze percentile vs. a
    symbol's own trailing 120-day history, and ATR-normalized breakout
    distance). New features: `earnings_turnaround` (currently profitable
    AND had a losing quarter in the trailing ~2 years — the literal
    turnaround signal), `consecutive_profitable_quarters`,
    `earnings_growth_yoy`, `eps_growth_yoy`, `net_margin_trend`,
    `piotroski_f_score`, `channel_squeeze_percentile`,
    `breakout_distance_atr`, plus a `turnaround_volume` interaction term.
  - **`mechanism/ml_enhancement/ml_signal_enhancer.py`**: mirrored both new
    feature methods on the live-inference side
    (`get_earnings_trajectory_features` / `get_breakout_quality_features`,
    querying the DB directly since quarterly data isn't in the
    stock_data/fundamentals payloads already passed around), wired into
    `predict_ml_momentum()`. This was necessary, not optional — the
    training-side features are useless in production without a matching
    inference-side implementation (training/serving skew); before this,
    only `extract_ml_features_from_data()`'s older feature set existed at
    inference time.
  - **Not done (needs the user's DB-reachable environment, unavailable in
    the session that built this):** running `add_piotroski_score.sql`,
    backfilling `quarterly_fundamentals` with the updated updater, rebuilding
    `enhanced_ml_training_data` via `feature_builder.py`, and retraining via
    `momentum_predictor.py` (or `ml_training/scripts/ml_pipeline_runner.py
    full`). The new inference-side code is inert until a model trained with
    these feature names is loaded — see the comment at the
    `predict_ml_momentum()` call site. This is also the point to finally
    address §4's "model hasn't been retrained since 2025-07-29" finding.
  - Known limitation carried over from `momentum_predictor.py`'s existing
    imputation (`features_df.fillna(features_df.median())`): since
    `piotroski_f_score` is null for ~99% of rows (Dow-30-only), median-fill
    will make it near-constant and thus low-signal in practice despite
    being a real, well-established metric — a consequence of the Dow-30
    restriction, not of how it was wired in.

- **2026-09-17** — Built Tiingo as a third `DATA_PROVIDER` option
  (`mechanism/shared/tiingo_client.py`), decided in conversation as the
  migration target for scaling from ~1,000 symbols to Russell 3000
  (~3,000), after ruling out Polygon.io ($2,000/mo — too expensive) and
  IEX Cloud (shut down). Wired into both `daily_data_updater.py` (prices,
  Tiingo-then-yfinance-fallback, same pattern as the existing Alpaca path)
  and `fundamentals_updater.py` (fundamentals — new, Tiingo bundles this at
  the same $30/mo tier so it replaces the yfinance `ticker.info` path too,
  not just prices). Added `tiingo_api_key` to `config.py` and a
  `TIINGO_API_KEY` placeholder to `.env`. **Not yet activated** — needs a
  real Tiingo API key (`DATA_PROVIDER` is still `alpaca` in `.env`). See
  §6a for the field-coverage gap this introduces (Tiingo's Fundamentals API
  lacks beta/dividendYield/sharesOutstanding/floatShares). Still open: the
  Russell 2000/3000 constituent list itself hasn't been sourced yet — this
  change only removes the data-vendor ceiling that would have blocked
  adding those symbols (see §7.2).
- **2026-09-14** — Initial CLAUDE.md created after full codebase audit (Claude
  Sonnet 5). Documented architecture, live vs. legacy paths, data flow, known
  bugs, and open questions. Follow-up actions taken same day, per user decision:
  - Deleted the bare `frontend/` scaffold and promoted `frontend_1/` to
    `frontend/` — one canonical Next.js app now.
  - Rewrote `.gitignore`: removed blanket `*.json` / `*.csv` / `stock_lists/`
    rules that would have silently dropped `package.json`, `package-lock.json`,
    `tsconfig.json`, and the symbol-universe CSVs the moment anything under
    `frontend/` or `mechanism/stock_lists/` got `git add`-ed. Replaced with
    explicit ignores scoped to actual generated-output directories
    (`data/`, `frontend_data/`, `breakout_results/`, `reports/`, `logs/`, and
    their `mechanism/` duplicates) plus `.next/`, `.pytest_cache/`, and
    `backups/`. **Net effect: `mechanism/stock_lists/*.csv|.json|.txt` (the
    actual symbol universe) is no longer gitignored and is now visible as
    untracked in `git status` — it should be committed, since a fresh clone
    otherwise has no symbol list to run against.**
  - Did **not** run the ML model performance check
    (`ml_training/evaluation/performance_tracker.py`) — this environment has
    neither `psycopg2`/`asyncpg` installed nor a reachable Postgres instance.
    Still an open action item (§7.1).
