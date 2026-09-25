# VPS deployment scripts (Hetzner, `/opt/donchian`)

Versioned copies of everything that runs on the VPS. The VPS copies are installed from here by an
administrator (root SSH) — the GitHub Actions key cannot change them.

| File | Installed as | Owner / mode | Role |
|---|---|---|---|
| `ci-entry.sh` | `/opt/donchian/scripts/ci-entry.sh` | deploy, 755 | Forced command for the GitHub Actions key: accepts only `deploy backend <12-hex-sha>` + the release bundle on stdin |
| `deploy.sh` | `/opt/donchian/scripts/deploy.sh` | deploy, 755 | Health-gated backend deploy at an immutable SHA tag; auto-restores the previous good tag on failure; records `CURRENT_SHA` / `PREVIOUS_GOOD_SHA` |
| `rollback.sh` | `/opt/donchian/scripts/rollback.sh` | deploy, 755 | Redeploys `PREVIOUS_GOOD_SHA` (image + that commit's Compose config) |
| `docker-user-firewall.sh` | `/usr/local/sbin/donchian-docker-firewall.sh` | root, 755 | DOCKER-USER filter: internet → containers allowed on tcp/80, tcp/443 only |
| `donchian-docker-firewall.service` | `/etc/systemd/system/` | root, 644 | Re-applies the filter at boot and after every Docker restart |

## Scheduling (systemd timers)

The 15 `donchian-*.service`/`donchian-*.timer` files below are **verbatim copies of what is
installed and running on the VPS**, pulled read-only via root SSH and confirmed byte-identical by
SHA-256 (2026-09-25, as part of the repository consolidation audit's P1-1 cleanup item — before this,
none of them existed in git at all; the only record of production's actual scheduling was
`docs/devops/CUTOVER_PLAN.md`'s prose description). All of them live at `/etc/systemd/system/` on the
VPS, owner root, mode 644, all under `WantedBy=timers.target` (the four continuous/boot-time services —
`donchian-bot.service`, `donchian-docker-firewall.service` — are `WantedBy=multi-user.target` instead;
they have no matching `.timer`).

| Timer | Schedule (Asia/Jerusalem unless noted) | Service it triggers |
|---|---|---|
| `donchian-pipeline.timer` | 23:45, 01:00 retry | Full 13-step pipeline (`run_pipeline.sh`) |
| `donchian-postmarket-retry.timer` | 23:45, then every ~20 min through 06:00 | Post-market package only (`run_postmarket_retry.sh`) — `Persistent=false` deliberately, so a VPS outage never fires a stale retry for an old session on restart |
| `donchian-firstlight1-prices.timer` | 05:00 | Price safety-net + snapshot (`firstlight1_updateonly.sh`) |
| `donchian-earnings-today.timer` | 10:00 | Earnings-today post |
| `donchian-notice-midday.timer` | 12:00 | Disclaimer + assistant-promo notice, slot 1 |
| `donchian-notice-evening.timer` | 20:00 | Same, slot 2 |
| `donchian-nightly-backup.timer` | 23:30 **UTC**, not Asia/Jerusalem | Production DB dump — the one timer that predates the Asia/Jerusalem-tag convention and will drift an hour at the next DST change; a known, low-priority inconsistency, not yet fixed |

**These files are a snapshot, not a live sync** — editing them here does nothing to the VPS until
someone deliberately re-installs them (`scp` + `systemctl daemon-reload` + `systemctl restart <unit>`,
by hand, same as every other VPS-side change in this project). Before changing any of them: pull the
live copy again and diff it against this directory first (a manual edit made directly on the VPS after
this snapshot was taken would otherwise be silently overwritten) — never push a change from git to the
VPS blind.

```bash
# Read-only comparison before touching anything (root SSH key required):
ssh -i ~/.ssh/donchian_deploy root@<vps-host> "cd /etc/systemd/system && sha256sum donchian-*.service donchian-*.timer" \
  | sort > /tmp/remote.sha
sha256sum deploy/vps/donchian-*.service deploy/vps/donchian-*.timer | sed 's#deploy/vps/##' | sort > /tmp/local.sha
diff /tmp/remote.sha /tmp/local.sha
```

## Layout

```
/opt/donchian/
  env/.env                 real config, chmod 600, never in git (compose/.env is a symlink to it)
  compose/                 active Compose config (copied from releases/<sha>/ by deploy.sh) + bind-mounted data dirs
  releases/<sha>/          per-commit bundle: docker-compose.yml, docker-compose.prod.yml, docker/Caddyfile.prod
  CURRENT_SHA, PREVIOUS_GOOD_SHA
  logs/deploy.log
```

## Network model

Public: tcp/22 (UFW), tcp/80 + tcp/443 (Caddy; UFW for IPv6 docker-proxy, DOCKER-USER for IPv4 DNAT).
Never public: Postgres 5432 and FastAPI 8000 — not published at all, and DOCKER-USER drops any other
published port even if one is added by mistake.

## Staging vs. production

Until cutover the VPS `.env` holds staging values (`PROD_SENDING_ENABLED=0`, placeholder bot token,
`API_SITE_ADDRESS=:80`, disposable database). The production Postgres volume is **never**
auto-bootstrapped (`docker-compose.prod.yml`); production data arrives by restoring a `pg_dump`
(docs/devops/PRODUCTION_MIGRATION_RUNBOOK.md).
