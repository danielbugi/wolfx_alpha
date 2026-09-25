> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** Preserved for reference/history.
> For current architecture, see [docs/architecture/](../architecture/), [docs/operations/](../operations/),
> and [CLAUDE.md](../../CLAUDE.md). Moved here 2026-09-25 as part of the repository documentation
> consolidation — content below is unmodified from before the move except for this banner.

# CHANNEL_CONTENT_REPORT_2026-09-21.md — what we have, what is free, what is paid, what to post and build next

> Every number below was read from the live database on 2026-09-21 (latest stored session **Fri 18 Sep**) unless marked *ad hoc*.
> Ad-hoc numbers were computed for this report with throw-away scripts (liquid = 20-day average dollar volume ≥ $5M, 2,456 stocks) and are
> **not** the production digest's numbers (its universe is ≥ $1M, 2,915 stocks). Nothing was sent to any channel while writing this.
> Binding rules that shape every proposal: a channel carries only data / promotion / news / information; assistant screens stay in the private chat;
> facts only, no advice wording; no fabricated number; production stays locked (see [HANDOFF.md](HANDOFF.md) §1).

> **Update (later the same day):** this report was executed - see [CHANNEL_CONTENT_MILESTONES.md](CHANNEL_CONTENT_MILESTONES.md) for what was built, the evidence, and what is blocked. Two numbers in this report were ad hoc and are superseded by the built posts: market-health figures use the digest's own universe (>= $1M, 2,915 stocks) instead of the ad hoc $5M subset, and the sector post uses the median 20-session change of each sector's stocks (the ad hoc figure of Technology +0.5% used a different method, daily equal-weight averages compounded; by the median, all 11 sectors were lower on 18 Sep). The 62-vs-51 breakout gap (section 4.3, B2) is explained: the digest's 51 are a strict subset of the screener's 62 (no liquidity floor or integrity guard in the screener).

## 0. Bottom line

1. **We already own more data than the channel shows.** The channel posts one market card and three lists. The database also holds 10 years of prices, weekly/monthly
   channels, quarterly financials, daily valuation/sector data, index/macro history, sector history and 330k historical long breakouts with outcomes.
   Roughly **eight new free posts** can be built from data already stored, with no new vendor.
2. **Free = what happened (market-wide, public-style facts). Paid = what it means for *me* (my stocks, my alerts, deeper lists, history, export).** Do not sell
   "picks" or performance: our own studies found no proven edge, and the ML score is switched off.
3. **The biggest gap is not content, it is plumbing.** Nothing runs on a schedule, the stored data is three days old, the dashboard and the digest disagree on
   how many breakouts there are (62 vs 51), and no human has used the bot end to end. A paid tier built on that would break on day one.
4. **Paid needs three checks we have not done:** data-vendor licences (Yahoo/Alpaca/Tiingo terms for redistribution), financial-advice regulation for a
   paid service (you are in Israel; get a lawyer), and the privacy review already flagged as the launch gate.
5. **Recommended next step:** add the two cheapest, highest-value free posts (market health + sector rotation), finish the approved news post, register the scheduler on
   dev, and let it run five trading days before touching production.

## 1. What we already have

### 1.1 Data in the database (verified)

| Data | Coverage | Freshness | Used today by | Trust |
|---|---|---|---|---|
| **Daily prices** `stock_prices` | 6.43M rows · 3,114 symbols · 2016-09-20 → 2026-09-18 · 3,070 priced on the last session | last session Fri 18 Sep | digest, bot, chart, ML dataset | Good. Vendor-adjusted series; 1,039 discontinuity days registered (121 symbols) and skipped |
| **Weekly / monthly channels** | weekly 252,574 rows / 3,033 symbols (week ending 18 Sep) · monthly 53,356 rows / 2,961 symbols | current | dashboard screener only — **not in the channel** | Good |
| **Daily indicators** `technical_indicators` | 6.38M rows · 3,086 symbols | current | dashboard | ⚠ ~24% of 2023-25 rows disagree with current prices (stale price basis). The digest does not use this table; it recomputes from prices |
| **Daily fundamentals** | 511k rows · 3,112 symbols · 2023-07 → 2026-09-21. Sector on 3,089 · market cap on 3,088 · P/E on 2,267 (73%) | current (the "Unknown sector" gap in CLAUDE.md is closed: 8 stocks lack a sector on the card) | dashboard, market card sectors | Vendor-dependent (yfinance); beta / dividend / shares often missing |
| **Quarterly fundamentals** | 15,885 rows · 3,000 symbols · revenue growth YoY on 2,745 of 3,000 | latest label 2026-08-31 | dashboard "deep value" / turnaround | Piotroski score exists **only for Dow 30** (vendor limit) |
| **Index / macro** `market_index_prices` | S&P 500, Nasdaq, Russell 2000, Dow, VIX, 10-yr yield, Gold, Crude, Dollar index, Bitcoin — about 1 year each | to 18 Sep (gold/crude/DXY/BTC to 21 Sep) | market card shows 6 of the 10 | Good |
| **Sector history** | 984 rows · 82 sessions (2026-05-22 → 09-18) | current | dashboard trend chart, card | ⚠ backfilled with *today's* sector tags, so old days are approximate |
| **Daily scan snapshot** `digest_runs/digest_stocks` | **one** session stored (18 Sep): ~2,900 stocks with close, 1-day %, volume ×, range ×ATR, ATR, distance below the 20-day high, group, yesterday's group | only when a real send happens | bot | Good, but no history yet, so no scoreboard |
| **Historical breakouts** `ml_breakout_dataset_v2` | 594,888 breakouts 2018 → 2026; 329,986 long with a matured outcome | rebuild is manual | ML only | Good (survivor-biased: today's constituents) |
| **News** `news_items` | 30 rows, 2 symbols | on-demand cache: the bot fetches when someone opens a stock | bot stock card | Provider licence for republishing **not checked** |
| **ML** `ml_models`, `ml_predictions` | 1 model row, 1,537 predictions | — | nothing | **No model passes the gate. ML score is unavailable. Never advertise "AI picks".** |
| **Usage** `bot_users`, `bot_tracked`, `alerts` | 2 users, 0 tracked stocks, 0 alerts | — | — | Nobody has used it yet |

### 1.2 What is built and can already be shown

| Asset | Where it lives | Free-channel safe? |
|---|---|---|
| Daily **market card** (6 tiles + sparklines, stocks up vs down bar, sector bars, "Midnight Dawn" palette) | channel | yes (this is the current lead item) |
| Daily **lists** — Breakout (51 on 18 Sep) and Near breakout (282), each ranked by gain, ATR and volume, ★ = in 2+ lists, NEW / "was near yesterday" | channel | yes |
| **Start-here** pinned post + one **promo** image + bot avatar / channel logo | channel | yes |
| Stock card: facts, list membership, news, candle chart, ATR risk framework (`/levels`) | private chat only | no (never in a channel) |
| Watchlist + Portfolio with change **since the day added**, split guard, CSV export, delete-me | private chat only | no |
| Invite-only access, request-access flow, `/funnel` counts | bot | — |
| Dashboard (FastAPI + Next.js): screener with weekly/monthly alignment score, quality grades, deep-value / turnaround scan, sector heatmap and trend, strategy page | local only, not hosted | not as-is |

### 1.3 What the data honestly supports (and does not)

- **Base rate, our own dataset** (long 20-day-high breakouts, 2018 → 2026, n = 329,986, the dashboard's 2×ATR risk level with +2/+4/+6 ATR reference levels):
  51% hit the risk level, 45% ended above zero R, median −0.33 R, mean +0.04 R; by year the mean swings from −0.12 R (2022) to +0.15 R (2023).
  Read: **most breakouts fail, a minority run, and the result depends on the market regime.** That is a strong *educational* fact, not a selling point.
- **No predictive edge is proven** — ranking by gain / ATR / volume produced a positive 60-day drift in one study, but about two thirds of alerts lost at 20 days and a wide-trail exit, not the selection, drove the mean. All copy stays descriptive.
- **The lists lean to small, cheap stocks.** On 18 Sep the top gainers were GEMI $5.81, FWDI $7.70, ABTC $10.10, MARA $13.24. The $5M/day floor still admits them.
  We hold market cap for 3,088 symbols, so a market-cap tag is cheap and would make the lists more useful (see D5 below).

## 2. Free channel vs paid — the split

**Principles**
1. **Free explains the market; paid personalises and saves time.** Anything computed for *everyone* from public prices can be free. Anything that depends on *my* stocks, *my* price or *my* attention (alerts, portfolio, screens) is paid.
2. **Free teases with counts, paid unlocks names and depth.** Example: free says "14 of 42 breakouts also sit at weekly and monthly highs"; paid lists the 14.
3. **Sell tools and time, never predictions.** No "top pick", no win-rate claims, no ML score. Base rates and scoreboards are published *as facts*, including the unflattering ones — that is what builds trust.
4. **The channel never carries assistant screens** (rule stays). Any paid *channel* would carry deeper **data**, not personal tools; personal tools stay in the private chat.
5. **Licensing decides the ceiling of paid** (see §5). Derived facts (ranks, percentages, counts) are safer to sell than raw redistributed prices or news text.

| Content / feature | Free channel | Paid | Why |
|---|:-:|:-:|---|
| Market card, breakout / near-breakout lists (top 5 per list) | ✔ | | The shop window; proves value daily |
| Full lists (top 15–25, all 51 breakouts) and CSV of the whole scan (~2,900 stocks) | | ✔ | Depth, same data |
| Market-health post (breadth, % above 50/200-day, new highs/lows, breakout count) | ✔ | | Public-style context, high shareability |
| Sector rotation chart (20 sessions) | ✔ | | Same |
| Multi-timeframe aligned breakouts | count only | names + grades | Uses weekly/monthly tables already built |
| Market-news post on the movers | ✔ | | Traffic driver; needs a licence check |
| Personal news per watched stock, chart, ATR levels | | ✔ (already the assistant) | Personal |
| Watchlist / Portfolio tracking since the day added | | ✔ (built) | Personal, stored data |
| Morning DM "what changed on my list" and watchlist alerts | | ✔ | Attention saved; the retention loop |
| Fundamental screens (quality grade, turnaround, growth) | teaser | ✔ | Needs vendor-licence check |
| Historical context per stock ("past breakouts of this stock and what followed") | | ✔ | Built on the 330k-breakout dataset |
| List scoreboard (what happened to each day's list after 1 / 5 / 20 days) | summary monthly | full history | Trust-builder; see risk in §5 |
| Education series, base-rate cards, weekly recap | ✔ | | Promotion + authority |
| Earnings calendar | ✔ (week ahead) | ✔ (personal) | Needs a new data source |
| Intraday alerts | | ✔ | Needs a feed + hosting |
| Dashboard web access | | ✔ | Needs hosting + login |
| ML scores | — | — | **Unavailable; do not promise** |

## 3. What else we can post now — from data already stored (no new vendor)

Sample figures are *ad hoc* for Fri 18 Sep.

| # | Post | What it says (sample) | Data | Effort |
|---|---|---|---|---|
| **P1** | **Market health** (text + small chart) | "S&P 500 ▲0.17% but only 33% of 2,456 liquid stocks rose. Above their 50-day average: 31% (49% on 4 Sep). Above 200-day: 51%. New 52-week highs 36 · lows 102. Breakouts (20-day high): 42." — the index-vs-breadth divergence is the most interesting thing in the data that day and the card does not say it in words | `stock_prices` | S |
| **P2** | **Sector rotation** (20-session bars, dataviz skill) | Technology +0.5% vs Consumer Cyclical −8.7%, Real Estate −6.3% (equal-weight, compounded from daily averages; note the current-sector-tag caveat) | `sector_performance_daily` | S |
| **P3** | **Second macro strip** | Gold, crude, dollar index, Bitcoin (weekend-alive) next to the six tiles already shown | `market_index_prices` | S |
| **P4** | **Gaps and volume** ("what moved and on what") | 8 stocks gapped up ≥ 5% (largest USDE +23.7%, on 16.5× volume), 1 gapped down ≥ 5%; 687 traded ≥ 3× normal volume — show the top 5 by dollar volume, not the 687. Caveat for the copy: 18 Sep was the third Friday of the month (quarterly options expiry), which inflates volume across the board — a "volume ×" list should say so on such days | `stock_prices` | S |
| **P5** | **Near 52-week high with volume** | HALO $112.45 ▲2.5%, vol 6.2×, within 2% of its 52-week high; HGTY, TXNM, UNM, TH | `stock_prices` | S |
| **P6** | **Aligned-timeframes teaser** | "42 daily breakouts today; 18 also within 3% of their 20-week high, 14 also within 3% of their 12-month high." Names for members | weekly + monthly tables | S |
| **P7** | **Market news on the movers** (already asked for; pending your yes) | 1–2 headlines, source and link for the day's top movers; headline + link only | Alpaca news | M |
| **P8** | **Weekly recap** (Saturday) | Breakout count by day, the week's sector leaders/laggards, names that stayed on the lists all week, one educational line | prices + snapshot | S–M |
| **P9** | **Base-rate card** (education, monthly) | "Since 2018, about half of 20-day breakouts hit a 2×ATR risk level before running. Results vary by year." with the yearly table | `ml_breakout_dataset_v2` | S |
| **P10** | **Seven promo formats** (already designed, FUNNEL_PLAN §8) | track since the day you add · news + chart per stock · what ATR means · how to read ★ and vol × · the card explained · free beta · weekly recap | copy | S each |

Rhythm proposal (to confirm): **06:00 digest** every trading day → **one** extra post that day, rotating P1 / P2 / P4 / P5 / P7 / P10 (never more than two posts a day so the channel is not muted) → **Saturday** recap (P8) → **monthly** base-rate / scoreboard note (P9).

## 4. What to develop next

Effort: **S** = one session, **M** = 2–4 sessions, **L** = a week or more. "Dep." = what must exist first.

### 4.1 Free channel

| # | Item | Effort | Dep. | Note |
|---|---|:-:|---|---|
| D1 | P1 market-health post + P2 sector-rotation chart | S | none | Best value for cost; do first |
| D2 | P4 gaps/volume, P5 near-highs, P6 teaser, P3 macro strip | S each | none | Same computations already used in this report |
| D3 | P7 market-news post | M | your yes; **news licence check**; batch fetch (today the cache fills only when someone opens a stock) | Headline + link only |
| D4 | P8 weekly recap + P9 base-rate card | S–M | none | Base rates need the survivor-bias caveat in the text |
| D5 | **Market-cap / price tag on list rows** ("small cap") | S | daily fundamentals | Keeps micro-cap noise from dominating the top gainers |
| D6 | Earnings calendar (week ahead, liquid stocks) | M | **no earnings table exists**; only on-demand yfinance lookups (fragile). Needs a source decision (a free calendar API vs paid) | Earnings is the #1 reason a breakout moves; high value |
| D7 | Hebrew posts | M | your call (open decision 6 in HANDOFF) | Doubles the audience for an Israeli morning routine |

### 4.2 Paid / private assistant

| # | Item | Effort | Dep. | Note |
|---|---|:-:|---|---|
| D8 | Morning DM + watchlist alerts (breakout of a watched stock, > 2×ATR move, news) | M | scheduler, hosting, `bot_user_settings` (plan 7.2) | The retention loop; needs the daily job to run reliably first |
| D9 | Full lists + CSV of the whole daily scan | S | none | Cheapest "paid" feature: same data, more rows |
| D10 | Aligned-timeframes list with grades | S | reconcile the dashboard vs digest logic | See the 62 vs 51 discrepancy below |
| D11 | Fundamental screens (quality, turnaround, growth) | M | vendor licence for fundamentals; validate the turnaround logic (a rough count in this session gave ~600 candidates and was **not** validated) | Turnaround + breakout is the strongest thematic story we own |
| D12 | **List scoreboard** (history of each day's list at 1 / 5 / 20 days) | M | store a snapshot every day; backfill 10 years from prices | Highest trust value, highest wording risk (facts only, with the losses) |
| D13 | Per-stock history ("last N breakouts of this stock, what followed") | M | dataset rebuild automated | Uses the 330k-row dataset |
| D14 | Custom screens (user picks price / volume / sector / ATR% filters) | M–L | tested SQL layer, per-user limits | Strong "paid" hook |
| D15 | Portfolio weekly summary (facts only) | S–M | bot in daily use | Uses tables already built |
| D16 | Dashboard web access with login | L | hosting, auth, single source of truth | Dashboard exists but is local-only |
| D17 | Intraday alerts | L | an intraday feed (Alpaca free is IEX-only), hosting | Real "timing" value, real cost |
| D18 | Payments (Telegram Stars; a `tier` column exists) | M | legal review | Last, not first |

### 4.3 Plumbing that blocks everything above

| # | Item | Why it blocks | Effort |
|---|---|---|:-:|
| B1 | **Scheduler + always-on host.** No Task Scheduler entry is registered; the bot and the 06:00 job stop when the PC sleeps; data is 3 days old | Every daily post and every alert depends on it | M |
| B2 | **One source of truth for signals.** The dashboard screener found **62** breakouts among 1,832 stocks; the digest found **51** among 2,915 (different universe and rules; cause still unconfirmed). Members would see both | A paid product cannot show two counts | S–M |
| B3 | **Snapshot every day** (only one session is stored) | Needed for the scoreboard, "was near yesterday" history and any recap | S |
| B4 | **Human QA** (RUNBOOK §2, §8) and a friend session; popup buttons inside a channel are still untested | Nobody has used the bot yet | S |
| B5 | **Commit the work** (about 85 uncommitted entries; never add `.env`, `reports/`, `logs/`, `backups/`) | One disk failure loses the whole build | S |

## 5. Risks and things not to claim

- **Licences (unchecked — verify before charging anyone).** Yahoo's unofficial API is for personal use; Alpaca's free market data and news, and Tiingo's Free/Power plans, are, as I understand their terms, not licensed for redistribution or for a commercial service. I did not open their terms in this session. Digests of derived facts are lower risk than republishing prices or news text; a paid tier should not launch until this is confirmed in writing.
- **Regulation.** A *paid* service that ranks stocks daily may be treated as investment advice or marketing in Israel (and elsewhere), even with disclaimers, especially if it becomes personalised (alerts on *my* stocks, levels for *my* price). This is the reason the plan already lists a lawyer as a hard gate. I am not a lawyer; treat this as a flag, not a conclusion.
- **Scoreboard cuts both ways.** Publishing "what happened to yesterday's list" is honest and trust-building, and our own study says about two thirds of such lists lose at 20 days. Publish it with the losses, or not at all. Never cherry-pick a single winner.
- **Survivorship and regime.** Every historical statistic here uses today's constituents and a mostly rising market; say so in the copy.
- **Freshness.** All numbers describe the 18 Sep close. If the daily job is not automated, a post that says "today" is wrong.
- **Data quality reminders.** Sector history is approximate before today; the old indicator tables are on a stale price basis; fundamentals beta/dividend are often missing; Piotroski is Dow-30 only.
- **What I did *not* do.** No message was sent anywhere; the production lock is untouched. Running the digest in preview mode with `--image` re-saved `reports/first_light/first_light_2026-09-18.png` (git-ignored, regenerated from the same data).

## 6. Recommended order (next two to three weeks)

1. **Now (no decisions needed):** D1 (market health + sector rotation), D5 (small-cap tag), B3 (daily snapshot), B5 (commit) — all tested on dev only.
2. **Needs your answers:** approve D3 (news post) and confirm the rhythm in §3; decide D6 (earnings source) and D7 (Hebrew).
3. **Then:** B1 register both Task Scheduler jobs `-To dev`, let five trading days run untouched; B4 human QA and one friend session; B2 reconcile the counts.
4. **Before any strangers or any payment:** licence check (§5), legal/privacy review, hosting decision.
5. **Paid tier first cut (after the above):** D9 (full lists + CSV), D8 (morning DM + alerts), D10 (aligned list). Add D12 (scoreboard) once 30+ daily snapshots exist.
6. **Launch** per FUNNEL_PLAN §8 on a **new** production channel; flip `PROD_SENDING_ENABLED` only with your explicit go-ahead.

## 7. Decisions I need from you

| # | Question | My recommendation |
|---|---|---|
| Q1 | Approve the daily **market-news post** (D3)? | Yes, after the news licence check |
| Q2 | Approve **P1 + P2** now as extra free posts on dev? | Yes |
| Q3 | Posting rhythm: digest + ≤ 1 extra post/day + Saturday recap + monthly base-rate note? | Yes |
| Q4 | What do you want to be **paid** first: personal alerts (D8), full lists + CSV (D9), or fundamentals screens (D11)? | D9 then D8: cheapest and least regulatory exposure |
| Q5 | Earnings calendar source (D6): free API now vs paid later? | Free API on dev to learn; decide after licence review |
| Q6 | Hebrew (D7): now or after the English flow is proven? | After |
| Q7 | Do you want a small-cap tag or a market-cap floor on the lists (D5)? | Tag first, keep the lists unchanged |
| Q8 | Commit the work to git now? | Yes |

## Appendix — how these numbers were produced

- Table sizes and coverage: `select count(*), count(distinct symbol), min(date), max(date)` per table against `trading_production` (the planner's cached row estimates were stale and were **not** used).
- Market-health, gaps, near-highs, sector 20-session and aligned-timeframe counts: one throw-away pandas script over `stock_prices` (from 2025-08-01), `weekly_technical_indicators`, `monthly_technical_indicators`, `sector_performance_daily`; liquid = 20-day average dollar volume ≥ $5M. "Aligned" = daily close above the prior 20-day high **and** the latest weekly close within 3% of the 20-week high (or above) **and** the latest monthly close within 3% of the 12-month high (or above).
- Base rates: `ml_breakout_dataset_v2` where `direction = 1` and `plan_r` is not null (a matured 20-day window).
- Digest sample: `python mechanism/alerts/send_daily_digest.py --image` (dry run; nothing sent).
