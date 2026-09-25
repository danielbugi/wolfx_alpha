> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** Preserved for reference/history.
> For current architecture, see [docs/architecture/](../architecture/), [docs/operations/](../operations/),
> and [CLAUDE.md](../../CLAUDE.md). Moved here 2026-09-25 as part of the repository documentation
> consolidation — content below is unmodified from before the move except for this banner.

# BOT_DESIGN_REPORT.md — how to make the private assistant a real product

> **STATUS (2026-09-21, later): BUILT and running** with the recommended answers to Q1–Q8 (the user asked to build without answering them). Steps 1-5 of §9 are done (tracker core, Today's lists + card, watchlist/portfolio, onboarding + menu, news, chart); step 6 (morning DM) is not. Deviations from the text below: news is fetched lazily per symbol (cached 6 h, shared) instead of by a batch job; Today's lists pages hold 15 stocks; the personal chart is rendered per request and only the plain chart is cached by file_id; the split guard also recognises common split factors (a 2-for-1 is a 0.5 ratio, which a plain >3x rule misses).
>
> Written 2026-09-21 after phase 7.0 (invite-only access). It is an **analysis + design**. It refines
> phases 7.1–7.3 of [PRIVATE_ASSISTANT_PLAN.md](../../PRIVATE_ASSISTANT_PLAN.md) around your idea: *the bot follows the channel's lists, and
> each user tracks performance from the day they added a stock.* Facts marked **[checked]** were verified against the live system today.

## 1. What you asked for (as I understood it)

1. **Onboarding guide** on first contact.
2. **Watchlist and Portfolio** as two saved lists per user. Each entry keeps the symbol, the price and the day it was added, so the bot
   can show **performance since that day**.
3. The bot works **only with the stocks from the channel's lists** (top gainers / ATR / volume × Breakout / Near breakout) that the
   daily system already produces; users fetch those daily, analyse them, and watch their own lists.
4. Best UI = **one command that shows all the list symbols**; from each symbol: **data + news + chart**.
5. **Each symbol shown once** (no flood, no redundant requests).

## 2. Findings — how good is the bot today? (honest)

| # | Finding | Evidence | Consequence |
|---|---|---|---|
| F1 | The bot is **disconnected from the channel**. `/levels` works for any of 2,866 stocks, so users must already know a ticker. | [checked] the channel lists hold only **23 unique symbols** on the latest day (6 lists × 5, with overlaps). | The natural home screen is "today's 23", not a search box. |
| F2 | The watchlist stores **no price and no meaningful date** (`bot_watchlist` = user, symbol, added_at). | [checked] columns. | Cannot show "since you added". `/mylist` only repeats today's facts. |
| F3 | **Command-only UI**: typed arguments, no buttons, no persistent menu, a wall of legal text as the first thing a user sees, then a command list. | `run_bot.py`, `texts.py`. | High drop-off on first contact; users must remember commands. |
| F4 | Only **one snapshot day exists** (`digest_runs`: 2026-09-18) because snapshots are written only when the digest is sent. | [checked]. | "Since added" **must not depend on `digest_stocks`**. Use `stock_prices` (3,076 symbols, current to 09-18, indexed by symbol+date). |
| F5 | **Split / adjustment breaks are real in exactly this kind of stock**: 2 of the 23 list symbols (BNC, SBET) have a break in their stored history. | [checked] `price_discontinuities` (1,039 rows, 121 symbols). | A naive "now ÷ your price − 1" would show wrong numbers. A guard is required from day one (§5). |
| F6 | **News is solved**: Alpaca's free plan returns symbol-tagged headlines with links (Benzinga source). | [checked] HTTP 200, 5 items with `symbols` and `url` for MSTR/AAPL. | Decision D4 is closed in favour of Alpaca (licensing still needs the §9.2 check). Keeps yfinance only as fallback. |
| F7 | **Charts need no new dependency**: `matplotlib`, `mplfinance` and Pillow are installed. | [checked]. | A chart PNG can be rendered locally from `stock_prices`, in the Midnight Dawn palette of the market card. |
| F8 | The data behind everything is only as fresh as the last pipeline + digest run; **nothing schedules them** (CLAUDE.md §7.3). | snapshot is 3 days old today. | Every screen must print "US close Fri 18 Sep" and warn when older than 4 days (already done for `/levels`). Hosting + scheduling is a prerequisite for a daily product. |
| F9 | English only, audience is Israeli. | — | Keep all copy in `texts.py` (already the case) so Hebrew is a translation, not a rewrite. |

## 3. Product design

### 3.1 Concepts
- **Today's lists** — the union of the channel's six lists for the latest session, **each symbol once**, tagged with where it appears
  (e.g. `gainers #1 · ATR #1 · volume #4`). Source: `digest_stocks.list_ranks` (facts already computed for the channel).
- **Stock card** — one screen per symbol: today's facts, the lists it is in, and buttons for *News*, *Chart*, *ATR levels*, *Add*.
- **Watchlist** — stocks you follow. Reference = the price when you added it.
- **Portfolio** — stocks you hold. Reference = **your** price (and optionally your shares).
- One row per symbol per user: a stock is either *watched* or *held*; "Move to portfolio" asks for your price and keeps the
  original "on your list since" date.

### 3.2 Navigation (mobile-first, per the `telegram-bot-ui-design` skill: ≤3 columns, Back button always first, edit the message in place)

Persistent bottom keyboard (four core actions, so nobody has to remember commands):

```
[ Today's lists ] [ Portfolio ]
[ Watchlist     ] [ Help      ]
```

Commands stay as shortcuts: `/today` `/portfolio` `/watchlist` `/stock AAPL` `/add AAPL 140.5 10` `/remove AAPL` `/guide`
`/privacy` `/export` `/deleteme`. (`/levels` moves inside the stock card.)

### 3.3 Screens (illustrative numbers)

**Today's lists** — one message, edited in place; tabs and paging instead of one long scroll:

```
First Light · Fri 18 Sep · 23 stocks (each shown once)
[ Breakout 10 ]  [ Near breakout 13 ]        <- tab

GEMI    ▲ gainers #1 · ATR #1 · volume #4   ★
FWDI    ▲ gainers #2 · ATR #2
MSTR    ▲ gainers #3 · ATR #3                ✓ in your portfolio
PS      ATR #4 · volume #2
...
[ GEMI ] [ FWDI ] [ MSTR ]                   <- tap a symbol -> stock card
[ PS   ] [ ABTC ] [ MARA ]
[ ‹ ]  page 1/2  [ › ]
US close Fri 18 Sep · educational data, not advice
```
Stocks already on the user's lists are marked (`✓`) so the list doubles as a quick "which of mine are in the news today" check.

**Stock card** (from the snapshot + `stock_prices`; never a provider call):

```
‹ Back                       MSTR · Breakout
Close $153.92 · day ▲16.4% · volume 3.1× · range 2.6× ATR
In today's lists: gainers #3 · ATR #3
Tracked by you: portfolio since Mon 14 Sep at $140.00 → ▲9.9% (4 days)

[ News ] [ Chart ] [ ATR levels ]
[ Move to watchlist ] [ Remove ]        (or [ Add to watchlist ] [ Add to portfolio ] when not tracked)
```

**Watchlist** and **Portfolio** (performance since the day added):

```
Portfolio · US close Fri 18 Sep                       (values at the last close; USD; no fees)
MSTR   10 sh   your price $140.00  now $153.92  ▲9.9%   $1,539.20   61%   since Mon 14 Sep
COIN    5 sh   your price $190.00  now $194.25  ▲2.2%     $971.25   39%   since Wed 16 Sep
Total  cost $2,350.00 · value $2,510.45 · change ▲6.8%
Largest position: MSTR 61% of the portfolio.        (a fact, never "reduce it")
[ MSTR ] [ COIN ]   [ Add stock ]   [ Export ]
```
Watchlist rows are the same without shares/value. A position without shares is shown but **left out of the totals, and the message says
so**. UI copy says "your price" / "since you added" — the wording guard bans "entry", "profit", "buy", "sell", "target", "stop".

### 3.4 Onboarding (first `/start` after the invitation)

Four short screens in **one message edited in place** (`[Next ›]`, `[Skip guide]`), then the mandatory notice. Nothing before the
notice contains market data, so acknowledging last is legally the same as acknowledging first, and far friendlier.

1. **Welcome** — "This is your private First Light assistant. It reads the same end-of-day scan as the channel and helps you follow the
   stocks you care about. Other users cannot see your lists."
2. **How it works** — ① *Today's lists*: every stock from the channel's six lists, each shown once. ② Tap a stock: its data, news
   and a chart. ③ Add it to your *Watchlist* or *Portfolio*: the bot remembers your price and the day. ④ Come back any time: it shows
   how each stock changed **since that day**.
3. **Your data** — "Stored: the symbols you add, the price you give (or the last close), the day, and optionally your shares.
   Nothing else. Prices are end-of-day. `/export` gives you everything, `/deleteme` erases it. The owner of this bot can technically
   read the database." (honest, per plan §4)
4. **One thing first** — the educational notice (existing text) + `[I understand - continue]`.

Then: the bottom keyboard appears, plus a 3-line checklist ("1. Open Today's lists · 2. Tap a stock and add it · 3. Check your
Portfolio tomorrow") with `[Open today's lists]`. `/guide` replays it. Copy lives in `texts.py` (Hebrew later = a translation).

## 4. Data model (additive migration; supersedes `bot_watchlist`)

```sql
bot_tracked (
  telegram_user_id BIGINT, symbol VARCHAR(20),
  kind        TEXT CHECK (kind IN ('watch','hold')),
  ref_price   NUMERIC(14,4) NOT NULL,          -- what "since added" is measured from
  ref_source  TEXT CHECK (ref_source IN ('entered','close')),   -- typed by the user vs the last close at that moment
  ref_date    DATE NOT NULL,                   -- the day added (or the day the price was set)
  shares      NUMERIC(18,6),                   -- portfolio only, optional
  first_added_at TIMESTAMPTZ, first_price NUMERIC(14,4),        -- kept when a watched stock is moved to the portfolio
  PRIMARY KEY (telegram_user_id, symbol))
bot_symbol_cache (symbol, session_date, chart_file_id, chart_built_at, PRIMARY KEY (symbol, session_date))
news_items (per plan §5: UNIQUE(symbol, url_hash))
```
`bot_watchlist` has 0 rows today, so the migration is trivial. Nothing here is logged; every query filters on `telegram_user_id` (tested).

## 5. Performance maths and honesty rules

- **since added** = `close_now / ref_price − 1`; **day** = last two closes; **days** = calendar days since `ref_date`; optional facts: highest
  close since added and the distance from it. All from `stock_prices.close`, indexed → milliseconds, **no provider call**.
- **Price you entered vs last close:** watchlist defaults to the last close (one tap; editable). Portfolio asks for your price with a
  `[Use last close $153.92]` button and an optional share count. `ref_source` records which, and the card says "(last close when added)".
- **Split / adjustment guard (F5):** if `price_discontinuities` has a row for the symbol after `ref_date` (or the close-to-close ratio
  jumped > 3×/< ⅓), show **"n/a - the price series was adjusted after you added (split?). Check your broker."** instead of a number.
  Fabricating a wrong % on exactly the volatile small caps the lists favour is the worst possible failure for a tracker.
- Stated limits: end-of-day prices; USD; no fees, no dividends; a symbol that stops trading shows "no recent price".
- **Facts only:** "largest position 61%" is allowed; "you should…" never. The wording guard test covers all new copy.

## 6. Economics — "each symbol once"

I read this two ways and the design does both (please confirm in §10 Q4):
1. **In every view a symbol appears once** — the lists are a de-duplicated union with tags (GEMI is in three lists but is one row).
2. **Each symbol's expensive data is produced once per day and shared by all users:**
   - *News*: one Alpaca call per unique symbol per day (the ~23 list symbols + every tracked symbol), stored in `news_items`; all users read the table.
   - *Chart*: rendered once per symbol per session, stored on disk, and uploaded to Telegram **once** — later sends reuse the `file_id` (zero upload, zero rendering).
   - *Data*: snapshot / `stock_prices` reads only.
   So cost grows with **unique symbols (~30–300)**, not with users. Per-user rate limits stay.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Wrong performance on split / adjusted symbols (F5) | guard in §5 + fixtures for BNC / SBET |
| Stale data (no scheduler, F8) | "as of" line on every screen; hosting + a 06:00 job before real users |
| Portfolio data is sensitive | plan §4 (isolation, never logged, `/export`, `/deleteme`); encryption decision D5 |
| Personal P&L tracking feels closer to advice | facts-only wording, list-only browsing (no personalised picks), legal review before the first non-owner (plan §9) |
| Benzinga headline licensing | headline + link only, no article text; verify terms (plan §9.2) |
| Telegram limits | ≤3 button columns, ≤64-byte callbacks, 4096 chars; paging instead of long lists |

## 8. What else would make it better (ranked by value ÷ cost)

1. **Morning DM (opt-in, only when something changed):** your tracked stocks' change since added + which of them entered/left the lists (plan 7.2; the snapshot already stores `prev_category`).
2. **"New today" marker** in Today's lists (group vs yesterday's group).
3. **Public list scoreboard:** track every list symbol from the day it appeared in the channel — the same "since day X" idea, applied to the lists themselves. Honest evidence for you and the audience; must be shown neutrally (our own studies found no proven edge).
4. **Hebrew UI** after the English flow is stable.
5. **Channel post pointing to the bot** ("Private assistant — invite only") and a pinned "Start here".
6. Weekly summary; a fact-alert when a tracked stock closes within 1 ATR of its own risk level.
7. Hosting (VPS) + scheduler — the single biggest reliability upgrade.

## 9. Build order (each step ships with the QA families in plan §8: hand-computed fixtures, isolation/leak, hostile input, mutation)

1. **Migration + store + pure performance functions** (`bot_tracked`, `performance.py`; cents-exact tests incl. split-guard cases).
2. **Today's lists + stock card** (snapshot + `stock_prices`; de-duplicated union; paging; ✓ markers).
3. **Watchlist / Portfolio** (add / move / remove, guided price entry, totals, `/export`, `/deleteme`).
4. **Onboarding + bottom keyboard** (built last among the UI steps because its copy describes 1–3).
5. **News** (Alpaca batch job → `news_items`) and **Chart** (mplfinance, dataviz skill loaded first, file_id cache).
6. **Morning DM.**

## 10. Decisions I need from you (recommended answers first)

| # | Question | Recommendation |
|---|---|---|
| Q1 | "Work only with the channel's stocks": may users **add any** liquid stock they own, or only stocks that appeared in the lists? | **Browse = lists only. Add = any stock in the daily scan** (2,866). A portfolio limited to list stocks can't hold what people actually own. |
| Q2 | Price when adding | Watchlist: last close, one tap, editable. Portfolio: user's own price (button "use last close") + optional shares. |
| Q3 | One row per symbol (watch **or** hold)? | Yes. |
| Q4 | "Each symbol once" = union once per view + one fetch per symbol per day for everyone (§6)? Or do you also want a user to see a symbol's detail only once per day? | The first; the second would block re-reading a card. |
| Q5 | Language | English first, Hebrew after the flow is stable. |
| Q6 | Caps | 25 watchlist + 25 portfolio per user. |
| Q7 (D5) | Encrypt shares/prices in the DB? | Restricted DB role now; field encryption before real users. |
| Q8 | Start with step 1–3 (tracker core) now? | Yes — onboarding after them, so its text matches what exists. |
