# RUNBOOK_FIRST_LIGHT.md — run the bot, test it by hand, run the morning notification

> **Scope note (added 2026-09-25):** this document describes running things **on the Windows
> development machine** — still valid for local/dev-channel testing, but **not** how production
> scheduling works today. Production runs on VPS systemd timers — see
> [docs/architecture/SCHEDULING.md](docs/architecture/SCHEDULING.md). Do not follow this document as
> production operating instructions.

All commands are run from the repo root (`E:\מסמכים\pythonProjects\donchian_screener_0.1`) in **PowerShell** unless noted.
Two independent things exist: **the bot** (`run_bot.py`, a process that must stay running) and **the morning notification**
(`send_daily_digest.py`, a job that runs once a day and exits). They share the database but not the process.

---------------------------------------------------------------------------------------------------------------------------------

## 1. Run the bot

| What | Command |
|---|---|
| Is it running? | `python mechanism\alerts\qa_live.py` → look for `[PASS] bot process is running` |
| Start (own window) | `python mechanism\alerts\run_bot.py` — wait for `Starting @aplha_wolf_bot (invite-only; owner id set)` and `Start polling` |
| Stop | `Ctrl+C` in its window |
| Stop a copy you cannot see | `$c = Get-NetTCPConnection -LocalPort 47831 -State Listen -EA SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force }` |
| After ANY code change | stop, then start again (an old process keeps running old code) |

Rules: only **one** copy can run (a second one refuses to start: "Another run_bot.py is already running"). The bot only answers while the
process is alive and the PC is on. A copy was started in the background from the Claude session on 2026-09-21; if you want it in your own
window, stop it with the command above first.

`.env` needs: `TELEGRAM_BOT_TOKEN`, `BOT_OWNER_ID` (your Telegram id), `ALPACA_API_KEY` + `ALPACA_API_SECRET` (news), `TELEGRAM_DEV_CHAT_ID`.
One BotFather step only you can do: **/mybots → the bot → Bot Settings → Allow Groups → Turn off**.

---------------------------------------------------------------------------------------------------------------------------------

## 2. Test the bot by hand (about 25 minutes)

Use **your** account for A–C, a **second Telegram account** (a friend, or another phone number/account of yours) for D.
Tick each line. Anything that differs from "expect" is a bug — write down what you saw.

> Your own account already accepted the notice earlier, so `/start` shows "Welcome back", not the guide. Use `/guide` to see the guide.

### A. First screens
1. `/start` → **Welcome back** + a menu at the bottom: *Today's lists · Portfolio · Watchlist · Help*.
2. `/guide` → step 1/4 with **Next › / Skip guide**. Tap Next twice, Back once, Next, Next → step 4 shows the disclaimer and **I understand - continue**. Tap it → the step-4 message loses its buttons and says *Accepted*; a new message *You are all set* appears.
3. Tap **Help** → the command list (owner section with /invite … is visible to you only).

### B. Today's lists and the stock card
4. Tap **Today's lists** → header `US close <last session>`, `N stocks from the channel's lists, each shown once`, two tabs (*Breakout · n*, *Near breakout · n*). Expect **up to ~45 stocks per tab** (the channel lists are now 15 deep; ~23 in a session stored before 22 Sep), no symbol twice, ★ only on stocks in the top 5 of 2+ lists.
5. Tap the **Near breakout** tab → the same message changes (no new message). Tap **What do these mean?** → a popup.
6. Tap a stock (e.g. the first) → its **card**: close, day %, volume ×, range × ATR, "In today's lists: gainers #1 · ATR #1 …". Buttons: *News · Chart · ATR levels*, *Add to watchlist · Add to portfolio*, *‹ Today's lists*.
7. **News** → up to 5 headlines with time ago; tap one → opens the publisher. **‹ SYMBOL** goes back to the card.
8. **Chart** → a picture (candles, volume, dashed "prior 20-day high") arrives as a **new message** with a short caption. Tap **News** under it → works. Request the *same* stock's chart with a second account later: it should appear instantly (cached).
9. **ATR levels** → risk level and reference levels with the "distances, not predictions" note.
10. Type a ticker, e.g. `aapl` → its card (no Back button). Type `zzzz` → "not in the daily scan". Type `hello there` → a hint.

### C. Watchlist and Portfolio (the important part)
11. On a card tap **Add to watchlist** → the card now says *On your watchlist since <today> at $X (last close when added)* and buttons become *Move to portfolio / Remove*.
12. Tap **Move to portfolio** → a prompt. Send `140 10` (price, then shares) → *Moved to your portfolio: 10 sh at $140.00 (your price)* and a *Value … (+/-$…)* line.
13. Send a bad reply first next time: tap the button, send `abc`, then `0`, then `140,5` → each gets "I could not read that…", the prompt stays open, nothing is saved. Then send `140.5 10`.
14. **Portfolio** → `Total cost … · value … · ▲x.x% (+$…)`, `Largest position …`, each row `10 sh · your price … · now … · $value · weight%`. **Check the arithmetic yourself:** value = shares × now; cost = shares × your price; change = value − cost.
15. `/add COIN` (watchlist, last close), `/add COIN 190` (watchlist, your price), `/add AAPL 180 5` (portfolio), `/add MSTR COIN AAPL` (several at once → one summary), `/add MSTR 1 2 3` and `/add MSTR abc` (both refused with an explanation).
16. **Watchlist** → each row `▲x.x% since <day>` with the price you added at.
17. On a card tap **Remove** → confirmation *Remove X from your portfolio? Its saved price and date are erased.* → **Keep it** changes nothing; **Yes, remove** removes. `/remove AAPL` removes without asking.
18. `/export` → a CSV file arrives; open it (Excel): symbol, list, your_price, date_added, shares, last_close, change_since_added_pct.
19. `/privacy` → says what is stored and that the owner can technically read the database.
20. `/deleteme` → confirmation → **Yes, erase everything** → lists are empty (`/portfolio` says empty).

### D. Invitations and privacy (needs the second account)
21. You: `/invite Test` → a link `https://t.me/aplha_wolf_bot?start=inv_…` (valid 72 hours, single use). Send it to the second account.
22. Second account opens the link → *Welcome. Your invitation was accepted.* then the **guide from step 1** → accept → menu. **You** get a message *Invitation used (Test): Telegram id <number>* — note the number.
23. Second account: add a stock with `/add MSTR 111 3`. **You:** `/portfolio` must NOT show it; second account's `/portfolio` must NOT show yours. (Isolation.)
24. Open the **same link again** from another account → *This invitation link is not valid any more*. A third account with no link: `/start` → *private assistant for invited users* + its own id, **no menu, no data**.
25. You: `/users` → counts only (never ids/holdings). `/status` → snapshot date and counts.
26. You: `/revoke <the id>` → the second account's very next message gets the "invitation required" refusal. Their old link cannot bring them back; only `/approve <id>` can.

### E. Where the bot must stay quiet
27. **Group:** if "Allow Groups" is still on, add the bot to a throw-away group and send `/portfolio` → the bot only says it is a private assistant and shows an *Open the private chat* button — never data. (After you turn Allow Groups off, the bot can't be added at all.)
28. **Channel:** the bot never posts or replies in the channel. Only the buttons under the daily header work there (see §3 step 4).
29. Press the **stale-button check:** tap a button on a message that is hours old → it still works or says "message too old", never silence.

### F. If something looks wrong
`python mechanism\alerts\qa_live.py` (health, read-only) · `python -m pytest mechanism/alerts/tests -q` (1,289 tests, ~25 s) ·
the bot window shows every error with a traceback · `python mechanism\alerts\qa_live.py --send-to-owner` sends every assistant screen to **your private chat with the bot** (never a channel) so real Telegram validates it.

---------------------------------------------------------------------------------------------------------------------------------

## 3. The morning notification ("First Light" digest)

**What it posts:** one market-card picture (indices, breadth, sectors) + a header + one message each for **Breakout** and **Near breakout**
(top gainers / top ATR / top volume). Only the picture (or header) makes a sound. It also saves the day's **snapshot** that the bot reads —
so the digest must run before the bot's *Today's lists* shows the new day.

**How long it takes** (measured in your logs): price update ≈ **21 minutes**, digest ≈ **1 minute**. The full pipeline (2.5+ hours) is
**not** needed first — run it later in the day.

> **Everything is built and tested on the DEV group. Production is LOCKED**: `-Send -To prod` (and any script's `--send --to prod`) is refused until
> `PROD_SENDING_ENABLED=1` is set in `.env` (it is `0` now). Flip it only at launch (see FUNNEL_PLAN.md §8). The dev group is always open.

### 3.1 One command: `run_first_light_morning.ps1`
```powershell
.\run_first_light_morning.ps1                              # PREVIEW: builds + prints the digest and the card PNG, sends NOTHING, updates NOTHING
.\run_first_light_morning.ps1 -Send                        # real run to the DEV group: gate -> index -> prices (~21 min) -> digest
.\run_first_light_morning.ps1 -Send -To prod               # real run to the PRODUCTION channel
.\run_first_light_morning.ps1 -UpdateOnly -To prod         # only refresh prices (for a scheduled early start)
.\run_first_light_morning.ps1 -Send -To prod -SkipUpdate   # only build + post (prices already fresh)
```
Extra switches: `-Force` (ignore the trading-day gate: re-send a session / run on a weekend), `-NoImage`, `-NoButtons`, `-Update` (refresh
prices even in a preview). Log: `logs\first_light_morning_<date>.log`. Preview picture: `reports\first_light\first_light_<session>.png`.

The **trading-day gate** makes weekends, NYSE holidays and an already-sent session a no-op (exit 0). Right now (Mon 21 Sep, before the US
open) the newest completed session is Fri 18 Sep: **dev already has it** (a plain `-Send` skips), **prod has never received a digest**
(`-Send -To prod` would post Friday's).

### 3.2 First real send — recommended order
1. Preview: `.\run_first_light_morning.ps1` → read the text in the console, look at the PNG.
2. See it in Telegram without touching the channel: `.\run_first_light_morning.ps1 -Send -SkipUpdate -Force` (dev group; `-Force` because dev already had Friday).
3. Check on your phone: picture first, then header, then the two group messages; only one notification sound.
4. **Only at launch** (after the checklist in FUNNEL_PLAN.md §8, and after setting `PROD_SENDING_ENABLED=1` in `.env`): `.\run_first_light_morning.ps1 -Send -To prod -SkipUpdate`.
5. The three popup buttons under the header (*What is ATR? / vol × / groups*) only answer **while the bot is running** (§1). The *Private assistant (invite only)* button opens the bot and shows the refusal to anyone without an invitation.

### 3.3 Make it run every morning at 06:00 Jerusalem (Windows Task Scheduler) — two tasks
Use `-To dev` while testing (the same commands, `-To dev` instead of `-To prod`) — that is how you prove the schedule works for a week before launch.
06:00 Jerusalem is 23:00 New York, ~7 hours after the US close, so the finished session is available. Two tasks give an exact post time:
```powershell
$dir = 'E:\מסמכים\pythonProjects\donchian_screener_0.1'
$ps  = 'powershell.exe'
$upd = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $dir -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\run_first_light_morning.ps1`" -UpdateOnly -To prod"
$snd = New-ScheduledTaskAction -Execute $ps -WorkingDirectory $dir -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\run_first_light_morning.ps1`" -Send -To prod -SkipUpdate"
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName 'FirstLight-1-UpdatePrices' -Action $upd -Trigger (New-ScheduledTaskTrigger -Daily -At 5:00am) -Settings $set
Register-ScheduledTask -TaskName 'FirstLight-2-SendDigest'   -Action $snd -Trigger (New-ScheduledTaskTrigger -Daily -At 6:00am) -Settings $set
```
Remove later: `Unregister-ScheduledTask -TaskName 'FirstLight-1-UpdatePrices' -Confirm:$false` (same for `-2-`).
Notes: the PC must be **on** (or able to wake) and you **logged in** (default task mode); on days with no new session the gate makes both tasks exit
immediately — so it posts **Tuesday–Saturday mornings** (covering Mon–Fri sessions) and stays silent on Sunday/Monday. To also stay silent
on Saturday morning set `ALERTS_SKIP_WEEKDAYS=sat` in `.env`. Keep the bot itself running too (§1) — a third task "at log on" running
`python mechanism\alerts\run_bot.py` does that (the lock port prevents duplicates). **Not created for you** — it would post to the public channel every day.

---------------------------------------------------------------------------------------------------------------------------------

## 4. Naming the channel and the bot

Your idea **"First Light — Stocks & Info"** works: short, matches the header the digest already prints ("First Light"), says what it is.
Nothing in the code needs to change for a channel rename (the bot's refusal text says "the owner of the First Light channel").
Suggested set-up (all done in the Telegram app / BotFather, not in code):

| Where | Suggestion |
|---|---|
| Channel name | `First Light — Stocks & Info` |
| Channel description (≤255) | `Daily end-of-day scan of ~2,900 liquid US stocks: today's breakouts and near-breakouts, ranked by gain, ATR and volume. Educational data, not investment advice.` |
| Pinned "Start here" post | what the two groups mean, how to read ★ / vol × / ATR, the disclaimer, and "Private assistant: invite only". |
| Bot display name (BotFather `/setname`) | `First Light Assistant` (now "AlphaWolf") |
| Bot texts (`/setdescription`, `/setabouttext`) | "Private assistant for First Light members. Invitation required." |
| Bot picture | `/setuserpic` (use the same logo as the channel) |
| Bot username | `@aplha_wolf_bot` has "aplha" spelled wrong; BotFather `/setusername` can change it, but old links stop working (the digest builds its button link from the live username, so it follows automatically). |

Tell me the final channel name and I will align the wording in the bot and the digest header.

---------------------------------------------------------------------------------------------------------------------------------

## 5. Invite a friend (the first non-owner) — channel + bot

Where things run (decided 2026-09-21): **everything is tested in the DEV channel** ("First Light - Dev", private; it replaced the old flooded group "BOT_SPAMMING",
see §7). The production channel ("Top Gainers - Daily", public `t.me/top_gainers_daily`, 5 members, old top-15 posts) is **locked and untouched** until launch; a NEW
production channel replaces it then (FUNNEL_PLAN.md §8). So a friend who tests joins the **dev channel** to see the digest, Start-here post and promo, and reaches the
**bot** by tapping the channel's "Private assistant" button → *Request access* (§8) or by your invitation link.
Dev channel invite link: open the channel → ⋮ → *Invite links* — send it privately.

### 5.1 Before you invite anyone
- The bot process must be **running** (§1) when she opens the link — otherwise nothing answers.
- **Legal/privacy gate (PRIVATE_ASSISTANT_PLAN.md §9):** the first non-owner is the moment the plan says a legal review should already be done
  (personalised tracking closer to advice than the public digest; her holdings are stored, and you can technically read them). For ONE trusted
  friend testing, the practical risk is small — but it is your decision, so tell her: it is a **beta**, the data is educational, and she should not
  enter real holdings she is not comfortable with. Do the review before opening it to strangers.

### 5.2 Step by step
| # | Who | What | She / you see |
|---|---|---|---|
| 1 | You | Send her the **dev channel's invite link** (channel → ⋮ → Invite links) — *not* the production channel | She taps **Join**; she sees the digest and the pinned Start-here post |
| 2 | You | In your **private chat with the bot** send `/invite Dana` (any label; only you see it) | A link `https://t.me/aplha_wolf_bot?start=inv_…` — **single use, valid 72 h** |
| 3 | You | Send that link to her **privately** (not in the channel, not in a group) | |
| 4 | She | Taps the link → the bot chat opens → **Start** | *Welcome. Your invitation was accepted.* then the **4-step guide** |
| 5 | She | Steps through the guide → **I understand - continue** | *You are all set* and the bottom menu (Today's lists · Portfolio · Watchlist · Help) |
| 6 | You | — | A message **"Invitation used (Dana): Telegram id 123456789"** — keep that number (it is what `/revoke` needs) |
| 7 | You | `/users` | `1 active · 0 revoked · 0 open invitation(s)` |

Tip: opening your **own** invitation link does not use it up (you are the owner), so you can check the link looks right before sending.

**If she opened the bot first without a link:** she gets the refusal *"private assistant for invited users"* with **Your Telegram id: 123456789**.
She sends you that number, you send `/approve 123456789`, she sends `/start` again. (Same result, no link needed.)

### 5.3 What can go wrong
| She sees | Cause | Fix |
|---|---|---|
| Nothing happens after Start | bot not running | start it (§1), she taps Start again |
| *This invitation link is not valid any more* | already used, or older than 72 h | you `/invite` again |
| *private assistant for invited users* + her id | no invitation yet | `/invite` (link) or `/approve <her id>` |
| *This account no longer has access* | you revoked her | `/approve <id>` re-admits (a new link does not) |
| Buttons under the channel post do nothing | bot not running | start it |

### 5.4 A 10-minute QA session with her (what to ask her to try)
1. Open the link → guide → menu. *(Was any step confusing? How long did it take?)*
2. Tap **Today's lists**, switch tab, open one stock.
3. Tap **News** and **Chart** on it.
4. **Add to watchlist**, then **Move to portfolio** with her own price and shares (`150 10`).
5. Open **Portfolio** — does she understand every number?
6. Type a ticker she knows (`nvda`), then something wrong (`zzzz`).
7. `/export`, then `/deleteme` at the end (leaves nothing behind).
On your side confirm: her data never appears in **your** `/portfolio`, and yours never in hers.
Remove her afterwards (or keep her): `/revoke 123456789`.

---------------------------------------------------------------------------------------------------------------------------------

## 6. Promotion images

```powershell
python mechanism\alerts\promo_assets.py     # writes three PNGs to reports\first_light\promo\ :
                                            #   first_light_assistant_promo.png  (1080x1350 promotion post)
                                            #   first_light_channel_logo.png     (640x640 the CHANNEL's photo: the sunrise mark)
                                            #   first_light_bot_avatar.png       (640x640 the ASSISTANT's photo: the mark in a chat bubble)
```
- **Bot picture:** Telegram -> @BotFather -> `/mybots` -> the bot -> *Edit Bot* -> *Edit Botpic* -> upload `first_light_bot_avatar.png` (shown as a circle; everything sits inside the safe area).
- **Channel picture:** channel -> menu -> *Manage channel* -> the photo -> upload `first_light_channel_logo.png` (dev channel now, production at launch).
- **The promotion post** (`python mechanism\alerts\channel_posts.py --promo --send`, dev first) goes to production **only at launch**. The request-access flow now exists, so its "Private assistant" button leads to *Request access*, not a dead end.
- Change wording or layout in `mechanism/alerts/promo_assets.py` (`PROMO_COPY`); `pytest mechanism/alerts/tests/test_promo_assets.py` re-checks size, contrast, circle-safe avatars and that the copy has no advice words or performance claims.

---------------------------------------------------------------------------------------------------------------------------------

## 7. A fresh DEV channel (why, how, and replaying everything into it)

**Why a new one:** Telegram does **not let a bot delete messages older than ~48 hours** (it answers "message can't be deleted", even for message #5). The
old dev group "BOT_SPAMMING" has ~20,000 old messages, so a bot cannot clear it; the same is true of the old production channel. A new chat is the only
clean slate (`mechanism/alerts/dev_chat_reset.py` only clears the last 48 h, then stops — its `--yes` run deleted 88 recent messages and gave up).
Make the new dev chat a **private channel** (the same kind as production) so you see posts and buttons exactly as subscribers will.

**Set it up (5 minutes, in the Telegram app):**
1. New Channel → name `First Light DEV` → **Private** → create. Add yourself (and a friend later).
2. Channel → ⋮ → *Administrators* → *Add administrator* → your bot → allow **Post messages, Edit messages, Delete messages** (pinning uses *Edit messages*).
3. Post any message in the channel, **forward it to the bot** (your private chat). The bot answers *"Chat id: -100…"* (owner only).
4. Send that id to Claude (or put it in `.env` as `TELEGRAM_DEV_CHAT_ID=-100…`). Restart nothing — the senders read `.env` each run; the bot does not use it.
5. `.\replay_dev_channel.ps1` — posts to the channel, in order: the pinned **Start here** post → the daily **digest** (market card, header + buttons, Breakout, Near breakout) →
   the **promo image** → and nothing else: **a channel carries only data, promotion, news and information — the assistant's screens live in the private chat with the bot.** Switches: `-SkipDigest`; `-Tour` additionally sends the labelled assistant tour (`[QA n/N]`) to **your private chat with the bot**, not to the channel. It never targets production.
6. Check on your phone: the pinned post, the order, that the popup buttons under the digest header answer (this is the first test of buttons in a *channel*), and that the
   *Private assistant* button opens the bot with the **Request access** screen.

The old group can simply be left (or deleted in the app). Later, production gets the same treatment at launch: a new channel, its id in `TELEGRAM_CHAT_ID`, then `PROD_SENDING_ENABLED=1`.

## 8. The request-access flow (how a stranger becomes a user)

| Step | Who | What happens |
|---|---|---|
| 1 | stranger | taps the channel's **Private assistant** button (or opens the bot) → *Request access / What is this?* — **nothing is stored yet** |
| 2 | stranger | taps **Request access** → their Telegram id + the time are stored; they see *your request was sent* |
| 3 | you | get a message *Access request from Telegram id …* with **Approve / Decline** (or use `/requests` to see the queue) |
| 4a | you | **Approve** → the person is told *You are in* and the 4-step guide starts |
| 4b | you | **Decline** → they are told, and cannot ask again for 7 days |
`/funnel` shows counts only (channel opens, requests, approvals, guide finished, first stock tracked within 24 h, active this week). Waiting requests are deleted after 14 days.
`.env`: `BOT_ACCESS_MODE=approve` (default) | `auto` (accept the first `BOT_MAX_MEMBERS`=25 automatically, then queue) | `closed` (no requests); `BOT_MAX_PENDING`=100 caps the waiting list.
Tell the bot to restart to change the mode. A typed `/invite` link and `/approve <id>` still work.


## 9. Channel content and the new assistant commands (added 2026-09-21, late)

**Every morning (DEV, registered in Task Scheduler):** 05:00 `FirstLight-1-UpdatePrices` (index + prices + saves the day's snapshot), 06:00 `FirstLight-2-SendDigest` (the digest, then ONE extra silent post; on Sunday the weekly recap).
Check `logs/first_light_morning_<date>.log`. Remove a task: `Unregister-ScheduledTask -TaskName FirstLight-1-UpdatePrices -Confirm:$false` (same for `-2-`).

**Preview / review the extra posts (nothing goes to a channel):**
```powershell
python mechanism\alerts\send_channel_posts.py                 # today's extra post, printed (dry run)
python mechanism\alerts\send_channel_posts.py --all           # every kind (images saved under reports\first_light\posts\)
python mechanism\alerts\send_channel_posts.py --all --send --to owner --force   # every kind in YOUR private chat with the bot (real Telegram check)
python mechanism\alerts\send_channel_posts.py --kind health --send               # one post to the DEV channel
```
Rotation by the session's weekday: Mon sector rotation / macro strip, Tue gaps and volume / news, Wed market health, Thu near 52-week highs / aligned timeframes, Fri education or promotion;
first Thursday of the month = base rates; first Friday = scoreboard (only if enabled); Sunday = weekly recap.
**Off until you switch them on in `.env`:** `CHANNEL_NEWS_ENABLED=1` (licence unchecked) and `CHANNEL_SCOREBOARD_ENABLED=1` (unflattering numbers). Production stays locked.

**New assistant commands to try in your private chat (add to the 29-step test):** `/full` and `/full near` (all stocks of a group, sorted by gainers / ATR / volume) · `/aligned` · `/history MSTR` · `/week` (add a stock first) ·
`/scan` (a CSV of the whole scan) · `/screen breakout vol>3` and `/screen near price>10 below<1.5 sort=vol top=10` (send `/screen` alone for the rules) · `/morning on` (the first message arrives with the next scan; `/morning off` removes it).

**Rebuild history:** `python mechanism\alerts\backfill_snapshots.py --sessions 60` (resumable; reconstructs past sessions from today's adjusted prices; ~30 s each).
