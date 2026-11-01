# Fundamental Inflection Engine

Detects meaningful improvements in company fundamentals and shows how much of it the market has already priced in. Code: `mechanism/inflection/`. Tables: migration 40.
API: `/api/inflection/{events,summary,stock/<symbol>}` (read-only). **Research signals only.** Nothing here is labelled predictive; whether inflections carry forward excess returns is a Quant
Lab question (see `docs/research/strategy_lab/`), and the Lab's verdict, not this page, is what says so.

## What it computes (method `inflection_v1`, parameters in `config.py`; a change is a new version, never an edit)

| Event type | Fires when (all must hold) | Strength (50 at the threshold, 100 at the strong value) |
|---|---|---|
| Revenue acceleration | revenue YoY between 10% and 200%, YoY growth up at least 5 pp on the previous quarter's YoY, quarter-on-quarter growth positive | acceleration 5 → 20 pp |
| Margin expansion | operating margin up at least 300 bps YoY, margin positive, revenue not shrinking | 300 → 1000 bps |
| Operating leverage inflection | operating income growing at least 2x as fast as revenue, revenue YoY at least 8%, both operating incomes positive | 2x → 5x |
| Profitability turnaround | net income positive and operating income positive after a loss/zero a year earlier, swing at least 2% of revenue | 2% → 10% of revenue; +10 if the previous quarter was also a loss; -15 if cash conversion < 0.5 |
| Free cash flow turnaround | FCF positive after non-positive a year earlier, FCF margin up at least 3 pp | 3 → 15 pp; -15 if cash conversion < 0.5 |
| Improvement with limited repricing | inflection score at least 40 with at least two event types, and the stock beat its sector by no more than 3% over the 10 sessions after the reaction window | the inflection score |

The **inflection score** (0-100) is the transparent weighted sum of the event strengths (revenue 25%, margin 20%, turnaround 20%, operating leverage 15%, FCF 15%) plus a 5% quality bonus for
cash conversion >= 0.8 with positive net income. Its components are stored with every event so the screen shows where a score came from.

## Point in time

* Every feature of a quarter uses only that quarter and earlier ones (a test appends later quarters and requires earlier features unchanged).
* **Availability**: the date the figures were public. The earnings report (earnings calendar, with a reported EPS) in [quarter end + 5, + 130] days; otherwise quarter end + 75 days (a conservative
  assumption, labelled `assumed_lag_75d`); or `first_seen` when this system held the figures earlier than that. Once SEC filings are ingested (migration 38), the filing date replaces the proxy.
* A quarter is used only if `available_at <= asof`; the composite event is dated by the END of its repricing window (it is not knowable earlier). Running with a historical `--asof` reproduces
  what the engine would have said that day.
* Revisions: a restated quarter that changes an event's content (hash) updates it in place, increments `revision_count` and keeps `first_detected_at`; market context never changes the hash.

## Handling of awkward data

Missing fields stay missing (a note says which); growth with a non-positive base is undefined, never extreme; a revenue jump above 60% quarter-on-quarter or 4x year-on-year is flagged as a probable
acquisition or reclassification and produces no event; banks, insurers and REITs get no operating-margin, operating-leverage or FCF events; YoY comparisons cancel seasonality (52/53-week calendars
match within 25 days).

## Known limit (2026-10-31)

The stored quarterly fundamentals come from yfinance and cover only the last 5-6 quarters per company, so almost every event falls in one reporting season and the engine cannot yet be backtested
over a long history. The SEC EDGAR pipeline (migration 38, built, waiting for the operator contact for its User-Agent) gives 10+ years of filed quarters and exact filing dates and removes this limit.

## Running it

`python mechanism/inflection/updater.py` (incremental), `--full`, `--symbols A B`, `--asof YYYY-MM-DD`, `--dry-run`, `--audit`. Pipeline step 8b runs it nightly after the quarterly fundamentals
step as an OPTIONAL step (hard limit 15 minutes; a failure is a warning, not a pipeline failure) and skips cleanly until migration 40 is applied. One log line per run (`INFLECTION ...`) and a row in
`inflection_run_log`.

## Alerts (owner only)

`mechanism/inflection/alerts.py`, pipeline step 8c (optional). **Off until `INFLECTION_ALERTS_ENABLED=1`**; without it the run logs what it would send and sends nothing.

* **Telegram**: one private message per new event of severity `high` (strength >= 75, or composite score >= 60) made public in the last 3 days, strongest first, at most 10 per run (the rest in one summary
  message). Sent to the owner's private chat with the bot (`BOT_OWNER_ID`), never a channel. Each says RESEARCH SIGNAL, not an order recommendation, with the numbers, why it may matter, the public date and
  how it was determined, price reaction, valuation and the stock link.
* **Email**: a daily digest (events detected in the last 26 hours, medium and high) and, on the Friday run, a weekly research report (the week's events, the Quant Lab experiments and their verdicts,
  failure patterns, newly registered hypotheses, what is blocked on data, data-quality and coverage gaps). HTML with a plain-text alternative. The recipient is `INFLECTION_EMAIL_TO` (an address, or `owner`
  for the owner's own login address); unset = no email is prepared. No address is invented.
* **Exactly once**: every alert claims a row in `inflection_alert_delivery` (UNIQUE `dedupe_key`: symbol + quarter + event type for Telegram, date or ISO week for email) before sending; a restated event does not
  alert again; a failed send is retried on a later run, at most 3 attempts, and stays visible as `failed`. Observability: the table itself plus the `INFLECTION_ALERTS` log line of each run.
* Other settings: `INFLECTION_TELEGRAM_MIN_SEVERITY` (default high), `INFLECTION_TELEGRAM_MAX_PER_RUN` (10), `INFLECTION_FRESH_DAYS` (3), `INFLECTION_BASE_URL`, `LAB_REPORTS_DIR` (the weekly research section).

## SEC-sourced run (`inflection_v1_sec`)

`python mechanism/inflection/updater.py --source sec` runs the SAME engine on quarters from the SEC pipeline (`sec_quarter`, migration 38) instead of `quarterly_fundamentals`. It writes its own event set
(`method_version = inflection_v1_sec`, `source = sec_edgar`), so it never overwrites the yfinance-sourced events and the owner alerts (which read `inflection_v1`) are unaffected.

- **Availability** is when the quarter FIRST became public: the earliest filing in `sec_fact` carrying its revenue, operating income or net income. The date is the SEC acceptance date when that filing is in
  `sec_filing` (basis `sec_acceptance`, on the SEC's own US-Eastern clock, never the database session timezone), else the filing date (`sec_filed_date`). A quarter is unknown before that date.
- **Figures** are the latest the SEC pipeline holds (a later restatement overwrites in place), so values are not as-originally-reported; only the availability date is point in time.
- On a 20-company sample: median 35 days from quarter end to availability (90th percentile 59), none before quarter end, history back to 2007; a median 1 day after the yfinance-based availability on shared quarters.
- Not scheduled: nothing in the pipeline runs `--source sec` yet. It needs the SEC updater to have covered the universe first.

## Filing-version-aware (point-in-time) SEC run — `inflection_v2_sec_pit`

`inflection_v1_sec` uses the latest value of every quarter (`sec_quarter`): the *availability date* is point in time, the *figures* are not, because a restatement filed years later overwrites the earlier value.
That is fine for monitoring and **not valid for historical validation**. `inflection_v2_sec_pit` (module `mechanism/inflection/pit.py`, `updater.py --source sec_pit`, pipeline step 8e) fixes that:

- Each company-quarter is evaluated with only the filings that were public at the quarter's own **first-public date** (SEC acceptance date, US Eastern; filing date when the filing row is missing; kind recorded).
  The raw facts of every filing are kept (`sec_fact` is append-only), and the same `normalize()` that builds `sec_quarter` resolves, per period and metric, the newest filing *among those public by then*.
- The prior-year and previous-quarter figures a feature compares against are likewise the ones public at that date (a restatement filed earlier is visible, one filed later is not).
- Provenance on every feature (`quality_notes`): `pit_as_of:<date>`, `prov:q=`, `prov:py=`, `prov:pq=` (accession numbers). A later restatement of the quarter is flagged `restated_since:<metrics>` for information;
  the stored feature and event are never rewritten, so a re-run on unchanged filings is a no-op and a restatement cannot move a historical event.
- Availability is the 10-Q/10-K acceptance date, which can follow the earnings press release (8-K) by days: it is late, never early, relative to the true first-public moment (the safe direction for leakage).
- Namespaces are separate rows (`method_version`), so nothing existing is overwritten: `inflection_v1` (yfinance, restated, not PIT), `inflection_v1_sec` (SEC latest values, **exploratory only**), `inflection_v2_sec_pit` (**confirmation-eligible** once ≥ 80% of mapped filers are ingested, and only on data the hypothesis was not found on).
- First measurement (20-company sample, 1,831 quarters): 160 quarters (8.7%) were later restated in revenue, operating income or net income; 24 of 690 latest-value events (3.5%) differ from the as-published ones.

## SEC vs yfinance event reconciliation (`inflection/reconcile.py`, `reconcile_db.py`)

On the 124 company-quarters both sources cover: 35 events in both, 48 SEC-only, 6 yfinance-only. Every one-sided event gets exactly one cause; none is hidden and no threshold was changed:

| Cause | Events | Meaning |
|---|---|---|
| history_coverage | 23 | yfinance holds 5–6 quarters, so the prior-year / previous-quarter an event compares against is absent there |
| input_missing_in_other | 13 | an input exists in one source only (e.g. yfinance revenue or prior-year revenue null) |
| input_differs | 9 | same input, > 1% apart: free cash flow definition (SEC capex = PP&E purchases only; yfinance subtracts a broader capex, e.g. CAT 5–10% lower), IPGP operating income |
| composite_dependency | 9 | `improvement_with_limited_repricing` follows from the base events and prices |
| unexplained | 0 | |

Availability dates differ by construction (SEC acceptance vs earnings-report date): median +1 day, 90th percentile +20 days, SEC later in 76 of 124 (CAT's 10-Q follows its earnings release by a week or more).
Found by it and fixed: SEC `capex` only mapped `PaymentsToAcquirePropertyPlantAndEquipment`, so Home Depot and Vishay (which report `PaymentsToAcquireProductiveAssets`) had no free cash flow; the fallback tag is now mapped.
Found while building it: the PIT window had to reach ~506 days back (previous quarter *and its* prior year) or revenue acceleration silently lost its base.

## Quant Lab: Data quality tab

`GET /api/lab/data-quality` (read-only, cached 10 min; `backend/services/data_quality_service.py`) serves SEC coverage (`sec_fundamentals_updater.py --coverage`: by status, sector, reporting period, newest filing, missing fields, freshness vs yfinance,
failures / retry backlog), the two reconciliations above, and which event families are exploratory vs confirmation-eligible. The six registered forward-only hypotheses are unaffected.
Foreign issuers are not assumed to have equivalent coverage (20-F/40-F filers have no 10-Q): `no_xbrl` / `not_found` are reported separately, never as failures. 19 universe symbols had no CIK in the SEC ticker file at the last check (e.g. WBD, AVB, EQR, QRVO).
