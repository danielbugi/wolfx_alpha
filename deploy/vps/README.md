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
