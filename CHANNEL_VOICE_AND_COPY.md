# CHANNEL_VOICE_AND_COPY.md — the soul, the voice, the copy and the image system of First Light

> Status (2026-09-21, later): **§4 partly BUILT on DEV** (see §9 for exactly what) after the owner delegated the minor changes; §10-§12 record the owner's later decisions (send time 00:00 Israel,
> VIP staging, "hold" content). Everything else here is still a proposal. Written after the audit of every automated post (dry runs of the real builders, Fri 18 Sep data).
> Skills applied: `SKILLS/marketing-strategy-pmm` (positioning, messaging hierarchy), `SKILLS/marketing-cro` (one CTA, first-person action button, funnel
> measurement), `SKILLS/telegram-bot-ui-design` (mobile-first, no dead ends, no emoji spam). There is no copywriting skill in the repo.
> Binding rules that still apply: facts only, no advice wording, no fabricated number, dev first / prod locked, a channel never carries assistant screens
> ([HANDOFF.md](HANDOFF.md) §1). The wording guard (`BANNED` in `mechanism/alerts/tests/test_channel_content.py`) rejects: buy, sell, target, stop, entry,
> recommend, signal, opportunity, pick, should, profit, **winner, guarantee, alpha, return, beat, outperform, earn, money**.

## 1. The offer (what the copy has to sell) — my reading, to confirm

| Rung | What | Price | Job of the channel toward it |
|---|---|---|---|
| Free channel | The daily scan: what moved, at 06:00 Israel, before the US open | 0 | Habit + curiosity. Show the *what*, withhold the *which* and the *how* |
| **Playbook** (e-book) | The process: fundamental + technical analysis, every core concept, how to build your own shortlist on a small budget. **Includes 2 weeks in the closed group** | $59 one-off | Explains the jargon we deliberately do not explain in posts |
| **Assistant** (subscription) | The names behind the counts, your own stocks tracked from the day you add them, `/screen` (find the assets that fit *your* rules), the morning message | $29 / month | Runs the playbook's scan for you every day |

The story that ties them, and it is true: **the channel shows what happened; the Playbook teaches how to find it yourself; the Assistant does it for you every morning.**
"Low budget for information" is the promise of the whole ladder: $29 a month instead of a terminal, and a process instead of a subscription to someone else's picks.

## 2. The soul

**Name and image.** First Light: the hour before the crowd wakes up. Sunrise mark, Midnight Dawn palette, a post at 06:00 Israel time, hours before the US open. Everything in the brand
is a version of one idea: **you read it first.**

**Positioning line (PMM template):** *First Light helps active retail investors start their day knowing what broke out at the US close, and gives them the method to find the next one themselves.*

**Tagline:** **Before the open.** (True: the digest lands hours before the US session starts.) Sign-off ritual on the digest: **Tomorrow, 06:00.** (Only while the schedule really runs.)

**Three feelings, in this order**
1. **Curious.** Every post ends on an open loop that is real: a count without the names, a term without the definition, a pattern without the explanation.
2. **Behind.** The market has already moved; the crowd finds out at the open. If you did not read at 06:00, you are the crowd. (A fact about timing, not a threat.)
3. **Capable.** "I can learn to do this." The tone never talks down and never hypes. It is the voice of someone who already did the homework.

**Voice: the night-desk editor.** Calm, sharp, numbers first, never excited, never explains, never apologises. Short sentences. One idea per line. No exclamation marks, no emoji beyond ▲ ▼ ★.
Cool, not loud: the excitement is in the numbers.

**Anatomy of every non-digest post (max ~280 characters)**
1. **Hook**: the finding, at most 8 words ("Index up. Most stocks down.").
2. **Proof**: two or three numbers, nothing else.
3. **Door**: one line, one action (names inside / tomorrow 06:00 / the Playbook). Never two doors.

## 3. Curiosity and FOMO that stay true

FOMO works here because these four things are real. Use only them.

| Lever | Real basis | Example |
|---|---|---|
| Information gap | The names of the aligned stocks, the tracker and `/screen` are only in the assistant | "51 broke out. 16 lined up on every timeframe. The names are in the assistant." |
| Timing | The digest is out before the open; the crowd reads it later | "Out at 06:00. The US opens later today." (a fact about the clock; never a claim about what price will do) |
| Scarcity | `BOT_MAX_MEMBERS` = 25 and approval is manual | "Access on request. Seats are limited." (show a number only if computed from the database) |
| Unexplained terms | ATR, vol ×, ★ are taught in the Playbook and in the tap-only popups | "range 3.6× ATR" with no gloss; the popup and the Playbook answer it |

**Never:** a countdown that resets, invented member counts, testimonials we do not have, a cherry-picked "look what this one did", "don't miss", "last chance", "next big winner", "secret",
"insider", and any sentence that says or implies a listed stock will rise. Our own base rate (51% of long breakouts hit the 2× ATR risk level) and list scoreboard (the Breakout lists trailed the
whole liquid universe over 60 sessions) say a list alone is not an edge. **So the FOMO is about information and method, never about the stocks.** That is also what makes the Playbook the honest next step:
the lists are a starting point, the analysis is the work.

## 4. Copy v2 for every post (proposal, Fri 18 Sep data; wording guard clean by construction)

Rule for all of them: definitions and caveats leave the post. Definitions go to the tap-only popups and the Playbook; legal caveats stay in the pinned post, untouched.

**1. Digest photo + caption** (the notification line; the header message is dropped and its buttons move onto the photo, so the digest is 3 messages instead of 4)
```
Index up. 65% of stocks down.
51 broke out · 282 within 3% of it
★ GEMI · FWDI · MSTR · PS · SBET · COIN
Tomorrow, 06:00.
```
The first line is generated by a rule from facts (index direction × breadth): divergence → "Index up. 65% of stocks down."; broad rally → "Broad rally. 74% of stocks up."; broad sell-off → "Broad sell-off. 81% of stocks down."; otherwise the neutral "Mixed. 48% of stocks up.". The same line becomes the hero title on the market-card image.

**2. Breakout / Near-breakout messages**: title `BREAKOUT · 51`, no italic definitions, no "Lists use the 38…" sentence (becomes a two-word footer `38 ranked · $5M+ a day`), list subtitles removed (`Top gainers`, `Top ATR`, `Top volume` are enough). Three rows open per list, the rest expandable.
The `NEW` tag is shown only when it is not on nearly every row.

**3. Momentum board**
```
Board · Fri 18 Sep
1  PS    ▲13.7%
2  COIN  ▲11.7%
3  BNC   ▲10.3%
81 tracked · 25 up · 53 down · 3 flat
```
(Fixes 25 + 53 ≠ 81: the three flat stocks are now stated.)

**4. Market health**
```
Index up. Most stocks down.
S&P 500 ▲0.2% · 34% of stocks up
Above 50-day: 32%  (was 50%)
Above 200-day: 53%  (was 65%)
Highs 40 · Lows 126
```

**5. Sector rotation**: the title is chosen by the facts. All sectors lower: `Every sector lower than 20 sessions ago.` then `Least: Financial Services −1.7%` / `Most: Consumer Cyclical −8.9%`. "Rotation" is used only when some sectors are up and some down.

**6. Beyond stocks**: `Gold ▲0.57% · Oil ▼1.58%` / `Dollar flat.` A tile with no value is left out, never shown as "n/a".

**7. Gaps**: `7 gapped up 5%+. 1 gapped down.` then the rows (sign style ▲ ▼ only, no `+7.7%` / `-5.4%` mix). On options-expiry days one line: `Options-expiry Friday: volume runs high everywhere.`

**8. Near 52-week highs**: `33 within 2% of a 52-week high, on 2× volume.` then the top 5.

**9. Longer timeframes (the strongest curiosity post)**
```
51 broke out. 16 lined up on every timeframe.
They sit within 3% of their 20-week and 52-week highs.
The names are in the assistant.
```
button: **Request access**

**10. Base rates**
```
Since 2018: 329,986 breakouts.
51% fell 2× ATR below the breakout price first.
9% climbed 6× ATR above it first.
The question is not what broke out. It is which ones, and why.
```
Door: the Playbook. (This is a true statement of the question, not a claim that the method works.)

**11. Weekly recap**: an image of the five days (breakouts per day as bars, share of stocks up as a line) and three lines: `Breakouts: 72 · 77 · 42 · 70 · 51` / `Stayed on 3+ days: ATRC · FLGT · MPC · CRWD · DVA +13` / `Sectors: Healthcare least down, Energy most.` (Fix in the builder: `BRK.B` and `BRK/B` are the same company; show it once.)

**12. Education teaser (replaces "Reading the market card")**: one concept a week, one line each, door = Playbook. Format: `ATR. How big is a normal day for this stock? Everything else is measured against it.` (Chapter references only once the e-book has chapters.)

**13. News** (stays off until the licence is checked): `Moving today` + at most four names, one headline each. Headlines that ask a price question ("Where is the top for …?") are dropped by the filter (currently one got through).

**14. Scoreboard** (stays off until you decide): as paired bars, one sentence: `20 sessions later: Breakout lists 44% higher. All stocks 50% higher.` Door: `Lists are where the work starts.` → Playbook. This turns the unflattering number into the honest reason the Playbook exists.

**15. Disclaimer notice**: one line, once a day at most: `Educational data. Not advice. Read the pinned message.`

**16. Assistant promos** (4 variants; one button, first person)
- A `The channel shows what moved. The assistant shows what moved for you.` / `Your stocks, tracked from the day you add them.`
- B `Find any stock that fits your rules.` / `/screen breakout vol>3. One line.`
- C `06:00. Your stocks. One message.`
- D `Type a ticker. Get the card.`

Footer on all: `Access on request · seats limited`. Button label: **Request access** (today: "Private assistant (invite only)").

**17. Pinned Start-here**: the first line is what the pinned banner shows, so it carries the value: `First Light · the US market, read before the open.` Then: what you get (5 lines), how to read a row (1 line), two doors (Assistant, Playbook), then the legal block and the per-post notes **unchanged** (they get a lawyer's eyes before launch).

**18. Morning message (assistant)**: the count goes first (it is the notification preview): `3 of your 8 stocks moved` then the rows, with two buttons: **Open watchlist** and **Turn off** (no need to type `/morning off`).

## 5. The image system ("every post shines")

**One frame, eight cards.** Today: market card, board, health, sector, macro, promo, avatars. Every card gets the same anatomy so the channel reads as one product:
`kicker (BREADTH) · the hook as a large title · one hero visual · footer "First Light · Before the open"`. The **hook line is the image title**, so the story survives without opening the post.
Alt-text does not exist in Telegram: the same facts always stay in the caption (accessibility, notification preview, search).

| Post | Card | Hero visual |
|---|---|---|
| Digest | Market card + hero line | Existing tiles, breadth bar, sectors; new: the day's hook as the title |
| Board | Podium | Candles (exists), add a subtle glow behind 1st |
| Health | Breadth | Two-line chart (exists) + ▲/▼ tile pairs instead of `40 · 126` |
| Sector | Sector bars | Exists; title by rule (see 5) |
| Beyond stocks | Macro tiles | 3 tiles when one is missing |
| Gaps | **New** | Dot plot: each gapper as a dot at its gap size, sized by volume × |
| Near highs | **New** | Range bars: 52-week range per stock, a marker at the close |
| Longer timeframes | **New** | Funnel 51 → 22 / 17 → 16 with **locked ticker chips** (blurred names). Honest curiosity visual: the names really are behind the door |
| Base rates | **New** | 329,986 as a dot matrix, 51% highlighted; or yearly bars |
| Weekly recap | **New** | Five-day bars + breadth line |
| Scoreboard | **New** | Paired bars: lists vs all stocks at 1 / 5 / 20 sessions |
| Assistant promos | 4 covers | One phone-frame per variant (list, tracker, morning message, `/screen`); the hero gain must be mixed (one up, one down), never a lone green +9.9% |
| Disclaimer notice | none | Plain text, on purpose |

**Tooling.** Pillow only, as today. To look modern: bundle one open-licence typeface (Inter, SIL OFL) instead of DejaVu; a soft dawn gradient glow (already used in the promo); 24 px radii; validated palette (dataviz skill validator; two-line charts keep `#4f86e8` / `#bf8514`). Generated (AI) art is for one reusable background texture at most, never inside a data card.

## 6. Measurement (CRO)

- Tag every channel door with its post: `?start=ch_aligned`, `ch_promo_b`, `ch_pin`… (Telegram allows A-Z a-z 0-9 _ -, 64 chars; `deeplink.link` already validates). Store **counts only** in `funnel_events`, no identity, so the privacy rules hold.
- Read weekly: taps → requests → approvals → first stock added → 7-day return. The post kinds that convert get more slots; the rest are cut.
- Change one thing at a time (a copy variant per week), judge on at least two weeks. With ~5 members today there is no statistical power: this phase is qualitative.

## 7. Risks and rules this proposal touches

1. **"Next big winner" cannot be used in the channel**: it is on our own banned list (`winners?`) and reads as a promise. The Playbook can be about *the process of finding candidates*. A paid e-book plus a paid group plus a subscription that ranks stocks can be treated as investment advice/marketing in Israel even with disclaimers; the lawyer gate in the plan stays, and the exact e-book promise is the first thing to show them. (I am not a lawyer.)
2. **Dropping the word "beta".** Not saying it is fine; saying something false is not. Do not write "free forever", do not show a price until the paid tier exists, keep "Access on request · seats limited" (true while approval is manual and capped). One existing test (`test_channel_content.py`, "says exactly what access is today") asserts the words "beta" and "invitation"; it encodes the old rule and will be changed together with the copy.
3. **Terse copy trades clarity for curiosity.** A newcomer sees "range 3.6× ATR" with no gloss. The popup buttons and the pinned post carry the minimum; watch the funnel counts, and if requests drop after the change, restore one line of gloss.
4. **The closed group is a new surface.** The rule "a channel carries data, promotion, news, information" needs a sibling rule for the group: education and Q&A about method, no personalised "should I buy X". A group where the owner answers that is advice.
5. **Vendor data licences** (Yahoo / Alpaca / Tiingo redistribution) are still unchecked and matter the moment money changes hands.
6. **Promise only what runs.** "Tomorrow, 06:00" and the daily posts depend on the scheduler and an always-on host (blocker B1); do not print the sign-off until five trading days have run clean.

## 8. Decisions needed

| # | Question | My recommendation |
|---|---|---|
| Q1 | Is the ladder in §1 right ($29/month = assistant; $59 one-off = Playbook + 2 weeks group)? Which is the front door from the channel? | Front door = **Request access** (free assistant), Playbook second; when paid starts, front door = Playbook ($59 is a low-risk first purchase, and it teaches the jargon the posts no longer explain) |
| Q2 | Approve the tagline "Before the open.", the rule-based first line ("Index up. 65% of stocks down.") and the voice in §2? | Yes |
| Q3 | Digest: drop the header message and put the buttons on the photo (3 messages instead of 4)? | Yes |
| Q4 | Twice-daily notices: keep, or one slot and the disclaimer once a day? | One promo slot + one short disclaimer line a day; the current 4 non-data posts a day are the biggest muting risk |
| Q5 | Start the build with the digest (caption, structure, hero line on the card), then the rest in the order of the audit? | Yes |

(Q3, Q4 and Q5 were answered by the owner's delegation on 2026-09-21: "do the minor changes as you think will be good". Q1 (the ladder) stands as the working assumption; §10-§12 hold the owner's later decisions.)

## 9. Built on DEV (2026-09-21, the "minor changes"; 1,576 tests pass, 54/54 mutants caught)

Decided by me under the owner's delegation; each is small and reversible.
- **Digest.** The photo caption is now the whole opening: the day's rule-generated hook (`digest_format.headline`: "Index up. 65% of stocks down." / "Broad rally…" / "Broad sell-off…" / "Mixed…"), the counts
  ("51 broke out · 282 within 3% of it") and the ★ names. It carries the buttons, so the separate header message is gone when the card renders (3 messages instead of 4); the text header remains the fallback.
  The same hook is printed on the market-card image under the title. The "Sent … Jerusalem" stamp is gone. Group messages: the title carries the count, list subtitles are gone, the eligibility line is one short line, and
  `NEW` is left off a list where every row has it.
- **Posts.** Health, sector, macro, gaps, near highs, longer-timeframes, base rates, recap and board rewritten in the terse voice of §4. The sector title follows the facts ("Every sector is lower than 20 sessions ago";
  the card title is "Sectors", never "rotation"). Macro leaves a missing market out (no "n/a" tile; the card is one row when only one or two tiles exist). Board counts now add up (flat stocks named). The recap shows one company once
  (BRK.B / BRK/B). Gap signs use ▲ ▼ only.
- **Funnel copy.** "beta" and "invitation" are gone from every public text (channel, pinned post, promo caption, promo image, the bot's stranger screens). Access line: "Access on request · seats limited."; the button is
  **Request access**; four new assistant variants; the disclaimer notice is one line and goes out **once a day** (slot 1); the assistant post stays in both slots. No clock time appears in public copy (the pinned post no longer says "06:00 Israel time").
- **Morning message (assistant).** The count leads ("3 of your 8 stocks changed · US close Fri 18 Sep").
- **Tests changed on purpose:** the old rule "a promo must say beta + invitation" became "a promo says *on request* and *seats limited*, and never says beta, free, or an offer price ($29 / $59)". New tests cover the hook rule,
  the caption, `NEW` suppression, the macro / sector / board / recap changes, and the once-a-day disclaimer.
- **Not built yet (needs your yes or more work):** the new image cards (gaps, near highs, funnel with locked ticker chips, base rates, recap, scoreboard), a modern typeface, the mixed-result promo hero (the promo image still
  shows one green +9.9%), the education teasers pointing to the Playbook (the five explanatory promos still explain; the Playbook does not exist yet), per-post deep-link tags, buttons on the morning message.

## 10. Send time: 00:00 Israel (owner decision, to be scheduled)

00:00 Israel is 17:00 New York / 14:00 Los Angeles: one to two hours after the 16:00 ET close all year, and the US after-work evening. Copy is already clock-free, so the move needs no copy change. What has to change:

| Item | Today | For 00:00 |
|---|---|---|
| Trading-day gate | `MARKET_SETTLE_MINUTES` default **120** (`market_calendar.py`): a session is "complete" at close + 2 h = 01:00 Israel, so a 00:00 run would be **skipped** | Set 45-60 in `.env`; tests exist for the setting |
| Data readiness | Prices are fetched at 05:00; the vendor (`DATA_PROVIDER=tiingo`) has had all night | **Unmeasured:** when is Tiingo's final daily bar (close, adjusted price, full volume) available after 16:00 ET? A provisional bar would make the 00:00 post disagree with the morning data and pollute the snapshot, the scoreboard and the tracker. Measure first: run the updater at 23:05 on a trading day and compare with the next morning's rows |
| Robustness | Fixed times | Prefer a **readiness poll** over a fixed sleep: start the price step ~23:10 (it takes ~21 min), and send when ≥ 95% of symbols carry the session's bar, at 00:00 or as soon as that holds; the existing half-loaded guard already aborts a bad run |
| Task Scheduler (DEV) | `FirstLight-1-UpdatePrices` 05:00, `-2-SendDigest` 06:00, notices 12:00 / 20:00 | ~23:10 and 00:00; retime the notice slot(s) for the US evening as well (for example 02:00-03:00 Israel = 19:00-20:00 ET) |
| Weekend | Friday session posted Saturday 06:00 | Friday session posted Saturday 00:00; the weekday rotation and the weekly recap key on the session date, so they are unaffected |

## 11. VIP staging (owner plan: the full data moves to a subscribers-only channel once there is traffic)

- **Phase 1 (now):** one free channel, the assistant on request. **Phase 2 (traction):** a VIP channel gets the full lists (15 deep), the longer-timeframe names, the extra posts; the free channel keeps the market card, the counts,
  the momentum board, health / sector / macro, and teasers with the names withheld. The free channel must never look empty: counts + the top 3 + the board.
- **Build so that the move is a switch, not a rewrite:** one table `post kind -> tier (free / vip / both)`, senders route by it; a new `vip` target in `TelegramClient.from_env` with its own lock (`VIP_SENDING_ENABLED`) and chat id; the
  structural tests that forbid other constructors and forbid channel modules from importing assistant screens extend to it. The pure builders stay unchanged; only the depth (top 3 vs 15) becomes a parameter.
- **Access:** the bot already has `bot_access` and a `tier` column. Payment options to evaluate: Telegram's paid subscription invite links (Stars, a 30-day period; check the price mapping to $29) versus an external processor.
  Data licences and the legal review gate come first (report §5).

## 12. FOMO and the "for those who hold" content (owner request) - the compliant design

The owner's strategy: FOMO about the stocks, using the momentum board as it is, plus celebration posts. This works, and it is also the riskiest content in the plan, so here is how to build it safely.

- **The actor is the scan, never "we".** The lists are formula-ranked, not chosen. "The stocks we choose" turns a data feed into stock recommendations, and "congratulations to those who hold" says we told people to hold. Voice: *the scan surfaced it;
  it has climbed since.* This is also the night-desk editor of §2 (reports, does not advise).
- **Do not address readers as holders.** No "you", no "congratulations to those who hold", no hypothetical account outcomes. Celebrate the move, not the reader: `PS · day 10 on the board · ▲38% since it first appeared.`
- **A follow-up is a cohort, not a highlight.** Every "still climbing" post shows the whole group next to the best names: `Listed 10 sessions ago: 64 stocks. Higher today: 27. Lower: 37.` then the top five by change since first appearing. The board already
  works this way (winners with the whole pool's counts beside them); this is the same pattern on a 10-20 session horizon.
- **A rule and a rhythm, fixed in advance.** One post a week on a fixed day, published even when the cohort lost. Never only when there is a winner: that is cherry-picking and would contradict the scoreboard and the base-rate post.
- **Wording:** keep the wording guard as it is (return, profit, beat, earn, winner, money stay banned); say "since it first appeared", "higher", "still up".
- **Guards to reuse:** `price_guard.py` (splits), the denominator rule of `board.py`, snapshots from `digest_stocks`. New: a post kind, a card (best five as candles, cohort counts as a bar), a pinned-post note (a test forces one per kind).
- **Lawyer first:** it is performance-adjacent marketing for a service that will be paid. Show this section with the sample before building.
- **Not built.** Say go and it becomes the next slice after the image cards.
