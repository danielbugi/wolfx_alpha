# MVP UX audit: Overview → Sectors → Screener → Stock → Watchlist

Status: **audit for owner review**, 2026-10-25. Nothing in this document is implemented.

**Evidence:** the code on `main@936c42b`, plus the Watchlist branch (draft PR #62) built locally and run against production responses captured read-only (session 2026-10-23). Each finding is marked:
- **[code]** verified in the source;
- **[shot]** seen in a screenshot;
- **[walk]** to confirm in a hands-on walkthrough before the trial.

## 1. The journey as it is today

| Step | Route | Menu name | Hands off to |
|---|---|---|---|
| Overview | `/` | Overview ("Market overview") | Sector pages; **the internal `/momentum`**; the Screener (movers links, broken: see F1) |
| Sector Intelligence | `/sectors`, `/sectors/[slug]` | Sectors | Back to `/sectors`; stock pages via hover links. **No hand-off to the Screener** |
| Smart Screener | `/screener` | Screener | Stock pages, ★ to Watchlist |
| Stock Intelligence | `/stock/[symbol]` | (none, reached from tables) | ★ to Watchlist; sector page |
| Watchlist | `/watchlist` | Watchlist | Stock pages, Screener (empty state) |

**Deep Value:** already removed from the product navigation. It is under Internal, owner-only (`/alerts`), its page and `/api/deep-value/scan` are intact, and no product page links to it **[code]**. The one leftover is the header comment of `frontend/src/lib/navigation.ts`, which still lists "deep value" in the product journey (a docs-only fix).

## 2. Findings

### Navigation friction
- **F1. Overview's "see all" movers links do nothing. [code]** `MarketMovers` links to `/screener?filter=gainers|losers|volume`. The Screener's URL parser (`lib/screenerV2.ts` `fromSearchParams`) has no `filter` parameter, so the user lands on the default, unfiltered Screener.
- **F2. A product page links into an internal tool. [code]** Overview's sector panel links to `/momentum?sector=…` (the internal Momentum Board, commented as such in `app/page.tsx`). A trial user leaves the product without warning.
- **F3. Sector pages are a dead end for screening. [code]** The Screener already accepts `?sector=` (`s.sectors = p.getAll('sector')`), but neither the sector list nor the sector page links to it. "Show me this sector's strongest stocks" needs a manual re-entry of the filter.
- **F4. The journey order differs from the menu. [code]** The menu goes Overview, Momentum Today, Sectors, Screener, Watchlist, Markets, Earnings. Momentum Today sits between Overview and Sectors, and Stock Intelligence has no entry point except tables and search. That is acceptable, but the trial script should follow the menu, not the planned journey.
- **F5. Watchlists live in one browser only. [code]** They are stored in `localStorage` (labelled "saved in this browser"). A trial user who opens the product on a phone and a laptop sees two different lists. The owner already reviews on a phone.

### Confusing terminology
- **F6. Unexplained labels on the main screens. [code]** Screener column labels include "RS pctl 20", "vs sector 20", "Channel position", "ATR %" and "20-session return". The Stock page explains several of them in plain language (`StockContextPanel`); the Screener table does not.
- **F7. Units and baselines.** "pp" (percentage points) appears in the Watchlist and elsewhere without a definition. The Strength Score's 0–100 scale and its gate ("withheld") need a one-line explanation where the score appears. **[shot]**
- **F8. Names.** The planned names ("Sector Intelligence", "Smart Screener", "Stock Intelligence") appear only in code comments; the UI says Sectors, Screener and the symbol. Choose one vocabulary for the trial materials. The short UI names are the better fit.

### Missing visualisations
- **F9. Watchlist performance over time.** Addressed by draft PR #62: normalized chart, S&P 500, summary, sector mix, ranking, cards. **[shot]**
- **F10. Sector page → constituents' relative strength.** The sector page has a rebased comparison chart, but no constituents-on-a-map view like the Screener's Strength Map, filtered to the sector. **[walk]**
- **F11. Stock page → the stock against its sector and the S&P 500 over the same windows** as the Watchlist chart. Today these are numbers in the context panel; a small rebased chart would reuse `lib/performance.ts` without a new endpoint. **[walk]**

### Data freshness gaps
- **F12. Several "as of" clocks, not shown together. [code]** The pages use different sessions or dates:
  - the Screener session (explicit);
  - the Watchlist panel session ("Closes for the … session");
  - the Overview market snapshot (`DataStamp`);
  - earnings (fetch time, which the backend now returns as `earnings_fetched_at`);
  - fundamentals (ingestion time, not point-in-time).

  The Stock page shows no single "prices as of" stamp at the top. Its `asOfDate` is set only in the `no_signal` case. **[walk]**
- **F13. Weekend and holiday states.** The sidebar clock says "Closed · weekend". Each page should say "latest session: Fri 2026-10-23" consistently rather than leaving it implicit. **[shot]**
- **F14. Earnings dates.** The board and discovery now use the current-row rule (PR #52). The Earnings page and the stock page's earnings tab should be checked against the same rule before the trial, so that no superseded date shows. **[walk]**
- **F15. Two screener engines on product pages. [code]** The Smart Screener, the Watchlist and the Stock context panel use Screener v2: one explicit session, with provenance. The Overview's market overview (`screenerApi.getMarketOverview`) and the Stock page's Peers panel (`screenerApi.search`) still use the legacy v1 engine (`backend/services/market_service.py`). That engine has its own session logic and defaults, for example a missing volume ratio becomes `1.0`, though Peers does not display it. Numbers on the same page can therefore come from different rules. **[walk]** compare Peers' day changes with the Screener's for the same session.

## 3. Top 5 before an internal user trial

| # | Improvement | Fixes | Size | Dependencies |
|---|---|---|---|---|
| 1 | **Fix the Overview hand-offs.** Movers links go to a real Screener query (sort by `change_1d` asc/desc, or by volume ratio), and the sector panel links to `/screener?sector=…` instead of the internal `/momentum`. | F1, F2 | Small, frontend only | None |
| 2 | **"Screen this sector"** on the sector list and sector page (`/screener?sector=…&sort=rs_20`). | F3 | Small, frontend only | None |
| 3 | **One freshness stamp per page**, in the same words everywhere ("Prices: session 2026-10-23 · Fundamentals: stored 2026-10-19 · Earnings: checked …"), including the Stock page header. | F12, F13 | Medium, frontend; uses fields the APIs already return | Confirm each field per page during the walkthrough |
| 4 | **Plain-language definitions** wherever a metric appears: tooltips on Screener column headers (the `/api/screener/v2/definitions` response already carries a description for every metric), plus a short glossary for pp, RS percentile, Strength Score and channel position. | F6, F7 | Small–medium, frontend | None |
| 5 | **Ship the Watchlist Performance view (PR #62).** Decide on cross-device lists: either keep "this browser" with export/import for the trial, or approve per-user server lists (backend + migration). | F5, F9 | PR #62 is ready for review; server lists are larger | Server lists need a migration and backend release, so owner approval |

**Deliberately not in the top 5:**
- F10 and F11 (nice, not blocking);
- F4 and F8 (naming and ordering for the trial script).
- F15 (moving the Overview and Peers to Screener v2 is a larger consistency task; verify in the walkthrough first).

## 4. Trial readiness checklist (for the walkthrough)

1. Run the full journey on a phone (390 px) and a laptop with one test account: Overview → a sector → its stocks in the Screener → a stock → ★ → Watchlist Performance.
2. On each page, note the date it says its data is from, and compare it with the latest session.
3. Check the earnings dates for 5 stocks on the Stock page, the Earnings page and the Screener against the current-row rule.
4. Confirm no internal page is reachable from a product page except by typing the URL.
5. Confirm product pages carry the disclaimer and no trade plans or performance claims (pinned by tests on `PRODUCT_ROUTE_PREFIXES`).

## 5. Deployment dependencies and risks for this batch

| Item | Ships as | Depends on | Risks | Rollback |
|---|---|---|---|---|
| Watchlist Performance (PR #62) | Frontend only (Vercel, manual `vercel deploy --prod` from a clean export) | Nothing new: the existing `/api/stock/{s}/price-history`, `/api/market/instruments/^GSPC/history` and Screener v2 search. **Independent of Stage 2**, the mechanism pin, migrations and backend releases. | (1) It becomes the default Watchlist view, so returning users see a new first screen (the view is remembered after one click). (2) The stored S&P 500 history begins 2025-09-19: the 1Y baseline (2025-10-23 today) is covered, but a longer period would not be. (3) Six histories of 300 bars each per visit, cached for 10 minutes, the same as PR #39. | Redeploy the previous Vercel deployment |
| Top-5 items 1, 2 and 4 | Frontend only | None | Low; link and label changes, covered by component tests | Previous Vercel deployment |
| Top-5 item 3 (freshness stamps) | Frontend first | Uses fields the APIs already return; a missing field means a backend change and a release | Showing a wrong date is worse than none, so each stamp needs a test with a stale fixture | Previous Vercel deployment |
| Cross-device watchlists | Backend + migration + frontend | Owner approval, a new table (additive migration), a health-gated backend release | Privacy (per-user data), and migrating existing browser lists | Backend `rollback.sh`; the migration stays (additive) |
| Ledger analytics (Notebook 03/04) | Docs/notebooks only | Read-only export; ≥ 20 observed sessions for full-k results (from ≈ 2026-10-26) | Misreading censored or resolved-only figures. Each figure carries its k and its OOS status. | — |

**Timing against Stage 2:** none of this needs the 2026-10-27 window. A Vercel deploy can happen before or after it without interaction, but it should not be done *during* the window (10:00–12:00 UTC), to keep any incident attributable.
