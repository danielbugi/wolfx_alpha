#!/usr/bin/env bash
# /opt/donchian/scripts/run_channel_sender.sh <python script path> [args...]
#
# One-shot `docker compose run --rm channel-sender python <script> <args>` at the mechanism image
# pinned in CURRENT_MECHANISM_SHA. Used by the FirstLight-1/3/4/5-equivalent timers (price
# safety-net, twice-daily notices, earnings-today post). Each target script has its own
# PROD_SENDING_ENABLED / --send gate -- this wrapper does not add one.
set -euo pipefail

BASE=/opt/donchian
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/channel_sender_runs.log"
SHA_FILE="$BASE/CURRENT_MECHANISM_SHA"

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- mechanism image not yet pinned" >&2; exit 1; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_MECHANISM_SHA is not a 12-hex sha: '$T'" >&2; exit 1; }
[ "$#" -ge 1 ] || { echo "usage: run_channel_sender.sh <script.py> [args...]" >&2; exit 2; }

mkdir -p "$BASE/logs"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

cd "$COMPOSE_DIR"
LOCKNAME=".channel_sender_$(basename "$1" .py).lock"
exec 9>"$BASE/$LOCKNAME"
flock -n 9 || { log "SKIP: another run of $1 is already in progress"; exit 0; }

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

log "channel-sender starting: $* (mechanism sha=$T)"
if dc run --rm channel-sender "$@"; then
  log "channel-sender OK: $1 (mechanism sha=$T)"
  exit 0
else
  rc=$?
  log "channel-sender FAILED (exit $rc): $1 (mechanism sha=$T)"
  exit "$rc"
fi
