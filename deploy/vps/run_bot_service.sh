#!/usr/bin/env bash
# /opt/donchian/scripts/run_bot_service.sh <up|stop>
#
# PROPOSED (Phase 4A item 6, 2026-09-25) -- NOT YET INSTALLED ON THE VPS. See
# deploy/vps/donchian-bot.service's own header for the design rationale and the exact cutover +
# rollback steps. Do not apply either file to the live VPS without separate, explicit approval.
#
# ExecStart/ExecStop for donchian-bot.service -- reads the mechanism image tag fresh from
# CURRENT_MECHANISM_SHA on every invocation, exactly like every other wrapper script in this
# directory (run_pipeline.sh, run_postmarket_retry.sh, run_channel_sender.sh,
# firstlight1_updateonly.sh). Replaces the unit's former EnvironmentFile=
# /opt/donchian/env/mechanism_image_tag.env + embedded docker compose command -- that was the one
# unit with a separate, independently-maintained pin file, and nothing kept the two in sync: confirmed
# live 2026-09-25 that CURRENT_MECHANISM_SHA (56dd7f1fa63a) and mechanism_image_tag.env's IMAGE_TAG
# had already drifted apart from the running container's actual image (0fa7f8b98f71) at least once.
set -euo pipefail

MODE="${1:?usage: run_bot_service.sh <up|stop>}"
BASE=/opt/donchian
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
SHA_FILE="$BASE/CURRENT_MECHANISM_SHA"

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- mechanism image not yet pinned" >&2; exit 1; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_MECHANISM_SHA is not a 12-hex sha: '$T'" >&2; exit 1; }

cd "$COMPOSE_DIR"
dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

case "$MODE" in
  up)   dc --profile bot up -d bot ;;
  stop) dc --profile bot stop bot ;;
  *)    echo "unknown mode '$MODE' (expected up|stop)" >&2; exit 2 ;;
esac
