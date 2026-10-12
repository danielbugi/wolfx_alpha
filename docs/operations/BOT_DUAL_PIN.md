# The bot's separate image pin: findings and a proposed fix (NOT applied)

Status 2026-10-12. Owner decision: **do not change the bot image, the mechanism pin or the arming file now.** This document records what was verified on the VPS and proposes a
separate change so that a backend deployment can never recreate or retag the bot. Nothing here has been installed.

## What exists today (verified read-only on 2026-10-12)

| Consumer | Tag source | Value now |
|---|---|---|
| backend container | `/opt/donchian/CURRENT_SHA`, written by `deploy/vps/deploy.sh backend <sha>` | `575fa5dc0599` |
| pipeline, collector, scan, notices (oneshot units) | `/opt/donchian/CURRENT_MECHANISM_SHA`, read fresh by each wrapper; `FORWARD_COLLECTION_ARMED` holds the pin the collector was armed for | `5c511899505a` (armed file: same) |
| bot container (`donchian-bot.service`, `Type=oneshot RemainAfterExit`) | `EnvironmentFile=/opt/donchian/env/mechanism_image_tag.env` (`IMAGE_TAG=`), a third, separately maintained file | `575fa5dc0599` |

All three services are defined in one compose project and every image line uses the same variable, `${IMAGE_TAG}` (`docker-compose.prod.yml`).

## What actually happened on 2026-10-11 (the "unexpected" bot change)

- The bot container was created at 18:57:34Z on `mechanism:575fa5dc0599` (it had been on `e3feb64d3e86`). `mechanism_image_tag.env` was rewritten by root one second earlier (18:57:33Z).
- The systemd unit did not restart (`ActiveEnterTimestamp` 2026-09-24, `NRestarts=0`): the container was recreated by a direct `docker compose ... up -d bot`, i.e. a **deliberate manual bot update**
  (the bot-members/auto-access feature merged as PR #8), not a side effect of the backend deploy that ran 24 minutes earlier.
- `deploy/vps/deploy.sh` cannot do this: it refuses any service but `backend`, and recreates with `up -d --no-deps backend` (and `--no-deps reverse-proxy`). The deploy log shows backend entries only.

So the premise "a backend deployment recreated the bot" is not what the evidence shows. The real weaknesses are:

1. **One variable name for three services.** Any compose command run with `IMAGE_TAG=<x>` that is not pinned to a single service with `--no-deps` (for example a bare `up -d`) can retag whatever it touches. The only protection is operator discipline.
2. **Three pin files with no consistency check.** `CURRENT_SHA`, `CURRENT_MECHANISM_SHA`, `mechanism_image_tag.env` already disagree by design (backend and bot are at `575fa5d`, the collector pin is `5c51189`). Nothing
   records that this is intended, and nothing alerts if it stops being intended.
3. **The earlier proposal is now unsafe.** `deploy/vps/donchian-bot.service.proposed` + `run_bot_service.sh` make the bot read `CURRENT_MECHANISM_SHA`. With today's values that would **downgrade the bot to
   `5c511899505a` on its next restart** and silently remove the bot-members features. It must not be installed as written.

## Proposed fix (separate change, needs its own approval)

1. **Per-service tag variables** in `docker-compose.prod.yml`: `${BACKEND_IMAGE_TAG}`, `${BOT_IMAGE_TAG}`, `${MECHANISM_IMAGE_TAG}` (each `:?` required). A command that sets one cannot retag another.
2. **One helper** `deploy/vps/compose_env.sh` that every wrapper and `deploy.sh` source: it exports all three from their own pin files (`CURRENT_SHA`, new `CURRENT_BOT_SHA`, `CURRENT_MECHANISM_SHA`), validating
   12-hex format, so every compose invocation is fully and explicitly specified.
3. **A real bot pin file**, `/opt/donchian/CURRENT_BOT_SHA`, initialised to the image the bot runs *now* (`575fa5dc0599`), so the cutover is a no-op. `run_bot_service.sh` reads this file (not
   `CURRENT_MECHANISM_SHA`); `mechanism_image_tag.env` is retired. The bot is then updated only by writing `CURRENT_BOT_SHA` and restarting the unit.
4. **A blast-radius guard in `deploy.sh`**: record the image ID of every other running container of the project (bot, postgres, reverse-proxy) before the deploy and fail loudly (and log) if any
   differs afterwards; never auto-restart them.
5. **A static CI test**: the compose file uses a distinct tag variable per service; `deploy.sh` exports only the backend variable and always uses `--no-deps`; no script writes a pin file it does not own.
6. **Drift check** (read-only): a small script/timer-free check, run by hand before releases, that prints the three pins, the arming file and the running image of each container, and flags
   any disagreement that is not recorded in a `docs/operations` table.

Cutover (when approved): install the helper and the new pin file with today's values, deploy the compose change, `docker compose config` diff to prove every service resolves to the same image
as now, install the new unit file, `daemon-reload` only (no restart), and verify no container was recreated. The bot would pick up `CURRENT_BOT_SHA` at its next deliberate restart.
Rollback: restore the previous compose file and unit (kept as `.bak`); `mechanism_image_tag.env` is left in place until the next release.

Until then: **a backend deployment is safe for the bot** (`deploy.sh` is backend-only and `--no-deps`); the risk is a hand-run compose command, and the rule for those is to name one service and its
variable explicitly and to compare running images before and after.
