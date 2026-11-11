# Stage 2 cutover readiness report — window Tue 2026-10-27, 12:00–14:00 Asia/Jerusalem

Status: **PREPARED. Awaiting the owner's final GO.** Nothing has been re-pinned. Prepared 2026-10-23, read-only against production except that the new image was pulled into the VPS image cache. Procedure: [STAGE2_MECHANISM_REPIN_RUNBOOK.md](STAGE2_MECHANISM_REPIN_RUNBOOK.md).

**Time note:** Israel moves to winter time on 2026-10-25 (UTC+2). The window is therefore **10:00–12:00 UTC**.

## 1. What the window changes (and nothing else)

One mechanism image re-pin, to an image **derived** from the currently pinned image by digest, with exactly 8 reviewed files.

| # | File in the image | From | Behaviour change |
|---|---|---|---|
| 1 | `automation_pipeline.sh` | Stage 2 (approved 2026-10-15) | Step 8 becomes `run_optional_step` (a failure warns, the pipeline continues) |
| 2 | `mechanism/data_updaters/quarterly_fundamentals_updater.py` | Stage 2 | `--time-budget-seconds 2700` (hard limit 3300 s), stalest-first ordering, one `QUARTERLY_SUMMARY` line |
| 3–4 | `test_quarterly_budget.py`, `test_quarterly_ordering_db.py` | Stage 2 | Tests only |
| 5 | `mechanism/alerts/send_earnings_today.py` | PR #55 (owner-approved 2026-10-23) | The "reports today" post reads only **current** earnings rows. It is inert while `donchian-earnings-today.timer` stays disabled. |
| 6–7 | `mechanism/alerts/earnings_current.py`, `earnings_today_query.py` | PR #55 | New modules, imported only by #5 |
| 8 | `mechanism/alerts/tests/test_earnings_today_current.py` | PR #55 | Tests only |

**Unchanged:**
- **Code:** the screener, signal-ledger writer, evaluator, forward collector, research capture, market intelligence and ML gate. The image's other ~35,165 files are byte-identical to the pinned image (§3).
- **Pins and services:** the paper tracker (own pin), the bot (own pin), the backend (own release), all timers, and the database schema (no migration).

## 2. Pin matrix

| Pin / file | Current production | Proposed after the window | Rollback |
|---|---|---|---|
| `CURRENT_MECHANISM_SHA` (pipeline, collector, channel senders) | `5c511899505a` · `sha256:be0ec3ce31176dcb1b7ee7713765705b64d0b10e79b05ce784721b41b0cc1760` | **`46b8b8ae0342` · `sha256:2beafa741aae2c5f9adb5aeded8d4c9452d6d84f3c360a4788816473275536f3`** | `stage2_repin.sh rollback <state dir>` restores `5c511899505a` |
| `FORWARD_COLLECTION_ARMED` | `5c511899505a` | `46b8b8ae0342` (armed **by hand** by the operator, then `collector-check`) | The operator writes `5c511899505a` back (the tool prints the command), then `collector-check` |
| `CURRENT_PAPER_SHA` / `PAPER_TRACKER_ARMED` | `45c2ac39249d` / `45c2ac39249d` | unchanged | — (never touched by the tool) |
| Bot container | `mechanism:575fa5dc0599` · `sha256:5ce890882a53…` | unchanged | — |
| Backend `CURRENT_SHA` | `b8b706d33bab` · `sha256:ea814649898d…` (`PREVIOUS_GOOD_SHA=c6827ff0ff76`) | unchanged | Own `rollback.sh` (PR #54, installed) |

The Stage 2-only image `0384c9b91c63` (`sha256:4a1232…`) remains in the cache. It is a fallback if the owner decides to ship Stage 2 **without** the earnings fix.

## 3. Final derived image: build and test report

| Item | Result |
|---|---|
| Source | `release/stage2-quarterly-earnings` @ `46b8b8ae0342…` (PR #59, draft, base `release/stage2-quarterly-minimal` @ `0384c9b`). The 5 PR #55 files are byte-identical to `main@292192e`. |
| CI on that exact head | **6/6 green**, including `db-bootstrap-integration` with `test_earnings_today_current.py` added |
| Build | CD `build-and-push` on that ref, run 39911780554. The `deploy` job was refused by the `production` environment (non-main ref, no steps run), as for the original Stage 2 build. |
| Image | tag `46b8b8ae0342`, digest `sha256:2beafa741aae2c5f9adb5aeded8d4c9452d6d84f3c360a4788816473275536f3`, 23 layers (the 15 of the pinned image + 4 Stage 2 + 4 earnings) |
| Full file diff vs the pinned `5c511899505a` (every file, sha256, `--network none`) | **Exactly the 8 files of §1** (3 modified, 5 added). Nothing else differs. |
| Diff vs the approved Stage 2 `0384c9b91c63` | Exactly the 4 earnings files |
| The 4 earnings files in the image vs the repo blobs | sha256 identical |
| Mechanism suites run **inside the image** against an isolated scratch Postgres (8 suites, 37 min) | **New: 3,737 passed / 91 failed / 171 errors. Pinned image, same command: 3,720 / 91 / 169.** The 91 failures and 169 errors are identical in both. They are environment-only: tests needing repo files that aren't shipped in the image (`docs/` evidence, `docker-compose.yml`, `deploy/db`, `backend/`). The +17 passes are the 5 earnings and 12 quarterly-budget tests. The +2 errors are `test_quarterly_ordering_db.py`; with the repo's `docker-compose.yml` and `deploy/db` mounted read-only, **both pass in the image**. With `backend/` mounted, the drift test passes too (6/6). |

## 4. Rehearsals (isolated scratch copy; production never touched)

**Scratch:** the 2026-10-22 08:50Z nightly dump (sha256 OK), restored into a throwaway `postgres:16-alpine` on its own Docker network and volume (56 s, 0 errors), then removed.

| Rehearsal | Result |
|---|---|
| **Earnings post dry-run**: old image vs new image, `load_todays_reporters` for 10-22 to 10-30 (7 trading days) | New = old **minus 267 names, plus 0** (e.g. AAPL is no longer listed for its obsolete 10-29 date; its current date is 11-02). **All 267** removed rows were verified by raw SQL as not in the symbol's latest fetch and never reported; 0 had a reported EPS. |
| **Earnings post CLI**, `send_earnings_today.py --date 2026-10-23` (no `--send`, empty Telegram token, `PROD_SENDING_ENABLED=0`) | Printed the post (16 names) and `DRY RUN - nothing was sent.` |
| **Quarterly cohort night**: scratch aged by 26 days (3,075 due), new image, `--time-budget-seconds 150` | 42 processed in 153 s, clean stop. `QUARTERLY_SUMMARY {"stopped_by_budget": true, "symbols_due": 3075, "symbols_processed": 42, "symbols_remaining": 3033, "symbols_failed": 0}`. At the production budget of 2,700 s that is about 740 a night, so the 11-07/08 cohort clears in about 4–5 nights, inside the 3,300 s hard limit. |
| **Window tool `preflight`** (installed copy = repo sha `064282ba…`), new tag + digest, 2026-10-23 07:22Z | **ALL PASS (14/14)**: pin = rollback image, arming = pin, tracker pin and arming equal, new digest equal, rollback image present, no service running, all locks free, no timer within 30 min, gate = no new session, 56 GB free, backup helper sha256 trusted. |

## 5. Database backup and restore status

- **Nightly backups** run at 11:45 Jerusalem. The latest is `20261022T084854Z`: 668 MB dump with a sha256 file, unit result `success`.
- **Restore check (2026-10-23):** sha256 `OK`; full restore of all **75/75 tables**.
  - 50 tables have counts identical to production.
  - The 25 that differ all **grew**, consistent with one night of pipeline and collector writes after the dump (one session in digest, capture, regime and universe; +3,055 bars). No table shrank.
- **In the window:** `stage2_repin.sh backup` takes a **fresh** dump with its own restore check before `apply` (runbook §5).

## 6. Timers and services (2026-10-23)

| Timer | State | Schedule | Image |
|---|---|---|---|
| `donchian-pipeline` | enabled | 17:45 and 19:15 America/New_York | mechanism pin |
| `donchian-forward-collection` | enabled | 06:45, 08:15 Jerusalem | mechanism pin (arming must equal it) |
| `donchian-paper-tracker` | enabled | 09:30, 11:30 Jerusalem | tracker pin |
| `donchian-nightly-backup` | enabled | 11:45 Jerusalem | — |
| `donchian-signal-ledger-eval` | enabled | 03:30 Jerusalem | mechanism pin (channel-sender) |
| `donchian-discontinuity-scan` | enabled | 06:20 Jerusalem | mechanism pin |
| `donchian-firstlight1-prices` | enabled | 05:00 Jerusalem | mechanism pin (via `run_channel_sender.sh`) |
| `donchian-postmarket-retry` | enabled | 23:45 + 00–05 every 20 min + 06:00 Jerusalem | mechanism pin |
| `donchian-notice-evening` | enabled | 20:00 Jerusalem | mechanism pin |
| `donchian-notice-midday` | **disabled** (changed outside this preparation after 2026-10-22; not touched here) | 12:00 Jerusalem | — |
| `donchian-earnings-today` | **disabled** (2026-10-22, owner-approved; stays disabled) | 10:00 Jerusalem | — |

**On 10-27 (Jerusalem):**
- The 11:30 tracker fire and the 11:45 backup run before the window.
- The midday notice no longer fires.
- The next mechanism-pin job after the window is the pipeline at 17:45 New York (23:45 Jerusalem). It is the first real use of the new image.
- Run `preflight` from about 12:05, after the backup has finished, as the runbook says.

## 7. Expected impact and failure conditions

**Expected:**
- **Normal nights:** the step-8 log shows one `QUARTERLY_SUMMARY` line with about 116–136 symbols due and `stopped_by_budget: false`.
- **From 11-07/08:** `stopped_by_budget: true` for about 4–5 nights, then back to normal.
- **Earnings post:** no visible change while its timer stays disabled.

**Hard stop / rollback criteria** (any one triggers runbook §6 rollback, or no `apply`):
1. `preflight` reports any FAIL in the window. Stop, don't override.
2. The fresh backup's checksum or restore check fails. Stop before `apply`.
3. `collector-check` doesn't exit 0 after arming. Roll back.
4. `verify` reports changed row counts beyond the expected writers, a recreated container, a changed tracker pin or bot tag, or a missing re-arm. Roll back.
5. The first pipeline run on the new image (10-27 23:45 Jerusalem) exits non-zero in a **critical** step, or no `QUARTERLY_SUMMARY` line appears. Roll back the next morning, using the same tool and the saved state dir.
6. Any unexpected change to trading or research outputs (ledger rows, capture, regime, RS) compared with the previous night's pattern. Roll back and investigate.

**Rollback cost:**
- **Pin:** a pin and arming file restore with `collector-check`, in seconds. Both images are cached.
- **Data:** nothing to undo. Step 8 only upserts `quarterly_fundamentals`, and those upserts are correct refreshes. The earnings post writes nothing while disabled.

## 8. Not included (needs its own decision)

- Re-enabling `donchian-earnings-today.timer`: a separate approval after the window and a dry-run on the live pin.
- Any other channel or content change, for example the "Market Daily" work (PR #49). It isn't in this image, and adding it would need a new image and re-validation.

## 9. Recommendation

**GO for 2026-10-27, 12:00–14:00 Jerusalem, subject to:**
- `preflight` ALL PASS in the window;
- the fresh backup and restore check;
- the runbook's manual arming and `collector-check`.

Every gate measured so far passed, the image diff is exactly the 8 approved files, and rollback is a single pin restore with both images cached.

**Fallback:** if the owner prefers a pure Stage 2, the original `0384c9b91c63` image is still valid and cached. Run the same procedure with that tag and digest.
