> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** Preserved for reference/history.
> For current architecture, see [docs/architecture/](../architecture/), [docs/operations/](../operations/),
> and [CLAUDE.md](../../CLAUDE.md). Moved here 2026-09-25 as part of the repository documentation
> consolidation — content below is unmodified from before the move except for this banner.

# CHANNEL_CONTENT_MILESTONES.md — execution tracker for the channel-content plan

> Source of truth for *what* and *why*: [CHANNEL_CONTENT_REPORT_2026-09-21.md](CHANNEL_CONTENT_REPORT_2026-09-21.md) (item ids P1-P10, D1-D18, B1-B5, Q1-Q8 refer to it).
> This file tracks **status**. Update it at the end of every milestone (status, what changed, what was verified, what is left).
> Binding rules (unchanged): everything is built and tested on the **DEV** channel / owner's private chat; production stays locked
> (`PROD_SENDING_ENABLED=0`); a channel carries only data / promotion / news / information, never assistant screens; facts only, no advice words
> (the wording guard scans every public string); a number that cannot be trusted is `n/a`, never invented; every new module ships with tests.
> The user's instruction (2026-09-21): "save that plan and execute everything by the milestones". Items that need a decision or something outside the
> repo are listed under **Blocked** with the reason — they are not silently skipped.

## Status legend
`[ ]` not started · `[~]` in progress · `[x]` done and verified · `[!]` blocked (needs the user / an outside decision)

## M0 — Foundation
- [x] M0.1 Plan saved (this file); previous session's work committed locally (B5) — `.env`, `reports/`, `logs/`, `backups/`, `*.joblib` excluded
- [x] M0.2 Daily snapshot without a send (B3): `-UpdateOnly` also runs `send_daily_digest.py --snapshot-only`; `backfill_snapshots.py` rebuilt **59 past sessions** (25 Jun – 17 Sep)
- [x] M0.3 Breakout counts reconciled (B2) — **finding:** the digest's 51 are a strict subset of the screener's 62. 8 of the 11 extras fail the $1M/day liquidity floor (illiquid preferreds such as AFGB/AFGC/AFGD/AFGE), 3 (CYPH, SECZ, USDE) fail the digest's data-integrity rules (short history / discontinuity). The screener has neither guard and needs `technical_indicators` + fundamentals joined (1,832 stocks vs 2,915). **Decision:** the digest is the reference; the channel and the assistant only ever show the digest's counts. The dashboard should adopt the same guards before it is hosted (D16).

## M1 — Free channel posts I (market context)
- [x] M1.1 `market_stats.py` — wide date x symbol maths; found + fixed on real data: a stray partial date emptied the 200-day statistic (now dropped as "not a session")
- [x] M1.2 P1 Market-health post (PNG card + text). 200-day breadth and new-highs/lows verified against the digest's own universe (the near-breakout count, 282, equals the digest's; breakouts differ by one stock with a missing bar, see the data notes)
- [x] M1.3 P2 Sector-rotation card (median 20-session change; median, so one outlier cannot move a sector)
- [x] M1.4 P3 Macro strip (gold, crude, dollar index, Bitcoin). Bitcoin shows `n/a` for 18 Sep: the stored series has no row for that date (real data gap, not filled)
- [x] M1.5 D5 "small cap" tag (< $2B market value, no tag when unknown) on digest rows + a legend line

## M2 — Free channel posts II
- [x] M2.1 P4 Gaps and volume (options-expiry caveat on third Fridays)
- [x] M2.2 P5 Near 52-week highs on heavy volume
- [x] M2.3 P6 Aligned-timeframes teaser (counts only; a stock missing yesterday's bar is not dropped from the counts — regression test)
- [x] M2.4 P9 Base-rate card (329,986 long breakouts, exact wording from the label definition)
- [x] M2.5 P8 Weekly recap (Sunday; exact digest counts from the stored snapshots)
- [x] M2.6 P10 Seven rotating education / promotion posts
- [x] M2.7 `send_channel_posts.py` — ≤ 1 extra silent post per session, weekday rotation with fallbacks, its own gate keys (`posts:<target>`, `recap:<target>`), dev by default, production lock, `--all --send` only to the owner's private chat. All eight kinds accepted by real Telegram in the owner's private chat

## M3 — News post
- [x] M3.1 P7 `channel_news.py` + `post_news` — one Alpaca call per mover, headline + source + link only, advice-like headlines dropped (ratings, price targets, "buy" calls). **Off in the rotation until `CHANNEL_NEWS_ENABLED=1`; the news licence is unchecked.** Live dry run: 4 of 7 movers had fresh headlines

## M4 — Assistant, members first (free beta; tier gating comes with payments)
- [x] M4.1 D9 `/full` + "All N" button on Today's lists (three orderings, paged, back-navigation context `f<tab><order><page>`) and `/scan` (CSV of the whole scan, ~2,866 rows)
- [x] M4.2 D10 `/aligned` (names, which longer-timeframe highs each is near, short-history counts). Same numbers as the channel teaser once the missing-bar fix is applied
- [x] M4.3 D13 `/history SYM` + "Past breakouts" button on the stock card (real data: MSTR 165 breakouts, 160 followed for 20 sessions)
- [x] M4.4 D15 `/week` (5-session change of the caller's own lists, split-shaped windows reported as adjusted; isolation test)

## M5 — Assistant, retention and trust
- [x] M5.1 D12 `scoreboard.py` + `post_scoreboard` — computed on the 59 backfilled sessions. **Result on 25 Jun – 18 Sep: Breakout-list stocks were higher after 1 / 5 / 20 sessions in 41% / 40% / 44% of stock-days (median −0.6% / −1.5% / −1.3%) against 48% / 48% / 49% for all liquid stocks.** It is unflattering and consistent with our earlier studies (no proven edge). **Off in the rotation until `CHANNEL_SCOREBOARD_ENABLED=1` — publishing it is the owner's decision.** Uses a new shared split guard (`price_guard.py`), found necessary because the ML dataset's 3x rule misses 2-for-1 splits
- [x] M5.2 D8 `/morning on|off` + `morning.py` + sending loop in `run_bot.py`; table `bot_user_settings` (migration applied); opt-in, ≤ 1 message per session, only when one of the member's own stocks changed, stale scans never pushed, blocked/revoked members switched off, removed by `/deleteme` and `/morning off`. **The first message comes with the next scan after opting in**
- [x] M5.3 D14 `/screen` — strict grammar (`breakout|near|all|scan`, `price|day|vol|range|below` with `> < >= <=`, `sort=`, `top=`); nothing typed is ever evaluated; an unknown value never matches

## M6 — Operations
- [x] M6.1 B1 Two Task Scheduler jobs registered, **`-To dev` only**: `FirstLight-1-UpdatePrices` (05:00) and `FirstLight-2-SendDigest` (06:00), next run Tue 22 Sep. A manual trigger proved the launch path (result 0, gate skipped correctly). `run_first_light_morning.ps1` now sends the extra post after the digest (`-NoExtraPost` to skip) and the recap on Sunday. Bot restarted on the new code (PID changes on every restart)
- [x] M6.2 Docs: CLAUDE.md, HANDOFF.md, RUNBOOK, the report, this file, memory
- [x] M6.3 Final commit (local, no push)

## M7 — Momentum board + deeper lists (2026-09-21, owner request)
- [x] M7.1 Lists 15 deep, 5 open + ranks 6-15 in an expandable quote; messages packed by Telegram's parsed length (real Telegram accepted raw 5,332 / 6,086 chars in the owner's private chat)
- [x] M7.2 ★ = top 5 of 2+ lists (`alerts/star.py`), shared by channel and assistant so deeper lists do not dilute it
- [x] M7.3 Momentum board (`board.py`): podium of the previous-5-session list stocks that closed higher, ranked by today's %, whole-pool counts beside it, split guard, leader's 10-session chart; kind `board`, sent daily before the rotating post (`CHANNEL_BOARD_ENABLED=0` = off). Accepted by real Telegram in the owner's private chat
- [ ] M7.4 **Owner decisions:** keep 15-deep lists? board as a third daily message or replace the rotating post? pin-and-replace the latest board (edit-in-place)? intraday updates (needs an intraday feed + hosting)?
- [ ] M7.5 Watch the first scheduled run (Tue 22 Sep 06:00): digest groups + board must arrive; check `logs/first_light_morning_<date>.log`. Restart the bot for the new star rule

## M8 — Candles, one-line disclaimer, pinned info post, twice-daily notices (2026-09-21, owner request)
- [x] M8.1 Momentum board chart = candlesticks (leader + mini candles for 2nd / 3rd); inconsistent / missing bars are gaps
- [x] M8.2 One-line `* Not investment advice.` on every channel post and image; the full plain-language disclaimer moved to the pinned Start-here post (rewritten, less technical)
- [x] M8.3 `send_channel_notices.py`: disclaimer notice + assistant commercial post (4 variants), twice a day; DEV tasks `FirstLight-3-Notices-Midday` 12:00 / `-4-Notices-Evening` 20:00; posted once to the dev channel for review
- [ ] M8.4 **Owner decisions:** are 4 notice posts a day too many (a muted channel is the risk)? weekends too? delete-the-previous-notice so only the latest stays? Hebrew? Then remove the old pinned post by hand in the dev channel

## M9 — Disclaimers moved to the pinned post (2026-09-21, owner request)
- [x] M9.1 No per-post / per-image disclaimer or caveat text; the general disclaimer + one note per service live in the pinned Start-here post (`SERVICE_NOTES`, `NOTE_FOR_KIND`, coverage test)
- [x] M9.2 Digest header keeps only a pointer to the pinned message; the twice-daily notice stays the one repeated disclaimer
- [ ] M9.3 **Owner decision / legal:** is a post without any disclaimer of its own acceptable once the channel is public (screenshots and forwards lose the pinned message)? Review with the lawyer before launch

## Blocked — needs the user or something outside the repo
| Item | Why it is blocked | What unblocks it |
|---|---|---|
| Licence check (Yahoo / Alpaca / Tiingo / news) | Needs the vendors' terms read and answered in writing | The user (or a lawyer) confirms redistribution / commercial use. Then set `CHANNEL_NEWS_ENABLED=1` |
| Publishing the scoreboard | The numbers are unflattering; a business decision | The user; then set `CHANNEL_SCOREBOARD_ENABLED=1` |
| Legal + privacy review (advice regulation in Israel, stored portfolio data, the new morning switch) | Not something code can settle | A lawyer; then update `/privacy` |
| D6 Earnings calendar | No earnings table; the source (free API vs paid) is decision Q5 | The user picks a source |
| D7 Hebrew | Decision Q6 (after English is proven) | The user |
| D16 Dashboard web access, D17 Intraday alerts | Need hosting / a feed (and the dashboard needs the digest's guards, see M0.3) | Hosting decision, feed decision |
| D18 Payments | Depends on the legal review | Legal review first |
| B4 Human QA + a friend session | Needs a person on Telegram (RUNBOOK §2, §8), including the new commands | The user |
| Production launch | Locked on purpose | The user's explicit go-ahead |
| Turning "Allow Groups" off, uploading the avatars | Only the bot owner can, in BotFather | The user |

## Known data notes found on the way (not fixed, not caused by this work)
- The universe contains both `BRK.B` and `BRK/B` (the same company twice), so the recap and the lists can show it twice.
- `market_index_prices` has no Bitcoin row for 2026-09-18 (rows exist for the 17th and 19th–21st); the macro card shows `n/a` for it.
- `HSHP` has missing daily bars (20 Aug, 17 Sep); it is handled (a missing prior bar blanks 1-day figures only) but it is a hole in the price data.
- The sector Trend history before today uses today's sector tags (approximate).

## Log
(newest first; one line per finished item with the evidence)
- 2026-09-21 M7 — momentum board + 15-deep collapsible lists + star rule. Real 18 Sep data: 81 stocks in the pool, 25 closed higher, 53 lower, 8 at the top of their day's range; podium PS ▲13.7%, COIN ▲11.7%, BNC ▲10.3%. 1,518 tests, 8/8 board mutants caught, 47 patterns present. Earlier snapshots hold 5-deep lists (the pool of the first days is smaller); the scoreboard (off) would mix depths.
- 2026-09-21 M6 — tasks registered and triggered once (exit 0); extra post + Sunday recap wired into the morning script (structural test); bot restarted; all docs updated. 1,494 tests pass (alerts + ML), 39/39 mutation patterns present, all 16 tracker mutants caught after moving the split rule.
- 2026-09-21 M5.3 — `/screen`: grammar rejects code-like input, an unknown value never matches; 6 tests.
- 2026-09-21 M5.2 — `bot_user_settings` migration applied; real-Postgres round trip of the five `dm_*` queries (test user removed afterwards); 12 tests incl. blocked / revoked / transient failure / stale scan / isolation.
- 2026-09-21 M5.1 — scoreboard on real snapshots; the split guard moved to `price_guard.py` and shared with `performance.py`; the mutant "common split factors not recognised" re-pointed and caught.
- 2026-09-21 M4 — `insights.py` + store queries verified on real Postgres (51 breakouts, 282 near, 2,866 scan rows, MSTR history 165/160); a test found that a stock missing yesterday's bar was silently dropped from the aligned counts (fixed, regression test).
- 2026-09-21 M3 — news post dry run against Alpaca.
- 2026-09-21 M2/M1 — eight post kinds; all accepted by real Telegram in the owner's private chat; the two-line chart colours validated (`#4f86e8` / `#bf8514`, dark surface: lightness band, chroma, colour-blind dE 27.8, normal dE 29.3, contrast ≥ 3:1).
- 2026-09-21 M0 — commit `1f9fc35` (checkpoint), `4d4df43` (M0–M5.1); B2 diagnosed; backfill of 59 sessions.
