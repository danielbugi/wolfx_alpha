# Stage 2: deploy the bounded quarterly-fundamentals step (new mechanism image and re-pin)

Status 2026-10-15: **PREPARED, NOT DEPLOYED.** Needs the owner's separate approval. Must be complete before **2026-11-06** (the next full quarterly cohort falls due 2026-11-07/08; see `PIPELINE_FIRST_FIRE_RELIABILITY.md`). Stage 1 (the timer) is already applied and independent of this.

## 1. What changes, and what must not

| Changes | Does not change |
|---|---|
| `/opt/donchian/CURRENT_MECHANISM_SHA` -> new 12-hex tag | the bot (`env/mechanism_image_tag.env` = `575fa5dc0599`, its own pin) |
| `/opt/donchian/FORWARD_COLLECTION_ARMED` -> the same tag (the collector refuses unless the file equals the pin) | the paper tracker (`CURRENT_PAPER_SHA` = `45c2ac39249d`, `PAPER_TRACKER_ARMED`; image `45c2ac39249d`) |
| the image the pipeline, post-market retry, channel-sender (notices) and discontinuity scan run | any timer, unit or wrapper file; the database; any immutable evidence |

Wrappers that read `CURRENT_MECHANISM_SHA` (checked on the box): `run_pipeline.sh`, `run_postmarket_retry.sh`, `run_channel_sender.sh`, `run_discontinuity_scan.sh`, `run_forward_collection.sh` (the only one with an arming file). `run_paper_tracker.sh` reads its own pin.

## 2. Which image: a minimal-delta image, not "main"

The pinned image `5c511899505a` was built from the R1-R3 commit. Between that commit and today's `main` there are 66 changed files under `mechanism/` and `ml_training/`, including pipeline step 1 (`market_index_updater.py`), the Telegram notice and bot code (`mechanism/alerts/*`) and the research modules. An image built from `main` would therefore re-pin **all of that** on the notice, post-market and pipeline paths, not only the quarterly fix.

**Recommendation:** build the Stage 2 image from the pinned commit plus only the three pipeline files, so the production-proven code is otherwise byte-identical:

* base branch `release/r1r3-5c51189` = commit `5c51189`;
* head branch `release/stage2-quarterly-minimal` = that commit + `automation_pipeline.sh`, `mechanism/data_updaters/quarterly_fundamentals_updater.py` and their tests (these files are identical between `5c51189` and `main`, so the change applies cleanly);
* a draft PR head -> base runs the repository's own CI on exactly that content; the image is then built from the head commit with the existing manual build-and-push (`cd.yml`, service `mechanism`, which deploys nothing).

Option B (build from `main`) is possible but is a much larger release and would need its own review of the notice/bot/market-index changes.

## 3. Preconditions (all must hold; any failure = stop)

1. CI green on the minimal-delta PR; image built and pushed; tag `T2` (12 hex) and digest recorded.
2. On the VPS: `docker pull ghcr.io/danielbugi/wolfx_alpha-mechanism:T2`; the pulled digest equals the recorded one.
3. In isolation (no production container touched): the quarterly tests pass **inside the image**; `bash -n automation_pipeline.sh`; the updater `--help` shows `--time-budget-seconds`; a scratch rehearsal (aged universe, 150 s budget, real vendor) stops cleanly and prints `QUARTERLY_SUMMARY`.
4. Quiet window (section 4): no donchian unit running, `.pipeline.lock`, `.forward_collection.lock`, `.paper_tracker.lock` free, no timer due within the next 30 minutes.
5. Pre-state captured: `prod_state.sh` snapshot, `sha256` of the three pin/arming files, row counts of the research and evidence tables.

## 4. Proposed window

Weekday daytime in Asia/Jerusalem, after the 11:30 tracker fire and well before the 17:00 evening notice: **Wednesday 2026-10-21, 12:00-14:00** (primary), **Tuesday 2026-10-27, 12:00-14:00** (fallback). Both are before the pipeline's next first fire (00:45) and leave 2+ weeks before the 2026-11-07 cohort. Avoid 2026-10-25 and 2026-11-01/02 (clock changes).

## 5. Procedure (root on the VPS; each step has a stop condition)

```
D=/opt/donchian     NEW=<T2>     OLD=5c511899505a
0. snapshot:   bash prod_state.sh > before.txt ; sha256sum $D/CURRENT_MECHANISM_SHA $D/FORWARD_COLLECTION_ARMED $D/env/mechanism_image_tag.env $D/CURRENT_PAPER_SHA $D/PAPER_TRACKER_ARMED > pins_before.txt
               cp -p $D/CURRENT_MECHANISM_SHA $D/CURRENT_MECHANISM_SHA.prev-$(date -u +%Y%m%dT%H%M%SZ) ; cp -p $D/FORWARD_COLLECTION_ARMED $D/FORWARD_COLLECTION_ARMED.prev-<same ts>
1. quiet check: systemctl list-units 'donchian-*' --state=running  (none) ; flock -n on the three lock files (all free) ; systemctl list-timers (nothing due in 30 min)
2. pin:        printf '%s\n' $NEW > $D/CURRENT_MECHANISM_SHA.new && mv $D/CURRENT_MECHANISM_SHA.new $D/CURRENT_MECHANISM_SHA
   >> from this moment run_forward_collection.sh REFUSES (exit 5: armed for a different image) until step 3; do not leave the window between 2 and 3.
3. re-arm:     printf '%s\n' $NEW > $D/FORWARD_COLLECTION_ARMED        (explicit; this is the only place the arming file is written)
4. re-verify the collector (idempotent: it re-checks the latest completed session and applies nothing new when it is already complete):
               /opt/donchian/scripts/run_forward_collection.sh
   expect: exit 0 and 'next_action: none: the session is complete and verified'; row counts of candidate_observation / feature_snapshot / market_snapshot / stock_relative_strength / sector tables identical to before.
5. smoke the other consumers (read-only or gated no-ops):
               /opt/donchian/scripts/run_pipeline.sh          -> 'SKIPPED: no new US session' (daytime), exit 0 ; the log line shows the NEW sha
               /opt/donchian/scripts/run_postmarket_retry.sh  -> OK (it is idempotent and bounded)       [only if no post-market package is pending]
               docker run --rm --network none -e PYTHONPATH=/app/mechanism --entrypoint python ghcr.io/danielbugi/wolfx_alpha-mechanism:$NEW -c 'import alerts.channel_content, alerts.publish_post_market'   (import smoke for the notice path)
6. verify:     bash prod_state.sh > after.txt ; diff before/after: only CURRENT_MECHANISM_SHA, FORWARD_COLLECTION_ARMED, and the image id of freshly started one-off containers differ ;
               sha256 of mechanism_image_tag.env, CURRENT_PAPER_SHA, PAPER_TRACKER_ARMED identical ; tracker timer, bot container and backend untouched ; paper_* and evidence counts identical.
7. watch the next cycle (section 7).
```

**Stop conditions:** any step 1 check fails (do not start); step 4 exit != 0 or any count changes (roll back at once); anything unexpected in step 6.

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
