# Data providers: what we use, what each can actually supply, and the gaps

Verified 2026-10-31 by live, read-only calls with the production keys (status codes and field names only; no
values or keys recorded) and by reading the code. Vendor claims that were NOT re-verified are marked ◇.
Complements `docs/research/EARNINGS_SOURCE_ASSESSMENT.md` (the point-in-time view for research); this page is
about the product's day-to-day data.

## 1. Verified capabilities (our plans)

| Capability | Tiingo (Power, $30/mo) | Alpaca (free keys) | SEC EDGAR | Yahoo via `yfinance` |
|---|---|---|---|---|
| Daily OHLCV (split/dividend adjusted) | **Yes**, consolidated, all symbols | Yes (IEX and SIP both answer; IEX has coverage gaps, ~38/1005 symbols empty per the code notes) | no | yes (scraped) |
| Real-time / last quote | IEX quote **Yes** | snapshot **Yes** | no | yes |
| Company news | **Yes** (`/tiingo/news`) | **Yes** (`/v1beta1/news`) | no | yes (`Ticker.news`) |
| Corporate actions (dividends, splits) | in price rows (`divCash`, split factor) | **Yes** (`/v1/corporate-actions`) | no | yes |
| **Earnings dates, EPS actual / estimate / surprise** | **No endpoint** | **No** (`/v1beta1/earnings` is 404) | report event with timestamp (8-K Item 2.02); no estimates | **Yes**, the only source we have |
| **Quarterly statements (revenue, net income, assets, cash flow)** | **Dow 30 only** on our plan; every other symbol returns HTTP 400 ("limited to the DOW 30"). Full access = Tiingo's paid Fundamentals add-on (price unknown; email support) | no | **Yes, official and free**, 10-Q/10-K XBRL (`companyfacts`) the day it is filed | yes, with a lag of days to weeks after the report |
| Daily valuation metrics (market cap, P/E) | Dow 30 only | no | derivable | yes |
| Indices, futures, FX, crypto | FX/crypto yes; indices and continuous futures no | crypto yes; indices/futures no | no | yes |
| Licence / reliability | documented, paid, redistribution terms apply | documented | public domain; fair-access rules (declared User-Agent with a contact, ≤10 req/s) | unofficial scraper, no SLA, no stated redistribution rights |

## 2. What each production feature depends on today

| Feature | Source | Fallback | Notes |
|---|---|---|---|
| Daily prices (`stock_prices`) | Tiingo (`DATA_PROVIDER=tiingo`) | Alpaca / yfinance by config | primary is the right one |
| Index / futures / commodity / crypto series (`market_index_updater`) | yfinance, always | none | Tiingo and Alpaca do not carry these; the code says so |
| Earnings calendar, EPS actual / estimate / surprise (`earnings_calendar_updater`) | yfinance | none | no other provider supplies it |
| Earnings tab / chart markers (live `earnings_service`) | yfinance on demand, 60-min cache | none | superseded by the DB read model in release 4522d29 |
| Quarterly statements (`quarterly_fundamentals_updater`) | Tiingo for the Dow 30, **yfinance for the other ~2,970** | per-symbol | the statements lag: HRI reported 2026-10-27, its quarter ending 2026-09-30 was still absent four days later |
| Daily valuation / sector / industry (`fundamentals_updater`) | Tiingo for the Dow 30, yfinance for the rest | per-symbol | sector map is not point-in-time |
| Stock news links (`news_links`) | yfinance `Ticker.news` | none | Tiingo and Alpaca both have news endpoints we are not using |

So yfinance is the dependency for every non-price dataset because the plans we pay for do not cover
earnings or fundamentals beyond 30 symbols. This is a coverage fact, not a wiring mistake.

## 3. Decisions

1. **Prices stay on Tiingo.** Already primary; nothing to change.
2. **Results of a report (EPS vs estimate, surprise): yfinance stays, but it is now asked in time.** The nightly job used to
   re-ask a symbol only every 14 days once next quarter's estimate was on file, so most fresh results arrived up to two weeks
   late (2026-10-31: 40-47% of each of the previous five days' reports had no actual EPS). PR #75 re-asks every night until the
   result is captured, and logs `EARNINGS_FRESHNESS` counts. The page shows what the database holds with its date, never a guess.
3. **Quarterly statements: SEC EDGAR is the right primary for US filers** (official, free, available the day the 10-Q is filed,
   no scraping risk), with yfinance as the fallback. The repo already contains the pure XBRL parser
   (`mechanism/market_intelligence/sec_financials.py`) and a rate-limited, circuit-broken transport (`sec_transport.py`).
   **Blocked on one input: SEC requires a declared User-Agent with a real operator contact, and none is configured**
   (`SEC_USER_AGENT` is absent on the server; the research code deliberately never invents one or uses a personal address).
4. **Consensus estimates and surprise history:** no free source exists. yfinance is the only supply we have; a paid estimates
   vendor would be a licensing and cost decision (a public channel and a paid product are the sharp edge). Not made here.
5. **Tiingo Fundamentals add-on** would cover statements and daily metrics for all symbols from an already-integrated vendor.
   Price and redistribution terms are unknown; that is a cost decision, so it is listed for the owner rather than purchased.
6. **News:** Tiingo and Alpaca both return company news. Moving `news_links` off yfinance is a small, safe change, listed as a next step.

## 4. Rules for any new provider code

* Every stored value keeps its source and fetch time; a value is shown with its date or not at all.
* A missing value is missing: never a default, never another provider's number silently substituted. A fallback writes its own
  provenance.
* A provider call has a timeout, a rate limit and a failure that leaves the previous data in place.
* Each nightly updater logs a one-line freshness summary (`EARNINGS_FRESHNESS ...` is the first); stale or incomplete runs are
  visible without reading the whole log.
