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
