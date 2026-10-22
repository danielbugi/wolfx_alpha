# Earnings data quality — read-only audit and implementation plan

> **Update 2026-10-22.**
> - **Release A is implemented** as draft PR #50 (`fix/earnings-current-dates`). It uses the rule in §2.1, with one refinement: a fetch only counts as *complete* when it lists a date on or after its own fetch day **and** wrote at least 2 rows (or every row the symbol has).
> - **Production picture after the 10-21 full refresh:** 682 symbols had more than one upcoming row. With Release A that drops to 8, all of them symbols whose latest fetch was a single row, kept on purpose. Superseded rows with a reported EPS: 0.
> - **Release B and C details**, the timer audit and the verified `source_observation` compatibility are in [EARNINGS_RELEASE_BC_PLAN.md](EARNINGS_RELEASE_BC_PLAN.md).

Status: **plan for owner approval**, 2026-10-21. Nothing was implemented, written, activated or deployed.
- **Evidence:** read-only production queries (forced `default_transaction_read_only=on`), code on `origin/main` (`f3b88d9`), and a local 60-symbol probe of the same vendor library (no database involved).

## 1. Findings

### 1.1 The 274 conflicting upcoming dates are leftovers from rescheduled reports (100% deterministic)

| Measure (production, 2026-10-21) | Value |
|---|---|
| Symbols with two upcoming dates | 274 (548 rows) |
| Pairs where **exactly one** row came from the symbol's latest vendor fetch | **274 of 274** |
| Pairs where both rows came from the latest fetch (a genuine vendor double) | 0 |
| Gap between the two dates | 1 / 4 / 15 days (min / median / max) |
| Leftover row **earlier** than the current date, so consumers show the wrong date | **132 symbols** (e.g., AAPL: board shows 10-29, vendor now says 11-02) |
| Leftover row later than the current date | 142 |
| Past rows with no actual EPS that the vendor no longer lists | 71 (of 701 past rows without an actual) |

The vendor itself never returns two dates. The older row is simply never refreshed again.

### 1.2 How the updater handles a rescheduled report

`mechanism/data_updaters/earnings_calendar_updater.py`, pipeline step 7:
- `upsert_row` uses `ON CONFLICT (symbol, report_date) DO UPDATE`. A **new date inserts a new row; the old date's row is never removed or marked**. `fetched_at` is overwritten on every refresh, so it means "last confirmed", not "first seen".
- A failed or empty fetch writes nothing. A failure therefore never makes a valid row look stale, which matters for §2.1.
- Re-checks happen when the newest date on file is past, or every 14 days.

**Consumers and their exposure:**

| Consumer | Query | Effect of a leftover row |
|---|---|---|
| `mechanism/alerts/send_earnings_today.py` (public channel post, `donchian-earnings-today.timer`) | `report_date = today` | **Announces a report on a stale date.** Across the 19 production earnings posts (2026-09-23 → 10-21), 62 symbol mentions named a date the vendor no longer lists and on which no EPS was ever reported (e.g., NRIX on 10-08 and 10-12; TBN, NB, AIR, LEVI…). |
| `backend/services/momentum_board_service.py` (board, earnings page, dashboard card) | First `report_date >= CURRENT_DATE` | Shows the earlier leftover date (132 symbols today) |
| `backend/services/discovery_service.py` (Strength Map / discovery) | First `report_date > session` | Same |
| `backend/services/earnings_service.py` → `/api/stock/{symbol}/earnings` | **Live vendor call** with an in-memory cache, not the table | No leftovers, but it is a **third source**, so the stock page and the board can disagree |

### 1.3 Release time (BMO / AMC) is supplied by the vendor and thrown away

`get_earnings_dates()` returns a timezone-aware New York timestamp. The updater keeps only `idx.date()`; the backend service keeps only `strftime("%Y-%m-%d")`.

Probe of 60 random symbols (1,212 rows):

| Rows | Before 09:30 | 09:30–16:00 | 16:00 or later |
|---|---|---|---|
| Past, with actual EPS | 487 (06:00 = 241, 07:00 = 177) | **9** | 646 (16:00 = 541, 17:00 = 93) |
| Future | 30 | 19, of which **18 at exactly 15:00** | 6 |

**Reading:** past times are clean BMO/AMC, with 99% outside market hours. A 15:00 time on a future date is almost certainly a vendor placeholder for an **unconfirmed** time. Treat it as unknown. This is a sample of 60 symbols; re-verify it after the first captured season.

### 1.4 `source_observation` (migration 27) suits forward-only first-seen tracking

| Property | State |
|---|---|
| Applied in production | Yes; **0 rows** in `source_observation` and in `source_poll` |
| Immutability | `*_immutable_row` and `*_immutable_truncate` triggers, enabled **ALWAYS** (`A`); DB-stamped `observed_at` (`*_stamp`) |
| Privileges | `donchian_app`: SELECT + INSERT on both tables. `trading_user` (the bootstrap owner) is what the read-only checks ran as. **Preflight:** confirm which role the pipeline's step 7 connects as. |
| Code | `first_seen.py` (pure: series keys, hash chain, `plan_appends`, `as_of`), `first_seen_store.record_run(cur, observations, run_key, code_ref, apply=False)` (dry-run by default), DB tests exist |
| Existing adapter | `observations_from_earnings_calendar(rows, as_of_date)` builds observations **from the table**. Its upcoming-dates list would record leftover rows as current and carries no time, so it is **unsuitable as is**. A vendor-response adapter is needed (§2.2). |

**Conclusion:** schema, guarantees and store are ready. Only a correct adapter and a gated call site are missing. **No migration is needed.**

## 2. Smallest safe implementation (three independent releases, each separately approved)

### 2.1 Release A — read-side fix (no data change, no writer change)

**One rule, defined once in each image and kept identical by a drift test.**

| Classification | Condition |
|---|---|
| `current` | `fetched_at >= max(fetched_at for the symbol) − 1 hour`, i.e. returned by the latest successful fetch |
| `superseded` | not current **and** `eps_actual IS NULL` (no longer listed, never reported) |
| `history` | not current **and** `eps_actual IS NOT NULL` (aged out of the vendor's 12-date window; still valid) |

Upcoming = `current` rows on or after the session date. Past = `current` or `history`. A `superseded` row is never shown as a date. It feeds only the "date changed recently" flag.

**Where it applies:**
- `send_earnings_today.load_todays_reporters`: add the predicate. **This is the most user-visible fix.**
- `momentum_board_service` and `discovery_service`: use the predicate in their next-date and last-date lateral joins.

**Tests (fixtures, no database):**
- An earlier leftover, a later leftover, an aged-out past row, and a symbol whose latest fetch failed (no new rows, so nothing becomes superseded).
- A same-run millisecond spread (`fetched_at` set per row) stays within the 1-hour tolerance.
- Two symbols sharing a run.
- **Drift test:** the SQL fragment text in `mechanism/` equals the one in `backend/`.
- A DB-level test on the scratch database with the real `earnings_calendar` DDL.

**Delivery and coupling:**
- The backend part ships with a normal manual backend release.
- The channel part runs in the **mechanism image pinned in `CURRENT_MECHANISM_SHA`** (`run_channel_sender.sh`). It can only reach production with a mechanism re-pin, which is coupled to Stage 2 and to the collector arming file. So it waits for Stage 2 and is then included in the next approved mechanism release.
- **Optional interim mitigation (owner decision; ops only, reversible, no code):** pause `donchian-earnings-today.timer` until then. Alternatively, accept the risk: about 3 stale mentions per post-day.

**Rollback:** redeploy the previous backend image or mechanism pin. There is no data to restore.

### 2.2 Release B — preserve release time and capture first-seen (writer, gated, default OFF)

1. **New pure adapter** `observations_from_vendor_response(symbol, entries, fetched_on)` in `first_seen.py`, built from the vendor response (never from the table), with new dataset names so the old adapter's semantics stay unmixed:
   - `earnings_schedule`: one series per symbol, value `{"entries": [{"date", "time_et"}…]}` for dates on or after `fetched_on`. A reschedule, a time change, or a time becoming confirmed is a new link in the chain.
   - `earnings_report`: one series per (symbol, report date), value `{eps_estimate, eps_actual, surprise_pct, time_et}`, so estimate revisions and the arrival of an actual are recorded.
2. **`fetch_symbol` keeps the timestamp** (`time_et` = `HH:MM` New York) in its in-memory rows. `earnings_calendar` itself is **unchanged**: no column, no migration, same upsert.
3. **Gated call:** after a symbol's upsert, if `EARNINGS_FIRST_SEEN_ENABLED=1` (default **off**), call `record_run(..., apply=True)` in its own transaction. One `source_poll` row per run lists the symbols that answered. Any failure there is logged and **never** fails the updater or step 7.
4. **Timing is classified at read time, never stored:**

   | Time (New York) | Classification |
   |---|---|
   | before 09:30 | BMO |
   | 16:00 or later | AMC |
   | 09:30–16:00 | during market |
   | 15:00 on a future date | unconfirmed |
   | missing | unknown |

**Tests:**
- Adapter purity and canonical hashing, including a leftover row present in the table but absent from the response (it must not be recorded).
- Re-running the same response gives 0 appends (`plan_appends`). A reschedule appends exactly one link. A time change appends one link.
- The flag off performs **zero** writes (asserted with a fake cursor). The flag on writes. A writer exception leaves the upsert result unchanged.
- Scratch-database integration using the existing `test_first_seen_db` pattern, plus a guard test that no repo file sets the flag on.

**Activation preflight:**
- Confirm the role step 7 connects as has INSERT on both tables.
- A dry run (`apply=False`) on production that prints counts only.
- One manual run with the flag on.
- `verify_chain` over the new rows.

**Rollback:**
- Unset the flag. The writer stops immediately and nothing else changes.
- **Rows already written cannot be deleted** (ALWAYS-enabled immutability triggers). That is acceptable: they are append-only facts, and nothing reads them until Release C.
- There is no per-table rollback for migration 27. `deploy/db/rollback_24_31.sql` drops migrations 24–31 **together**, including tables that already hold production data. It refuses while any of them has rows unless data loss is approved in writing. So the only realistic rollback is "stop writing", and the rows stay.

### 2.3 Release C — surface confidence, freshness and conflicts (backend read model and frontend)

**One read model for every surface:** board, discovery, Screener, Stock Intelligence, Watchlist, the earnings page and the dashboard card.
- The read model replaces the live vendor call for `/api/stock/{symbol}/earnings`. The live call stays only as a labelled fallback when the table has no row, which removes the third-source disagreement.

**Fields** (per the Earnings Risk spec):
- `next_report_date`, `sessions_to_report` (market calendar)
- `release_timing` (from Release B when available, otherwise `unknown`)
- `date_confidence`, `date_changed_recently` (+ `previous_date`, taken from the superseded row now and from the first-seen chain later)
- `freshness_days` (now − latest fetch), `source_label`

**`date_confidence` rules:**

| Level | Rule |
|---|---|
| high | One current upcoming date, fetched ≤ 7 days ago, ≤ 20 sessions away, time confirmed |
| medium | Time unconfirmed or unknown, or fetched 8–14 days ago, or > 20 sessions away |
| low | Date changed recently, or fetched > 14 days ago |
| unavailable | No current upcoming row |

**Frontend:**
- One badge/formatter component shared by all surfaces.
- Explicit "—" for unknown or unavailable values.
- An amber "date changed" note and a grey "may be out of date" note.
- No direction or advice wording (the guard test applies).

**Tests:**
- Every confidence state, with the edges at 7/14 days and 20 sessions.
- A weekend/holiday session count.
- Contract tests showing the board and the stock page return the same date.

**Rollback:** previous backend and Vercel deployments.

## 3. Recommended order

| Step | Content | Needs |
|---|---|---|
| 1 | Release A, backend part (board, discovery) | Approval, then a manual backend release |
| 2 | Decide the interim channel mitigation (pause the earnings-today timer, or accept) | Owner decision |
| 3 | Release A, channel part, inside the next mechanism release after Stage 2 | Approval of that release |
| 4 | Release C (read model, surfaces), using `unknown` timing until step 5 | Approval, then backend + frontend releases |
| 5 | Release B code merged with the flag OFF; activation later, after the preflight | Two approvals (merge; activate) |

**Explicitly not proposed:**
- Deleting or rewriting `earnings_calendar` rows: destructive, and loses evidence.
- A migration: not needed.
- Any change to quarterly-fundamentals gating: leftover rows don't harm it.
- Any ML work.
