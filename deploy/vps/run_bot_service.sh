#!/usr/bin/env bash
# /opt/donchian/scripts/run_bot_service.sh <up|stop>
#
# PROPOSED -- NOT INSTALLED ON THE VPS. REPLACES the earlier 2026-09-25 proposal, which read CURRENT_MECHANISM_SHA and would, today, DOWNGRADE the bot (docs/operations/BOT_DUAL_PIN.md).
#
# ExecStart/ExecStop for donchian-bot.service. The bot's image comes from its OWN pin file, /opt/donchian/CURRENT_BOT_SHA, through compose_env.sh (installed beside this script), so neither a backend
# deploy (CURRENT_SHA) nor a mechanism pin change (CURRENT_MECHANISM_SHA) can retag it. `up` recreates ONLY the bot (--no-deps) and refuses to start if postgres is not already running.
set -euo pipefail

MODE="${1:?usage: run_bot_service.sh <up|stop>}"
BASE="${DONCHIAN_BASE:-/opt/donchian}"
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
# shellcheck source=compose_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/compose_env.sh"
compose_env_export

cd "$COMPOSE_DIR"
dc() {
  docker compose --project-directory "$COMPOSE_DIR" -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" --env-file "$ENV_FILE" "$@"
}

case "$MODE" in
  up)
    [ -n "$(dc ps -q --status running postgres)" ] || { echo "run_bot_service: postgres is not running; not starting the bot" >&2; exit 1; }
    dc --profile bot up -d --no-deps bot
    ;;
  stop) dc --profile bot stop bot ;;
  *) echo "unknown mode '$MODE' (expected up|stop)" >&2; exit 2 ;;
esac
