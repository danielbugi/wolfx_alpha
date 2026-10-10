# R1–R3 release candidate — preflight, backup, rollback (LAB, NOT DEPLOYED)

Status: prepared on `lab/first-light-algo`. **Nothing here has been performed.** Every step below needs an explicit owner approval. Design: [DISCONTINUITY_SCAN_AND_COLLECTOR_TIMING.md](DISCONTINUITY_SCAN_AND_COLLECTOR_TIMING.md).

## 1. Candidate and diff against the deployed SHA
* Deployed mechanism pin (verified read-only on the VPS): `7d00081e9f3b` (`7d00081e9f3b0696ef3887fdc74bac4f77223cc5`, an ancestor of the candidate).
* Candidate = the final commit of `lab/first-light-algo` (the image tag is its first 12 hex characters). Build the image from that exact SHA only.
* Changed files outside tests/docs (complete list, `git diff 7d00081e9f3b HEAD`):
  | file | change |
  |---|---|
  | `ml_training/data_preparation/build_dataset.py` | adds `--scan-only` / `latest-completed`; the **full-build path is unchanged** (pinned by an equivalence test); pipeline step 10 still runs the full build |
  | `mechanism/forward_collection/{steps,contract,activation}.py` | R1 reuse + guarded repair, R3 contract/preflight (only run by the collector, which is unarmed) |
  | `mechanism/market_intelligence/{runner,store}.py` | `repair=True` mode and `computed_universe_hash`; default behaviour unchanged (every existing caller passes neither) |
  | `deploy/vps/donchian-discontinuity-scan.{service,timer}`, `run_discontinuity_scan.sh` | NEW (not installed) |
  | `deploy/vps/donchian-forward-collection.timer` | first fire 04:00 → 06:45 (08:15 kept) |
  | `deploy/db/research_roles.sql`, `research_roles_verify.sql` | sector-helper EXECUTE grants — **already applied to production** (earlier approval); committed here only so the repo matches |
  | `ops/checkpoint/*` | Checkpoint tooling (not shipped in the image or installed) |
  | `.github/workflows/ci.yml` | lab-only CI gate: the scan-wrapper real-flock tests must run on Linux |
* **Unrelated research changes in the image:** none beyond the above. No change to `mechanism/alerts`, `mechanism/data_updaters`, `backend`, `docker-compose*.yml`, `automation_pipeline.sh`, any other wrapper or unit. The sector-helper grant file is the only item that is not R1–R3 (already live).

## 2. Compatibility with what is deployed
* **No migration.** The diff contains no `.sql` under `mechanism/`; migration 32 (scan table and functions) is already applied and is all `--scan-only` uses. Runtime role needs nothing new (tested as `donchian_app` against the real role script).
* **Flags:** the recorder, capture and boundary rows are not touched. The collector stays unarmed, and its wrapper also refuses when the arming file names a different image than the pin, so a pin change alone disarms it.
* The new `repair` mode is reachable only from the collector's `observe` step; the pipeline, Telegram sender and recorder call `mi_runner.run` exactly as before.

## 3. What the pin change touches
Every wrapper that reads `CURRENT_MECHANISM_SHA` (pipeline, post-market retry, channel sender, forward collection, signal-ledger eval) will run the candidate image. Their code paths are unchanged except `build_dataset.py` (step 10, full build; `--scan-only` is a new flag only the new wrapper uses). The bot container (`e3feb64d3e86`) keeps running its current image; the known dual-pin issue (`donchian-bot.service.proposed`) is unchanged: a bot restart already resolves to the pin.

## 4. systemd units that would change
| unit / file | action | enabled afterwards |
|---|---|---|
| `donchian-discontinuity-scan.service` | NEW install | static |
| `donchian-discontinuity-scan.timer` | NEW install | **not enabled** until its own approval |
| `scripts/run_discontinuity_scan.sh` | NEW install (executable) | n/a |
| `donchian-forward-collection.timer` | REPLACE (installed `c98c5e25…` = repo@7d00081 → candidate `d6ba456b…`) | stays disabled |
| everything else (pipeline, postmarket-retry, firstlight1-prices, notices, earnings, signal-ledger-eval, nightly-backup, bot, firewall, forward-collection.service, wrappers) | **unchanged** — installed hashes match the repo; repo diff for them is empty | as today |

## 5. Preflight (read-only, before any change)
1. `git rev-parse` of the candidate equals the pushed `lab/first-light-algo` head; its CI run is green (all six jobs, including the Linux real-flock step with zero skips).
2. Image `ghcr.io/danielbugi/wolfx_alpha-mechanism:<candidate12>` built from that SHA; verify the digest and file identity (as for earlier pins).
3. VPS: `cat /opt/donchian/CURRENT_MECHANISM_SHA` = `7d00081e9f3b`; `systemctl is-active` for pipeline/retry timers; no pipeline/retry/scan run in progress (`flock -n` probes on `.pipeline.lock`, `.postmarket_retry.lock`); `FORWARD_COLLECTION_ARMED` absent; forward-collection timer disabled; bot `e3feb64d3e86` and backend `963ab19318b7` running.
4. Last nightly backup is recent and its verification passed (see [BACKUPS.md](BACKUPS.md)); take a manual `pg_dump` of `trading_production` before the pin change if the last backup is older than one pipeline cycle. (The release adds no schema or data change, so the dump is a precaution, not a requirement.)
5. Time of day: avoid the 23:45–06:20 Asia/Jerusalem window, and do not pin during a pipeline or retry run.

## 6. Rollback readiness (verified read-only 2026-10-10)
* The current pin image `7d00081e9f3b` is present locally on the VPS, as are `df4f8e73c9fe`, `8dec4e2b38fe`, `66b23b9dcbfa`, `cfd83f72f960`, `4d9bf93069cb`.
* Saved previous-pin files exist: `PREVIOUS_MECHANISM_SHA.{20261002T172757Z=4d9bf93069cb, forward-research-20261007T082927Z=cfd83f72f960, collector-timing-20261008T083125Z=66b23b9dcbfa, migration-32-20261008T140307Z=8dec4e2b38fe}`. Before pinning, save `PREVIOUS_MECHANISM_SHA.r1r3-<UTC>` = `7d00081e9f3b`.
* **Rollback, in this order** (nothing in the database needs undoing; scan rows are append-only history):
  1. `systemctl disable --now donchian-discontinuity-scan.timer` (if enabled) and remove the scan service/timer/wrapper;
  2. restore the previous collector timer file (`c98c5e25…`), `daemon-reload`, leave it disabled;
  3. write `7d00081e9f3b` back into `CURRENT_MECHANISM_SHA`; confirm the next wrapper run logs that sha.
* A mid-scan failure needs no cleanup: the scan is one DB transaction and the kernel releases its locks with the process.

## 7. Staged rollout (separate approvals)
1. Merge, build, verify the image. 2. Pin. 3. Install scan files (not enabled); dry-run the wrapper once between 06:21 and 23:44 local. 4. Enable the scan timer; observe one night read-only. 5. Install the new collector timer (disabled). 6. Run the corrected checkpoint (`ops/checkpoint`): Verdict A PASS and Verdict B READY. 7. Arm the collector (own approval).
