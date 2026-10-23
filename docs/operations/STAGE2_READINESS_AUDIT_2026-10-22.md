# Stage 2 readiness audit (read-only, 2026-10-22)

Scope: why Stage 2 has not run, the exact pins, whether the prepared cutover is still valid, the quarterly-fundamentals
question, and whether the channel earnings fix can ship without changing trading behaviour.
- **Nothing was executed or re-pinned.** Evidence comes from the VPS (read-only, root and deploy) and the repo at `3fd6e2c`.
- Runbook: [STAGE2_MECHANISM_REPIN_RUNBOOK.md](STAGE2_MECHANISM_REPIN_RUNBOOK.md).

## 1. Why Stage 2 has not run

- **Status in the runbook:** "PREPARED, NOT DEPLOYED. Needs the owner's separate approval."
- **The window passed unused.** The proposed primary window (Wed 2026-10-21, 12:00–14:00 Jerusalem) passed without that approval being given, so the window tool was never run. There is no backup or state directory and no pin-history file for Stage 2.
- **Deadline:** the runbook's fallback window is **Tue 2026-10-27, 12:00–14:00 Jerusalem**. The hard deadline is still **before 2026-11-06**.

## 2. Exact pins (2026-10-22)

| Pin / file | Value | Last changed |
|---|---|---|
| `CURRENT_MECHANISM_SHA` (pipeline, collector, channel senders via `run_channel_sender.sh`) | `5c511899505a` | 2026-10-10 20:12Z |
| `FORWARD_COLLECTION_ARMED` | `5c511899505a` (equal, consistent) | 2026-10-11 09:12Z |
| `CURRENT_PAPER_SHA` / `PAPER_TRACKER_ARMED` | `45c2ac39249d` / `45c2ac39249d` | 2026-10-14 / 2026-10-15 |
| Bot container | `mechanism:575fa5dc0599` (own pin) | 2026-10-11 |
| Backend | `c6827ff0ff76` (`PREVIOUS_GOOD_SHA=662cb38593ed`) | 2026-10-22 |

**Images in the VPS cache, with digests verified:**

| Image | Digest |
|---|---|
| Stage 2 image `0384c9b91c63` | `sha256:4a12328919eb…` (the recorded one) |
| Rollback image `5c511899505a` | `sha256:be0ec3ce3117…` |
| Tracker image `45c2ac39249d` | present |
| Bot image `575fa5dc0599` | present |

## 3. Is the prepared cutover still valid?

**Yes, technically.** Nothing it depends on has changed:
- **The Stage 2 image is built** (a derived image FROM the pinned digest plus 4 reviewed files) and present with the recorded digest.
- **The rollback image is present**, and the pin and arming file are still the consistent old pair the preflight expects.
- **The window tool** (`/root/stage2_repin.sh`, 36 tests) is unchanged since 2026-10-17.

**Re-checks needed on the day** (all part of the tool's `preflight`; none have been run today):
- the 14 checks, including no running donchian unit, all `.lock` files free, and no timer due within 30 minutes;
- the fresh-backup helper's sha256;
- disk space.

**Changed context:**
- The backend is now `c6827ff0ff76`. It is independent of the mechanism pin.
- `donchian-earnings-today.timer` is **disabled** (2026-10-22), so it can't collide with the window.
- 2026-10-25 is a clock change, which is why the runbook already avoids it.

## 4. Quarterly fundamentals: they did not stop

An earlier statement of mine ("last written 2026-10-14") came from `max(created_at)` and was **wrong**.

| Evidence (read-only) | Value |
|---|---|
| `max(updated_at)` | **2026-10-19 23:03Z** |
| Rows touched by day | 10-13: 3,786 · 10-14: 10,949 (the full-cohort refresh) · 10-19: 100 |
| Nightly step-8 summary since 10-14 | "Found 116–136 symbols due"; 0–20 successful; **116 failed every night** |
| The 116 | Closed-end funds and similar (`No quarterly data available for NVA, NXP, OXLC, …`) that never succeed |

**Reading:**
- The updater refreshes a symbol only when it is due; the refresh is gated on earnings dates and staleness.
- After the 10-13/14 full cohort, nothing else is due except the 116 symbols with no quarterly data and the few that just reported. That is expected behaviour, not a stall.
- **The real Stage 2 risk is unchanged:** the next **full cohort is due 2026-11-07/08**. With the current image, the whole universe (~3,055 symbols, ~4.9 s each) runs inside step 8, and the 4 h unit timeout killed the run the last time (10-13).
- Stage 2's `--time-budget-seconds`, stalest-first ordering and `run_optional_step` exist for exactly that night.

## 5. Can the Telegram earnings fix ship without changing trading behaviour?

**Yes, as a derived image.** It can't ship as a rebuild, and it can't ship separately from the mechanism pin.

- **Same pin as the pipeline.** The post runs through `run_channel_sender.sh` on the image in `CURRENT_MECHANISM_SHA`, the pin the pipeline and collector use. Any image change for it is a re-pin of that pin, so it must follow Stage 2's procedure: the arming file must equal the pin, and the window rules apply.
- **Small delta.** At the pinned commit `5c51189`, `send_earnings_today.py` differs from `main` **only** by PR #55. The fix is therefore exactly 3 files:
  - `mechanism/alerts/send_earnings_today.py`
  - `mechanism/alerts/earnings_current.py` (new)
  - `mechanism/alerts/earnings_today_query.py` (new)
- **No trading behaviour.** None of them is imported by the screener, the ledger writer, the evaluator, the collector or the tracker.

**Recommended path (each step needs its own approval):**
1. Extend the Stage 2 derived image with those 3 files: `Dockerfile.stage2` gains 3 `COPY` lines (plus the test file). Then rebuild on the release branch, take a new tag and digest, and get CI green on exactly that content.
2. Run the same window procedure (preflight, backup, apply, manual arm, collector-check, verify) with the new tag. One re-pin delivers both fixes.
3. After the window, a manual `send_earnings_today.py` **dry run** (prints, sends nothing) for the next trading day, compared with the current-row rule. Then a separate approval to re-enable `donchian-earnings-today.timer`.

**Alternatives:**
- Ship the channel fix in a second derived image after Stage 2, with a second window.
- Introduce a separate channel-sender pin, following the PR #11 bot-pin precedent. That decouples posts from the pipeline but is a larger change.

## 6. Required tests, preflight and rollback for the (extended) Stage 2 release

**Before the window:**
1. CI green on the release-branch head that builds the extended derived image: the 6 jobs, including the quarterly budget/ordering tests and `test_earnings_today_current.py`.
2. A full image diff against the pinned image showing only the listed files.
3. The new tag and digest recorded in the runbook's readiness table.

**In the window (runbook §5):** `preflight` (14 checks) → fresh backup with a restore check → `apply` (writes the pin only) → the operator arms by hand → `collector-check` → `verify`.

**Rollback (runbook §6):**
- `stage2_repin.sh rollback <state dir>` restores the pin bytes and prints the command to restore the arming. The operator runs that command, then runs `collector-check` for `5c511899505a`.
- The rollback image `5c511899505a` is present (digest verified above).
- The forward tracker pin and arming, and the bot tag, are never touched.

**After:**
- The nightly `QUARTERLY_SUMMARY` / `OPTIONAL_STEP_WARNING` log check (runbook §7).
- A dry run of the earnings post.
- `donchian-earnings-today.timer` stays disabled until it is separately approved.
