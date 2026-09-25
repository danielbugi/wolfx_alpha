#!/usr/bin/env bash
# /opt/donchian/scripts/run_pipeline.sh
#
# One production pipeline cycle (docker compose run --rm pipeline ./automation_pipeline.sh) at the
# mechanism image pinned in /opt/donchian/CURRENT_MECHANISM_SHA -- the same immutable-tag pattern
# deploy.sh uses for the backend. Sending is governed entirely by PROD_SENDING_ENABLED in the real
# .env (read by the mechanism code itself); this wrapper does not gate on it.
set -euo pipefail

BASE=/opt/donchian
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/pipeline_runs.log"
SHA_FILE="$BASE/CURRENT_MECHANISM_SHA"

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- mechanism image not yet pinned" >&2; exit 1; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_MECHANISM_SHA is not a 12-hex sha: '$T'" >&2; exit 1; }

mkdir -p "$BASE/logs"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

cd "$COMPOSE_DIR"
exec 9>"$BASE/.pipeline.lock"
flock -n 9 || { log "SKIP: another pipeline run is already in progress"; exit 0; }

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

log "pipeline run starting (mechanism sha=$T)"
if dc run --rm pipeline; then
  log "pipeline run OK (mechanism sha=$T)"
  exit 0
else
  rc=$?
  log "pipeline run FAILED (exit $rc, mechanism sha=$T)"
  exit "$rc"
fi
