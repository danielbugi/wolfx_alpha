# Scan-only, snapshot reuse and the collector's new timing (R1–R3) — LAB, NOT DEPLOYED

Status: implemented on `lab/first-light-algo`, **not installed, not deployed, not pinned, nothing scheduled in production.** The strict fingerprint rule is unchanged.

## Why
The first real nights showed the whole-table price fingerprint is correctly strict and the schedule did not respect it. `donchian-postmarket-retry` (23:45 then every 20 min until 06:00 Asia/Jerusalem) loads late-arriving bars
(PSKY arrived 23:46Z, 02:46Z, 02:47Z and 23:46Z on four consecutive sessions). The scan taken by the pipeline at ~23:22Z therefore went stale, the 04:00 Jerusalem collector fire failed on two of the four nights, and on the other two the
08:15 recovery fire would have reported a **false INCOMPLETE for a session that was already complete** (the observe step recomputed fresh evidence before looking at its own committed rows).

## R1 — immutable snapshot reuse (`forward_collection/steps.py: committed_snapshot`, `make_observe`)
Before any freshness check, `observe` verifies the session's stored evidence, **read-only**:
* **absent** (no row of the session's observed snapshot exists) → the strict first-time path, unchanged: fresh price AND scan evidence required.
* **valid** → reused: the step returns `already_present` with `reuse: true`; the Market Intelligence runner is not called, nothing is written. Valid means: universe, market, sector and relative-strength rows (every horizon) all exist; the
  market row points at that universe row; the relative-strength rows of every horizon equal the universe size and belong to ONE run (`run_content_hash`); `source`/`code_ref`/hashes are present; every DB-stamped time lies in
  `[13:00 New York on the session day, min(knowledge cutoff, decision deadline))`; and a **complete scan for the session existed before the first snapshot row was written** (and before the cutoff).
* **repairable** (ONLY relative-strength rows missing or short; universe/market/sector present) → deliberately NOT reused: it falls through to the strict path (the documented crash-after-market-write repair), which writes only missing rows under fresh evidence and fails closed (INCOMPLETE, no writes) on stale evidence. **Owner decision point:** the alternative is to treat this as permanent MISSED.
* **partial** (universe/market/sector rows missing) or **inconsistent** (contradiction, stamp outside the window, no scan before it, mixed runs) → `FAILED` with a permanent reason (`committed_snapshot_partial` /
  `committed_snapshot_inconsistent`; verdict MISSED, alert state). It is never written over or "completed".
Historical validity at capture time is separate from freshness against current prices: a valid snapshot reports `live_scan_evidence_ok/reason` as information only. The orchestrator's existing rule (after the decision deadline the read-back
`verify` decides) is unchanged, so the later weekend/Monday re-checks stay idempotent.

## R2 — `build_dataset.py --scan-only --session S` (`ml_training/data_preparation/build_dataset.py`)
Detects discontinuities with exactly the full builder's rule (`symbol_discontinuities`, pinned equal by a test) and records the same append-only `price_discontinuity_scan` evidence. It never reads, rebuilds, truncates or alters
`ml_breakout_dataset_v2` (a test makes the table refuse every DML and TRUNCATE for the whole run), preserves every first-detected timestamp (upsert/prune are the same functions as the full run), is idempotent across both modes (an identical
input and result is the same evidence, no new row; the first completion time stands) and **recomputes from the price state it started with**: the database re-fingerprints at insert and refuses the row if prices changed meanwhile (that run is
a `failed` scan row, exit 1, and the prune rolls back with it). The target session must equal the calendar's latest completed session (`session_not_latest_completed`, exit 3; no trustworthy calendar → exit 5, nothing recorded) and the
session's bar must be the newest in the table (`price_data_not_at_session`). Measured on production data: about 57–70 s (read-only timing, 2026-10-09). Run as `donchian_app` it needs nothing new (tested under the real role script).

`deploy/vps/run_discontinuity_scan.sh` (committed, not installed) coordinates with every scheduled writer: it refuses outside 06:01–23:44 Jerusalem time (the retry window is 23:45–06:00), takes **its own lock, then the retry lock
(waiting up to 17 min for a running retry), then the pipeline lock, and holds all three for the whole scan** (the retry wrapper skips when its lock is held), runs exactly one `--scan-only --session latest-completed` and passes its exit
status through (a failed scan is a failed unit). Exit: 6 outside the window, 7 a writer still running after the wait, 8 another scan running, 9 setup. The 05:00 safety net is finished 5 h earlier; any writer outside the schedule is caught by the
database's mid-scan fingerprint check.

## R3 — timing (contract `SCHEDULER_DESIGN`, `deploy/vps/donchian-discontinuity-scan.timer`, `donchian-forward-collection.timer`)
Asia/Jerusalem, every night, **one scan, no loop, no catch-up (`Persistent=false`)**:
| time (local) | what | UTC summer / winter |
|---|---|---|
| 06:00 | final post-market retry starts (9–16 min; bound 20) | 03:00 / 04:00 |
| 06:20 | the single scan (after the retry's worst-case end; waits on the lock if needed) | 03:20 / 04:20 |
| 06:45 | collector first attempt (≥ scan start + 25 min budget) | 03:45 / 04:45 |
| 08:15 | collector recovery attempt (retained) | 05:15 / 06:15 |
All four are Jerusalem-local like the retry, so their order never changes; sessions and the knowledge cutoff (noon New York next day) are in New York time, and the contract/preflight check the ordering for summer, winter and both mismatch
windows (`scan_schedule_problems`, tests). The collector remains dependent on a successful, matching scan: no scan, a failed scan, a stale scan or a scan after the cutoff leaves `observe` failed (INCOMPLETE).

## Checkpoint tooling (`ops/checkpoint/`)
Corrected assertions with **two separate verdicts**: `assert_forward_research.sql` (Verdict A) and `assert_collector_readiness.sql` (Verdict B). `tiingo_meta` is an accepted diagnostic chain with authoritative `yfinance_info` coverage required separately;
canonical symbols are validated against the price universe (BRK/A is canonical, request forms are not); late rows are detected in one explicit timezone (`PRICE_WRITER_TZ`, UTC); the price and result fingerprints are separate assertions; a stale scan can
never fail Verdict A. `checkpoint.sh` is the unattended runner template.

## Candidate guard semantics (verified on production data, session 2026-10-09)
The capture records the WHOLE pre-guard funnel as research evidence: 250 breakouts + 1,210 near misses = 1,460. 80 were guard-rejected (73 `illiquid_dollar_volume`, 5 `price_discontinuity`, 4 `insufficient_history`): they are kept with
`passed_guard=false` and `guard_reasons`, have `tracked_intent=false` and **none is in the ledger**. Near misses are never tracked. `tracked_intent` is the eligibility predicate shared with the ledger writer, evaluated BEFORE its stateful
position check: of 240 eligible breakouts, 109 became new ledger rows (all linked to their observation) and 131 were already open positions from earlier days (the position invariant skips them). No defect; the capture contract is unchanged;
`assert_forward_research.sql` now asserts all of this.

## Deployment order (each step needs an owner approval; none has been performed)
1. Merge the lab commit (CI green); build the mechanism image from the exact SHA; verify digest and file identity (as for the earlier pins).
2. **Pin the new image** (one root file write; the old pin is saved). Existing behaviour is unchanged until step 4: the pipeline's step 10 is the unchanged full run; the collector is still unarmed and its timer disabled.
3. Install the scan wrapper + service + timer (root: copy, `daemon-reload`) but **do not enable**. Dry-run the wrapper once manually inside the 06:01–23:44 window after the retry window (read the log; expect "recorded" or "already recorded").
4. Enable the scan timer; observe one night read-only (one scan row at ~06:20 Jerusalem, `code_ref …#scan_only@v1`, fingerprints equal to the stored prices and table, nothing else changed).
5. Replace the installed collector timer with the new committed one (06:45 / 08:15); keep it disabled and unarmed.
6. Run the corrected checkpoint (`ops/checkpoint`) for the first full night; both verdicts must be PASS/READY.
7. Only then, with a separate approval, arm the collector.

## Rollback
Disable and remove the scan timer/service/wrapper; restore the previous collector timer file; re-pin the previous image. Nothing in the database needs undoing: scan rows are append-only history and harmless; the discontinuity table's
stamps are never rewritten. No migration is involved (the scan table already exists).

## Remaining risks
* A price write after ~06:20 Jerusalem (a manual run, or a reboot catch-up of the pipeline/safety-net timers) makes the night's scan stale before the first fire → INCOMPLETE and an alert (fail-closed, by design).
* A vendor straggler that arrives after the final retry (06:00) is not in that night's evidence; it is ingested by the next night's first run.
* The reuse path trusts that the collector's own write path validated freshness at write time (only the runtime role can write these tables); it proves the necessary conditions (rows, lineage, window, scan before) rather than re-deriving the old fingerprints.
* The scan waits on the retry lock; a retry that hangs beyond 17 min blocks the scan (exit 7) rather than racing it.
* The alert transport is still journald/`systemctl --failed` only.
