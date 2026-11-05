# Inflection fixes (PR #93 + PR #95): deploy preflight, impact and the stale-event cleanup plan

**Status: PREPARED, NOTHING EXECUTED** (2026-11-05). Every number below comes from read-only queries on production on 2026-11-05 (sessions forced read-only).

**Deploying needs the owner's explicit authorisation, and so does the cleanup.**

## 1. What would be deployed
- **Production today:** mechanism image `b4de88d790b1`, the derived Stage 2 image from `release/stage2-inflection`. It is **not** main: main differs from it by 205 files.
- **Candidate:** commit `3dfc51e` on `release/stage2-inflection-fixes`, which is `b4de88d` plus exactly the PR #93 and #95 files taken from main `8520923`.
  - Every file it touches was identical between `b4de88d` and pre-PR main, so the diff is the reviewed PR diff and nothing else: 8 files, +351 / −22.
  - `mechanism/inflection/tests`: **88 passed, 13 skipped** (database tests) on this branch. Main CI was green on both PRs.
- **Deploy path** ([DEPLOYMENT.md](DEPLOYMENT.md) §2):
  1. Build the image from `3dfc51e`.
  2. Pin it in `/opt/donchian/CURRENT_MECHANISM_SHA`.
  3. The next pipeline run uses it. The bot is unaffected, because the inflection steps run only inside the pipeline.

## 2. Impact (dry-run assessment against production data)
### A. Original-engine events that will be re-dated (availability window 130 → 104 days)
- **140 feature rows on 131 symbols** have an earnings-report date 105–130 days after quarter end. On the first run they fall back to the 75-day assumed lag, or to "first seen" when that is earlier.
- **18 events** are revised in place (`revision_count + 1`; `first_detected_at` is kept):

| IDs | Events |
|---|---|
| 114–117 | ARXS ×4 (one composite) |
| 336 | CVX (composite) |
| 466 | FLXS |
| 575 | HUN |
| 677–679 | KODK ×3 |
| 706 | LITE |
| 861, 862 | NSSC ×2 |
| 925 | OSIS |
| 1108, 1109 | SMCI ×2 |
| 1202 | TWIN |
| 1250 | VZ (composite) |

### B. Alerts
**The old code has already sent 8 misleading Telegram alerts**, all to the owner's private chat:

| Night (UTC) | Events |
|---|---|
| 2026-11-03 | NSSC ×2 |
| 2026-11-04 | HUN |
| 2026-11-05 | KODK ×2, LITE, SMCI ×2 |

Every one is a re-dated event first detected on 2026-10-31. **The bug keeps sending them until the fix is deployed.**
- **After deploy:** all 8 high-severity events now in the 3-day window fail the new rule (detected 2026-10-31, before their stated public date), so none would alert. A genuinely new event needs detection on or after its public date.
- **Duplicates:** the 11 existing delivery rows stay, so nothing already sent is sent again. The cross-version check finds no SEC alerts (none were ever sent).
- **Digest:** it starts from the last sent digest (2026-11-05 00:30 UTC). Today 0 events would be listed and 0 counted as backfill. The 18 re-dated events keep their `first_detected_at`, so they are not "new".
- **Weekly report:** now keyed by the session date, so a Friday session sends it even when the step runs after UTC midnight.

### C. Market-session dates
The run log shows `asof` = UTC wall-clock date: runs 11–16 were stamped 11-03, 11-04 and 11-05 for the sessions of 11-02, 11-03 and 11-04.
- After deploy, steps 8b, 8c and 8e receive `--asof $SESSION_DATE` from the trading-day gate, so `asof` equals the session.
- The SEC point-in-time 200-day window becomes session − 200 instead of today − 200: at most one day's difference.
- A forced run, or one where the gate failed open (no `SESSION_DATE`), keeps today's fallback.

### D. SEC point-in-time stale-quarter guard
- From the next run, quarters first public more than 120 days after quarter end are not evaluated, so **no new stale events are created**. On 2026-11-04 two more were created (QNST, RR).
- Existing rows are **not** deleted by the code. Section 3 covers them.

## 3. Cleanup plan for the stale SEC events (not executed)
**Current stale set, 6 events** (it was 4 on 2026-11-03; it grows each night until the guard is live):

| ID | Symbol | Quarter end | Event | Severity | Public | Days after quarter end |
|---|---|---|---|---|---|---|
| 14826 | DIN | 2025-06-29 | revenue_acceleration | medium | 2026-08-05 | 402 |
| 14875 | DRVN | 2025-12-27 | margin_expansion | high | 2026-05-19 | 143 |
| 14876 | DRVN | 2025-12-27 | fcf_turnaround | high | 2026-05-19 | 143 |
| 15149 | FUBO | 2025-03-29 | revenue_acceleration | medium | 2026-05-06 | 403 |
| 19738 | QNST | 2025-06-30 | profitability_turnaround | low | 2026-08-26 | 422 |
| 19892 | RR | 2026-03-31 | revenue_acceleration | high | 2026-08-07 | 129 |

**Dependencies:**
- The only foreign key to `fundamental_inflection_event` is `inflection_alert_delivery.event_id`, and **0 rows** reference these events.
- The data-quality gate counts features, not events, so it is unaffected.
- SEC events are not alertable, so no alert depends on them.

**Not included:** 286 stale **feature** rows. Most are partial-data comparatives with no event, for example quarters first seen about 400 days late. Many of them come from an ingestion gap: about 168 companies have SEC history only from mid-2025 (XOM has 56 facts, all from one 2026 filing). That is a separate data-quality issue in the SEC updater. Deleting those features is a separate owner decision.

**Order and commands:**
1. **After** the guard is deployed and has run once, so the set cannot grow again, take the targeted backup and record the file's `sha256sum`:
   `psql -v ON_ERROR_STOP=1 -X -v backup_file=/tmp/sec_pit_stale_events_backup.csv -f deploy/db/sec_pit_stale_events_backup.sql`
   - It prints `ids` and `full_row_fingerprint`.
   - **Take the backup right before the cleanup.** Each nightly run refreshes these rows' market context, so the fingerprint changes every night. Today's value is `296523f66ea04d17b1c510d6d4ac2c41`, for IDs `14826,14875,14876,15149,19738,19892`.
2. Confirm that the nightly `pg_dump` from the same day exists ([BACKUPS.md](BACKUPS.md)).
3. Dry run (default):
   `psql -v ON_ERROR_STOP=1 -X -v expected_ids=<ids> -v backup_fp=<fp> -f deploy/db/sec_pit_stale_events_cleanup.sql`
   It refuses if the set or the rows differ from the backup, or if any alert references them. It should print `stale_remaining = 0` and then roll back.
4. Apply: the same command with `-v commit=on`.
5. Verify with a read-only query: no `inflection_v2_sec_pit` event has `available_at - quarter_end > 120`.

**Expected effect:**
- 6 fewer SEC point-in-time events (from 3,245 to 3,239, the count of 2026-11-05).
- No alert or API change; the Lab data-quality counts of features are unchanged.

**Rollback:** `psql -v ON_ERROR_STOP=1 -X -v commit=on -f deploy/db/sec_pit_stale_events_restore.sql < /tmp/sec_pit_stale_events_backup.csv`. This re-inserts missing IDs with their original values; it is dry run by default and idempotent. The fallback is the pg_dump.

## 4. Rollback of the code deploy
1. Put `b4de88d790b1` back into `/opt/donchian/CURRENT_MECHANISM_SHA`. The next pipeline run uses it; there is nothing to restart, because the bot pin is separate and unchanged.
2. **Data effect of a rollback:**
   - The old window re-dates the 18 events back (`revision_count + 1` again).
   - Misleading alerts would resume for any re-dated event not yet sent. Already-sent keys never repeat.
   - Stale SEC events would be re-created on the next 8e run if they had been cleaned up. Restore or leave them; they are harmless to alerts.
3. Nothing in the database schema changes in either direction: no migration is involved.

## 5. Decisions for the owner
1. Authorise the deploy of `3dfc51e` (build, pin, and the first-night check).
2. After the first post-deploy run, authorise the stale-event cleanup (steps 1–5).
3. Decide on the 286 stale feature rows and on investigating the SEC ingestion gap (about 168 companies with history only from mid-2025).
