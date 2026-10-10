# First Light Algo — Earnings / revenue / guidance source-capability assessment (Slice 2)

Status: assessment and recommended architecture. **No vendor is chosen, bought, called or encoded in any schema by this document.** Nothing here
touches production. The provider-neutral landing zone already exists in the lab branch: migration 25 (`market_event`, `market_event_revision`,
`events.py`) — not applied to production.

**Evidence standard.** Statements about the *repository* were verified against the code in this branch. Statements about *vendors* (coverage,
limits, price, licence) come from general knowledge, were **not** re-verified against live vendor documentation in this session, and change often.
They rank candidates for a trial; they are not a procurement decision. §6 defines the trial that turns each claim into evidence.

## 1. What the platform needs (and why PIT is the whole question)

| Need | Used for |
|---|---|
| EPS estimate, actual, surprise | catalyst features, earnings-proximity / surprise performance breakdowns |
| Revenue estimate, actual, surprise | same |
| Report date **and timestamp**, BMO/AMC timing | proximity features, avoiding trading into a print, post-earnings label windows |
| Estimate-revision history (consensus as it was on each day) | the only way to compute a surprise or a revision trend *as known at T0* |
| Guidance (raised / cut / initiated, with numbers) | catalyst classification |
| Post-earnings price and volume reaction | already derivable from `stock_prices` once the report time is trustworthy |

A feature is usable for research or ML only if we can prove **when each value became known**. A source that returns today's consensus for a
past quarter, or today's final date for a date that moved, makes every backtest on it look better than reality (leakage). So sources are
ranked first on PIT suitability, then on coverage and cost.

## 2. What exists today, and why it cannot be the PIT source

`earnings_calendar` (`mechanism/add_earnings_calendar_table.sql`, filled by `earnings_calendar_updater.py` from yfinance):

* One row per `(symbol, report_date)`, **upserted**. When a company moves its date the old row is left and a new one appears, or an estimate is
  overwritten; first-seen time is not stored. A date that moved looks as though it was always the final date.
* `eps_actual` / `surprise_pct` are whatever Yahoo currently shows; later restatements overwrite earlier values.
* No timestamp, no BMO/AMC from a proven source, no revenue, no guidance, no revision history.
* yfinance is an unofficial scraper of a consumer website: no SLA, no stated redistribution rights, undocumented breaking changes.

Verdict: fine as a **convenience display and a staleness hint** (its current use); `not PIT`. It is never backfilled into migration 25, and
nothing may label it observed.

## 3. Candidate sources

PIT columns: **Ts** = does the source supply its own publication/knowledge time; **Hist** = does it keep an as-of history of estimates and
revisions; **Rev** = revenue estimates + actuals; **Gd** = guidance.

| Candidate | Type | Ts | Hist | Rev | Gd | Notes (unverified vendor claims marked ◇) |
|---|---|---|---|---|---|---|
| **SEC EDGAR** (submissions API, 8-K, XBRL company-facts) | official, free | **Yes** (acceptance timestamp, to the second) | n/a (no estimates) | actuals only (XBRL `Revenues`, with `filed` date) | text only (8-K Ex. 99.1 press release) | The one source whose time is PIT-grade and free. No consensus. Fair-access rule ◇ ~10 req/s and a descriptive User-Agent with contact e-mail. |
| **yfinance / Yahoo** | scraped | No | No | partial | No | Current state only. Not PIT. Licensing grey. Keep for display only. |
| **Financial Modeling Prep** | low-cost API | ◇ partial | ◇ limited | ◇ yes | ◇ some | Cheap breadth; PIT quality of the estimate history must be proven in trial. |
| **Finnhub / EOD Historical Data / Alpha Vantage** | low-cost APIs | ◇ mostly no | ◇ mostly no | ◇ varies | ◇ no | Good calendars and actuals; estimate *history* usually absent or restated. |
| **Polygon / Massive, Benzinga data** | market-data vendors | ◇ news/calendar timestamps | ◇ calendar revisions | ◇ | ◇ Benzinga guidance feed | Benzinga is a news-grade timestamped feed; strong for event time and guidance headlines. Licence for storage/redistribution needs reading. |
| **Nasdaq Data Link (Zacks / Sharadar)** | dataset vendor | ◇ filing-date based | ◇ Sharadar fundamentals keep `datekey` | ◇ | No | Sharadar's PIT fundamentals (filing-date keyed) are a credible *actuals* cross-check. Zacks estimates ◇ revisions history varies by product. |
| **LSEG I/B/E/S, FactSet Estimates, S&P Capital IQ, Visible Alpha** | institutional | **Yes** | **Yes** (as-of consensus, revisions) | **Yes** | ◇ yes (FactSet/LSEG guidance) | The only class that natively offers point-in-time consensus and revision history. Enterprise pricing, strict redistribution limits (a public Telegram channel or paid product is the sharp edge). |
| **Wall Street Horizon** | event-date specialist | ◇ yes (confirmed vs estimated date, change log) | ◇ date-change history | No | No | Best-in-class for *when* a report will occur and date revisions; no consensus. |
| **Estimize** | crowd estimates | ◇ timestamped submissions | ◇ yes | ◇ | No | Useful signal, not "consensus"; coverage and licensing to be checked. |

## 4. What each need can honestly be sourced from

| Need | Best PIT-grade source in reach | Residual gap |
|---|---|---|
| Report **happened**, with timestamp | SEC 8-K Item 2.02 acceptance time (free, PIT-grade) | The press release usually precedes its 8-K, so acceptance time is a *late* bound (safe: never earlier than truth). Foreign filers (6-K) are less regular. |
| EPS / revenue **actual** | SEC XBRL (`filed` date, form, period) as the audited value; vendor feed as the fast value | Press-release "adjusted" EPS is not in XBRL. State which basis a value uses (`gaap` vs `adjusted`) in the payload. |
| Report **date before it happens**, BMO/AMC | company-confirmed date from a vendor with a change log (Wall Street Horizon-class) or our own first-seen log of any calendar | Our own first-seen log is PIT-honest **only going forward**; history is unrecoverable from yfinance. |
| **Consensus estimate and revisions** | an institutional estimates vendor with as-of history | No free source. Without it, surprise is computable only forward from our own first-seen snapshots (grade B). |
| **Guidance** | structured vendor feed (◇ FactSet/LSEG/Benzinga) or classification of 8-K Ex. 99.1 text | Text classification is an *interpretation* (versioned, never a fact); numbers must be extracted by a deterministic parser or a human, never invented by a model. |
| Post-earnings reaction | `stock_prices` + `fwd_v1` labels anchored at the proven report time | Only as good as the report time. |

## 5. Recommended architecture: a spine you own, adapters you can swap

Recommendation — **a combination, in this order, each step independently useful and independently stoppable:**

1. **SEC EDGAR as the factual spine (free, PIT-grade).** Ingest filing *facts* per `docs/research/SEC_EDGAR_EVENT_CONTRACT.md`: 8-K Item 2.02 gives
   the earnings-print event with a proven timestamp; 10-Q/10-K XBRL gives audited actuals with their `filed` time. This alone makes
   earnings-proximity and post-earnings-reaction labelling PIT-correct for the stocks we track, without buying anything.
2. **Our own first-seen observation log of every calendar and estimate source we already touch** (including yfinance), as grade-B `ingested`
   revisions. It cannot fix the past, but from the day it runs every date move and estimate change is preserved with a database-stamped
   time. This is the cheapest way to start accumulating the revision history that the platform otherwise lacks. (Registry item 2 in
   `MIGRATION_REGISTRY.md`.)
3. **One paid source for what EDGAR cannot give — consensus-with-history and guidance — chosen by trial (§6), not by brochure.** Two sensible
   tiers: (a) a low-cost API **only if** the trial proves its history is as-of rather than restated, for research use; (b) an institutional
   estimates vendor if and when consensus drives a *published* signal, because redistribution rights then matter more than price.
4. **Schema stays provider-neutral.** Migration 25 already is: `source`, `source_ref`, `known_at`, `known_at_basis`, `pit_grade`, `payload` and
   separate estimate/actual columns, with surprise derived at read time. A provider is an adapter implementing `events.CatalystSource`
   (`fetch` / `normalise`) that declares the strongest basis it can prove; `normalise_checked` refuses a draft that claims more. Swapping
   vendors changes an adapter and a `source` string, never a table. Nothing downstream may branch on a vendor name.

Rejected: using yfinance as PIT; buying a vendor before a trial; coupling columns to one vendor's field names; a single "earnings" table that
upserts; letting a model fill missing estimates, dates or guidance numbers.

### Trade-offs

| Choice | Gain | Cost / risk |
|---|---|---|
| EDGAR spine only | free, PIT-grade timestamps, no licence worry | no consensus, so no *surprise vs expectation* — only actual vs prior period |
| + own first-seen log | revision history from today, no spend | starts empty; slow to accumulate; a meaningful sample needs several quarters |
| + low-cost API | breadth, fast | history may be restated; licence for redistribution often unclear → keep research-only |
| + institutional vendor | true as-of consensus and revisions | cost; contract limits on public/paid redistribution; integration effort |

## 6. The trial that turns claims into evidence (run before any purchase)

For each candidate, on a sample of ≥ 200 symbol-quarters spanning at least 8 quarters (include date moves, a split, a delisting, an ADR):

1. **Report time check.** Compare the source's report timestamp / timing with EDGAR 8-K Item 2.02 acceptance times. Pass = source never later than EDGAR and BMO/AMC agrees with the acceptance-time bucket (allowing for the press-release-precedes-8-K case).
2. **As-of check.** Request the consensus *as of* dates before a known print. Pass = values differ across as-of dates and equal our own first-seen snapshot where we have one. A source that returns the same final number for every as-of date is restated: research-unsafe.
3. **Restatement check.** Re-pull the same quarter a week later. Any silent change in a historical value is recorded as `C` (may revise in place).
4. **Actuals reconciliation.** Compare reported EPS/revenue with SEC XBRL for the same period and state the basis difference (GAAP vs adjusted).
5. **Coverage.** Percentage of our universe with each field; revenue and guidance separately.
6. **Operational.** Rate limit against a full-universe daily refresh, outage behaviour, pagination, auth.
7. **Licence.** Written answer on: storing history, deriving signals, publishing derived numbers to a public Telegram channel, a paid product, and what happens to stored data on cancellation.

The result is a table of observed `pit_grade` per field per vendor. A field earns grade A only if checks 1–3 pass; otherwise it is C or X and
the existing `ml_view` gate excludes it automatically.

## 7. Data-quality and leakage risks specific to this domain

* **Date drift:** a rescheduled report overwrites the original date in an upsert store. Mitigation: append-only revisions (M25), first-seen log.
* **Restated actuals and estimates:** vendors correct history in place. Mitigation: grade C, payload hash per revision, restatement check.
* **GAAP vs adjusted EPS:** surprise computed across mixed bases is noise. Mitigation: basis recorded; surprise computed only when both sides share it.
* **Fiscal-period alignment:** `fiscal_period` labels differ across vendors and for non-calendar fiscal years. Mitigation: store the vendor label verbatim plus a normalised period end date; never match on label alone.
* **Split / share-count changes:** per-share estimates move with splits. Mitigation: record the split basis; do not compare per-share values across a split boundary without adjustment.
* **Time zone and 'after the close':** a date without a time is ambiguous; AMC on day D is information on D+1's session. Mitigation: timing is explicit, session semantics follow `fwd_v1` (decision at close).
* **Survivorship:** vendors drop delisted symbols. Mitigation: coverage reported per field; absence is not zero.
* **Look-ahead through "latest":** any join to a "current" value. Mitigation: all reads go through `visible_as_of(cutoff)` / `ml_view`.

## 8. What is implementation-ready now vs what needs a decision

* Ready now (no vendor, no purchase): the EDGAR fact contract + mapping (`filing_contract.py`, tested), the M25 event store, `events.ml_view`, the
  first-seen log design (needs a migration number at merge time).
* Needs an owner decision: whether to approve a SEC User-Agent contact address; which paid source(s) to trial; the licence questions in §6.7.
* Needs production activation (separate, owner-authorised, after S11): applying migrations 24/25 and any later one, scheduling any collector.
