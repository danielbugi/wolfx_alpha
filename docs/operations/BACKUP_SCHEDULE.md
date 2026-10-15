# Nightly backup schedule: 11:45 Asia/Jerusalem

Status: **APPLIED 2026-10-15 ~08:47Z** (owner-approved). Replaces the fixed `23:30 UTC` timer. Backup mechanics (what the script does, retention, restore) are unchanged: see [BACKUPS.md](BACKUPS.md).

## 1. Why the old time had to move

Stage 1 of [PIPELINE_FIRST_FIRE_RELIABILITY.md](PIPELINE_FIRST_FIRE_RELIABILITY.md) moved the pipeline to 17:45 / 19:15 America/New_York. Measured durations: **85 minutes** on a normal night, **133 minutes** on the slowest night seen (2026-10-07), up to **~141 minutes** in the worst case with the bounded quarterly step. A fixed 23:30 UTC backup then falls inside the pipeline's normal run on every US-winter night (22:45Z-00:10Z), because the US and Israeli clock changes fall on different dates and the UTC instant of the run moves while the backup's did not.

## 2. Candidates, evaluated day by day (731 days, 2026-10-15 .. 2028-10-15, both countries' clock changes)

Overlap = the backup window (start + up to 5 min jitter + ~3 min run) intersects a pipeline run. Counted per scenario; `test_backup_schedule_timeline.py` recomputes this from the tracked unit files.

| Schedule | Normal night (85 min) | First fire fails, retry runs (85 min) | Slowest seen on the retry (133 min) | Worst case on the retry (141 min) | Starts before the collector / notice / tracker of its own day |
|---|---|---|---|---|---|
| previous: fixed 23:30 UTC | **259 days** | 472 | 472 | 472 | every day |
| candidate: 21:00 America/New_York | 0 | 0 | **731** | **731** | every day |
| **installed: 11:45 Asia/Jerusalem** | **0** | **0** | **0** | **0** | **never** |

21:00 New York keeps clear of a normal night and of the 85-minute retry, but any slow retry-path night overlaps it, and, like the old time, it always runs BEFORE that morning's forward collector (06:45 / 08:15 Jerusalem), notices (09:00) and paper tracker (09:30 / 11:30). Those write evidence that cannot be re-created later (observed market snapshots and relative strength, the tracker's sessions). Under 21:00 NY or the old time, they would wait for the *next* night's dump, up to ~48 hours unprotected.

## 3. The installed schedule and the recovery point it defines

`OnCalendar=*-*-* 11:45:00 Asia/Jerusalem`, `Persistent=true`, `RandomizedDelaySec=300`.

* **Contents of one dump (the recovery point):** the whole previous US session: the pipeline's price data, candidate capture, signal ledger and ML outputs (finished by ~02:10 Jerusalem normally, ~04:40 on the slowest retry path), the forward collector's market snapshot / relative strength / sector rows (06:45, retry 08:15), the midday notice (09:00) and the paper tracker's session (09:30, retry 11:30).
* **Not in it:** the 17:00 evening notice and the next night's pipeline. Worst-case loss on a total failure is therefore up to ~24 hours of the *next* session's pipeline output (re-created by re-running the pipeline for the missed session) plus the evening notice-delivery records.
* **Idle margin:** at least 6 hours after the slowest possible pipeline run has ended and at least 8 hours before the next primary fire, every day (asserted in the test).
* **Anchoring:** Jerusalem time, so the relationships above hold through both countries' clock changes. systemd's own `--iterations=200` output: 08:45Z (summer) -> 09:45Z from 2026-10-25 -> 08:45Z from 2027-03-26, always 11:45 Jerusalem.
* **No contention measured:** during the first dump under the new timer the API's `/api/health` latency was unchanged (baseline ~447 ms, 410-528 ms during the dump); the dump takes ~100 s, 650 MB, 3.5 s of CPU.

## 4. Verification performed (2026-10-15)

* Pre-checks: backup service and pipeline inactive, no running donchian service.
* The new timer fired once on its own at 08:47Z (Persistent catch-up, today's 11:45 had just passed): `20261015T084721Z`, 649,956,874 bytes, `OK` in `nightly_backup.log`, `sha256sum -c` OK, `pg_restore -l` reads 995 TOC entries.
* **Restore test:** restored into a throwaway same-major Postgres; 75 of 75 tables had row counts identical to the live (idle) database; throwaway container and network removed.
* Other timers unchanged (pipeline 21:45Z, collector 03:45Z, tracker 06:30Z); no trading, screening, collector or tracker execution path was touched (this is a timer file only).

## 5. Rollback (prepared, not run)

The previous unit is saved at `/opt/donchian/donchian-nightly-backup.timer.pre-schedule-20261015T084438Z` (sha256 `4508b849acd856c9...`, identical to the previous repo version). `/root/rollback_backup_timer.sh` restores it, `daemon-reload`, restarts the timer and lists it. A restart of a `Persistent=true` timer can fire one extra catch-up dump, which is harmless.

## 6. Retention note
Retention removes runs older than `RETAIN_DAYS=14` by modification time, so an extra dump (such as the catch-up one) does not shorten the retained window. The first run under the new timer removed `20260929T233328Z` as usual.
