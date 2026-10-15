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
5. Pre-state captured: `prod_state.sh` snapshot, `sha256` of the three pin/arming files, row counts of the research and evidence tables.

## 4. Proposed window

Weekday daytime in Asia/Jerusalem, after the 11:30 tracker fire and well before the 17:00 evening notice: **Wednesday 2026-10-21, 12:00-14:00** (primary), **Tuesday 2026-10-27, 12:00-14:00** (fallback). Both are before the pipeline's next first fire (00:45) and leave 2+ weeks before the 2026-11-07 cohort. Avoid 2026-10-25 and 2026-11-01/02 (clock changes).

## 5. Procedure: one tested tool, `deploy/vps/stage2_repin.sh` (root on the VPS; dormant, referenced by nothing)

```
T=<new 12-hex tag>   DIGEST=sha256:<recorded digest>    S=/root/stage2_repin.sh    (copy of deploy/vps/stage2_repin.sh from the reviewed commit, LF endings; checksum compared with the repo)
1. $S preflight $T $DIGEST     read-only. FAILS (exit 1) unless: tag/digest well-formed; CURRENT_MECHANISM_SHA is 5c511899505a (the rollback image) and FORWARD_COLLECTION_ARMED equals it;
                               tracker pin and arming present and equal; the new image is local and its digest equals the recorded one; the rollback image is local; no donchian service running; every
                               .lock file free (pipeline, collector, tracker, notices, backup, postmarket); no timer due within 30 min; the trading-day gate says "no new session" (so the pipeline wrapper is a
                               no-op); disk free.
2. $S backup                   fresh production dump via pre_activation_backup.sh stage2-repin + checksum verified. Then restore-check it into a throwaway database (row counts == live) BEFORE step 3.
3. $S apply $T $DIGEST         repeats the preflight; requires that backup (younger than 45 min, checksum OK); snapshots pins, row counts of every table and the container list into /opt/donchian/stage2-repin/<ts>/;
                               writes the new pin and then the new arming file (atomic renames); runs run_forward_collection.sh once (idempotent) and requires exit 0. It NEVER rolls back by itself.
4. $S verify <state dir>       pin and arming are the new tag; tracker pin, tracker arming and the bot's tag file are byte-identical; every public table has the same row count; no long-lived container was
                               recreated; run_pipeline.sh exits 0 as a gated no-op with the new image (skipped, and reported, if a new session is pending).
5. (only if anything FAILS)    $S rollback <state dir>      restores the saved pin and arming file and re-runs the collector wrapper. Existing forward observations are never rewritten.
```

Between the two writes in step 3 (milliseconds) the collector wrapper would refuse (exit 5); no timer is due within 30 minutes by construction. The tool is covered by 24 tests against a fake tree with stub docker/systemctl/wrappers (every stop condition stops before anything changes; apply writes exactly the two files; a failing collector re-verification does not auto-roll-back; verify catches a changed row count, a recreated container and a changed tracker pin; rollback restores the bytes).

## 6. Rollback (rehearsed on paper; each file restore is a single `cp`)

```
cp -p $D/CURRENT_MECHANISM_SHA.prev-<ts> $D/CURRENT_MECHANISM_SHA
cp -p $D/FORWARD_COLLECTION_ARMED.prev-<ts> $D/FORWARD_COLLECTION_ARMED       # both must again be 5c511899505a
/opt/donchian/scripts/run_forward_collection.sh                                # expect exit 0, 'complete and verified'
```
The old image remains in the local Docker cache and in the registry. A pin change takes effect at the next one-off container start; nothing long-lived runs from the mechanism image except the bot, which has its own pin.

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
