# MVP acceptance checklist: internal user trial

Status: **checklist for owner review**, 2026-10-26.

**How to run it:**
- Use the **production** build after the planned releases (§0), on one test account, on a **phone** (390 px) **and** a laptop.
- Mark each line ✅ / ❌ with a note, and include a screenshot for every ❌.
- The trial starts only when every **must** line is ✅.
- **Should** lines may be ❌ if a known-issue note is written.

## 0. Preconditions (release state)

| # | Item | Must/Should |
|---|---|---|
| 0.1 | Stage 2 cutover done, and the first scheduled pipeline run after it validated | must |
| 0.2 | Backend released with PR #65 (movers + peers on the Screener engine), plus the earnings read model and the Overview metrics fixes if approved | must |
| 0.3 | Frontend deployed with PRs #62, #64, #65 and the follow-ups, built against that backend | must |
| 0.4 | `main` CI green at the released commit; backend health gates passed; rollback pointers recorded | must |
| 0.5 | The newest stored session is the expected one (e.g. Monday's checklist expects Friday's session) | must |

## 1. Journey: Overview → Sector Intelligence → Smart Screener → Stock Intelligence → Watchlist

| # | Step | Expected | Must/Should |
|---|---|---|---|
| 1.1 | Open Overview | Loads in < 3 s. Every card shows "Data as of" / its session. No internal page is linked. | must |
| 1.2 | Overview → a sector (heatmap or "Sectors today" tile) | Opens `/sectors/<slug>` for that sector | must |
| 1.3 | Sector page → "Screen this sector" | Screener filtered to that sector, rows > 0, every row in that sector, same session as the sector page | must |
| 1.4 | Overview movers "View all" (each of 3 lists) | Screener opens with the same filters (price ≥ $1, ≥ $1M/day; volume ratio ≥ 2 for unusual volume) and the same session; its first rows equal the card's | must |
| 1.5 | Screener → a stock | Stock page for that symbol; header "Data as of" = the Screener session | must |
| 1.6 | Stock page ★ | Adds to the watchlist; the badge state persists after a reload | must |
| 1.7 | Watchlist → Performance | The starred stock is selectable; chart, summary, ranking and cards load | must |
| 1.8 | Watchlist list → a stock | Opens its Chart view; the stock page link works | must |
| 1.9 | Back/forward through the whole journey | No blank pages, no lost filters (Screener filters live in the URL) | should |

## 2. Cross-page numerical consistency (same session, same stock)

Pick 3 stocks: one large cap, one mover from the Overview list, one in a small sector.

| # | Check | Expected | Must/Should |
|---|---|---|---|
| 2.1 | Close and 1-day change: Overview movers row vs Screener row vs Stock page header vs Watchlist close strip | Identical values and session | must |
| 2.2 | Volume ratio: Overview unusual-volume row vs Screener column | Identical | must |
| 2.3 | Sector: Screener row vs Stock page Peers sector vs sector page membership | Same sector, or a labelled difference (observed vs reference) | must |
| 2.4 | Peers: Stock page Peers vs Screener "All <sector> stocks" link | Same first 8 symbols (excluding the stock) and same values | must |
| 2.5 | RS 20 / Strength Score: Screener vs Stock page context | Identical | must |
| 2.6 | Overview "Today's activity" counts vs the Screener with the equivalent filters (price ≥ $1, the session) | Equal, or the tile's label states the difference (e.g. no liquidity floor) | must |
| 2.7 | Watchlist returns vs a manual check of two closes | Matches stored adjusted closes over the stated window | should |

## 3. Earnings date parity

| # | Check | Expected | Must/Should |
|---|---|---|---|
| 3.1 | For 5 stocks (incl. one with a moved date and one with an "unverified" date), compare the Stock page badge, stat and Earnings tab, the Earnings page row, the Overview earnings card and the Screener `next_earnings_date` | Same date everywhere (read model = board = Screener rule) | must |
| 3.2 | A date the latest calendar check did not re-list | Shown, labelled **Unverified**, never hidden or shown as certain | must |
| 3.3 | A stock with no stored earnings | "not scheduled" / dashes; no guessed date | must |
| 3.4 | Earnings tab shows when the calendar was last checked | Present | must |
| 3.5 | No report time (before open / after close) is claimed anywhere | True | must |

## 4. Freshness and missing data

| # | Check | Expected | Must/Should |
|---|---|---|---|
| 4.1 | Every product page shows one "Data as of" line (Overview, Momentum Today, Sectors, sector page, Screener, Stock, Watchlist, Markets, Earnings) | Present, with a date | must |
| 4.2 | Weekend/holiday visit | Shows the last session date; no "today" wording implying a live market | must |
| 4.3 | Missing values (no P/E, no RS, no change) | "—", never 0 or a fabricated default | must |
| 4.4 | A stock with partial history on Watchlist Performance | Flagged ("history starts …"), excluded from the equal-weight average and named | must |
| 4.5 | Backend unreachable (simulate: disconnect network) | Error states with the pages still usable; lists kept | should |

## 5. Mobile (390 px)

| # | Check | Expected | Must/Should |
|---|---|---|---|
| 5.1 | No horizontal page scroll on every journey page | True (tables scroll inside their cards) | must |
| 5.2 | Tap targets: tabs, ★, info tips, chips | Usable with a thumb; info tips open on tap and close on a tap elsewhere | must |
| 5.3 | Charts readable (Overview, sector, Stock, Watchlist) | Axes and legends legible; hover data reachable by tap | should |
| 5.4 | Sidebar / navigation on phone | Every product page reachable in ≤ 2 taps | should |

## 6. Watchlist browser-local storage disclosure

| # | Check | Expected | Must/Should |
|---|---|---|---|
| 6.1 | The Watchlist page says lists are saved in this browser only | Visible on the page | must |
| 6.2 | Trial instructions tell users lists don't sync across devices | In the trial brief | must |
| 6.3 | Clearing site data | Lists are lost; nothing else breaks | should |

## 7. Loading, empty and error states

| # | Check | Expected | Must/Should |
|---|---|---|---|
| 7.1 | Empty watchlist | Explains how to add stocks | must |
| 7.2 | Screener with filters matching nothing | "0 matching stocks", filters still editable | must |
| 7.3 | Movers list empty (e.g. no ratios yet for the session) | States why | must |
| 7.4 | Slow API (throttle to slow 3G) | Skeletons or spinners; no layout jumps that hide content | should |
| 7.5 | Unknown symbol URL | "not found" message | must |

## 8. Internal research isolation

| # | Check | Expected | Must/Should |
|---|---|---|---|
| 8.1 | Product navigation lists only product pages; Internal is collapsed or hidden for collaborators | True (Deep Value: owner-only) | must |
| 8.2 | No product page links to an internal page | True (pinned by `productContentGuard.test.ts`) | must |
| 8.3 | No trade plans, sizing, ML scores, win rates or performance claims on product pages | True (pinned by the same guard) | must |
| 8.4 | Ledger and strategy analytics (Strategies, Quant Lab, Track Record) not reachable from product pages | True | must |
| 8.5 | Collaborator role cannot open owner-only pages | 403 or hidden | must |

## 9. Sign-off

Owner name/date · pages and devices tested · ❌ items with known-issue notes · go / no-go for the trial.
