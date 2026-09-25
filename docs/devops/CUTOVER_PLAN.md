# Production cutover plan (Windows PC → Hetzner VPS)

## Status 2026-09-24 (end of day): GATE 1 + GATE 4 DONE. The VPS is now the production environment.

**The Hetzner VPS is now authoritative for production.** `trading_production` on the VPS holds the
migrated database plus one real, successful automated pipeline write (2026-09-24 evening). The backend,
Telegram bot, and all 5 production timers (pipeline, price safety-net, two notices, earnings-today) are
live there, `PROD_SENDING_ENABLED=1`. **The Windows PC is now the frozen former production /
development environment** — its database was only ever read (never written) during the migration, so
it remains an exact snapshot as of the freeze; its own scheduled tasks and bot are deliberately left
`Disabled`, not deleted, pending a separate GATE 5 retirement decision. Do not re-enable any Windows
writer against `trading_production` without first assessing divergence — the two databases have
diverged by one real pipeline cycle already, and will diverge further with every VPS-side run.

**Not yet done** (separate approvals, untouched by Gate 4): GATE 2 (DNS), GATE 3 (Vercel/public routing),
GATE 5 (Windows retirement).

**Fixed during Gate 4 execution** (real, previously-undiscovered infra bugs, both committed at the repo
level, not just patched on the VPS):
- `docker-compose.prod.yml`'s `pipeline` service `mem_limit` raised 3g → 5g — a real production run was
  OOM-killed at the ML training step (peaked ~3.7GB resident; the old 3g cap had no margin at all).
- `.dockerignore` was excluding `ml_training/models` wholesale, silently dropping the trainer source
  (`momentum_predictor.py`) from every image build (mechanism AND backend). Now only generated artifacts
  are excluded; the pipeline's `ml_training/models` mount changed from a host bind-mount to a named
  Docker volume (`ml_training_models`), which auto-seeds from the image's own content instead of
  silently shadowing it.
- The VPS `.env`'s `TELEGRAM_BOT_TOKEN` was stale/corrupted (49 chars vs. the real 48) from whenever the
  VPS env was first assembled, pre-dating Gate 4 — caused a `TelegramUnauthorizedError` crash loop on
  first bot start. Replaced with the correct value (transferred directly over SSH, never printed).

**Security finding, not yet remediated** (recorded per the owner's explicit instruction — no broad
rotation performed during this cutover since no confirmed exposure occurred and none was required to
complete Gate 4): running `docker compose config` from the repo root resolves services' `env_file:`
entries against the real repo-root `.env` **even when `--env-file docker/.env.example` is explicitly
passed** — confirmed empirically (a key that exists only in the real `.env`, absent from
`docker/.env.example` entirely, still resolved correctly in the rendered config). `docker/.env.example`
itself is NOT compromised (verified: its own on-disk secret fields are genuinely blank) — the risk is
local-only tooling behavior, not a committed leak, and does not affect the VPS (which correctly uses its
own real `.env` deliberately via `env_file: !override`). Recommend: investigate the root Compose
behavior and consider rotating the local dev-affected secrets (Telegram bot token, Telegram control
token, JWT secret, Alpaca key/secret, Tiingo key, SMTP password) next time credentials are touched.

## What is live today

| Component | Where | Schedule / state |
|---|---|---|
| Postgres `trading_production` | VPS, internal only | **authoritative**, always on |
| Backend | VPS, `ghcr.io/danielbugi/wolfx_alpha-backend:a55b35705380` | always on |
| Telegram bot `run_bot.py` | VPS, `ghcr.io/danielbugi/wolfx_alpha-mechanism:0fa7f8b98f71`, long polling | running |
| `donchian-pipeline.timer` (`automation_pipeline.sh`, incl. channel posts) | VPS systemd | 23:45 + 01:00 Israel |
| `donchian-firstlight1-prices.timer` | VPS systemd | 05:00 Israel |
| `donchian-notice-midday.timer` / `-evening.timer` | VPS systemd | 12:00 / 20:00 Israel |
| `donchian-earnings-today.timer` | VPS systemd | 11:00 Israel |
| `donchian-nightly-backup.timer` | VPS systemd | 23:30 UTC (≈ 02:30 Israel) |
| Dashboard frontend | local `next dev` (Vercel not yet activated — GATE 3) | on demand |
| Windows `trading_production` | Windows PC, localhost:5432 | **frozen, read-only reference**, not written to |
| Windows scheduled tasks (`DonchianScreenerDailyPipeline`, `FirstLight-1..5`) | Task Scheduler | **all Disabled**, kept for GATE 5 rollback only |
| Windows `run_bot.py` | — | **stopped**, not running |

All 5 new VPS timers use systemd's native per-timer `Asia/Jerusalem` calendar tag (not a hardcoded UTC
offset), so they self-adjust across DST — verified directly via `systemctl show -p TimersCalendar`.

Invariant for every step: **never two live schedulers or two bots at once** (duplicate channel posts;
two long-polling bots on one token fight for updates) — confirmed clean at Gate 4 cutover (Windows
disabled, VPS is the only live scheduler/bot for every job).

## GATE 1 + GATE 4 — database migration and scheduler cutover — DONE 2026-09-24

Executed as two separate windows in practice (Gate 1 approved and completed first; Gate 4 approved and
completed the same day, staged Phase A–E per the owner's explicit plan) rather than the single window
this section originally described — both steps below are historical/reference now, not a to-do list.

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
docs/history/planning-archive/INFRASTRUCTURE_PLAN.md §6.6; rotating `JWT_SECRET` signs everyone out once),
`ALLOWED_ORIGINS=https://dashboard.first-light.finance`, `API_SITE_ADDRESS=:80` until GATE 2.
Keys the production overlay needs are read from this file only — nothing falls back to
`docker/.env.example` anymore.

### Rollback (GATE 1/4) — UPDATED 2026-09-24, VPS has now accepted real production writes

**The simple "stop VPS, re-enable Windows" rollback described here originally no longer applies as-is.**
As of Gate 4 completion, `trading_production` on the VPS has accepted a real, successful pipeline write
that Windows's copy does not have — the two databases have diverged. Per the owner's explicit standing
instruction: **do not automatically fail back to Windows after VPS writes have occurred.** If rollback
is ever needed: (1) stop VPS timers and set `PROD_SENDING_ENABLED=0`, (2) stop the VPS bot, (3) preserve
the VPS database and its logs/state as-is — do not delete or overwrite it, (4) assess the actual
divergence between the two databases before deciding anything, (5) take a fresh backup of the VPS
database if the decision is to keep it, (6) only re-enable Windows writers after a deliberate decision
that Windows is the one to keep going forward (which would mean discarding everything the VPS wrote).
Windows itself was never written to during the migration or since, so it remains available as a frozen
reference point regardless.

Nightly `pg_dump` of the VPS database + off-box copy is now built, tested, and running
(`deploy/db/nightly_backup.sh`, `donchian-nightly-backup.timer`, `deploy/db/pull_nightly_backup.ps1`) —
no longer an open item.

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

## Known open items (updated 2026-09-24)

Resolved during Gate 1/4: `/opt/donchian/git-token` (removed — was a duplicate of the GHCR docker-login
credential, shredded after confirming nothing else referenced it); nightly `pg_dump` + off-box copy (see
Rollback section above).

Still open:
- GitHub Environment `production` has no required reviewer (only a `main` branch policy); the design
  assumed one. Add the owner as required reviewer in repo Settings → Environments.
- Hetzner Cloud Firewall (22/80/443) as an extra layer; the Docker-aware DOCKER-USER filter and UFW are in place.
- The off-box nightly backup copy (`deploy/db/pull_nightly_backup.ps1`) currently lands on the same
  Windows machine as the old production DB, not a genuinely independent third location (e.g. S3/B2) —
  no cloud storage credential exists yet.
- **Security remediation item** (found 2026-09-24, not yet fixed — see the status note at the top of this
  file for detail): `docker compose config` run from the repo root resolves real secrets from the repo
  root `.env` into rendered service config even when `--env-file docker/.env.example` is explicitly
  passed. Local-dev-only; does not affect the VPS. Investigate the Compose env-resolution precedence and
  consider rotating the locally-affected secrets next time credentials are touched.
