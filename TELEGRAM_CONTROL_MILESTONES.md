# TELEGRAM_CONTROL_MILESTONES.md — the Telegram Control Center (built 2026-09-22; phase 2 same day)

> Tracker for the `/telegram` page of the dashboard: one place to **see, compose, edit, pin and delete** every message First Light posts to a channel, under the
> same rules as the rest of the system (production locked until explicitly opened, channels carry data only). Update this file as items change.
> **2026-09-22, later the same day:** the owner turned the dev channel into the real, public channel — see §6/§7 (phase 2) for what changed and why.
> Open it: backend on :8000, `cd frontend && npm run dev -- -p 3001`, then **http://localhost:3001/telegram** (use `localhost`, CORS is keyed on it).
> To edit or delete, paste the value of `TELEGRAM_CONTROL_TOKEN` from `.env` into the box at the top of the page (kept in that browser tab only).

## 0. Scope (avoid-feature-creep: written before any code)

**Core problem.** First Light posts up to ~7 silent messages a day to a channel and kept **no record of them**. A bot cannot read a channel's history (Telegram gives it
no such call), so the owner could not list what is up, fix a wrong post, or retract one without a hand-written script.

**In scope (v1) — built**
1. **A message ledger**: every message sent to the `dev` or `prod` channel is recorded (Telegram message id, kind, exact text/caption, keyboard, time) at the one place
   every send already goes through (`TelegramClient`), so no sender can forget.
2. **The page `/telegram`**: per-channel overview, message list with filters, a Telegram-style preview, the read-only posting configuration, edit and delete with
   confirmation, and the audit trail of what the page did.
3. **Edit** (text and photo captions, keyboard kept) and **delete**, only through `TelegramClient.from_env(target)`.

**Explicitly out of scope for v1** (compose, pin/unpin, photo-replace, a schedule view and an opt-in read-token were all originally deferred here — **all five were
built in phase 2, §6-7**; this list is v1's original scope boundary, kept for the record): back-filling posts sent before the ledger existed (impossible: bots
cannot read history) · anything about the private assistant's chats (never logged, by rule) · reading Windows Task Scheduler live (still out of scope in phase 2
too, and for the same reason — see §6 item 5).

**Non-negotiables inherited from the project** (each has a test)
- *Production is locked*: while `PROD_SENDING_ENABLED` ≠ 1 the page is **view-only for prod** — refused server side (HTTP 423), not just greyed out — and the `.env` file is
  re-read on every check, so putting `0` back re-locks a running API at once (opening needs a restart, on purpose).
- *Channel ≠ assistant*: ledger = channel posts only; the `owner` target (private chat) and dry runs are never recorded; no assistant module or table is touched.
- *No fabricated numbers*: unknown = `null` → "—". "Recording since <date>" is shown, never a made-up backfill.
- *Wording guard*: an edit that introduces an advice-style word is refused unless the owner explicitly acknowledges it.
- *Nothing changes without a secret*: `PATCH`/`DELETE` need `X-Control-Token` = `TELEGRAM_CONTROL_TOKEN`; unset **or a placeholder** = every change refused (one rule, `control_token()`).
- Bot token, control token, chat ids and the owner id never leave the API (the overview shows `…6979`, "Set", "Configured").

## 1. Design

| Layer | File | Job |
|---|---|---|
| Data | `mechanism/add_telegram_control_tables.sql` | `telegram_messages` (ledger) + `telegram_control_audit` |
| Store | `mechanism/alerts/message_ledger.py` | `PgLedger` (SQL), `MemoryLedger` (tests), `LedgerRecorder` (the hook the client calls; never raises) |
| Transport | `mechanism/alerts/telegram_client.py` | `kind=` on sends, `edit_message`, `plain_text`; records sent / edited / deleted / pinned for channel targets |
| Rules | `mechanism/alerts/channel_control.py` | who may do what: lock, wrong-chat guard, 48 h window, limits, wording guard, audit, overview / config, `control_token()` |
| API | `backend/routers/telegram_control.py`, `backend/services/telegram_control_service.py` | thin adapter, token dependency, `/api/telegram/*` |
| UI | `frontend/src/app/telegram/page.tsx`, `components/telegram/*`, `components/common/Dialog.tsx`, `services/telegramApi.ts`, `lib/TelegramHtml.tsx`, `lib/telegramFormat.ts`, `hooks/useControlToken.ts` | the page |

Telegram facts the design rests on: a bot **cannot list history**; it **cannot delete a message older than ~48 h** (found earlier, `dev_chat_reset.py`; the page shows the reason and
disables the button — remove it by hand in the app); editing has **no** such window; an edit that omits `reply_markup` **removes the inline keyboard**, so the ledger stores it and the
edit resends it; a message id is only meaningful in its own chat, so a ledger row whose chat is no longer the configured one is refused (the dev channel has been replaced before).

API: `GET /api/telegram/overview` · `GET /api/telegram/messages?target&kind&status&day&q&limit&offset` · `GET /api/telegram/messages/{id}` · `PATCH /api/telegram/messages/{id}`
`{text, acknowledge_wording}` · `DELETE /api/telegram/messages/{id}`. Refusals carry a stable `code`: `locked` 423, `wrong_chat` / `deleted` / `too_old` / `not_configured` 409,
`bad_filter` / `empty` / `too_long` / `unchanged` / `wording` 422, `telegram` 502, `not_found` 404; token: `controls_disabled` 403, `bad_token` 401.

## 2. Status board

| ID | Item | Status |
|---|---|---|
| T0 | Scope + design (this file) | ✅ |
| T1 | Ledger tables + store + client hook; every sender tags its posts with a `kind` | ✅ |
| T2 | Rules / service + Python tests + mutation checks | ✅ 1,625 tests pass (+49); `control` group = 23 mutants |
| T3 | API router + token guard, exercised against real Postgres and real Telegram (DEV) | ✅ |
| T4 | Frontend page, components, nav link, all UI states | ✅ |
| T5 | Verification: tsc, lint, isolated build, real browser run, live DEV edit / delete, security spot-checks | ✅ (see §3) |
| T6 | `code-review` (9 defects, all fixed), by-hand security review, docs updated | ✅ |
| T7 | Human check on a real phone; keyboard-after-edit eyeballed in Telegram | ⬜ owner |
| T8 | Phase 2: channel promotion (dev → prod), compose, pin/unpin, photo replace, schedule view, read-auth toggle | ✅ (see §6-7) |
| T9 | Owner authorizes a live send/pin/photo-replace test against the real channel | ⬜ owner |

## 3. Evidence

- **Python**: `python -m pytest mechanism/alerts/tests ml_training/tests -q` → 1,625 passed, 1 skipped. `test_channel_control.py` (48 tests): the ledger contract runs against **memory and real
  Postgres**, the client's edit / recording, every rule, structure (the control modules import nothing of the assistant, never construct `TelegramClient(`).
- **Mutation**: `mutation_checks.py --group control` — 23 re-introduced bugs (lock removed, empty switch counts as open, stale `.env` lock, wrong-chat guard, 48 h window, keyboard dropped, wording guard
  skipped, photo edited as text, double writer, missing audit, refused delete reported as success, full chat id shown, UTC "today", ledger failure breaking a send, error text logged, moved
  `deleted_at`, LIKE wildcards, private chat recorded, unlabelled digest card, placeholder token, stale record after a retried edit, panel flag mismatch, unguarded client build).
- **Live DEV round trip (real Telegram, dev channel only)**: sent through the normal senders → recorded → edit text (200), edit photo caption (200), same text (422), advice words (422),
  Telegram parse error (502, nothing changed), caption > 1,024 (422), delete text + photo (200), delete again (409), audit trail correct, overview counts moved; a synthetic production row
  was refused with **423 for both edit and delete using the real token**, and left no audit row. 21/21 checks. Every test message was deleted afterwards.
- **Real Chrome (DevTools protocol) on the running page**: 32 checks — both channel cards and lock chips, view-only banner, dialog in the top layer, editor without the token disabled with the reason,
  unlock (token in `sessionStorage` only), live counter and preview, **save reached Telegram**, list refreshed, restore-original, wording-guard warning with explicit override, status filter, text search,
  empty state, delete confirmation, **delete via the UI**, disabled actions on deleted posts, no content overflow at 390 / 820 px, table scrolls inside its card, **zero console errors**. Row tap opens the
  dialog at 390 px. Screenshots were inspected for the list, edit, wording, delete-confirm and phone layouts.
- **Security spot-checks (21/21)**: hostile SQL-ish input in `q` / `kind` / path id is data or 422 and the table stays intact; no secret value appears in `overview` or `messages`; a wrong token is
  401 and not echoed; a foreign origin gets no CORS allow-origin for the mutating call; a post containing `<script>`, `<img onerror>` and a `javascript:` link renders inert in the real page while a
  safe `https` link stays a link with `noopener`; `.env` is git-ignored. The `security-review` skill itself could not run (it diffs against `origin/HEAD`; this repo has no remote), so this was done by hand.
- **`code-review` (high) found 9 real defects, all fixed with a test**: stale production lock in a running API (`.env` now re-read) · a placeholder token accepted by the API · an unguarded client-build error
  (bare 500) · a retried edit reported "not modified" leaving the ledger stale · panel switches that disagreed with the senders (`CHANNEL_BOARD_ENABLED=false`) · backdrop drag-select closing the dialog and losing an
  unsaved draft (plus: an unsaved draft now blocks Escape / backdrop) · a timed-out mutation shown as "backend not running" (now "may or may not have reached Telegram", state re-read, 90 s timeout) · an empty page
  stranding the user after the last row was deleted · a duplicated tag-stripping rule.
- **Found and fixed on the way**: NextUI's `Modal` rendered unstyled (no NextUI Tailwind plugin in this app) → native `<dialog>` + native buttons; a `<div>` chip inside a `<p>` (hydration warning).
- **Not verified**: on a real phone; that Telegram keeps the inline keyboard after an edit (the stored keyboard is resent — asserted at request level; the Bot API has no call to read a message back, so
  eyeball it in the dev channel); the page against the production channel (locked by design; a synthetic row proved the refusal only).

## 4. Findings outside this scope (logged, not fixed — FRONTEND_FIX_MILESTONES.md §11)

FM-N15 global top-progress bar makes every page 905 px wide · FM-N16 TopNav does not collapse (already too wide at 820 px; the new link adds 78 px) · **FM-N17 `uvicorn --reload` hangs in shutdown after a file
change (reproduced; the wedge from CLAUDE.md §8)** · FM-N18 NextUI variants unstyled app-wide.

## 5. Decisions I made (the owner asked for "all messages, edit, delete according to the system configuration" — say so if any should change)

Channels only, never the assistant · production view-only while locked (and re-checked live) · edit / delete need a token, fail closed · no send-new / pin / photo-replace
in v1 (**built in phase 2, §6-7**) · configuration shown read-only instead of a scheduler view (**a schedule view was also built in phase 2**, from the senders' own state
files, never live Task Scheduler) · the wording guard applies to hand edits too · the ledger stores the original text so any edit can be undone.

## 6. Phase 2 (started 2026-09-22): channel promotion + compose, pin/unpin, photo replace, schedule view, read-auth toggle

**Trigger.** The owner turned the DEV channel into the real, public channel (renamed/promoted it as "First Light - Stocks & Market Analysis").
Confirmed and executed, exactly as authorized: `TELEGRAM_CHAT_ID` now points at that chat (the old, unused production id kept as a `.env` comment),
`PROD_SENDING_ENABLED=1`, and the five registered Task Scheduler jobs (`FirstLight-1..5`) switched from `-To dev` to `-To prod`. `TELEGRAM_DEV_CHAT_ID`
is unchanged by the owner's explicit choice — dev and prod are now the same physical chat. Verified live: `getChat` on the `prod` target resolves to
the real channel; `dev_chat_reset.py`'s existing safety check (refuses when the dev id equals the production id) now correctly refuses to run against
it, exactly as designed for this scenario. **Consequence to know:** any script run with the default `--to dev` now posts to the real audience too
(they are the same chat) — the "dev is a safe throwaway" assumption behind `qa_live.py`, `replay_dev_channel.ps1` and manual `--to dev` sends no
longer holds until a fresh dev channel exists. `overview()` now warns about this explicitly (§ below).

**In scope, this phase**
1. **A warning when dev and prod are the same chat** — `overview.warnings` gains an explicit line; no silent assumption anywhere else changes.
2. **Compose & send a new post** from the page: text or photo, target picker, kind label, preview, the same wording guard / length limits / control
   token as edit, through the SAME send path every other sender uses (`TelegramClient.send_message`/`send_photo`, so the ledger records it exactly
   like a script-sent post — no second write path to drift from the first).
3. **Pin / unpin** an existing message (Telegram's `pinChatMessage`/`unpinChatMessage`), same lock / wrong-chat rules as edit, its own audit actions.
4. **Photo replace on edit** (`editMessageMedia`): upload a new image + a (re-typed, not defaulted) caption for an existing photo message; recorded
   as an edit (caption + edit_count), the image itself is never stored (same reason the ledger never stored images before: `MessageModal`'s bubble
   already says so).
5. **A "Today's schedule" section**: what is configured to post today, and whether it already has, per the SENDERS' OWN state files
   (`data/session_state.json`, `data/notice_state.json`, `data/earnings_today_state.json`) and `market_calendar.check_new_session` /
   `is_trading_day` — never Windows Task Scheduler itself (still out of scope, still platform-specific; this reads the same cross-platform JSON
   files the schedulED scripts already write, so it works unchanged if this is ever hosted on Linux). Flags a job "overdue" when its known local
   time has passed and nothing was recorded.
6. **An opt-in token requirement for reads** (`TELEGRAM_READ_REQUIRES_TOKEN=1`; default off, so today's localhost read-only browsing keeps working
   unchanged) — GET endpoints then need the same `X-Control-Token` header as edit/delete. Off by default because nothing is hosted publicly yet;
   documented and tested so it is one flag away, not a rebuild, once it is.

**Verification plan.** Every new rule gets the same treatment as phase 1: `MemoryLedger`-backed unit tests + real-Postgres contract tests, a
mutation-checks group, tsc/lint/an isolated build, and a real-browser pass. **Compose, photo-replace and pin/unpin actually reach Telegram** — unlike
phase 1's DEV testing, that Telegram is now the real audience channel, so no automatic live-send test runs here. Before any live send/pin/photo-edit is
exercised for real, that is asked for explicitly, the same as any other outward-facing, hard-to-reverse action.

## 7. Phase 2 — evidence

- **The channel promotion**, done exactly as authorized: `.env` now has `TELEGRAM_CHAT_ID` = the former dev chat, `PROD_SENDING_ENABLED=1` (the old,
  unused production id kept as a comment); the five registered Task Scheduler jobs (`FirstLight-1..5`) switched `-To dev` → `-To prod`. Verified live:
  `getChat` on the `prod` target resolves to "First Light - Stocks & Market Analysis" (a real channel); `dev_chat_reset.py`'s existing safety check
  (refuses when the dev id equals the production id) now correctly refuses to run against it — exactly the scenario it was built for.
- **Tests**: `python -m pytest mechanism/alerts/tests ml_training/tests -q` → **1,679 pass** (+54 over phase 1: 76 in `test_channel_control.py` alone,
  incl. compose text/photo, pin/unpin, photo-replace, the schedule module's own behaviour incl. a genuine NY-vs-Israel-date case, the read-auth
  toggle tested directly against the FastAPI dependency, and the ledger-write fallback insert).
- **Mutation**: `mutation_checks.py --group control` grew from 23 to **35 mutants, all caught**. The harness's first run against the new mutants found
  **3 real coverage gaps** (not just theoretical ones): compose's default `_send_client` factory was never exercised by any test (every compose test
  used a fake), the new `TELEGRAM_READ_REQUIRES_TOKEN` toggle had no test calling the actual FastAPI dependency, and the schedule module's NY-date key
  for the earnings job was only checked as a source string, not behaviourally (a mutant swapping it for the local-timezone date slipped through). All
  three now have dedicated tests; re-run confirmed 35/35 caught.
- **`code-review` (high) on the new code found 6 issues**, all fixed:
  1. `compose_photo`/`replace_photo` were `async def` but called the blocking `ChannelControl` methods directly inside the coroutine, able to stall
     the whole event loop for the ~60s a Telegram 429 retry can take — fixed with `starlette.concurrency.run_in_threadpool` (kept `async def` since
     these two genuinely need `await photo.read()`, unlike every other endpoint here).
  2. A composed post that reached Telegram but whose ledger write silently failed (the recorder never raises, by design) was permanently invisible to
     this page — `_composed_result` now attempts its own fallback `record_sent` (safe via the same `ON CONFLICT DO NOTHING` the recorder itself relies
     on) before giving up and reporting `message: null`.
  3. `ComposeModal` had no unsaved-draft guard (unlike `MessageModal`), so a backdrop click or Escape silently discarded a typed post.
  4. A client-side timeout during compose (not idempotent, unlike edit/delete/pin) risked a genuine duplicate post on resubmission with no warning —
     `ComposeModal` now requires an explicit "I checked the channel" acknowledgement before a timed-out send can be retried.
  5. `schedule.py`'s slot number for an "always"-gated job was derived by string-comparing a job's key to the literal `"notice1"` instead of being a
     field on `ScheduleJob` — fixed (`slot: int = 0`, set explicitly per job) before it could silently collide for a future third such job.
  6. `PAGE_MAX` was reimplemented in `channel_control.py` instead of reusing `message_ledger.MAX_PAGE` — now a single import, one source of truth.
- **Found and fixed on the way**: `python-multipart` (needed by the new upload endpoints) was in `.venv` and `backend/requirements.txt` but not the
  system Python this project's tests run under — installed there too, closing the gap between what the backend actually needs and what the test
  environment has.
- **Build**: tsc and lint clean throughout; the isolated production build passes (`/telegram` route: 13.5 kB → 17 kB with compose + schedule added).
- **Not done**: no live send, pin, or photo-replace has been exercised against the real channel — that needs the owner's explicit go-ahead, since it is
  now the real audience channel and none of these three actions are safe to "just try" the way DEV testing was in phase 1.
