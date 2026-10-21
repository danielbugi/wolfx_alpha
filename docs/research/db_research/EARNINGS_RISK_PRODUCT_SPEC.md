# Earnings Risk — product specification (and point-in-time data audit)

Status: specification for owner review, 2026-10-21. **No implementation.** No migration, collector, bot or production change.

## 1. Purpose and boundaries

**What it shows.** For every stock the user sees in the Screener, Stock Intelligence or Watchlist: *when the next earnings report is, how sure we are of that date, and that the period around it carries larger price-gap risk.*

**Why.** Notebook 02 measured trades with a report in the first 5 sessions after entry. They lose 1.5R or more about 16 times as often as trades with no report nearby (5.7% vs 0.35%), with no change in average return. The value is risk awareness, not direction.

**Not in scope:**
- No up/down prediction, score, "buy/sell/avoid" wording, or automated trading decision.
- No new data source.
- No bot or channel copy in this phase.

**Copy rules that apply:**
- The advice-word guard (`CHANNEL_VOICE_AND_COPY.md`, `BANNED` list).
- No internals in user copy: no universe size, method or system numbers. User copy states facts ("Earnings in 3 trading days") and a neutral risk label. Measured base rates appear only in owner and internal views.

## 2. Data contract (one read model, used by all three surfaces)

| Field | Meaning | Source today | Unknown state |
|---|---|---|---|
| `next_report_date` | Next report date on or after today's session | `earnings_calendar` (vendor) | `null` → "No date on file" |
| `sessions_to_report` | Trading sessions from the current session to the report. Counted with the **market calendar**, never wall-clock days (session invariant). | Derived | `null` |
| `calendar_days_to_report` | Calendar days, for display only | Derived | `null` |
| `release_timing` | BMO / AMC / unknown | **Not stored** (the updater drops the time of day, §6) | Always `unknown` until fixed |
| `date_confidence` | `high` / `medium` / `low` / `unavailable` (rules in §3) | Derived | `unavailable` |
| `date_conflict` | Two different upcoming dates on file for the same report | Derived (§6: 274 symbols today) | `false` |
| `source_label` | User-facing: "Earnings calendar"; internal: vendor name | Static | — |
| `fetched_at` / `freshness_days` | When the date was last refreshed | `earnings_calendar.fetched_at` (last fetch only) | `null` |
| `last_report_date`, `sessions_since_report` | Most recent past report | `earnings_calendar` | `null` |
| `risk_window` | `in_1_5`, `in_6_10`, `in_11_20`, `none_within_20`, `just_reported` (≤ 3 sessions ago), `unknown` | Derived | `unknown` |

**Read-time rules:**
- Never fill a missing date with a projection in user views. The "last report + 91 days" projection is accurate to ±3 sessions only 54% of the time, so it may appear only as an internal, labelled "estimated from history" value.
- A past-dated row with no actual EPS is a probable leftover from a moved date (701 such rows). Never show it as "reported".

## 3. Date confidence (deterministic, explainable)

| Level | Rule |
|---|---|
| `high` | Exactly one upcoming date on file, refreshed ≤ 7 days ago, and ≤ 20 sessions away |
| `medium` | One upcoming date, refreshed 8–14 days ago, or > 20 sessions away |
| `low` | Conflicting upcoming dates (`date_conflict`), or refreshed > 14 days ago (the updater's own refresh cadence is 14 days) |
| `unavailable` | No upcoming row (153 symbols with a sector have no earnings rows at all) |

When there is a conflict, show the date from the most recently fetched row and mark it "date changed recently". Never average two dates.

## 4. Surfaces

| Surface | Behaviour |
|---|---|
| **Screener** | Column "Earnings" with `sessions_to_report` and a badge for `in_1_5` / `in_6_10`. A **user-controlled** filter, "Hide earnings within N sessions", off by default. The default ranking is unchanged. |
| **Stock Intelligence** (stock page) | An earnings card: date, sessions remaining, timing (`unknown` for now), confidence, freshness, last report and its surprise (if any). A neutral line: "Price gaps are typically larger around earnings." Earnings markers on the chart already exist. |
| **Watchlist** | Badge per row for `in_1_5` / `in_6_10`, plus an "Upcoming earnings (next 10 sessions)" strip sorted by date. |
| **Earnings page / dashboard card** | Already live. They adopt the same read model so all surfaces agree. |

**Display states:**
- **Unavailable / unknown:** an explicit "—" with a tooltip "No earnings date on file" or "Release time not known". Never "0" and never blank-as-safe.
- **Stale:** a grey badge and "date may have moved".
- **Conflict:** an amber note "date changed recently".

## 5. Delivery outline (later, each step separately approved)

1. **Backend.** One read-only endpoint (or extend the existing earnings service) returning §2 for a list of symbols. Conflict and confidence are resolved at read time. Unit tests for each state in §3/§4. Session counts come from the existing market-calendar logic.
2. **Frontend.** The three surfaces reuse one formatter and badge component. Tests cover every state, including unknown and conflict.
3. **Owner view.** An internal panel with the measured base rates (Notebook 02 Track 1) and the data-quality counters (conflicts, stale, missing).

**Acceptance criteria:**
- All three surfaces show identical values for the same symbol.
- Every unknown state renders explicitly.
- No advice wording (the guard test passes).
- `sessions_to_report` matches the market calendar across a weekend and a holiday.

## 6. Point-in-time data audit

**What exists today:**

| Item | State | Point-in-time value |
|---|---|---|
| `earnings_calendar` | Upsert on `(symbol, report_date)`; `fetched_at` **overwritten** every fetch; release **time of day discarded** (`idx.date()` in `earnings_calendar_updater.fetch_symbol`) | None for history. All 61,265 historical rows were fetched after the event. A moved date leaves the old row behind (274 symbols have two upcoming dates 1–7 days apart; 701 past rows have no actual). |
| `source_observation` + `source_poll` (migration 27) | **Applied in production, 0 rows.** Append-only, hash-chained, DB-stamped `observed_at`, no back-dating. Explicitly designed for `earnings_date` / `eps_estimate`. | Exactly the needed first-seen log |
| `market_intelligence/first_seen.py` + `first_seen_store.py` | Pure contract + store, tested; used today only by the scratch-validated SEC feed (`edgar_store`) | Reusable as is |
| `market_event` / `market_event_revision` (migration 25) | Applied, 0 rows; has `session_timing` (BMO/AMC/INTRADAY/UNKNOWN), `known_at`, `pit_grade` | Suitable for confirmed report events later |
| `sector_observation` | Applied and **populated** (an observed change log in production) | A working precedent for this pattern |

**Can schedule changes be recorded with the existing infrastructure? Yes, without a migration.** Migration 27 already exists in production for exactly this purpose. What is missing is only a writer.

**Minimum viable forward-only capture (proposal; needs separate approval as a collector change):**
1. In the existing earnings refresh, after each symbol's fetch, write one observation per symbol to `source_observation`:
   - series = (vendor, `earnings_schedule`, symbol, period `NULL`)
   - value = the verbatim upcoming entries **including the time of day**, plus the EPS estimate
   - Unchanged values write nothing.
2. Write one `source_poll` row per run, listing the symbols polled. That proves "still current at T".
3. Stop dropping the time of day: keep it in the observation value. `earnings_calendar` itself can stay as it is.
4. Forward-only: no backfill path exists by design. History before activation stays labelled retrospective.

**Open checks before that change:**
- Which database role the pipeline uses, and whether it has INSERT on the two tables. The research roles script treats them as append-only.
- Whether the vendor timestamp is reliably 16:00 or 09:30 ET style (verify on a sample before relying on BMO/AMC).

**Payoff:** after about one earnings season (around 60 sessions) we can test the *known-at-entry* version of the earnings filter. Today only the retrospective one can be tested. It would also make `release_timing` real for the product.
