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
- [ ] M0.1 Save the plan (this file) and commit the previous session's work (B5) — local commit only, no push; `.env`, `reports/`, `logs/`, `backups/`, `*.joblib` excluded
- [ ] M0.2 Daily snapshot without a send (B3): the update-only run also saves the bot snapshot; a backfill tool for past sessions
- [ ] M0.3 Reconcile the breakout counts (B2): find why the dashboard screener says 62 of 1,832 and the digest 51 of 2,915; document; make the channel/bot the single source

## M1 — Free channel posts I (market context)
- [ ] M1.1 `market_stats.py`: vectorised market-health facts from the universe history (breadth, % above 50/200-day, new 52-week highs/lows, breakout counts from the digest)
- [ ] M1.2 P1 Market-health post (image + text) — validated against an independent recomputation
- [ ] M1.3 P2 Sector-rotation chart (20-session median change per sector)
- [ ] M1.4 P3 Macro strip (gold, crude, dollar index, Bitcoin)
- [ ] M1.5 D5 Size tag on list rows (small cap < $2B) + legend line

## M2 — Free channel posts II
- [ ] M2.1 P4 Gaps and volume (with the options-expiry caveat on third Fridays)
- [ ] M2.2 P5 Near 52-week high with volume
- [ ] M2.3 P6 Aligned-timeframes teaser (counts only)
- [ ] M2.4 P9 Base-rate card (from `ml_breakout_dataset_v2`)
- [ ] M2.5 P8 Weekly recap
- [ ] M2.6 P10 Promotion formats (rotating educational / promo posts)
- [ ] M2.7 `send_channel_posts.py`: the rhythm (≤ 1 extra post per session, rotation, state, dev by default, prod locked)

## M3 — News post
- [ ] M3.1 P7 Market-news post on the day's movers (headline + link + source only; separate from the assistant's news service). **Licence of the news feed is unchecked — dev only, not in the schedule until the user confirms**

## M4 — Assistant, members first (free beta; tier gating comes with payments)
- [ ] M4.1 D9 Full lists (all members of a group, three orderings, paged) + CSV of the whole scan
- [ ] M4.2 D10 Aligned list (names, which levels each is near)
- [ ] M4.3 D13 Per-stock history (past breakouts of this stock and what followed)
- [ ] M4.4 D15 Portfolio / watchlist weekly summary (facts only)

## M5 — Assistant, retention and trust
- [ ] M5.1 D12 List scoreboard (what happened after each day's lists at 1 / 5 / 20 sessions, against the universe) + a monthly channel note
- [ ] M5.2 D8 Morning message for opted-in members (only when something changed) + `bot_user_settings`
- [ ] M5.3 D14 Custom screens (strict filter grammar over the snapshot)

## M6 — Operations
- [ ] M6.1 B1 Register the two Task Scheduler jobs (`-To dev` only) and add the channel-posts step
- [ ] M6.2 Docs: CLAUDE.md, HANDOFF.md, RUNBOOK, this file; final full test run + mutation checks
- [ ] M6.3 Final commit (local)

## Blocked — needs the user or something outside the repo
| Item | Why it is blocked | What unblocks it |
|---|---|---|
| Licence check (Yahoo / Alpaca / Tiingo / news) | Needs the vendors' terms read and answered in writing | The user (or a lawyer) confirms redistribution / commercial use |
| Legal + privacy review (advice regulation in Israel, stored portfolio data) | Not something code can settle | A lawyer; then update `/privacy` |
| D6 Earnings calendar | No earnings table; the source (free API vs paid) is a decision (Q5) | The user picks a source |
| D7 Hebrew | Decision Q6 (after English is proven) | The user |
| D16 Dashboard web access, D17 Intraday alerts | Need hosting / a feed | Hosting decision, feed decision |
| D18 Payments | Depends on the legal review | Legal review first |
| B4 Human QA + a friend session | Needs a person on Telegram | The user (RUNBOOK §2, §8) |
| Production launch | Locked on purpose | The user's explicit go-ahead |

## Log
(newest first; one line per finished item with the evidence)
