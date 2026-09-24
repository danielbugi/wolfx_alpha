#!/usr/bin/env bash
# /opt/donchian/scripts/deploy.sh <service> <image_tag>
#
# Deploys the backend at one immutable commit-SHA image tag, gated on the container's health check.
# Installed on the VPS from deploy/vps/ in the repo (see deploy/vps/README.md).
#
# - Only `backend` is deployable here. bot / pipeline / channel-sender are cutover-gated
#   (PRODUCTION_MIGRATION_RUNBOOK.md, CUTOVER) and never started by this script.
# - The Compose config used is the one shipped for THAT commit (releases/<tag>/), so a rollback
#   restores the matching config along with the matching image.
# - On a failed health check the previous good tag is restored automatically and the script still
#   exits non-zero, so the calling workflow fails loudly instead of reporting success.
# - Postgres is started if it isn't running, never recreated by a deploy.
set -euo pipefail

SERVICE="${1:?usage: deploy.sh backend <12-hex-sha>}"
TAG="${2:?usage: deploy.sh backend <12-hex-sha>}"
BASE=/opt/donchian
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/deploy.log"

if [ "$SERVICE" != "backend" ]; then
  echo "refused: only 'backend' is deployable by this script (bot/pipeline/channel-sender are cutover-gated)" >&2
  exit 2
fi
if ! [[ "$TAG" =~ ^[0-9a-f]{12}$ ]]; then
  echo "refused: image tag must be a 12-hex commit SHA (never :latest)" >&2
  exit 2
fi
for f in docker-compose.yml docker-compose.prod.yml docker/Caddyfile.prod; do
  [ -f "$BASE/releases/$TAG/$f" ] || { echo "release bundle for $TAG is missing $f" >&2; exit 1; }
done
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE" >&2; exit 1; }

# Compose stats the current directory while loading files; never depend on where the caller was
# (e.g. `su deploy` from /root fails with "stat .: permission denied").
cd "$COMPOSE_DIR"

exec 9>"$BASE/.deploy.lock"
flock -n 9 || { echo "another deploy/rollback is already running" >&2; exit 1; }

mkdir -p "$BASE/logs"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

PREV="$(cat "$BASE/CURRENT_SHA" 2>/dev/null || true)"
T=""

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

activate_config() {
  local rel="$BASE/releases/$1"
  install -m 644 "$rel/docker-compose.yml" "$rel/docker-compose.prod.yml" "$COMPOSE_DIR/"
  install -D -m 644 "$rel/docker/Caddyfile.prod" "$COMPOSE_DIR/docker/Caddyfile.prod"
}

wait_healthy() {
  local cid status
  for _ in $(seq 1 40); do
    cid="$(dc ps -q backend)"
    status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo missing)"
    [ "$status" = "healthy" ] && return 0
    [ "$status" = "unhealthy" ] && return 1
    sleep 3
  done
  return 1
}

# Every step checked explicitly: rollout() is called from an `if`, where bash disables `set -e`, and
# a silently failed pull/up would otherwise leave the OLD container running and pass the health gate.
rollout() {
  T="$1"
  activate_config "$T" || return 1
  dc pull backend || return 1
  if [ -z "$(dc ps -q --status running postgres)" ]; then
    dc up -d postgres || return 1
  fi
  dc up -d --no-deps backend || return 1
  local want got
  want="ghcr.io/danielbugi/wolfx_alpha-backend:$T"
  got="$(docker inspect -f '{{.Config.Image}}' "$(dc ps -q backend)" 2>/dev/null || true)"
  [ "$got" = "$want" ] || { echo "running backend image is '$got', expected '$want'" >&2; return 1; }
  wait_healthy
}

publish_proxy() {
  dc up -d --no-deps reverse-proxy || return 1
  # a changed Caddyfile is a bind mount Compose can't see; reload picks it up without dropping connections
  dc exec -T reverse-proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
}

log "deploy backend $TAG requested (current: ${PREV:-none})"
if rollout "$TAG"; then
  publish_proxy
  if [ -n "$PREV" ] && [ "$PREV" != "$TAG" ]; then
    echo "$PREV" > "$BASE/PREVIOUS_GOOD_SHA"
  fi
  echo "$TAG" > "$BASE/CURRENT_SHA"
  log "deploy backend $TAG OK (healthy); CURRENT_SHA=$TAG PREVIOUS_GOOD_SHA=$(cat "$BASE/PREVIOUS_GOOD_SHA" 2>/dev/null || echo none)"
  # keep the 10 most recent release bundles, never the current/previous ones
  keep="$(cat "$BASE/CURRENT_SHA") $(cat "$BASE/PREVIOUS_GOOD_SHA" 2>/dev/null || true)"
  ls -1t "$BASE/releases" 2>/dev/null | tail -n +11 | while read -r old; do
    case " $keep " in *" $old "*) ;; *) rm -rf "${BASE:?}/releases/$old" ;; esac
  done
  exit 0
fi

log "deploy backend $TAG FAILED health gate"
dc logs --tail 40 backend || true
if [ -n "$PREV" ] && [ "$PREV" != "$TAG" ]; then
  if rollout "$PREV"; then
    publish_proxy
    log "restored previous good backend $PREV after failed deploy of $TAG"
  else
    log "restoring $PREV ALSO failed health - manual intervention required"
  fi
fi
log "backend now running: $(docker inspect -f '{{.Config.Image}} ({{.State.Health.Status}})' "$(dc ps -q backend)" 2>/dev/null || echo 'none')"
exit 1
