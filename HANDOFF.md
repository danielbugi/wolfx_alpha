# HANDOFF.md — start here in the next conversation

> Written at the end of the 2026-09-21 session. It is the entry point for the **First Light channel + private assistant** work; the wider
> project (screening pipeline, dashboard, ML) is documented in [CLAUDE.md](CLAUDE.md). Keep this file current: at the end of every session
> update §2, §3 and §7, and move finished items out. No tokens, keys or personal ids are in it (they live in `.env`).

## 0. Read in this order (10 minutes)
1. This file, then **[CLAUDE.md](CLAUDE.md)** (§3.1 the bot paragraph, §5 tables, §6 env + production lock, §9 changelog top entries).
2. **[REPORT_FIRST_LIGHT_2026-09-21.md](REPORT_FIRST_LIGHT_2026-09-21.md)** — what was built, how it was tested, what is unproven (§10–§11 = later corrections).
3. **[RUNBOOK_FIRST_LIGHT.md](RUNBOOK_FIRST_LIGHT.md)** — run the bot, a 29-step manual test, the morning routine, inviting a friend, the fresh dev channel, the request-access flow.
4. **[FUNNEL_PLAN.md](FUNNEL_PLAN.md)** — channel → request → approve → activate → retain; the launch checklist (§8) and the channel content policy (§10).
4a. **[CHANNEL_CONTENT_MILESTONES.md](CHANNEL_CONTENT_MILESTONES.md)** — the execution tracker of the channel-content plan: what was built, evidence, what is blocked, data notes. **Read this for the current state of the extra posts and the new assistant commands.**
4b. **[CHANNEL_CONTENT_REPORT_2026-09-21.md](CHANNEL_CONTENT_REPORT_2026-09-21.md)** — data inventory, free-vs-paid split, ten extra channel posts buildable from stored data, development backlog (D1–D18), plumbing blockers (B1–B5), licence/regulation flags, decisions Q1–Q8.
5. Only if touching the bot: **[PRIVATE_ASSISTANT_PLAN.md](PRIVATE_ASSISTANT_PLAN.md)** §0 (binding rules) and **[BOT_DESIGN_REPORT.md](BOT_DESIGN_REPORT.md)**.
6. Memory notes (auto-loaded): `channel-vs-assistant-content`, `dev-first-prod-locked`, `private-assistant-plan`.

## 1. The product in one paragraph
A daily **First Light** digest (market card + Breakout / Near-breakout lists ranked by gain, ATR, volume) posted to a **channel**; a **private,
invite-only Telegram assistant** (display name "First Light Trading Assistant" (the user's BotFather rename, live), username `@aplha_wolf_bot` — sic, "aplha"; changing it would change every link) where a user browses *Today's lists*, opens a
stock card (facts, news, chart, ATR levels) and keeps a **Watchlist and a Portfolio that track performance since the day they added each stock**.
People reach the bot through the channel's "Private assistant" button → **Request access** → the owner approves. Free during the beta.
**Binding rules:** (a) *a channel carries only data, promotion, news and information — the assistant's screens live only in the private chat with the bot*;
(b) *everything is built and tested on the DEV channel; production is locked until launch*; (c) facts and the user's own numbers only — never advice,
never a fabricated number (`n/a` with a reason); (d) minimum personal data, isolated per user, never logged.

## 2. State at the end of the session (verified 2026-09-21)
| Area | State |
|---|---|
| Tests | **1,494 pass** (`python -m pytest mechanism/alerts/tests ml_training/tests -q`, ~55 s) |
| Mutation checks | **39 / 39 caught** — `python mechanism/alerts/tests/mutation_checks.py` (`--check` = patterns still present; `--group access|tracker|flow|guards`) |
| Bot | **restarted 2026-09-21 evening on the newest code** (background process; new commands `/full /aligned /history /week /scan /screen /morning` and the morning-message loop; the PID changes on every restart — verify, §8); invite-only; `BOT_ACCESS_MODE=approve` |
| Bot name | display name is "First Light Trading Assistant" (the user's BotFather rename, confirmed live via getMe); **Allow Groups is still ON** (`can_join_groups` = true; the user must turn it off) |
| Avatars | generated (`reports/first_light/promo/`), **not confirmed uploaded** — the user does it in BotFather (RUNBOOK §6) |
| Dev channel | private channel **"First Light - Dev"**, id in `.env` as `TELEGRAM_DEV_CHAT_ID` (the old group "BOT_SPAMMING" is kept as a comment). Holds only: pinned Start-here post, one digest (photo + 3 messages), the promo. |
| Production | old public channel "Top Gainers - Daily" (5 members, old top-15 posts, description from the old product) — **untouched and locked**: `PROD_SENDING_ENABLED=0`. A new prod channel will be created at launch (bots cannot delete posts older than ~48 h). |
| Database | migrations applied locally: `add_assistant_tables.sql`, `add_tracker_tables.sql`, `add_access_flow_tables.sql`, **`add_morning_dm_tables.sql`** (apply them on any other database). `digest_runs` now holds **60 sessions** (59 reconstructed by `backfill_snapshots.py` from today's adjusted prices + 18 Sep) |
| Data freshness | last stored session = **Fri 18 Sep** until the scheduled jobs run for the first time (Tue 22 Sep 05:00 / 06:00); the assistant shows its "US close" date and warns when stale |
| Scheduling | **Registered 2026-09-21, DEV only:** Task Scheduler `FirstLight-1-UpdatePrices` (05:00: index + prices + snapshot) and `FirstLight-2-SendDigest` (06:00: digest + ONE extra post, Sunday: weekly recap), both `-To dev`. The PC must be on and you logged in. Remove: `Unregister-ScheduledTask -TaskName FirstLight-1-UpdatePrices -Confirm:$false` (same for `-2-`). Let it run **5 trading days** untouched before considering launch |
| Git | **Committed locally, not pushed:** `1f9fc35` (checkpoint), `4d4df43` (M0–M5.1) and a final commit for M5.2–M6. `.env`, `reports/`, `logs/`, `backups/`, `*.joblib`, agent tooling are ignored |
| Channel content | Built and tested (owner's private chat + dry runs): 9 extra post kinds + the small-cap tag — see **CHANNEL_CONTENT_MILESTONES.md** (status of every item, what is blocked, data notes). Off until you enable them: **news** (`CHANNEL_NEWS_ENABLED=1`, licence unchecked) and the **scoreboard** (`CHANNEL_SCOREBOARD_ENABLED=1`, unflattering numbers) |
| Humans | **no human has used the bot end to end in Telegram yet**; no friend/beta user invited |

## 3. What exists (map)
**Bot** — `mechanism/alerts/`: `run_bot.py` (handlers, one process, lock port 47831) · `access.py` (owner/invite/approve/revoke, request-access, modes, funnel) ·
`tracker.py` (watchlist/portfolio service + input parsing) · `performance.py` (Decimal maths, half-up, split guard) · `screens.py` (every screen as text + buttons, pure) ·
`texts.py` (all copy; the wording guard scans it) · `news_service.py` (Alpaca, cached 6 h, shared) · `chart.py` (mplfinance) · `bot_service.py` (stores: `PgStore`; levels) · `deeplink.py`.
**Channel tooling** — `send_daily_digest.py` (digest) · `channel_posts.py` (Start-here + promo posts) · `promo_assets.py` (promo image, bot avatar, channel logo) ·
`telegram_client.py` (**the only** way to reach a chat; holds the production lock; targets `dev`, `prod`, `owner`) · `dev_chat_reset.py` (dev groups only; clears the last 48 h) · `qa_live.py` (health check; `--send-to-owner` = the assistant tour to the owner's PRIVATE chat).
**Scripts (repo root)** — `run_first_light_morning.ps1` (gate → index → prices ~21 min → digest ~1 min; `-Send`, `-To dev|prod`, `-UpdateOnly`, `-SkipUpdate`, `-Force`) · `replay_dev_channel.ps1` (channel content only; `-Tour` = private tour).
**Channel content (added late 2026-09-21; item map in CHANNEL_CONTENT_MILESTONES.md)** — `send_channel_posts.py` (the ONE extra silent post per session + Sunday recap; `--all` review; dry run default) · `channel_content.py` (pure builders + the weekday rotation `pick_kinds`) · `channel_cards.py` (health / sector / macro PNG cards) · `market_stats.py` (wide-matrix market facts) · `channel_news.py` (news, off by default) · `scoreboard.py` (list scoreboard, off by default) · `backfill_snapshots.py` · `price_guard.py` (the shared split-shape rule, also used by `performance.py`).
**Assistant member features (added late 2026-09-21)** — `insights.py` (full lists, aligned, history, week, scan CSV, `/screen` grammar) · `morning.py` (morning-message rules) + `morning_round`/`morning_loop` in `run_bot.py`.
**Tables** — `bot_users, bot_access, bot_invites, bot_audit, bot_requests, bot_tracked, bot_user_settings (morning switch), news_items, news_fetched, bot_chart_cache, funnel_events` (+ the earlier `digest_runs, digest_stocks`).
**Tests** — `mechanism/alerts/tests/`: `qa_harness.py` (in-memory store + fake Telegram + real dispatcher; `drive(..., enroll=False)` for access tests), `test_bot_qa.py` (section K = access), `test_assistant_flows.py`, `test_access_flow.py`, `test_tracker.py`, `test_performance.py`, `test_news_chart.py`, `test_channel_tools.py` (production lock + channel isolation), `test_promo_assets.py`.
**Bot commands.** Users: `/today /portfolio /watchlist /stock /add /remove /levels /export /deleteme /privacy /guide /help /about` **+ `/full [near] /aligned /history SYM /week /scan /screen … /morning on|off`** + the bottom menu + typing a ticker. Owner: `/invite /approve /revoke /users /requests /funnel /status` + forward a channel post to get its chat id.
**Config (`.env`)** — `TELEGRAM_BOT_TOKEN, BOT_OWNER_ID, TELEGRAM_DEV_CHAT_ID, TELEGRAM_CHAT_ID (prod), PROD_SENDING_ENABLED (0), BOT_ACCESS_MODE (approve|auto|closed), BOT_MAX_MEMBERS (25), BOT_MAX_PENDING (100), ALPACA_API_KEY/SECRET (news), BOT_MSG_LIMIT/BOT_POPUP_LIMIT/BOT_HEAVY_LIMIT, ALERTS_TIMEZONE`, **optional switches: `CHANNEL_NEWS_ENABLED=1` (news post in the rotation), `CHANNEL_SCOREBOARD_ENABLED=1` (first-Friday scoreboard)**.

## 4. Decisions
**Made** (the user said "build" without answering Q1–Q8, so the report's recommended defaults were used — say so if they want changes): browse the channel's lists only but any stock in the daily scan can be added ·
watchlist defaults to the last close, portfolio takes the user's own price + optional shares · one row per symbol · English first · caps 25 + 25 · no field encryption yet (restricted DB role only) ·
request-access flow built (stores Telegram id + time *only after the tap*, deleted on decision / after 14 days; 7-day decline cool-down) · new channel for dev (done) and for production (at launch) ·
prod locked in code · the user's phrase "daily advertisements" = the digest + one rotating promo post a day (**my interpretation, unconfirmed**).
**Pending — need the user:** (1) approve building a daily **market-news post** for the channel (headlines on the day's movers) and the weekly "how to read"/recap note · (2) confirm the daily-advertisements meaning ·
(3) legal/privacy review timing (hard gate before strangers get access) · (4) hosting: PC always on vs a small VPS · (5) field encryption of shares/prices (D5) · (6) Hebrew UI · (7) member referral invites (later) · (8) commit the work to git (see §2).

## 5. Next steps, in order
1. ~~Commit the work~~ **done** (local commits; not pushed — pushing/remote is the user's call).
2. **Human QA on dev** — the user runs RUNBOOK §2 (29 steps) and §8 (request-access with a second account), **plus the new commands** (`/full /aligned /history AAPL /week /scan /screen breakout vol>3 /morning on`); then a friend session (§5.4). **First real test of the popup buttons inside a CHANNEL.** Fix whatever they find (write a regression test first).
3. User uploads the two avatars in BotFather, turns **Allow Groups off**, optionally sets the dev channel photo/description.
4. ~~Build the approved channel content~~ **built** (CHANNEL_CONTENT_MILESTONES.md). **Owner decisions left:** enable the news post (after the licence check) and/or the scoreboard (`.env` switches), and review the extra posts in the dev channel after the first scheduled run.
5. ~~Register the two Task Scheduler tasks~~ **registered (dev)**. **Now:** let them run **5 trading days** untouched (first run Tue 22 Sep 05:00 / 06:00); check `logs/first_light_morning_<date>.log` each day; fix what breaks.
6. ~~Morning DM~~ **built** (`/morning on`, opt-in). Untested with a real member on Telegram — part of step 2.
7. Hosting decision + always-on bot; legal/privacy review; update `/privacy`; check the news licence; decide D5.
8. **Launch** (FUNNEL_PLAN §8 checklist): new production channel "First Light — Stocks & Info", its id in `TELEGRAM_CHAT_ID`, pinned Start-here, promo, then `PROD_SENDING_ENABLED=1`, then the daily posts + promotion.
9. Later: 7.4 personal strategy profile/journal, Hebrew, referral invites, paid tier (Telegram Stars; `tier` column exists).

## 6. Traps and lessons (they cost time this session — do not repeat)
- **Never write scripts with backslashes through a bash heredoc.** `\a`, `\f`, `\n` inside the heredoc became control characters and corrupted a runbook section (repaired; a check found no others). Use the **Write tool to create a script file, then run it**; use the Edit tool for exact-string edits.
- **Bots cannot delete messages older than ~48 h** (Telegram answers "message can't be deleted", even for message #5). A clean slate = a **new channel**. A bot also cannot read channel history.
- **Windows:** `run_bot.py` sets the *selector* event loop (`aiodns` refuses the default Proactor loop). Only one bot may poll: a second `getUpdates` consumer (e.g. opening `/getUpdates` in a browser) causes brief `TelegramConflictError`s.
- Stop the bot **by PID from the lock port** (`Get-NetTCPConnection -LocalPort 47831`), never by image name. Restart it after any code change.
- **Production is locked** (`PROD_SENDING_ENABLED`). Never flip it, post to, rename or delete in the prod channel without the user's explicit go-ahead naming that action. The reset tool refuses channels.
- **Channel ≠ assistant.** Never send assistant screens, QA dumps or tours to any channel; previews go to the owner's private chat (`qa_live.py --send-to-owner`).
- The **wording guard** bans advice words in all copy (`entry, buy, sell, target, stop, should, profit, pick, signal, alpha, …`) — write "your price", not "entry". It scans `texts.py`, every rendered screen, the promo copy and the channel posts.
- Grep/glob across the repo root is slow (`frontend/node_modules`, `backups/`): scope searches to `mechanism/`.
- A new test that fails is often a real bug (this session: unicode digits as ids, a 2-for-1 split missed, `$-0.00`, blank paging button, chart crash under 22 bars, "chat not found" read as "message gone"). Investigate before loosening an assertion.
- The command safety classifier can be temporarily rate-limited; read-only tools (Read/Grep/Glob) keep working — continue with those.

## 7. Unproven / risks (do not present these as done)
No end-to-end human test (also none for the new commands and the morning message) · scheduled morning job never ran unattended (first run Tue 22 Sep) · news feed licence not checked · vendor redistribution terms not checked · no legal/privacy review (now also the morning switch) · popup buttons in a channel untested · hosting (a sleeping PC stops both the bot and the post) ·
the scoreboard is unflattering (Breakout lists lagged the whole universe over 25 Jun – 18 Sep) — do not publish without the caveats · backfilled snapshots use today's adjusted prices · the universe has a duplicate ticker (BRK.B / BRK/B) ·
the lists have **no proven predictive edge** (our own studies) — all copy stays educational, no performance claims · Allow Groups on · data is 3 days old until the daily jobs run.

## 8. Verify the state in two minutes
```powershell
python -m pytest mechanism/alerts/tests ml_training/tests -q             # expect 1,364 passed
python mechanism\alerts\qa_live.py                                       # read-only health: expect 12+ PASS, only the Allow-Groups WARN
python mechanism\alerts\tests\mutation_checks.py --check                 # expect: 39 mutants | patterns not found: none
Get-NetTCPConnection -LocalPort 47831 -State Listen -EA SilentlyContinue # the bot running? (else: python mechanism\alerts\run_bot.py)
python mechanism\shared\market_calendar.py status                        # latest completed US session + per-job state
.\run_first_light_morning.ps1                                            # PREVIEW of the digest (sends nothing)
git status --short | Measure-Object -Line                                # how much is still uncommitted
```
