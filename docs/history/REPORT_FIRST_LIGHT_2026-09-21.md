> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** Preserved for reference/history.
> For current architecture, see [docs/architecture/](../architecture/), [docs/operations/](../operations/),
> and [CLAUDE.md](../../CLAUDE.md). Moved here 2026-09-25 as part of the repository documentation
> consolidation — content below is unmodified from before the move except for this banner.

# First Light — session report, 2026-09-21

**Goal.** A well-arranged channel where people arrive and get their **daily insights** (the market card and the Breakout / Near-breakout lists),
plus a **private assistant bot** that gives them everything the alerts have to offer — and a **funnel** that turns channel readers into bot users.
This report says what exists now, how well it is tested, what is *not* proven, and what stands between today and a public launch.
(No tokens, keys or personal ids are in this file.)

---

> **Next session: start at [HANDOFF.md](HANDOFF.md)** - it carries the current state, the ordered next steps and the traps. Sections 10-11 below correct the earlier sections.

## 1. The picture in one screen

```
 prices (Tiingo)  ─►  daily analysis  ─►  DAILY DIGEST  ─►  CHANNEL  ─►  "Private assistant" button  ─►  REQUEST/INVITE  ─►  BOT
 ~3,000 symbols       Donchian groups      06:00 Jerusalem    (dev now,       (measurable, planned)        (owner approves)      guide → lists → cards
 stock_prices         ranked lists         market card +      prod later)                                                         → watchlist/portfolio
                      (gainers/ATR/vol)    2 list messages                                                                         → news + chart
```
**Where things run today:** everything is built and tested on the **dev group**. The public channel ("Top Gainers - Daily") is **locked and untouched** (§6).

## 2. What we built

### 2.1 The private assistant (the bot) — running now
| Piece | What the user gets |
|---|---|
| **Access** | Invite-only. The owner creates a one-time link (`/invite`) or approves an id (`/approve`); `/revoke` blocks at once; strangers get one refusal and **nothing is stored**; groups get only a pointer; the channel gets nothing. |
| **First contact** | A 4-step guide edited in place, the educational notice as the last step, then a permanent menu: *Today's lists · Portfolio · Watchlist · Help*. |
| **Today's lists** | The channel's lists on one screen, **each stock once**, Breakout / Near-breakout tabs, ★ for stocks in 2+ lists, ✓ for stocks you track. |
| **Stock card** | Facts, the lists it is in, your own tracking, and buttons: **News** (headlines + links), **Chart** (candles, volume, prior 20-day high, your price line), **ATR levels**, Add. Typing a ticker also opens it. |
| **Watchlist & Portfolio** | Each stock stores the symbol, **your price (or the last close) and the day you added it**, and shows the change **since that day**; portfolio adds shares, value, weights and totals. |
| **Your data** | `/export` (CSV), `/deleteme` (erase, confirmed), `/privacy` (what is stored, honestly including that the owner can read the database). |

### 2.2 The daily notification (First Light digest)
`run_first_light_morning.ps1` — one command: trading-day gate → index update (~5 s) → price update (**~21 min**) → digest (**~1 min**). Preview by default;
`-Send` posts; `-UpdateOnly` / `-SkipUpdate` split it into two scheduled tasks (05:00 update, 06:00 post). Weekends, holidays and already-sent sessions are a no-op.
The 2.5-hour full pipeline is *not* needed before the digest (measured from your logs).

### 2.3 Trustworthy numbers
Money uses exact decimals with half-up rounding; anything not trustworthy is shown as **"n/a" with the reason** instead of a guess. A **split guard** stops a
2-for-1 or 3-for-2 split from showing as a fake −48%: two of the 23 stocks in the lists on 2026-09-18 (BNC, SBET) have adjustment breaks in their history.

### 2.4 Workflow and safety
- **Dev first, production locked.** `PROD_SENDING_ENABLED=0` in `.env`; `TelegramClient.from_env("prod")` refuses to send and the wrapper exits with code 4. A test proves no script can bypass it. Launch = one line.
- **Docs written:** `RUNBOOK_FIRST_LIGHT.md` (run the bot, a 29-step manual test, the morning routine, inviting a friend), `FUNNEL_PLAN.md`, `BOT_DESIGN_REPORT.md`, updated `PRIVATE_ASSISTANT_PLAN.md`, `CLAUDE.md`, `MILESTONES.md`.

### 2.5 Promotion images (new, in the same "Midnight Dawn" look as the market card)
`python mechanism/alerts/promo_assets.py` → `reports/first_light/promo/`
| File | Use |
|---|---|
| `first_light_assistant_promo.png` (1080×1350) | the promotion post: "Your private stock assistant", three example screens (lists, stock card, since-you-added), *Free during the beta · by invitation* |
| `first_light_bot_avatar.png` (640×640) | the **assistant's** profile picture: the sunrise mark inside a chat bubble with typing dots (see §10 for the split from the channel logo) |
| `first_light_channel_logo.png` (640×640) | the **channel's** photo: the sunrise mark (a sun rising behind a rising bar chart) |
Every screen on the promo is labelled **example**, the disclaimer is on the image, there are no performance claims, and the call to action says exactly what is true (free in the beta, by invitation).
Previews were sent to the dev group. To set them: BotFather → `/mybots` → the bot → *Edit Bot* → *Edit Botpic*; channel → *Edit* → photo.

## 3. How well was it tested

| Check | Result |
|---|---|
| Automated tests (`pytest mechanism/alerts/tests ml_training/tests`) | **1,364 pass** at the end of the day (1,309 when this report was first written, 992 at the end of the access phase) |
| Hand-computed maths (portfolio to the cent, rounding, splits) | 49 performance tests + 46 tracker tests; expected values worked out by hand, not copied from code |
| **Mutation checks** — re-introduce a bug, confirm the suite fails | **39 of 39 caught** (26 when first written; the set is preserved in `mechanism/alerts/tests/mutation_checks.py`) (auth bypass, group data leak, code in logs, split guard off, SQL leaking other users' rows, no news/chart cache, prod lock removed, …) |
| Real database | tracker SQL round-trip; the whole bot driven against the real Postgres snapshot with a fake Telegram network (real numbers matched the design report to the cent) |
| Real Telegram | 37 live checks: every new screen and keyboard accepted by Telegram on the dev group |
| Access / privacy | strangers store nothing; users cannot see each other's data; no invitation code, symbol or price in logs |

**Defects the testing found and we fixed** (each got a regression test): Unicode digits accepted as user ids · a "no owner" placeholder that could match a real id · a 2-for-1 split not recognised by the price guard · a rounded-away loss printed as `$-0.00` · a blank paging button Telegram can reject · the chart crashing for new listings (< 22 bars) · the banned word "entry" in a prompt · the bot failing to start on Windows (`aiodns` event loop) · four layout bugs in the first promo draft (text under panels, chart escaping its box, CTA overlapping a panel).

## 4. Current state (snapshot)
- **Bot:** running in the background from this session (`@aplha_wolf_bot`, invite-only, owner set). It only answers while that process and the PC are on.
- **Data:** last session in the database is **Fri 18 Sep** (3 days old — nothing schedules the daily jobs yet). Every screen prints its "US close" date and warns when stale.
- **Dev group:** all previews and QA messages. **Production channel** ("Top Gainers - Daily", public, 5 members): old top-15 posts, old description, nothing pinned; the bot is admin there. **Not touched.**
- **Access:** one user (you). A friend can be invited today (runbook §5).

## 5. What is NOT proven yet (be honest with yourself and with users)
1. **No human has used it end to end in Telegram.** Everything above was driven by tests and a fake network; Telegram itself accepted the screens, but real taps are unproven → do the 29-step test (runbook §2) and the friend session (§5.4).
2. **The scheduled morning job has never run unattended** (Task Scheduler commands are written, not registered; the 21-minute price step was not re-run through the wrapper).
3. **News licence** — Alpaca's free feed works (Benzinga headlines + links) but its display terms were not checked.
4. **Legal / privacy review has not happened.** Personalised tracking of holdings is closer to "advice" than the public digest, and it stores users' data. It is the plan's hard gate before strangers get access.
5. **Deleting old channel posts is NOT possible for a bot** (verified later the same day, §10): Telegram refuses to let a bot delete messages older than ~48 hours, so a new channel is the only clean reset.
6. **No edge is claimed or proven.** Our own studies found no proven predictive edge in the lists or in fixed ATR levels; all copy stays educational.
7. **Hosting.** A PC that sleeps or reboots stops both the bot and the morning post.

## 6. The path to launch (working rule: dev first, then promote on production)
| # | Step | Status |
|---|---|---|
| 1 | Human QA by you + one friend on dev (runbook §2, §5) | **next** |
| 2 | Build the **request-access** flow (button → owner Approve/Decline → guide), `/funnel` counts, `BOT_ACCESS_MODE` | proposed (`FUNNEL_PLAN.md` §3–§4) — needs your yes |
| 3 | Run the two scheduled tasks on **dev** for 5 trading days without touching them | not started |
| 4 | Morning DM for members (opt-in, only when something changed) — the retention loop | not built (plan 7.2) |
| 5 | Decide hosting (PC always on vs a small VPS) | open |
| 6 | Legal + privacy review; update `/privacy` | open — **hard gate** |
| 7 | Reset the production channel: **new channel "First Light — Stocks & Info"** (recommended) or clean the old one; set avatar, description, pinned "Start here" post | at launch, your explicit go-ahead first |
| 8 | Set `PROD_SENDING_ENABLED=1`; start the daily digest + **one promo post a day** using the promo image and rotating short messages | at launch |

**Do not publish the promo image yet:** its button says *Tap "Private assistant"*, which today leads a stranger to a refusal screen (no way to ask). Publish it together with step 2.

## 7. Blueprint for the arranged channel (what people should see)
- **Name / picture:** `First Light — Stocks & Info` with the sunrise mark.
- **Description:** what it is in two lines + "Educational data, not investment advice" + "Private assistant: free during the beta".
- **Pinned "Start here" post:** how to read ★ / vol × / ATR, what Breakout and Near-breakout mean, the assistant and how to request access (draft in `FUNNEL_PLAN.md` §5).
- **Every trading morning at 06:00:** market-card picture (the only notification) → header with three popup buttons + the *Private assistant* button → Breakout → Near breakout. Same order, same time, every day.
- **One short promotion post a day** (rotating: track since you add · news + chart · what ATR means · how to read ★ · the market card explained · free beta / request access · weekly recap). No performance claims; at most one per day.

## 8. Decisions waiting for you
| # | Decision | My recommendation |
|---|---|---|
| 1 | Build the request-access flow + `/funnel` next? | Yes |
| 2 | Storing a stranger's Telegram id + time *after they tap "Request access"* (purged after 14 days) | Yes, and say so in `/privacy` |
| 3 | Access mode during the beta | manual approval; automatic-with-a-cap later |
| 4 | Reset method for "Top Gainers - Daily" | new channel (option A) |
| 5 | What "daily advertisements" means | the daily digest + one rotating promo post (confirm; other-channel promotion is a different matter) |
| 6 | Encrypt shares/prices in the database (D5) | restricted DB role now, field encryption before real users |
| 7 | Hosting | small VPS before public launch |
| 8 | Bot display name/username (now "AlphaWolf" / `@aplha_wolf_bot`) | rename to "First Light Assistant"; fixing "aplha" changes the link |
| 9 | Hebrew UI | after the English flow is stable |

## 9. Where everything is
| What | Where |
|---|---|
| Run / test / morning routine / invite a friend | `RUNBOOK_FIRST_LIGHT.md` |
| Funnel, launch checklist, reset options, promotion plan | `FUNNEL_PLAN.md` |
| Assistant product design (built) | `BOT_DESIGN_REPORT.md` |
| Rules, access model, privacy, phases | `PRIVATE_ASSISTANT_PLAN.md` |
| Architecture notes | `CLAUDE.md` (§3.1, §5, §6, §9) |
| Bot code | `mechanism/alerts/` — `run_bot.py`, `access.py`, `tracker.py`, `performance.py`, `screens.py`, `news_service.py`, `chart.py`, `promo_assets.py` |
| Migrations (applied) | `mechanism/add_assistant_tables.sql`, `mechanism/add_tracker_tables.sql` |
| Morning routine | `run_first_light_morning.ps1` |
| Promotion images | `reports/first_light/promo/` (regenerate with `python mechanism/alerts/promo_assets.py`) |

---

## 10. Update (later on 2026-09-21): access flow built, dev reset lesson, new avatars

- **Request-access flow built and tested** (your "yes"): a stranger taps *Request access* → you get *Approve / Decline* → they are in. Stored only after the tap (Telegram id + time), deleted when you decide / after 14 days;
  7-day cool-down after a decline; waiting-list cap; `BOT_ACCESS_MODE` approve / auto (with a cap) / closed; `/requests`; `/funnel` (counts only); the channel button now carries a counter (`?start=ch`) that stores no user.
  Tests: **1,356 pass** (alerts + ML); 10 more mutation checks caught (36 of 36 in total).
- **Your point 2, in one line:** to approve someone later the bot must remember *who asked* — so it keeps their Telegram id number and the time, only after they tap, only until you decide.
- **Avatars:** two now — the **channel logo** (the sunrise mark) and the **bot avatar** (the mark in a chat bubble). Both in `reports/first_light/promo/`.
- **Lesson — a bot cannot clear an old chat:** the dev-group wipe deleted the 88 messages from the last 48 hours and was then refused on everything older (even message #5; the group has ~20,000 messages). Telegram simply does not allow a bot to delete messages older than ~48 hours. **A new channel is the right fix**, for dev now and production at launch (`RUNBOOK` §7). The tool now stops early instead of stalling, and a client bug it exposed ("chat not found" was read as "message already gone") was fixed and tested.
- **Bot name:** it now shows as "First Light Trading Assistant" (your BotFather rename is live).
- **New tools:** `dev_chat_reset.py` (dev only, refuses production/channels/no `--yes`), `channel_posts.py` (Start-here post + promo post, pins, goes through the production lock), `replay_dev_channel.ps1` (posts the whole experience into the dev channel in order), and an owner-only *forward a post → get its chat id* helper.
- **Still waiting for you:** create the new private **First Light DEV** channel (RUNBOOK §7) and forward a post to the bot; then one command replays everything.

## 11. Correction (same day): the channel is not the assistant's stage

I posted the assistant's 35-screen tour into the dev **channel**. That was wrong: **a channel carries only data (the daily digest), promotion, news and information; the private assistant gives its data and everything else in the private chat between the user and the bot.**
Fixed: the tour messages were deleted from the channel (only the pinned Start-here post, the digest and the promo remain); the tour tool now sends to **your private chat with the bot**; the replay script posts only channel content; and tests make it structurally impossible to regress (the tour cannot target a channel; channel-posting modules cannot import or query anything of the assistant).
**What a channel will hold:** the daily digest, the promotion posts, and — proposed, not built — a short daily **market-news** post (headlines on the day's movers, from the news feed the assistant already uses) and occasional educational notes ("how to read ★ / ATR"), plus the pinned Start-here post.

