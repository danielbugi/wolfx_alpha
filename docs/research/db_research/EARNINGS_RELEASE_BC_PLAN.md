# Earnings Releases B and C — implementation plan (and the earnings-today timer)

Status: **plan for owner approval**, 2026-10-22. Nothing here is implemented, activated or deployed.
- **Release A** is draft PR #50.
- **Evidence:** read-only production checks; a throwaway local PostgreSQL 16 cluster in the scratchpad (not the Windows dev database) with migration 27 applied; a 5-symbol vendor probe.

## 1. The earnings-today timer (Track A step 3): can it be paused on its own?

**Yes.**

| Item | Value (VPS, `systemctl cat` / `show`, read-only) |
|---|---|
| Timer | `donchian-earnings-today.timer`: `OnCalendar=*-*-* 10:00:00 Asia/Jerusalem`, `Persistent=true`, `RandomizedDelaySec=60`, `WantedBy=timers.target`. Enabled, active (waiting). Last trigger 2026-10-22 07:00:19 UTC, result success. |
| Service | `donchian-earnings-today.service`: `Type=oneshot`, static. `ExecStart=/opt/donchian/scripts/run_channel_sender.sh mechanism/alerts/send_earnings_today.py --to prod --send`. `Requires/After=docker.service`. `TimeoutStartSec=15m`. |
| Dependencies | The timer triggers only this service. **Nothing requires, wants or binds to either unit** (reverse dependencies: only `timers.target`). No `OnFailure`. No other unit file mentions it. |
| Shared parts | It shares only the mechanism image (`CURRENT_MECHANISM_SHA`) and the channel-sender compose service with other jobs. Pausing it changes neither. |
| Idempotency | A per-(target, day) state file (`already_sent`) plus `market_calendar.is_trading_day`. It does **not** use the `telegram_post_delivery` atomic claim. |

**Pause** (operational change; needs your explicit approval; not done):
```bash
sudo systemctl disable --now donchian-earnings-today.timer      # stops future 10:00 runs; the static service is not running
systemctl list-timers donchian-earnings-today.timer --all       # verify: no NEXT time
```

**Rollback:**
```bash
sudo systemctl enable --now donchian-earnings-today.timer
systemctl list-timers donchian-earnings-today.timer             # verify NEXT = next 10:00 Asia/Jerusalem
```

**Caveat (`Persistent=true`).** If a scheduled 10:00 was missed while the timer was disabled, re-enabling triggers **one immediate catch-up run**. On a trading day with no post yet, that sends the day's "reports today" list at the moment of re-enabling.
- No duplicate is possible, because the state file blocks a second send that day.
- To keep the 10:00 timing, re-enable on a trading morning **before 10:00 Jerusalem**. The catch-up run then sends that morning's list, and the 10:00 trigger finds it already sent.
- Alternatively, re-enable after the US close, when the post would be pointless. In that case, check whether the catch-up run sent anything.

## 2. Release B — preserve release time and record first-seen observations

### 2.1 Verified compatibility (throwaway Postgres + migration 27 + real vendor responses)

| Check | Result |
|---|---|
| Schema | Migration 27 applies cleanly. Observations need no schema change: `earnings_schedule` (one series per symbol) and `earnings_report` (one series per symbol and report date) fit `(source, dataset, subject, period_key, value)`. |
| Idempotency | Run 1 wrote 130 observations for 5 symbols. **Run 2 with the same response wrote 0** (130 unchanged). |
| Reschedule | Moving one date wrote **exactly 1** new link. The chain has 2 rows and `verify_chain` reports no problems. |
| Immutability | `UPDATE` is refused by the trigger (`IntegrityConstraintViolation`), even for the owner role. |
| Validation | An invalid subject is refused before anything is written (`FirstSeenError`). |
| Poll record | One `source_poll` row per run, with subjects polled and new-observation counts (130, then 0). |
| Failure isolation | `first_seen_store.record_run` writes on **the cursor's own connection and transaction**. The updater must therefore call it on a **separate connection**. Verified: the caller's work on the other connection is intact. |
| Volume | About 25 vendor rows per symbol on the first run (about 75k `earnings_report` rows for the universe), then only changes. |

### 2.2 Implementation (one PR, flag **off**)

| Change | Where | Notes |
|---|---|---|
| `observations_from_vendor_response(symbol, df, fetched_on)` (pure) | `mechanism/market_intelligence/first_seen.py` | Built from the **vendor response**, never from the table, so leftover rows can never be recorded. `earnings_schedule` = `{"entries": [{"date", "time_et"}]}` for dates on or after `fetched_on`. `earnings_report` = `{eps_estimate, eps_actual, surprise_pct, time_et}`. The source name is distinct from the old table adapter. |
| Keep the time | `earnings_calendar_updater.fetch_symbol` | Add `time_et` (`HH:MM` New York) to the in-memory rows. **`earnings_calendar` and its upsert stay byte-identical**: no column, no migration. |
| Gated write | `earnings_calendar_updater` (end of run) | If `EARNINGS_FIRST_SEEN_ENABLED=1` (default unset = off), use one **separate** connection. Collect observations per symbol, then call `record_run(apply=True)` with one `source_poll` row listing the subjects that answered. Status is `partial` when any symbol failed. Every exception is logged and swallowed, and step 7's exit code is unchanged. |
| Read-time timing | Release C read model | Before 09:30 New York = BMO. 16:00 or later = AMC. 09:30–16:00 = during market. **15:00 on a future date with no actual = unconfirmed** (18 of 61 future rows in the 60-symbol probe). Missing = unknown. Never stored as a derived label. |

**Tests:**
- **Adapter (pure):** canonical values; a leftover table row is absent; dates before `fetched_on` are excluded from the schedule; NaN becomes None.
- **Plan level:** an identical response gives 0 appends; a reschedule gives exactly 1; a time change (15:00 → 16:00) gives 1; a new actual gives 1 on that report's series.
- **Gating:** flag off means **zero** calls to `record_run` (fake). Flag on means one call with all symbols and one poll. An exception in `record_run` still leaves the updater's return value and upserts unchanged.
- **Scratch DB:** extend `market_intelligence/tests/test_first_seen_db.py` (self-skips; runs in `db-bootstrap-integration`).
- **Guards:** no repo file sets `EARNINGS_FIRST_SEEN_ENABLED=1`, and the activation-neutral regex guard stays green.

**Activation (separate approval, after merge):**
1. Confirm the role used by pipeline step 7 has INSERT on `source_observation` and `source_poll`. `donchian_app` has SELECT and INSERT; the role step 7 uses is still to be confirmed.
2. Dry run against production with `apply=False`; print the plan counts.
3. Set the flag for one manual run.
4. Run `verify_chain` over the new series.
5. Leave the flag on.

**Rollback:** unset the flag. Written rows are append-only facts and cannot be deleted (ALWAYS-enabled triggers; `rollback_24_31.sql` drops migrations 24–31 together and refuses while rows exist). Nothing reads them until Release C.

**Coupling:** the updater runs in the **pinned mechanism image**. Release B therefore reaches production only with a mechanism re-pin, after Stage 2, and the flag still stays off until step 3 above.

## 3. Release C — unified earnings read model

### 3.1 One read model for all surfaces

`backend/services/earnings_read_model.py` exposes one function, `earnings_for(symbols, session)`, built on Release A's `earnings_current` CTE. Release B's `source_observation` chain adds timing and the previous date where available. Endpoints:
- `GET /api/earnings?symbols=…` (new, read-only, authenticated).
- `/api/stock/{symbol}/earnings` reads the same model. The live vendor call stays only as a **labelled fallback** when the table has no row for the symbol.

### 3.2 Fields

| Field | Definition | Unknown state |
|---|---|---|
| `next_report_date` | First `is_current` date on or after `session` | `null` |
| `sessions_to_report` | Market sessions from `session` to the date (existing market-calendar logic); never wall-clock | `null` |
| `release_timing` | `BMO` / `AMC` / `during_market` / `unconfirmed` / `unknown` (§2.2) | `unknown` |
| `date_confidence` | §3.3 | `unavailable` |
| `date_changed_recently` / `previous_date` | A superseded row exists in the same quarter (Release A), or the schedule chain changed within 30 days (Release B) | `false` / `null` |
| `fetched_at`, `freshness_days` | Newest fetch for the symbol; age in days | `null` |
| `schedule_complete` | Release A's `latest_fetch_complete` | `null` |
| `last_report_date`, `last_surprise_pct` | Last current date on or before `session`, with its stored surprise | `null` |
| `source_label` | User copy: "Earnings calendar". Internal: vendor + table | — |

### 3.3 `date_confidence`

| Level | Rule (deterministic) |
|---|---|
| `high` | One current upcoming date, `schedule_complete`, fetched ≤ 7 days ago, ≤ 20 sessions away, `release_timing` is BMO or AMC |
| `medium` | As high, but the timing is unconfirmed or unknown, **or** it was fetched 8–14 days ago, **or** it is > 20 sessions away |
| `low` | `date_changed_recently`, **or** `schedule_complete` is false, **or** fetched > 14 days ago, **or** two current upcoming dates (the 8 residual symbols) |
| `unavailable` | No current upcoming row |

### 3.4 Surfaces (no direction, no advice wording; `BANNED`-list guard applies)

| Surface | Shows |
|---|---|
| **Screener** | "Earnings" column (`sessions_to_report` plus a badge for ≤ 5 / 6–10 sessions), a confidence dot, and a user-controlled filter "hide earnings within N sessions" (off by default). Ranking is unchanged. |
| **Stock Intelligence** | Earnings card: date, sessions, timing, confidence, freshness, "date changed" note with the previous date, last report and its surprise |
| **Watchlist** | Row badge plus an "Upcoming earnings (next 10 sessions)" strip |
| **Earnings page / dashboard card** | Same model, so every surface agrees. This replaces today's merge of board dates with live per-symbol calls. |

**States:**
- Unknown or unavailable: an explicit "—" with a reason tooltip.
- Stale (fetched > 14 days ago): grey "may be out of date".
- Date changed: amber "date changed recently".

**Tests:**
- Every confidence branch, including the 7/14-day and 20-session edges, and a weekend/holiday session count.
- A **contract test** that board, stock page and earnings endpoint return the same date for the same symbol.
- Frontend: snapshot/unit tests of each state, and the advice-word guard.

**Rollback:** previous backend image and previous Vercel deployment. No data is involved.

**Dependencies:**
- **Needs:** Release A merged. It can ship before Release B with `release_timing = unknown`.
- **Doesn't need:** the mechanism re-pin.
