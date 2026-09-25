# Database backup/restore tooling

The whole production backup toolchain lives here — the systemd unit files that trigger it, and
every script involved, co-located on purpose (previously the unit files were also duplicated under
`deploy/vps/`; consolidated here 2026-09-25 as part of Phase 4A of the codebase audit — see
`docs/architecture/CODEBASE_AUDIT.md`).

| File | Installed as | Role |
|---|---|---|
| `donchian-nightly-backup.service` / `.timer` | `/etc/systemd/system/`, root, 644 | Fires `nightly_backup.sh` at 23:30 UTC (~02:30 Israel) — the one timer that still hardcodes a UTC offset instead of the `Asia/Jerusalem` tag every other timer uses (known, low-priority inconsistency, not yet fixed) |
| `nightly_backup.sh` | `/opt/donchian/scripts/nightly_backup.sh`, root, 755 | The actual on-VPS `pg_dump` — read-only, integrity self-check, 14-day retention. See [../../docs/operations/BACKUPS.md](../../docs/operations/BACKUPS.md). |
| `pull_nightly_backup.ps1` | run from the Windows admin machine (not installed on the VPS) | Off-box leg — pulls the latest nightly dump over `scp`, re-verifies its checksum on both ends, 30-day retention |
| `verify_restore.sh` | run manually on the VPS as needed | Full restore-and-verify drill against a disposable Postgres + the live backend image. **Known gap**: expects an `inventory.json` file that only `backup_production.py` produces, not the routine `nightly_backup.sh` — see BACKUPS.md §3. |
| `backup_production.py` | run manually, historically from the machine hosting production Postgres | The Gate-1 migration-era backup tool (dump + exact row-count `inventory.json`). Its own docstring still describes the pre-migration topology (Windows hosting production) — stale prose, not currently accurate; the tool itself still works and is the one that produces the `inventory.json` shape `verify_restore.sh` expects. |

**These unit files are a snapshot, not a live sync** — see `../vps/README.md`'s note of the same
kind; the verification command there now covers both directories.
