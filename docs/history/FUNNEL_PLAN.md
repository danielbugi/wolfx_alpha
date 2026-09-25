> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** Preserved for reference/history.
> For current architecture, see [docs/architecture/](../architecture/), [docs/operations/](../operations/),
> and [CLAUDE.md](../../CLAUDE.md). Moved here 2026-09-25 as part of the repository documentation
> consolidation — content below is unmodified from before the move except for this banner.

# FUNNEL_PLAN.md — from the First Light channel to a working user of the private assistant

> **Working rules (user, 2026-09-21): (1) build and test everything on the DEV channel; when ready, promote on the production channel with daily promotion. (2) The assistant NEVER posts in a channel - the channel is data, advertisement, news and information (section 10); everything personal happens in the private chat.** Production is locked in code (`PROD_SENDING_ENABLED`, section 8) so it cannot be touched early.
>
> Written 2026-09-21; **status at the end of that day: sections 3-6 are BUILT and tested on dev** (request-access flow, owner Approve/Decline, `/requests`, `/funnel`, `BOT_ACCESS_MODE`); section 1 is the situation BEFORE that build (kept for the record). Not yet done: human QA, the launch itself (section 8), the morning DM, the extra channel content. Dev is now the private channel **"First Light - Dev"** (the old group "BOT_SPAMMING" was replaced because bots cannot delete old messages). Start a session at HANDOFF.md. How to invite a friend: RUNBOOK_FIRST_LIGHT.md section 5. Rules that still bind: PRIVATE_ASSISTANT_PLAN.md section 0.

## 1. The funnel BEFORE the request-access build (facts checked 2026-09-21, morning)

| Stage | What exists | Verdict |
|---|---|---|
| Reach | **Prod** = public channel "Top Gainers - Daily" (`t.me/top_gainers_daily`, 5 members, old top-15 posts, **locked, untouched**). **Dev** = private group "BOT_SPAMMING" (4 members) where every digest and bot feature is tested | prod is not live yet; name/link/description are from the old product |
| Interest | Button under every digest header: **"Private assistant (invite only)"** (a URL button) | works, but **cannot be measured** (Telegram gives no callback for URL buttons) |
| Request | **none** — a stranger who taps it sees *"private assistant for invited users … invitations come from the owner"* plus their own Telegram id | **dead end**: the only way in is that you already know them |
| Approve | you: `/invite <label>` (one-time link, 72 h) or `/approve <id>` | works, fully manual, you never see who is waiting |
| Activate | 4-step guide, notice, menu, Today's lists | built and tested; no measurement of who finishes |
| Retain | nothing pushed — a user must remember to open the bot | the **morning DM** (plan 7.2) is not built |
| Pay | free; `tier` column exists | nothing to build until there is traffic |

**The single biggest gap is Request.** A stranger who likes the channel has no way to say "I want in", and you have no queue.

## 2. Funnel model for the beta ("free until we gain traffic")

```
channel post  ->  tap "Private assistant"  ->  REQUEST ACCESS  ->  you APPROVE  ->  guide + notice  ->  first stock tracked  ->  comes back
   reach            interest                   (missing)            (manual)         activation          "aha" moment          retention
```
The "aha" moment to optimise for: **the user adds one stock and, days later, sees the change since they added it.** Everything before it should
take under two minutes; everything after it is the morning DM.

## 3. Recommended build: request-access with approve buttons

**User side** (only when someone without access opens the bot):
```
This is a private assistant for First Light members. It is free during the beta and access is by invitation.

  [ Request access ]        [ What is this? ]
```
- **Request access** stores `{telegram id, time}` with status `pending` (the schema already reserves `pending`) — that is the user's explicit act, so it is
  consent to store two facts; the button text says so ("Request access - we store your Telegram id and the time"). Nothing is stored before the tap.
- They get: *"Thanks - your request was sent. You'll hear back here."* Repeated taps do not duplicate.
- **What is this?** = a 3-line explanation + the disclaimer + a link back to the channel.

**Owner side:** a message to you with two buttons — `Approve` / `Decline` (+ the requester's numeric id; no name is stored or shown unless Telegram
supplies a public @username, which we do not read). `/requests` shows how many are waiting (counts and the oldest age, never a list of holdings).
- **Approve** → status `active`, the user gets *"You're in"* and the guide starts (they already pressed Start once, so the bot may message them).
- **Decline** → they get one polite line; a 7-day cool-down before they can ask again (needs a `declined` status → tiny migration).

**Protection (a public button attracts bots and trolls):** one open request per person; cap of e.g. 100 pending (beyond it the button answers "the
waiting list is full, try later" and stores nothing); per-user rate limit (already there); requests older than 14 days are purged with the same
daily housekeeping that purges revoked users.

**Access mode** (`.env`, no code change to switch): `BOT_ACCESS_MODE=approve` (default: you decide each), `auto` (approve the first
`BOT_MAX_MEMBERS` automatically — an open beta with a hard cap), `closed` (button shows "closed for now", stores nothing). Paid tier later =
Telegram Stars on top of the existing `tier` column.

## 4. Measure it (counts only — never who did what)

`/funnel` (owner) shows the last 7 / 30 days:
`opened from the channel → requested → approved → finished the guide → tracked a first stock (within 24 h) → active in the last 7 days`.
- *Opened from the channel*: the channel button switches to a distinct start payload (`?start=ch`); the bot only **increments a daily counter** (no
  user id stored for a stranger). Everything else is already in the database (`bot_access`, `bot_users.acknowledged_at`, `bot_tracked.first_added_at`, `last_seen`).
- Read it as ratios: request rate (requests ÷ opens), activation (first stock ÷ approved), weekly retention. With ~5 members most numbers are noise — the
  point is to have them ready **before** traffic arrives.

## 5. Channel side (no code)

1. **At launch** (not before - production stays untouched until then, see §8): rename the channel to `First Light — Stocks & Info` or create a new one (§8 option A).
   The *public username* (`top_gainers_daily`) is what people see in links; choose the new one deliberately, because changing it later breaks links.
2. **Pin a "Start here" post** (draft below) and make the description say what it is and that the assistant is free in the beta.
3. **Digest footer**: one line — `Private assistant (free beta): tap the button under this post to request access.` — after §3 exists.
4. Growth that works: the public link in your bio and other groups, forwardable digests, one consistent post at 06:00, a member-invite perk later
   (each member gets 2 invitation links). **Do not** mass-add people, DM strangers or buy members — bots cannot message someone who never
   started them, and Telegram penalises unsolicited adds.

Draft of the pinned post (channel copy, keep the disclaimer):
> **First Light — Stocks & Info**
> Every trading day we scan ~2,900 liquid US stocks and post the market card plus two lists — **Breakout** (closed above the prior 20-day high) and
> **Near breakout** (within 3% below it) — each ranked by gain, ATR and volume.
> How to read: ★ in more than one list · vol × = volume vs its 50-day median · ATR × = today's range vs its 14-day average.
> **Private assistant** (free during the beta, by invitation): follow your own watchlist and portfolio *since the day you add a stock*, with news and a
> chart for each stock. Tap the button under the daily post to request access.
> Educational information from public price data. Not investment advice.

## 6. Build order and QA

| # | Step | Size |
|---|---|---|
| 1 | At launch: channel reset/rename, description, pinned post (§5, §8) — no code | you + me, 30 min |
| 2 | Request access + approve/decline buttons + `/requests` + limits + purge (§3) | 1 session |
| 3 | `?start=ch` counter + `/funnel` (§4) | small |
| 4 | `BOT_ACCESS_MODE` (`approve` / `auto` + cap / `closed`) | small |
| 5 | Morning DM for members (plan 7.2) — the retention loop | 1 session |

QA scenarios to write with step 2 (same harness as the rest: real dispatcher, leak test, mutation checks): stranger requests → owner gets buttons →
approve → guide; decline → cool-down; double tap = one request; pending never sees data; 200 strangers flood → cap and silence; owner offline (the
request waits); approve after the user blocked the bot (message fails, status still active); revoke a pending; purge of stale requests; a stranger who
never taps Request leaves **no row**; the refusal never reveals owner commands; the request text stores nothing but id + time.

## 7. Decisions I need

| # | Question | Recommendation |
|---|---|---|
| F1 | Build the request-access flow (§3)? | **Yes** — it is the missing funnel step |
| F2 | Access mode for the beta | `approve` (you see everyone) now; switch to `auto` with a cap once requests exceed what you want to read |
| F3 | Is storing a stranger's id + time **after they tap "Request access"** acceptable? | Yes (explicit consent, purged after 14 days) — confirm, and mention it in `/privacy` |
| F4 | Rename channel + change the public username now? | Yes, before it grows |
| F5 | Member referral invites (2 each) | later, after the request flow works |
| F6 | Legal review | still the gate before opening to strangers; a single trusted friend is your call |

## 8. Dev -> production: launch plan (working rule: everything on dev first, then promote)

**Now:** all sends go to the dev group. The production channel is **locked** in code — `TelegramClient.from_env("prod", dry_run=False)` raises unless
`PROD_SENDING_ENABLED=1` is in `.env` (it is `0`); the wrapper `run_first_light_morning.ps1` refuses `-Send -To prod` up front (exit 4). Tests prove
the lock and that no script can bypass it. Launch = one edit to `.env`; rollback = set it back to `0`.

**"Ready" means (exit criteria — tick them before flipping the switch):**
1. Friend QA (RUNBOOK §5.4) done, confusing steps fixed.
2. Request-access flow built and tested (§3) — or an explicit decision to launch invite-only first.
3. **5 consecutive trading days** of the digest on the schedule in dev (both Task Scheduler tasks, `-To dev`) with no manual intervention, and the bot up all that time.
4. Hosting decision made (the PC must be on at 05:00-06:00 and the bot running; otherwise a small VPS).
5. Legal review done (personalised tracking + stored portfolio data) and `/privacy` updated to match — the hard gate before strangers get access.
6. **Production channel reset** (below) with new name, description and pinned post.
7. Promotion content ready (below).

**UPDATE 2026-09-21 - built and tested:** the request-access flow (§3), `/funnel` + the `?start=ch` counter (§4) and `BOT_ACCESS_MODE` are implemented on dev (RUNBOOK §8). **Resetting by deleting posts does not work:** Telegram refuses to let a bot delete messages older than ~48 hours (tested on the dev group: 88 recent messages deleted, then every older id refused, even #5). So **option A (a NEW channel) is the only clean reset**, for dev now and for production at launch; option B below is only useful for the last 48 h.

**Resetting "Top Gainers - Daily" (do this at launch, not before; it cannot be undone).** Facts: the bot is an admin there with *post, edit, delete, invite, change info*
rights; you are the creator; nothing is pinned; the description is the old Finviz-top-15 text. Bots cannot read a channel's history, so "delete everything" is not one call.
| Option | How | Trade-offs |
|---|---|---|
| **A. New channel (recommended)** | Create "First Light — Stocks & Info" with its public username, add the bot as admin (post, edit, delete, change info, invite), put the new chat id in `.env` (`TELEGRAM_CHAT_ID`; I can read it for you). Announce the move once in the old channel, then delete/archive it. | Clean start by construction, new username, no deletion limits. The 5 members must rejoin; the old link stops being the product. |
| **B. Clean the same channel** | I sweep message ids from the newest down to 1 with `deleteMessage` (after posting one marker to learn the newest id), then set title + description via the bot; you change the username in Telegram. | Keeps members and link. **Telegram may refuse old messages** (Bot API limits on deleting old posts are not verified for channels), so part may need doing by hand in the app; irreversible; nothing to test it against except the real posts. |
Either way I will show you exactly what will be deleted and wait for your explicit go-ahead first.

**Promotion ("daily advertisements") — what I understand, please confirm:** (1) the daily digest itself is the best advertisement (proof of value, every trading morning);
(2) plus **one short promotion post per day** after it, rotating: *track a stock since the day you add it · news + chart on every stock · what ATR means · how to read ★ and vol × ·
the market card explained · free beta, request access · weekly recap*. Rules: educational tone, never performance claims (our own studies found no proven edge), the
disclaimer stays, at most one promo a day so subscribers are not flooded. Promoting in **other** channels/groups is a different matter (their rules, and Telegram penalises
unsolicited adds) — tell me if that is what you meant.

## 9. Extra decisions

| # | Question | Recommendation |
|---|---|---|
| F7 | Reset method for "Top Gainers - Daily" | **A** (new channel) — cleanest, also gives the new name + username |
| F8 | Meaning of "daily advertisements" | digest + one rotating promo post a day (above); confirm, or say if you also mean promoting in other channels |
| F9 | When to flip `PROD_SENDING_ENABLED=1` | only when the §8 checklist is ticked |

## 10. Channel content policy (user rule, 2026-09-21)

**The channel** = the shop window: **data** (the daily digest: market card, Breakout, Near breakout), **promotion** (the assistant, the beta), **news** and **information** (how to read the lists, educational notes) — whatever brings traffic in.
**The private chat with the bot** = the product: Today's lists, stock cards, news and charts per stock, watchlist, portfolio, access requests. **Assistant screens are never posted to a channel** (enforced by tests: `test_channel_tools.py`).
Proposed channel rhythm (to confirm): 06:00 digest (daily) · one promotion post a day (rotating formats) · a short **market-news** post (headlines on the day's movers) · a weekly "how to read" / recap note.

