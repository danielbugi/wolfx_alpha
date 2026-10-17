# Stage 2: deploy the bounded quarterly-fundamentals step (new mechanism image and re-pin)

Status 2026-10-15: **PREPARED, NOT DEPLOYED.** Needs the owner's separate approval. Must be complete before **2026-11-06** (the next full quarterly cohort falls due 2026-11-07/08; see `PIPELINE_FIRST_FIRE_RELIABILITY.md`). Stage 1 (the timer) is already applied and independent of this.

## 1. What changes, and what must not

| Changes | Does not change |
|---|---|
| `/opt/donchian/CURRENT_MECHANISM_SHA` -> new 12-hex tag | the bot (`env/mechanism_image_tag.env` = `575fa5dc0599`, its own pin) |
| `/opt/donchian/FORWARD_COLLECTION_ARMED` -> the same tag (the collector refuses unless the file equals the pin) | the paper tracker (`CURRENT_PAPER_SHA` = `45c2ac39249d`, `PAPER_TRACKER_ARMED`; image `45c2ac39249d`) |
| the image the pipeline, post-market retry, channel-sender (notices) and discontinuity scan run | any timer, unit or wrapper file; the database; any immutable evidence |

Wrappers that read `CURRENT_MECHANISM_SHA` (checked on the box): `run_pipeline.sh`, `run_postmarket_retry.sh`, `run_channel_sender.sh`, `run_discontinuity_scan.sh`, `run_forward_collection.sh` (the only one with an arming file). `run_paper_tracker.sh` reads its own pin.

## 2. Which image: a DERIVED image (pinned image + only the reviewed files), not a rebuild and not "main"

The pinned image `5c511899505a` (digest `sha256:be0ec3ce31176dcb1b7ee7713765705b64d0b10e79b05ce784721b41b0cc1760`) was built from the R1-R3 commit. Two things make a fresh build a bigger change than the quarterly fix:

* Between that commit and today's `main` there are 66 changed files under `mechanism/` and `ml_training/` (pipeline step 1 `market_index_updater.py`, the Telegram notice and bot code, the research modules). Building from `main` would re-pin all of it.
* Even building from the pinned commit is not byte-reproducible: `mechanism/Dockerfile` builds `FROM python:3.11-slim` (a floating tag; its base layers happen to be unchanged today), runs `apt-get install`, and installs requirements of which 5 of 17 are unpinned ranges (`aiogram>=3.13,<4`, `pillow`, `matplotlib`, `mplfinance`, ...). A rebuild could silently move the Telegram library or Debian packages.

**Chosen:** `mechanism/Dockerfile.stage2` on the release branch `release/stage2-quarterly-minimal` (base branch `release/r1r3-5c51189` = commit `5c51189`): `FROM` the pinned image **by digest**, then `COPY --chmod=755 automation_pipeline.sh`, `quarterly_fundamentals_updater.py` and two test files. The release branch's `cd.yml` builds that Dockerfile and pushes the commit tag only (never `latest`). CI (draft PR #20, base = the pinned commit) runs on exactly this content.

**Verified complete image diff (pinned vs derived, run on the VPS, ~35,900 files):** full-filesystem sha256 listing, `pip freeze`, `dpkg` versions, Python version, environment, symlinks, runtime config (`User`, `WorkingDir`, `Cmd`, `Entrypoint`) and the mode of the changed script: **only the four copied files differ** (plus `/etc/hostname`, a per-container runtime artifact). The 15 original layers are unchanged and 4 are added. The real `automation_pipeline.sh` from the derived image was also run with a recording stand-in for the interpreter: identical 17-step sequence to the pinned image except step 8's `--time-budget-seconds 2700`; a failing or hanging step 8 only warns and steps 9-13 still run; a failing critical step (screener) still aborts with exit 1 and no session mark.

## 3. Preconditions (all must hold; any failure = stop)

1. CI green on the minimal-delta PR; image built and pushed; tag `T2` (12 hex) and digest recorded.
2. On the VPS: `docker pull ghcr.io/danielbugi/wolfx_alpha-mechanism:T2`; the pulled digest equals the recorded one.
3. In isolation (no production container touched): the quarterly tests pass **inside the image**; the real pipeline script runs through the stand-in harness (section 2); a scratch rehearsal with the real image (aged universe, short budget, real vendor) stops cleanly and prints `QUARTERLY_SUMMARY`.
4. Quiet window (section 4): no donchian unit running, `.pipeline.lock`, `.forward_collection.lock`, `.paper_tracker.lock` free, no timer due within the next 30 minutes.
5. The fresh-backup helper is installed on the box at the path the tool expects (`/root/pre_activation_backup.sh`, root-owned, mode 700) and its sha256 equals the trusted value recorded in `FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md` and in the tool (`e147ae29a396264330cd5b1ccc5e4e8d664b8cdd5b9a8028c15121e9b9ce00d1`). The preflight now checks this and fails safely when the helper is missing, not executable, a symlink, writable by group or others, or has a different hash; the `backup` step refuses to run it in any of those cases.
6. Pre-state captured: `prod_state.sh` snapshot, `sha256` of the three pin/arming files, row counts of the research and evidence tables.

## 4. Proposed window

Weekday daytime in Asia/Jerusalem: **Wednesday 2026-10-21, 12:00-14:00** (primary), **Tuesday 2026-10-27, 12:00-14:00** (fallback). Both leave 2+ weeks before the 2026-11-07 cohort. Avoid 2026-10-25 and 2026-11-01/02 (clock changes). The window itself is unchanged.

**Timer schedule around the window (read from the installed units on 2026-10-17; the VPS clock is UTC, every calendar below is in the zone stated).** The earlier text of this section said "after the 11:30 tracker fire and well before the 17:00 evening notice"; that was incomplete and the evening time was wrong.

| Time (Asia/Jerusalem) | Unit | Notes |
|---|---|---|
| 09:30 and 11:30 (+0-60 s) | `donchian-paper-tracker` | the 11:30 fire finishes before the window |
| 10:00 (+0-60 s) | `donchian-earnings-today` | |
| 11:45 (+0-300 s) | `donchian-nightly-backup` | a dump takes seconds; it is done well before 12:00 |
| **12:00 (+0-60 s)** | **`donchian-notice-midday`** | **fires at the very start of the window.** A channel-sender job that runs from the mechanism pin: it must have finished before anything is changed |
| 20:00 (+0-60 s) | `donchian-notice-evening` | not 17:00 |
| 00:45 and 02:15 on the following night (17:45 and 19:15 America/New_York) | `donchian-pipeline` | after the window; on 2026-10-21 the new image would be used from this fire |
| 23:45, then every 20 min 00:00-05:40, then 06:00 | `donchian-postmarket-retry` | |
| 03:30 / 05:00 / 06:20 / 06:45 and 08:15 | signal-ledger eval / firstlight1 prices / discontinuity scan / forward collection | overnight and early morning |

**Consequence:** the preflight is run **no earlier than about 12:05**, after `donchian-notice-midday` and the nightly backup have completed (confirm with the unit status, not by assuming a duration), and only when its own check "no timer is due within 30 minutes" passes. If it fails because of the midday notice, wait and run it again; do not override it. This leaves nearly two hours inside the window. The window is not moved.

## 5. Procedure: one tested tool, `deploy/vps/stage2_repin.sh` (root on the VPS; dormant, referenced by nothing)

Repository policy (enforced by tests) is that **no repository file may create the collector's arming file** and none may contain a unit-management command. The tool therefore never writes the arming file, and the arming file path, the collector check command and a read-only unit-introspection binary are supplied by the operator as environment variables. Arming is the deliberate human step in the middle.

```
export ARMING_FILE=/opt/donchian/<the collector's arming file>  COLLECTOR_CHECK=/opt/donchian/scripts/<the collector wrapper>  UNITCTL=$(command -v <unit tool>)  GATE_CMD='<run the trading-day gate in the OLD image>'
T=<new 12-hex tag>   DIGEST=sha256:<recorded digest>    S=/root/stage2_repin.sh    (copy of deploy/vps/stage2_repin.sh from the reviewed commit, LF endings; checksum compared with the repo)
1. $S preflight $T $DIGEST     read-only. FAILS (exit 1) unless: tag/digest well-formed; the fresh-backup helper is present, executable, not group/world-writable, not a symlink and matches the trusted sha256 (the 14th check); the mechanism pin is 5c511899505a (the rollback image) and the arming file equals it; tracker pin and
                               arming present and equal; the new image is local and its digest equals the recorded one; the rollback image is local; no donchian service running; every .lock file
                               free; no timer due within 30 min; the trading-day gate says 'no new session' (so the pipeline wrapper is a no-op); disk free.
2. $S backup                   fresh production dump via pre_activation_backup.sh stage2-repin + checksum verified. Then restore-check it into a throwaway database (row counts == live) BEFORE step 3.
3. $S apply $T $DIGEST         repeats the preflight; requires that backup (younger than 45 min, checksum OK); snapshots pin, arming, row counts of every table and the container list into
                               /opt/donchian/stage2-repin/<ts>/; writes the NEW PIN ONLY (atomic rename); prints the exact arming command. From this moment the collector refuses to run until step 4.
4. (operator, by hand)         run the printed command: write the new tag into the arming file. This is the explicit re-arm.
5. $S collector-check <state>  refuses unless the arming file now holds the new tag; then runs the collector check once and requires exit 0 ('complete and verified'). Never rolls back by itself.
6. $S verify <state dir>       pin and arming are the new tag; tracker pin, tracker arming and the bot's tag file are byte-identical; every public table has the same row count; no long-lived
                               container was recreated; run_pipeline.sh exits 0 as a gated no-op with the new image (skipped, and reported, if a new session is pending).
7. (only if anything FAILS)    $S rollback <state dir>      restores the saved pin and prints the command that restores the previous arming; the operator runs it, then
                               $S collector-check <state dir> 5c511899505a. Existing forward observations are never rewritten.
```

Between steps 3 and 4 (seconds, with the operator at the keyboard) the collector would refuse (exit 5); no timer is due within 30 minutes by construction. The tool is covered by 36 tests against a fake tree with stub docker, unit-introspection and collector-check scripts: every stop condition stops before anything changes; apply writes only the pin; collector-check refuses until the operator armed and does not auto-roll-back; verify catches a changed row count, a recreated container, a changed tracker pin and a missing re-arm; rollback restores the pin bytes and leaves the arming to the operator; and the file contains none of the words the repository's dormancy guards forbid.

## 6. Rollback (by the tool; the manual equivalent is below)

```
cp -p $D/CURRENT_MECHANISM_SHA.prev-<ts> $D/CURRENT_MECHANISM_SHA
cp -p $D/FORWARD_COLLECTION_ARMED.prev-<ts> $D/FORWARD_COLLECTION_ARMED       # both must again be 5c511899505a
/opt/donchian/scripts/run_forward_collection.sh                                # expect exit 0, 'complete and verified'
```
The old image remains in the local Docker cache and in the registry. A pin change takes effect at the next one-off container start; nothing long-lived runs from the mechanism image except the bot, which has its own pin.

### 6a. Recovery from a partial failure (pin and arming out of step)

The only unsafe end state is a pin and an arming file that disagree, or a change made while something else is writing. `status <state dir>` is read-only and can be run at any moment; it classifies the pair and prints the action. `rollback` is guarded: it refuses (exit 1, nothing restored) while a donchian service is running, a `.lock` file is held, or any table's row count moved since the snapshot, unless the operator sets `FORCE_ROLLBACK=1` after deciding that is safe.

| `status` class | What it means | Collector right now | Recovery (only after `status` shows QUIET) |
|---|---|---|---|
| CONSISTENT_NEW | pin and arming both the new tag | runs | finish: `collector-check`, then `verify` |
| CONSISTENT_OLD | pin and arming both 5c511899505a | runs | nothing to restore; confirm with `collector-check <state> 5c511899505a` |
| PARTIAL_PIN_NEW_ARMING_OLD | `apply` done, arming not yet changed (or the arming step failed) | refuses (exit 5) | either arm for the new tag by hand, then `collector-check`; or `rollback`, which restores the pin to the old tag and leaves a consistent old pair |
| PARTIAL_PIN_OLD_ARMING_NEW | pin was restored (or never written) but arming is new | refuses (exit 5) | write the old tag into the arming file by hand, then `collector-check <state> 5c511899505a`; or write the new pin and `collector-check` |
| UNEXPECTED | neither file holds the old or the new tag | refuses | do nothing automatic; compare with `pin_before` and `arming_before` in the state dir and restore each file by hand |

Order of operations on any failure: (1) stop, do not retry the failed step; (2) `status <state dir>` and read CLASS, ACTIVITY and the tracker/bot line; (3) if ACTIVE lines appear, wait for the run to finish (a timer-driven run is not an error) and run `status` again; (4) take the recovery action from the table; (5) `collector-check` for whichever tag is now armed; (6) `verify` (or the baseline verifier for the old state). Existing forward observations, the tracker pin/arming and the bot's tag are never touched by any step; if `status` reports a difference in them, do not repair it from this tool.

## 7. Monitoring after the change (step 8 can no longer fail the pipeline, so it must be watched)

* Every night's pipeline log: `grep -E "OPTIONAL_STEP_WARNING|QUARTERLY_SUMMARY"`. Expected on a normal night: one `QUARTERLY_SUMMARY` line, `symbols_due` about 116, `stopped_by_budget` false, no warning. On a cohort night (2026-11-07/08 first): `stopped_by_budget` true and `symbols_remaining` > 0 for several nights, then back to about 116.
* Weekly (read-only SQL): age of the stalest *successful* check must stay under 40 days; the count due must fall night by night after a cohort night:
  `select max(extract(epoch from now()-lc)/86400) from (select symbol, max(updated_at) lc from quarterly_fundamentals group by 1) x where lc > now() - interval '120 days';`
* A sustained `OPTIONAL_STEP_WARNING` (3 nights running), or `symbols_successful = 0` with `symbols_due` much larger than 116, means the vendor path is broken: investigate (the backend's fundamentals pages show data up to 25+ days old by design).

## 8. Why this is safe (evidence)

* Nothing after step 8 reads `quarterly_fundamentals` (guard test `test_nothing_in_the_steps_after_the_quarterly_updater_reads_quarterly_fundamentals` fails the build if that changes); steps 9-12 keep `run_step`, so a failure there still aborts.
* The budget and ordering are tested on the real SQL, including a multi-night simulation in which a 12-symbol stale cohort is cleared in three five-symbol nights and the never-succeeded symbols are first attempted the same night the cohort runs out, and are the only thing due afterwards.
* The optional-step wrapper is tested for success, failure and a hang that is killed at its limit, each followed by the pipeline continuing.

## 9. Rehearsal evidence (2026-10-15, scratch copy of production, real vendor; nothing in production touched)

A live read-only snapshot of production was restored into a throwaway database; every symbol's `updated_at` was aged to 26-60 days so the **whole universe (3,075 symbols) was due**, the 2026-10-13 scenario. The new updater was run with `--time-budget-seconds 150`: exit 0 at 153 s wall time, 41 symbols attempted, 41 ok, 0 failed, `stopped_by_budget: true`, 3,034 symbols left due, a `QUARTERLY_SUMMARY` line printed. The refreshed symbols were the stalest (their mean synthetic age 60 days against 43 for those still due), ~3.7 s per symbol. At that rate a 45-minute budget handles about 700 symbols a night, so a 3,000-symbol cohort is cleared in about five nights. The scratch environment was removed afterwards.

## 10. Readiness record (2026-10-15, for the 2026-10-21 12:00-14:00 Asia/Jerusalem window)

| Item | Value |
|---|---|
| Stage 2 image | `ghcr.io/danielbugi/wolfx_alpha-mechanism:0384c9b91c63` |
| Stage 2 digest | `sha256:4a12328919eb1e3c8edf0a773dd9d9ae1485656d86368d8c3bd968f0593de66b` (19 layers; the first 15 are identical to the pinned image) |
| Built from | `release/stage2-quarterly-minimal` @ `0384c9b91c63bb1612b1246f1f721696bf07e631` (base branch `release/r1r3-5c51189` = `5c51189`), CI green on that exact head (6/6 jobs), built by the existing build-and-push (the workflow's `deploy` job is refused for non-`main` refs by the `production` environment and, for mechanism, only prints a skip notice; nothing was deployed) |
| Rollback image | `ghcr.io/danielbugi/wolfx_alpha-mechanism:5c511899505a`, digest `sha256:be0ec3ce31176dcb1b7ee7713765705b64d0b10e79b05ce784721b41b0cc1760`, present in the VPS image cache |
| Complete image diff (registry-built vs pinned) | 40,458 vs 40,462 lines: files (sha256), `pip freeze`, `dpkg` versions, Python version, environment, symlinks, directories, modes of all `/app` files: **only** `automation_pipeline.sh`, `quarterly_fundamentals_updater.py` and the two new test files (+ their 644 modes) differ |
| Pipeline script from the image | 4 scenarios, identical 17-step sequence to the pinned image except step 8's `--time-budget-seconds 2700`; failing/hanging step 8 warns and steps 9-13 + the session mark still run (exit 0); a failing critical step aborts (exit 1, no mark) |
| Image tests (inside the image, throwaway DB, release-branch fixture data mounted read-only) | 14 passed (budget, wrapper, isolation guard, real-SQL ordering, multi-night starvation simulation) |
| Final scratch rehearsal (registry image, live read-only snapshot, real vendor) | steady state: 116 due (all never-succeeded), 100 s budget stops cleanly, due count unchanged; cohort night (3,075 due): 27 ok in 100 s (~3.7 s/symbol), 3,048 left, `QUARTERLY_SUMMARY` printed; no scratch leftovers |
| Window tool preflight on production (2026-10-15, real tag + digest; the tool had 13 checks then, 14 now) | ALL PASS, 13 checks (pin and arming consistent, tracker pin/arming equal, image digest equal, rollback image present, no running service, all locks free, no timer within 30 min, gate = no new session, 75 GB free) |
| Nightly backup | now 11:45 Jerusalem (`BACKUP_SCHEDULE.md`): the window starts at 12:00, after that day's dump; the window's own fresh backup is still taken first |
| Untouched by design | `CURRENT_PAPER_SHA`, `PAPER_TRACKER_ARMED`, `env/mechanism_image_tag.env` (bot), every timer and unit, all forward evidence |

The window begins by re-running `preflight` (it must be ALL PASS on the day) and the backup + restore check; if any item has changed, the window does not proceed.

### 10a. Preparation record (2026-10-17, read-only audit plus one file install)

* Re-verified on the box (no change): pin `5c511899505a` = arming; tracker `45c2ac39249d` = arming; bot tag `575fa5dc0599`; new image `0384c9b91c63` present with digest `sha256:4a123289...de66b` (equals the recorded one); rollback image present; no donchian service running; 68 GB free; PostgreSQL healthy; the newest nightly dump checksum OK; the installed `/root/stage2_repin.sh` was byte-identical to `main` at that time (it must be refreshed from the reviewed commit once the 14th check is merged, then compared by checksum).
* Found missing: `/root/pre_activation_backup.sh`. Installed from the repository blob after its sha256 matched the recorded value (`e147ae29...ce00d1`); root:root, mode 700; **not executed**.
* Preflight changes: the 14th check described above (tested, including on Linux).
* Still to repeat inside the window: all preflight checks (services, locks, timers, the trading-day gate), the fresh backup and its restore check into a throwaway database, apply, the manual arming step, collector check, verify.
