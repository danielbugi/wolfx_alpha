# Production cutover plan (Windows PC → Hetzner VPS)

> **Status correction (2026-09-27, Phase 4C doc audit):** this file is the one actively-maintained
> infrastructure doc (per CLAUDE.md) and is supposed to track live state, but three claims below had
> gone stale since 2026-09-24 and were never updated: **GATE 2 and GATE 3 are actually DONE** (verified
> live 2026-09-27: both `https://api.first-light.finance/api/health` and
> `https://dashboard.first-light.finance` return HTTP 200 right now) — not "not yet done" as the
> section below still said; the earnings-today timer fires at **10:00 Israel**, not 11:00 (verified
> live via `systemctl show -p TimersCalendar` on the VPS 2026-09-27; SCHEDULING.md already had this
> right); and the "security finding, not yet remediated" about `docker compose config` leaking real
> secrets even with `--env-file docker/.env.example` **does not reproduce today** — tested empirically
> 2026-09-27 with the current `docker-compose.yml` (base file's `env_file:` points at the literal
> path `docker/.env.example`, unrelated to the `--env-file` CLI flag, which only affects `${...}`
> interpolation) and the real local `.env`: the base file alone correctly shows the placeholder value,
> only adding `-f docker-compose.prod.yml` resolves the real secret, exactly as ENVIRONMENT.md already
> documented. The narrative below is left as originally written (it's the record of what was true
> during the migration); corrected status lines are added inline rather than silently changing the
> history.

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

**Not yet done:** GATE 5 (Windows retirement). ~~GATE 2 (DNS), GATE 3 (Vercel/public routing)~~ — **DONE**
as of some point before 2026-09-27 (confirmed live that day; the exact completion date was not
recorded when it happened, and this line was never updated at the time). See §"GATE 2" / §"GATE 3"
below for the live verification.

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

**Security finding, recorded 2026-09-24 — does not reproduce as of 2026-09-27, see the correction
banner at the top of this file:** the original claim was that running `docker compose config` from
the repo root resolves services' `env_file:` entries against the real repo-root `.env` even when
`--env-file docker/.env.example` is explicitly passed. Re-tested empirically 2026-09-27 with the
current `docker-compose.yml`/`docker-compose.prod.yml` and the real local `.env`: the base compose
file alone (with or without `--env-file docker/.env.example`) correctly resolves to the placeholder
value; only adding `-f docker-compose.prod.yml` resolves the real secret, exactly as designed
(`env_file: !override`). Whether this was a real bug at the time that got fixed incidentally during
later work, or a testing mistake in the original finding, was not determined — either way, no
rotation is needed on this basis today. See [../dev/ENVIRONMENT.md](../dev/ENVIRONMENT.md)'s "secret
leak non-issue" section for the current, confirmed behavior.

## What is live today

| Component | Where | Schedule / state |
|---|---|---|
| Postgres `trading_production` | VPS, internal only | **authoritative**, always on |
| Backend | VPS, `ghcr.io/danielbugi/wolfx_alpha-backend:a55b35705380` | always on |
| Telegram bot `run_bot.py` | VPS, `ghcr.io/danielbugi/wolfx_alpha-mechanism:0fa7f8b98f71`, long polling | running |
| `donchian-pipeline.timer` (`automation_pipeline.sh`, incl. channel posts) | VPS systemd | 23:45 + 01:00 Israel |
| `donchian-firstlight1-prices.timer` | VPS systemd | 05:00 Israel |
| `donchian-notice-midday.timer` / `-evening.timer` | VPS systemd | 12:00 / 20:00 Israel |
| `donchian-earnings-today.timer` | VPS systemd | 10:00 Israel |
| `donchian-nightly-backup.timer` | VPS systemd | 23:30 UTC (≈ 02:30 Israel) |
| Dashboard frontend | Vercel, `dashboard.first-light.finance` (was local `next dev` at the time this table was written — GATE 3 is now done) | always on |
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

## GATE 2 — DNS — DONE (exact completion date not recorded; confirmed live 2026-09-27)

`api.first-light.finance` A/AAAA → 116.203.220.219 (TTL 300 during the change). Then set
`API_SITE_ADDRESS=api.first-light.finance` in the VPS `.env` and redeploy (Caddy obtains the certificate
itself). Verify `https://api.first-light.finance/api/health`. Rollback: remove the record / set `:80`.

**Verified live 2026-09-27:** `https://api.first-light.finance/api/health` returns HTTP 200.

## GATE 3 — Vercel — DONE (exact completion date not recorded; confirmed live 2026-09-27)

Import `frontend/` into Vercel, `NEXT_PUBLIC_API_BASE_URL=https://api.first-light.finance`, domain
`dashboard.first-light.finance`. Requires GATE 2 first (HTTPS API, else mixed-content block). Verify login
end to end from a browser. Rollback: remove the domain from the Vercel project.

**Verified live 2026-09-27:** `https://dashboard.first-light.finance` returns HTTP 200. A full login
round trip was not re-verified as part of this doc audit — only reachability.

## GATE 5 — retirement

After a stable period (suggest 2 weeks of clean VPS nights): delete the disabled Windows tasks, stop the
local Postgres, keep its final dump off-box.

## Known open items (updated 2026-09-24)

Resolved during Gate 1/4: `/opt/donchian/git-token` (removed — was a duplicate of the GHCR docker-login
credential, shredded after confirming nothing else referenced it); nightly `pg_dump` + off-box copy (see
Rollback section above).

Still open:
- GATE 5 (Windows retirement) — never approved, do not act on this without explicit separate approval.
- GitHub Environment `production` has no required reviewer (only a `main` branch policy); the design
  assumed one. Add the owner as required reviewer in repo Settings → Environments. (Re-confirmed live
  2026-09-25 via the GitHub API — see [../architecture/CI_CD.md](../architecture/CI_CD.md) §5.)
- Hetzner Cloud Firewall (22/80/443) as an extra layer; the Docker-aware DOCKER-USER filter and UFW are in place.
- The off-box nightly backup copy (`deploy/db/pull_nightly_backup.ps1`) currently lands on the same
  Windows machine as the old production DB, not a genuinely independent third location (e.g. S3/B2) —
  no cloud storage credential exists yet.

Resolved since this section was last written:
- ~~GATE 2 (DNS), GATE 3 (Vercel)~~ — done, see the sections above.
- ~~Security remediation item (`docker compose config` resolving real secrets even with
  `--env-file docker/.env.example`)~~ — does not reproduce today; see the correction banner at the top
  of this file. No rotation performed on this basis.
