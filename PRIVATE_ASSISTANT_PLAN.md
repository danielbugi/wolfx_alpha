# PRIVATE_ASSISTANT_PLAN.md — the private, per-user portfolio assistant

> **STATUS (end of 2026-09-21): 7.0 (access), 7.1 (portfolio/watchlist tracker), 7.3 (news), the guide/menu/chart and the request-access flow are BUILT and tested on DEV
> (1,364 tests, 39 mutation checks). Not built: 7.2 (morning DM), 7.4 (personal strategy profile/journal), 7.6 (hosting). Start a new session at [HANDOFF.md](HANDOFF.md).**
>
> **Rule added by the user 2026-09-21 (binding, §0-equivalent): the assistant NEVER posts in a channel.** A channel carries only data, promotion, news and information;
> everything the assistant does happens in its private chat with the user. Build and test on the DEV channel; production stays locked until launch.
>
> Original 7.0 note: decided by the user at the end of the earlier 2026-09-21 session; D1 (invite-only) and D2 (one neutral
> channel button) were implemented with their recommended defaults - say so if you want them changed. Living document: check items off and log decisions here, and mirror the status in
> [MILESTONES.md](MILESTONES.md) (Milestone 7) and the architecture notes in [CLAUDE.md](CLAUDE.md).
>
> **Product design for 7.1–7.3 (2026-09-21, user's direction: the bot follows the channel's lists; watchlist + portfolio track performance
> "since the day added"; onboarding; per-stock data + news + chart, each symbol once):** see **[BOT_DESIGN_REPORT.md](BOT_DESIGN_REPORT.md)**
> (findings, screens, onboarding copy, data model, build order, decisions Q1–Q8). It refines 7.1–7.3 below; D4 is closed there (Alpaca
> free news verified working).

## 0. Ground rules (binding — read before touching the bot)

1. **Private by default.** Strategy levels, portfolio, journal and per-stock news are delivered ONLY in a private chat with an
   *authorised* user. Never in a group, never in the channel, never to a user who has not been let in.
2. **The channel stays public and educational.** It keeps the daily broadcast (market card + Breakout / Near breakout lists) and nothing
   personal. The bot's role there is at most one neutral pointer ("private assistant — invite only").
3. **Root cause, no band-aids; no fabricated numbers.** Missing data is shown as unavailable (`n/a`), never defaulted or guessed (same
   rule as FRONTEND_FIX_MILESTONES.md §0).
4. **Facts and the user's own numbers — not advice.** Personalisation must never turn into "you should". Everything computed from what
   the user entered or from the shared formulas. Keep the disclaimer + acknowledgement, the wording-guard test and the honest
   "distances, not predictions" caveats. A legal review is a hard gate before anyone other than the owner is given access (§9).
5. **Economic.** Cost must not grow per request: answers come from the daily snapshot / cached data; external calls (news) are batched
   per *unique symbol*, never per user; DMs are opt-in; per-user rate limits stay.
6. **Portfolio data is sensitive.** Minimum collection, isolated per user, never logged, exportable and deletable by the user (§4).
7. **Every feature ships with QA:** scenario tests in the existing harness (`tests/qa_harness.py`, `tests/tg_html.py`), a *leak* test
   (nothing personal reaches a group / another user / an unauthorised user) and a mutation check that the test really fails on the bug.
8. **Dry-run first, send only when asked.** The bot process must be restarted after any code change (stale processes caused real confusion).
9. **Skills to load:** `SKILLS/telegram-bot` + `SKILLS/telegram-bot-ui-design` (bot + message UX), `dataviz` for any image/chart,
   `SKILLS/backend-development` for schema/service work. Never print or commit the bot token.

## 1. Product boundary

| | Public channel | Private assistant (this plan) |
|---|---|---|
| Audience | anyone (public, ~30K target) | invited users only, one private chat each |
| Content | market card, Breakout / Near breakout lists, definitions | portfolio, positions, journal, personal levels, news for *their* stocks, personal morning brief |
| Personal data | none | yes (sensitive) |
| Interaction | read-only | commands + buttons, per user |
| Cost model | flat (one post) | ∝ users, kept tiny by snapshot reads + batched news |

The assistant is "connected to the channel": the channel is the funnel and the shared market context; the assistant is where a member
manages *their own* stocks against that context.

## 2. What already exists (reuse) and what must change (delta)

**Reuse as is:** `digest_runs` / `digest_stocks` snapshot (every liquid stock's close, 1-day %, volume ×, range × ATR, ATR(14), group,
list ranks); `bot_users` (+ acknowledgement), `bot_watchlist`; `bot_service.py` (rate limiter, member gate, watchlist, `/levels` render);
`levels.py` (2×ATR risk level, 1R/2R/3R reference levels — equal to the dashboard's Strategy page, asserted by a test); `run_bot.py`
(aiogram 3, single-instance lock, error handler, stale-tap tolerance, `/agree`); `deeplink.py`; QA harness, Telegram-HTML validator,
`qa_live.py`; `market_card.py`; the wording guard; `alerts/texts.py`.

**Must change (built in the 2026-09-21 session, contradicted the decision) — ✅ ALL DONE in 7.0 except the last line:**
- ⛔ **Group replies.** `run_bot.py` answers `/levels` publicly inside groups/supergroups (built for QA). Remove all strategy/levels
  content from groups; keep at most a pointer reply with a button that opens the private chat.
- ⛔ **`<TICKER> levels` buttons under the channel posts** (`--buttons`, `digest_format.group_keyboard`). Strategy is no longer "open to
  everyone": replace with ONE neutral "Private assistant (invite only)" button, or none (decision D2).
- ⛔ **Open mode.** With `BOT_GATE_CHAT_ID` unset, *any* Telegram user who finds `@aplha_wolf_bot` gets `/levels`. Replace with a real
  access layer (§3). `bot_users` acknowledgement stays but is not authorisation.
- ⚠ (open, phase 7.4) `/levels` today is impersonal and identical for everyone; the target is a per-user strategy profile (Phase 7.4).

## 3. Access model (decision D1 — recommendation: invite-only allowlist)

Options: (a) **channel-member gate** (`getChatMember`, already built) — cheap, but the channel is public, so it is *not* private;
(b) **invite-only allowlist** — the owner approves each Telegram user id; users join through a one-time deep link
`t.me/<bot>?start=inv_<code>`; (c) **paid tier** (Telegram Stars / card) — later, once (b) works.
**Recommended:** (b) now, designed so (c) can be added as a `tier` without a rewrite.

Behaviour: unauthorised users get one polite message ("private assistant, invitation required" + how to ask), nothing else, and no data
is stored for them beyond the request. Owner commands (owner id in `.env`): `/invite [note]`, `/approve <id>`, `/revoke <id>`,
`/users` (counts only), `/status` (process, snapshot age, last digest). Revoked users lose access immediately; their data is deleted
after a retention window (default 30 days) unless they export/delete earlier.

## 4. Privacy & security requirements (portfolio = sensitive financial data)

- Store only what a feature needs: symbol, shares, average cost, optional note / stop / tag. No names, no phone numbers, no message text.
- Per-user isolation in every query (`WHERE telegram_user_id = %s`, enforced in the store layer, tested).
- **Never log holdings or replies** (logs contain user id + command name only). A test scans log output for symbols/amounts.
- Encryption: disk encryption + a restricted Postgres role for the bot is the baseline; field-level encryption of positions (pgcrypto,
  key in `.env`) is decision D5. Backups encrypted.
- User rights in the bot: `/export` (CSV of everything stored), `/deleteme` (hard delete, confirmed by a button), `/privacy` (plain-language
  statement of what is stored and who can read it — the owner has database access; say so honestly).
- No sharing between users, no aggregate "what others hold" features, no analytics on individuals.
- Check privacy-law obligations before onboarding non-owners (Israel's Privacy Protection Law; GDPR if any EU user), including whether
  a portfolio table counts as a sensitive / registrable database. (Not verified — needs a legal check, §9.)

## 5. Data model (proposed; migration `mechanism/add_assistant_tables.sql`, additive)

| Table | Purpose |
|---|---|
| `bot_access` | telegram_user_id PK, status (`pending` / `active` / `revoked`), tier (`member` / `admin`), invited_by, approved_at, revoked_at |
| `bot_invites` | code PK, created_by, tier, max_uses, uses, expires_at, note |
| `bot_user_settings` | user PK, timezone (default Asia/Jerusalem), brief_enabled, brief_time, news_enabled, language, risk profile fields |
| `bot_trades` | append-only journal: user, symbol, side, qty, price, fees, ts, note, tag (positions are *derived* from trades) |
| `bot_positions` (view or cache) | open positions per user: symbol, shares, avg cost, opened_at |
| `bot_watchlist` | exists |
| `news_items` | id, symbol, published_at, title, source, url, url_hash, fetched_at — UNIQUE(symbol, url_hash) |
| `bot_news_seen` | user, news_id — so a brief never repeats a headline |
| `bot_audit` | admin actions only (who / when / what) — never holdings |

Existing `digest_stocks` gains nothing new for the first phases (close, ATR, group, volume × are enough for portfolio valuation at the
last close).

## 6. Roadmap (phases, each with acceptance criteria)

**7.0 Foundations — first thing next session**
- Access layer (§3) + owner commands; remove group content; replace/neutralise channel buttons (D2); gate every command.
- Acceptance: an unauthorised user, a group and a channel receive **no** strategy/portfolio content in any scenario (QA leak tests +
  a mutation check); existing 860 tests still pass; `qa_live.py` all PASS after restart.

**7.1 Portfolio core**
- `/add` (guided: symbol → shares → average cost, with buttons; also `/add AAPL 10 185.5`), `/positions`, `/close`, `/portfolio`
  (value at the last close, day change, per-position P&L %, allocation %, sector exposure with "unclassified" shown honestly),
  `/export`, `/deleteme`.
- Facts only: e.g. "largest position = 32% of the portfolio" — never "reduce it". Prices are end-of-day (say so).
- Acceptance: numbers reproduce a hand-computed example to the cent; isolation + leak tests; hostile-input tests.

**7.2 Personal morning brief** (opt-in DM, per-user time zone)
- Sent only when there is something to say: portfolio day change; holdings that entered / left Breakout or Near breakout; unusual
  volume / range on holdings; holdings that closed within 1 ATR of their own risk level (stated as a fact); headlines (7.3).
- Economics: one snapshot read per user; DMs staggered under Telegram's ~30 msg/s free limit; nothing is sent to users with no change.

**7.3 News for your stocks**
- Batch job after the price pipeline: fetch headlines for the **union of all users' symbols** once, dedupe, store in `news_items`;
  `/news [SYMBOL]` and the brief read from the table (cost ∝ unique symbols). Link out with short titles — never copy article text.
- **Provider is decision D4 (unverified — check first):** Alpaca's news endpoint (Alpaca keys already exist in `.env`; confirm the free
  plan includes it), Tiingo's news API (paid tier?), Finnhub free tier, yfinance as fragile fallback. Requirements: stable API, terms
  that allow showing headlines to users, symbol tagging.
- Acceptance: a symbol with no news says so; duplicates collapsed; a provider outage degrades to "news unavailable", never blocks the brief.

**7.4 Personal strategy profile, sizing and journal**
- Per-user profile (risk multiple in ATRs, reference-level ladder, trailing rule) so `/levels` uses *their* parameters; `/risk SYMBOL AMOUNT`
  turns **their own** risk budget into a share count (no suggested % of portfolio — the UI's hardcoded "2-3% of portfolio" is never published).
- Journal (`/trade`) + personal `/stats` (win rate, average R, expectancy from their own closed trades) — the honest mirror of the project's
  finding that fixed ATR ladders were roughly break-even in aggregate.

**7.5 Later / optional:** EOD alerts on their own conditions; on-demand chart image for one stock (rate-limited, cached); Hebrew UI (D7).

**7.6 Operations (parallel track, needed before real users)**
- Hosting decision D6 (a small VPS; the PC only as an interim), auto-restart (systemd/Task Scheduler), webhook vs polling, monitoring
  (`/status`, `qa_live.py` on a schedule), encrypted backups, secrets handling, scheduling of the 06:00 digest + brief + news job
  (still an open item in CLAUDE.md §7.3).

## 7. Economics (why this scales)

Per-user work = a few indexed Postgres reads from the daily snapshot. External calls happen once per unique symbol per day (news) and once
per day for prices (already in the pipeline). No per-request provider calls. Telegram's broadcast cost stays flat (one channel post);
personal DMs are opt-in and only sent when something changed.

## 8. QA strategy (extends what exists)

Reuse `qa_harness.py` (real dispatcher + fake network), `tg_html.py` (every reply must be valid Telegram HTML), `qa_live.py`.
New required scenario families: **authorisation** (unauthorised / pending / revoked / owner), **leak** (group, channel, another user,
logs), **portfolio maths** (hand-computed fixtures, fractional shares, splits are out of scope and must be stated), **privacy**
(`/export`, `/deleteme`, retention), **news** (provider down, duplicates, no results), **brief** (nothing-to-say = no DM, time zones,
staggering), plus a mutation check per family (re-introduce the bug, confirm the suite fails). Live QA posts to the DEV chat only.

## 9. Legal / compliance gates (hard gates, not paperwork)

1. Before any non-owner is invited: legal review of (a) personalised strategy levels + portfolio tools for a small private group and
   (b) privacy-law duties for storing portfolio data. Personalisation and per-stock levels are closer to "advice" than the public
   channel is; the impersonal-formula, user-supplied-numbers, facts-only design reduces but does not remove that risk.
2. Data licensing: confirm Tiingo / Yahoo / the news provider allow showing derived data / headlines to invited users (unverified).
3. Keep the acknowledgement + disclaimer, the wording guard (no buy / sell / target / stop / entry / signal / pick / should ...), and the
   "distances, not predictions" caveat on every levels-type reply.

## 10. Decisions needed (with my recommended defaults)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Access model | invite-only allowlist now; paid tier later |
| D2 | What the public channel shows about the assistant | one neutral "Private assistant (invite only)" button; no per-ticker levels buttons |
| D3 | Portfolio input UX | guided `/add` with buttons + one-line form; CSV import later |
| D4 | News provider | verify Alpaca free news first; otherwise Finnhub free; yfinance only as fallback |
| D5 | Protection level for positions | disk encryption + restricted DB role now; pgcrypto field encryption before real users |
| D6 | Hosting | small VPS; PC as interim with an auto-restart launcher |
| D7 | Languages | English first; Hebrew after 7.2 |
| D8 | Brief default time | 06:00 in the user's time zone, opt-in |
| D9 | Beta size | owner + 2–3 trusted users after the legal check |
| D10 | `/levels` model | keep the impersonal formula publicly-documented, add per-user profile in 7.4 |
| D11 | Legal review timing | before the first non-owner invite |

## 11. Status (end of 2026-09-21) - what is closed, what only the owner can do

Closed in code: open mode, group replies, per-ticker channel buttons, prod `--buttons` risk (see the 7.0 notes in MILESTONES.md); the channel-member gate was
removed (a public channel's members are not a private audience). The owner steps of 7.0 are DONE (`BOT_OWNER_ID` set, bot restarted, `qa_live.py` passing,
bot renamed in BotFather - now "First Light Trading Assistant"). The request-access flow exists (`access.py`, RUNBOOK section 8), so a stranger can ask and the owner approves with one tap.

**Still needed from the owner (nothing here can be done from code):**
1. BotFather -> the bot -> Bot Settings -> **Allow Groups -> off**; upload the bot avatar (`reports/first_light/promo/first_light_bot_avatar.png`).
2. Human QA on a phone (RUNBOOK sections 2 and 8), then one friend session; report every rough edge.
3. Answer the pending decisions (HANDOFF.md section 4), especially: the market-news post, hosting, encryption D5, legal review timing.
4. Hard gate unchanged (section 9): legal review before the first non-owner invitation.

Known limits (deliberate): invitations are single-use / 72 h; `/users` shows counts, not ids; the owner also accepts the educational notice once; only pull mode (no morning DM yet).

## 12. Next-session kickoff checklist

1. Read HANDOFF.md, run the two-minute verification in its section 8.
2. Fix whatever the owner's human QA found (write a regression test first).
3. Build the approved channel content (market-news post, promo rotation, weekly note) through the channel tools only - never through assistant code.
4. 7.2 Personal morning brief (opt-in DM, only when something changed, needs `bot_user_settings`), then 7.4, then hosting/operations (7.6).
5. Before launch: FUNNEL_PLAN.md section 8 checklist (new production channel, `TELEGRAM_CHAT_ID`, `PROD_SENDING_ENABLED=1` only on the user's explicit go-ahead).

## 13. Decision log

- 2026-09-21 — User: the channel is public and must not be "spammed" with strategy content; strategies and portfolio management are
  **not for everyone**; the bot becomes each user's **private personal assistant** (portfolio, strategy, news for their stocks),
  connected to the channel but not answering in it. Plan written; implementation deferred to the next conversation.
- 2026-09-21 (later) — Phase 7.0 built. Implemented with the recommended defaults for D1 (invite-only allowlist; one-time, 72 h, hashed
  codes; owner from `.env`) and D2 (three popups + one neutral link, no per-ticker buttons). Design choices made while building: the
  channel-member gate was removed rather than kept as a second layer; a revoked user can only be re-admitted by an explicit `/approve`; the
  three educational popups stay public (no personal or strategy content); group replies reduced to a pointer instead of removed entirely;
  `bot_user_settings` postponed to 7.2. Tests found two bugs on the way (Unicode digits accepted as ids; a `-1` sentinel matching a user).
- 2026-09-21 (later) — User asked for a proper bot design: onboarding guide, watchlist + portfolio that store the symbol, the price and the day added so performance is tracked since that day, browsing limited to the channel's daily lists (each symbol once), per-stock data + news + chart. Analysis and design written to BOT_DESIGN_REPORT.md; Q1–Q8 there await the user's answers before building.
- 2026-09-21 (later) - User did not answer Q1-Q8; the recommended defaults were used and the bot was built (tracker, news, chart, guide, menu) and run. Built the request-access flow (user: yes; a person is stored only after tapping *Request access*). The user renamed the bot in BotFather (it now shows as "First Light Trading Assistant"). Wrote a production lock (`PROD_SENDING_ENABLED`) after the user said to work only on dev and keep "Top Gainers - Daily" untouched until launch.
- 2026-09-21 (end) - Bots cannot delete messages older than ~48 h, so a NEW dev channel ("First Light - Dev") replaced the flooded dev group; the production channel will also be replaced at launch. **User rule: the assistant must never post in the channel; the channel is data, advertisement, news and information; the assistant serves everything in the private chat.** Enforced by tests (channel-isolation AST guards) and a memory note; the QA tour now goes only to the owner's private chat.
