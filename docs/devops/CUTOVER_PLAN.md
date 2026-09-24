# Production cutover plan (Windows PC → Hetzner VPS)

Status 2026-09-24: the VPS runs the **staging** stack (backend at an immutable GHCR SHA, Postgres with a
disposable `donchian_staging` database, Caddy on :80). Nothing below has been executed. Each step marked
**GATE** needs the owner's explicit go-ahead. Tooling referenced here is in `deploy/` and was rehearsed
end to end on synthetic data.

## What is live today (Windows PC, read-only inventory taken 2026-09-24)

| Component | Where | Schedule / state |
|---|---|---|
| Postgres `trading_production` | localhost:5432 | always on |
| Telegram bot `run_bot.py` | python process (long polling) | running |
| `DonchianScreenerDailyPipeline` (`automation_pipeline.sh`, incl. channel posts) | Task Scheduler | 23:45 + 01:00 backup |
| `FirstLight-1-UpdatePrices` | Task Scheduler | 05:00 |
| `FirstLight-2-SendDigest` | Task Scheduler | **disabled** (digest moved into the pipeline) |
| `FirstLight-3-Notices-Midday` / `-4-Notices-Evening` | Task Scheduler | 12:00 / 20:00 |
| `FirstLight-5-EarningsToday` | Task Scheduler | 11:00 |
| Dashboard | local `next dev` + `uvicorn` | on demand |

Invariant for every step: **never two live schedulers or two bots at once** (duplicate channel posts;
two long-polling bots on one token fight for updates).

## GATE 1 + GATE 4 — database migration and scheduler cutover (one maintenance window)

They must happen together: the Windows jobs and bot write to the database, so the dump that moves to the
VPS is only final once they are stopped.

Window: any time that is not 23:00–02:00 Israel (pipeline) and not 11:00/12:00/20:00 (posts). ~30 min.

1. **Freeze Windows writers** (reversible): disable `DonchianScreenerDailyPipeline` and `FirstLight-1/3/4/5`
   (`Disable-ScheduledTask`), stop `run_bot.py`. Leave Postgres running.
2. **Final backup** (read-only):
   `python deploy/db/backup_production.py --offbox root@116.203.220.219 --ssh-key <admin key>`
   → dump + same-snapshot inventory + sha256, copied to `/opt/donchian/backups/prod/<ts>/` and re-verified.
3. **Rehearse on the VPS** (touches nothing live): `bash deploy/db/verify_restore.sh /opt/donchian/backups/prod/<ts>`
   → exact row counts, schema, backend image + auth on the copy. Stop here if anything differs.
4. **Replace the staging database** on the VPS: stop backend and postgres; confirm the current volume
   holds `donchian_staging` (never production); move it aside (`docker volume` rename via copy, or delete
   — it is disposable); write the production `.env` (below); start postgres on an empty volume (the prod
   overlay never auto-bootstraps); `pg_restore --no-owner --no-privileges --single-transaction
   --exit-on-error`; compare counts against `inventory.json` exactly as step 3.
   **Never run the 17-file bootstrap against this database.**
5. **Start the app** at the current SHA: `deploy.sh backend <CURRENT_SHA>`; `/api/health` must report the
   database healthy with the real `stock_prices` count; owner signs in once (real 2FA email).
6. **Scheduler on the VPS**: install cron/systemd timers that call `docker compose run --rm pipeline` and
   the channel senders at the same times as today, still with `PROD_SENDING_ENABLED=0`; run the pipeline
   once manually and compare its output to the last Windows run.
7. **Go live**: set `PROD_SENDING_ENABLED=1`, start the bot (`--profile bot`), enable the VPS timers.
   From this moment the Windows jobs stay disabled (they are not deleted — GATE 5).

### Production `.env` on the VPS

Built from the current Windows `.env`, transferred over SSH directly into `/opt/donchian/env/.env`
(chmod 600, deploy-owned), never through git or chat. Changes from the Windows file: `DB_HOST=postgres`,
a new `DB_PASSWORD`, new `JWT_SECRET` and `TELEGRAM_CONTROL_TOKEN` (rotation recommended in
INFRASTRUCTURE_PLAN.md §6.6; rotating `JWT_SECRET` signs everyone out once),
`ALLOWED_ORIGINS=https://dashboard.first-light.finance`, `API_SITE_ADDRESS=:80` until GATE 2.
Keys the production overlay needs are read from this file only — nothing falls back to
`docker/.env.example` anymore.

### Rollback (GATE 1/4)

The Windows database is only ever read (the dump uses a read-only transaction), so rollback is:
stop the VPS bot/timers (`PROD_SENDING_ENABLED=0`), re-enable the Windows tasks, restart `run_bot.py`.
Anything written on the VPS after go-live would be lost on rollback — take
`backup_production.py`-style dumps on the VPS from the first night (nightly dump + off-box copy is still an
open item, PRODUCTION_READINESS_CHECKLIST.md).

## GATE 2 — DNS

`api.first-light.finance` A/AAAA → 116.203.220.219 (TTL 300 during the change). Then set
`API_SITE_ADDRESS=api.first-light.finance` in the VPS `.env` and redeploy (Caddy obtains the certificate
itself). Verify `https://api.first-light.finance/api/health`. Rollback: remove the record / set `:80`.

## GATE 3 — Vercel

Import `frontend/` into Vercel, `NEXT_PUBLIC_API_BASE_URL=https://api.first-light.finance`, domain
`dashboard.first-light.finance`. Requires GATE 2 first (HTTPS API, else mixed-content block). Verify login
end to end from a browser. Rollback: remove the domain from the Vercel project.

## GATE 5 — retirement

After a stable period (suggest 2 weeks of clean VPS nights): delete the disabled Windows tasks, stop the
local Postgres, keep its final dump off-box.

## Known open items before GATE 1

- GitHub Environment `production` has no required reviewer (only a `main` branch policy); the design
  assumed one. Add the owner as required reviewer in repo Settings → Environments.
- Nightly `pg_dump` of the VPS database + off-box copy (not yet scheduled).
- Hetzner Cloud Firewall (22/80/443) as an extra layer; the Docker-aware DOCKER-USER filter and UFW are in place.
- `/opt/donchian/git-token` (a credential placed on the VPS earlier, root-owned, 644 inside a 750
  directory) — confirm whether it is still needed; remove or `chmod 600` it.
