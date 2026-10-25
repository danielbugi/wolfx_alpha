# Earnings dates: consistency audit (read-only, 2026-10-25)

Status: **audit for owner review**. Nothing here is implemented, and no data was changed.

**Method:** the sources were traced in code, then checked read-only on production (session 2026-10-23, run 2026-10-25):
- database queries in a read-only transaction inside the backend container;
- the same yfinance lookup the Stock page makes, run in a separate process.

**The approved rule:** the "current earnings row", `backend/services/earnings_current.py` (PR #50/#52). Among each symbol's upcoming rows, a row is shown only if the symbol's latest complete vendor fetch still lists it.

## 1. Which surface reads which source

| Surface | Date source | Uses the approved rule? |
|---|---|---|
| Screener `next_earnings_date` / `days_to_earnings`, Stock context panel, Momentum Today | Database, `discovery_service` (`earnings_next`) | **Yes** |
| Momentum board → **Earnings page** rows for board stocks, **Overview Earnings card** | Database, `momentum_board_service` | **Yes** |
| Telegram "reports today" post | Database, `earnings_today_query` (in the Stage 2 image; timer disabled) | **Yes**, once Stage 2 ships |
| **Stock page**: header chip ("Earnings in 8d"), Earnings tab, chart markers | **yfinance live**, `/api/stock/{symbol}/earnings` → `earnings_service.get_earnings_dates` (1 h in-memory cache per backend process) | **No.** It is a different source, not the database. |
| **Watchlist** chart markers | yfinance live (same endpoint) | **No** |
| **Earnings page** rows for **watchlist** stocks, Overview Earnings card for watchlist stocks | yfinance live, through `useEarningsHistories`. **It wins over the board's date when a stock is on both.** | **No** |

So one page can show two answers for the same stock: the Earnings page uses the database for board stocks and yfinance for watchlist stocks. The Stock page's chip and the Screener's column on the same stock can also differ.

## 2. Measured on production (read-only)

| Check | Result |
|---|---|
| Symbols with any upcoming row in `earnings_calendar` | 2,877 |
| …where a naive "earliest upcoming row" differs from the approved rule (moved dates left behind as ghost rows) | **341** (11.9 %). Any reader that skips the rule would mis-date these. |
| …with upcoming rows but no current row (the latest fetch no longer lists any of them) | 26 |
| Database rule vs yfinance live, sample of 29 (large caps, the screenshot watchlist, 15 reporting within 14 days) | **28 agree, 1 disagrees** |
| The disagreement | **META**: the database says **2026-10-28** (in 3 days); yfinance's next listed date is **2027-01-27** (no 10-28 row). The Stock page would show no upcoming report while the Screener shows 10-28. |
| Example the rule fixes | **AAPL**: naive 2026-10-29 (ghost), rule 2026-11-02, yfinance 2026-11-02. The Stock page shows the right date here only because yfinance and the rule agree. |
| Database freshness | Most symbols' newest earnings fetch is 2026-10-21/22 (latest complete fetch per symbol). There is no "as of" for earnings dates on any page. |

## 3. User impact

- **High:** a stock about to report (META) can show no upcoming earnings on its own page and on the Watchlist chart, while the Screener and Earnings page (for board stocks) show the date. For earnings tail risk, a missing date is the worst failure: users take risk into a report they were told isn't coming.
- **Medium:** the same stock can carry different dates on the Earnings page depending on whether it is on the user's watchlist.
- **Low:** neither source's freshness is shown, so a user cannot tell which date is newer.

## 4. Options (not implemented; each needs approval)

1. **One source for every surface (recommended):** serve the Stock page chip, Earnings tab and Watchlist markers from the database through the approved rule. That means a small backend endpoint reusing `EARNINGS_CURRENT_CTES` for one symbol, with no new provider and no migration. yfinance stays only for **reported history** (EPS actual and surprise), which the database also has for reported rows.
2. **Show disagreement:** keep yfinance, but where it disagrees with the database within 30 days, show both dates with their sources ("Calendar: 10-28 · Yahoo: none listed").
3. **Earnings "as of":** show the database's latest fetch time next to every earnings date (`earnings_fetched_at` is already returned by the board).

**Not in scope here:** the earnings collector, first-seen capture, and any provider change.
