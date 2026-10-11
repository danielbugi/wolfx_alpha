# R1–R3 collector activation procedure (prepared; NOT executed)

Status: every step below needs the owner's explicit approval for the final production action (enable + arm). Nothing here has been done. Context: [R1_R3_RELEASE_CANDIDATE.md](R1_R3_RELEASE_CANDIDATE.md), [DISCONTINUITY_SCAN_AND_COLLECTOR_TIMING.md](DISCONTINUITY_SCAN_AND_COLLECTOR_TIMING.md).

## When
**After the Monday 2026-10-12 08:15 Asia/Jerusalem fire window (05:15Z) and before the 20:45Z pipeline.** Reason: until a session is collected, the nightly fires target the latest completed session. Before Monday morning that is Friday 2026-10-09, which is past its decision deadline (2026-10-11 00:00Z) and its scan cutoff — the collector would report MISSED (exit 4) and the failure-alert unit would fire for a session that can never be collected. That session is a permanent, expected gap (it predates the scan timer); it is never backfilled. Enabling after Monday's fires makes the first fire **Tuesday 2026-10-13 06:45 Jerusalem (03:45Z)**, targeting Monday's session 2026-10-12.

## 1. Read-only preflight (abort if any differs)
`CURRENT_MECHANISM_SHA` = `5c511899505a` · scan timer `enabled/active`, last scan service `success` · collector timer `disabled/inactive` and `/etc/systemd/system/donchian-forward-collection.timer` sha `d6ba456b95e0` · `FORWARD_COLLECTION_ARMED` absent · bot `e3feb64d3e86`, backend `963ab19318b7` · no pipeline/retry/scan/collector unit active; locks `.pipeline.lock` `.postmarket_retry.lock` free · backup `r1-r3-stage1-20261010T201054Z` present · CI green on the lab head.

## 2. Install the approved `Persistent=false` timer (collector stays disabled)
Source: `deploy/vps/donchian-forward-collection.timer` at the lab head (sha256 of the LF-normalised file `7e61a83a4c3f…`; only the `Persistent=` line and its comment differ from the installed file). As root: copy the installed file to `/opt/donchian/donchian-forward-collection.timer.pre-persistent-<UTC>`, write the new file (root:root 644, sha verified), `systemctl daemon-reload`, `systemd-analyze verify`. **Report the exact change and confirm** `disabled/inactive` before continuing.

## 3. Arm
`printf '%s' 5c511899505a > /opt/donchian/FORWARD_COLLECTION_ARMED` (root, 644). The wrapper refuses unless the file contains exactly the pinned tag, so a later pin change disarms it by itself.

## 4. Enable
`systemctl enable --now donchian-forward-collection.timer`. Expected: the service stays `inactive` (no catch-up, `Persistent=false`); `systemctl list-timers` shows the next fire Tue 2026-10-13 03:45Z and 05:15Z.

## 5. Autonomous first-execution report (no PC or session needed)
One transient, read-only timer on the VPS, after both fires of the night:
`systemd-run --unit=r13-first-collection --on-calendar='2026-10-13 05:45:00 UTC' --uid=deploy --gid=deploy --collect -E R13_POST=1 -E R13_SESSION=2026-10-12 -E CHECKPOINT_PIPELINE_START=2026-10-12T20:00:00Z -E CHECKPOINT_BOUNDARY=2026-10-12 -E CHECKPOINT_DEADLINE=2026-10-13T08:00:00Z /opt/donchian/checkpoint-r13/r13_confirm.sh`
Report: `/home/deploy/checkpoint-r13/reports/latest_r13_confirm.txt` (+ `.status`). It evidences the 03:20Z scan, the 03:45Z collector run (service exit 0/3, verdict COMPLETE, one universe/market/sector set and 3 horizons of relative strength, stamps inside the window), the 05:15Z recovery fire, and Verdict A / Verdict B. This is the single scheduled confirmation; no further overnight cycle is needed.

## Expected first execution
03:20Z scan (a new row, `scan_only@v1`) → 03:45Z collector: scan evidence fresh → `observe` creates the snapshot (≈3,064 universe rows, 11 sectors, 9,192 relative-strength rows) → capture/sector-history dependencies `already_present` → labels → `verify` ok, verdict `COMPLETE`, exit 0 → 05:15Z recovery: committed snapshot reused, zero writes, `COMPLETE`. A straggler bar after the scan would not change this (the committed snapshot is reused).

## Rollback (nothing in the database needs undoing; evidence written is append-only history)
`systemctl disable --now donchian-forward-collection.timer; rm -f /opt/donchian/FORWARD_COLLECTION_ARMED`; restore the saved timer file and `daemon-reload` (collector then unarmed and disabled again). Full release rollback: copy `PREVIOUS_MECHANISM_SHA.r1-r3-stage1-20261010T201247Z` (= `7d00081e9f3b`) over `CURRENT_MECHANISM_SHA`, remove the scan timer/service/wrapper.

## Operational risks
* A price write after ~06:20 Jerusalem makes that night's scan stale → INCOMPLETE (exit 2) and an alert; the next night does not repair it (never backfilled).
* A vendor bar after the 06:00 retry is only ingested the next night.
* A retry hanging past 17 minutes makes the scan exit 7 (nothing scanned) → collector INCOMPLETE.
* The failure-alert transport is journald / `systemctl --failed` only.
* The known `MCHB` numeric-overflow log line is unrelated to the collector.
