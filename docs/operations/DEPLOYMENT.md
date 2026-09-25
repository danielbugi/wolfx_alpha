# Deployment — a practical guide

> **Purpose:** the actual steps to deploy a change to production, for each of backend, mechanism,
> frontend, and the database. For *why* the pipeline is shaped this way, see
> [../architecture/CI_CD.md](../architecture/CI_CD.md) — this document doesn't repeat that reasoning.
> **Source of truth:** `deploy/vps/deploy.sh`, `deploy/vps/ci-entry.sh`, `.github/workflows/cd.yml`.
> **Last verified:** 2026-09-25.

## 1. Backend

```bash
# 1. Land the change on main, confirm ci.yml is green.
# 2. Dispatch the CD workflow:
gh workflow run cd.yml -f service=backend -f confirm_ci_passed=yes
```
That's the entire manual step — `deploy` then runs automatically: pulls
`ghcr.io/danielbugi/wolfx_alpha-backend:<sha>`, brings the `backend` service up `--no-deps`,
confirms the running container's image actually matches the requested tag, waits for Docker's own
healthcheck, reloads Caddy, and records `CURRENT_SHA`/`PREVIOUS_GOOD_SHA` on the VPS. **A failed
health check auto-restores the previous good tag and the workflow still fails** — there is no
"deployed but unhealthy" state left behind.

`deploy.sh` refuses anything that isn't `backend` (mechanism/frontend are not deployable through
this path — see below) and refuses any tag that isn't a 12-hex commit SHA (never `:latest`).

**Verification after a deploy**: `curl https://api.first-light.finance/api/health`, then a real login
round trip if the change touches auth. `deploy/vps/auth_smoke.sh` automates the latter.

## 2. Mechanism (pipeline / bot / channel-sender)

There is **no automatic deploy path** for this image — by design, since starting the bot or pipeline
against a bad image risks two long-polling processes fighting over one Telegram token, not just a
failed HTTP check `deploy.sh`'s health gate could catch.

```bash
# 1. Land the change on main, confirm ci.yml is green.
# 2. Build and push only (deploy job is a deliberate no-op for this service):
gh workflow run cd.yml -f service=mechanism -f confirm_ci_passed=yes

# 3. Manually, on the VPS (root SSH) -- pin the new SHA in BOTH places (there is no automatic sync
#    between them; verified 2026-09-25 that these can genuinely disagree -- see the note below):
docker pull ghcr.io/danielbugi/wolfx_alpha-mechanism:<sha>
echo "<sha>" > /opt/donchian/CURRENT_MECHANISM_SHA          # read fresh, on every invocation, by
                                                              # run_pipeline.sh / run_channel_sender.sh
echo "IMAGE_TAG=<sha>" > /opt/donchian/env/mechanism_image_tag.env   # read by donchian-bot.service's
                                                                       # own EnvironmentFile=
```

**`pipeline` and `channel-sender` pick up the new pin automatically** — `run_pipeline.sh` and
`run_channel_sender.sh` both read `CURRENT_MECHANISM_SHA` fresh on every invocation (`docker compose
run --rm ...`, not a long-running container), so their *next scheduled fire* already uses the new
image. Nothing to restart for these two.

**`bot` does not** — it's a continuous, long-running container (`Type=oneshot RemainAfterExit=yes`,
started once via `docker compose up -d`), so updating the pin file alone changes nothing until it's
explicitly recreated:
```bash
systemctl restart donchian-bot.service
docker inspect -f '{{.Config.Image}}' $(docker ps -q -f name=donchian-screener-bot-1)   # confirm it moved
```
**Before restarting `bot` specifically**: confirm no other bot process is polling the same token
(the Windows `run_bot.py` should already be stopped — see
[../architecture/SCHEDULING.md](../architecture/SCHEDULING.md) §4 — but a stale VPS-side process from
a previous manual run is also possible; check `docker ps` first).

**Confirmed live 2026-09-25**: `CURRENT_MECHANISM_SHA` and `mechanism_image_tag.env` were both
already updated to the newest SHA (`56dd7f1fa63a`) as part of that day's Telegram-publishing
redesign rollout, but `donchian-bot.service` had not been restarted since — the running bot
container was still on the previous image (`0fa7f8b98f71`). Harmless (no bot-affecting code changed
that day), but a concrete example of exactly the gap this section describes: pinning the SHA and
restarting the bot are two separate steps, and it's easy to do the first without remembering the
second.

**PROPOSED, not yet applied (Phase 4A item 6, 2026-09-25)**: `deploy/vps/run_bot_service.sh` +
`deploy/vps/donchian-bot.service.proposed` would give the bot the same wrapper-script pattern as
`run_pipeline.sh`/`run_channel_sender.sh` — reading `CURRENT_MECHANISM_SHA` fresh at its own start
time instead of a separately-maintained `EnvironmentFile=mechanism_image_tag.env`, retiring that
second file as a pin source entirely. This does not remove the "bot needs an explicit restart to
pick up a new pin" step above (that's inherent to it being a continuous process, not a per-invocation
job) — it only removes the second file nothing keeps in sync with the first. See the proposal files'
own headers for the exact cutover + rollback steps; installing it on the VPS is a separate, explicitly
gated step, not yet done.

## 3. Frontend

Deployed by **Vercel's own GitHub integration**, not `cd.yml` — a push to `main` that touches
`frontend/` triggers Vercel's own build and deploy automatically. `frontend/Dockerfile` and the
`frontend` Compose service exist only as a documented break-glass fallback
(`profiles: ["self-hosted-frontend-fallback"]`), not the real production path. Rollback is Vercel's
own deployment history (instant rollback to any previous build from the Vercel dashboard).

## 4. Database migrations

1. Write the new migration file at the repo root of `mechanism/` (`add_<thing>_tables.sql` or
   `widen_<thing>.sql`), every statement idempotent (`IF NOT EXISTS`).
2. Add it to `docker-compose.yml`'s numbered `docker-entrypoint-initdb.d` mount list, **as the next
   number** — this is also what CI's `db-bootstrap-integration` job discovers and validates
   automatically (see [../architecture/DATABASE.md](../architecture/DATABASE.md)).
3. Confirm `ci.yml` is green (this proves the migration applies cleanly to a fresh schema).
4. Apply to production **manually** — there is no migration-on-deploy:
   ```bash
   # On the VPS, or via a tunnel:
   docker exec -i donchian-screener-postgres-1 psql -U trading_user -d trading_production \
     -v ON_ERROR_STOP=1 -f - < mechanism/add_<thing>_tables.sql
   ```
5. Verify with a real query afterward (the new table/column exists, has the expected constraints) —
   never assume a clean `psql` exit alone proves correctness.

**Never** apply a migration to production that hasn't first gone through CI's fresh-schema bootstrap
— a migration that only works against an already-drifted local schema is exactly how silent schema
drift happens.

## 5. Verification checklist after any production change

- `curl https://api.first-light.finance/api/health` — backend up.
- `systemctl list-timers --all | grep donchian` (on the VPS) — schedule intact, nothing newly failed.
- For a Telegram-publishing change: a **dry run** first (`publish_post_market.py` without `--send`),
  never a direct `--send --to prod` as the first real-world test.
- For a database change: the new object exists and has the expected constraints, via a real query.

## 6. When to stop and use rollback instead

If a deploy fails health checks, `deploy.sh`'s auto-rollback already handles the backend case. For
anything else that goes wrong after a deploy — see [ROLLBACK.md](ROLLBACK.md) for the decision
framework across all five failure scenarios it covers.
