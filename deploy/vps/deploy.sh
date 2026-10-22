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
#
# Rollback pointers (2026-10-22 fix -- a rollback once recorded the release it rolled back FROM as the
# "previous good" one, so the next rollback would have redeployed the known-slow image):
#   CURRENT_SHA        the release running now (written only after a healthy rollout).
#   PREVIOUS_GOOD_SHA  what rollback.sh restores. Invariant: never a release listed in FAILED_SHAS.
#   KNOWN_GOOD_SHAS    releases that ran healthy and were later replaced by a normal deploy (newest last).
#   FAILED_SHAS        releases that failed the health gate, or that an operator rolled back from.
# DEPLOY_MODE=rollback (set only by rollback.sh) marks the release being left as failed and points
# PREVIOUS_GOOD_SHA at the next older known-good release; a normal deploy records the replaced release as
# known-good. A release in FAILED_SHAS is refused unless ALLOW_FAILED_TAG=1 (a deliberate operator retry).
# Missing record files are treated as empty, so this is compatible with a host that has only the two pointers.
set -euo pipefail

SERVICE="${1:?usage: deploy.sh backend <12-hex-sha>}"
TAG="${2:?usage: deploy.sh backend <12-hex-sha>}"
BASE=/opt/donchian
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/deploy.log"
KNOWN_GOOD="$BASE/KNOWN_GOOD_SHAS"
FAILED="$BASE/FAILED_SHAS"
MODE="${DEPLOY_MODE:-deploy}"

if [ "$MODE" != "deploy" ] && [ "$MODE" != "rollback" ]; then
  echo "refused: DEPLOY_MODE must be 'deploy' or 'rollback'" >&2
  exit 2
fi
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

in_list() { [ -n "$1" ] && [ -f "$2" ] && grep -qx "$1" "$2"; }
add_to() { in_list "$1" "$2" || echo "$1" >> "$2"; }
# A usable rollback target: a well-formed 12-hex tag whose release bundle is still on disk and that is not failed.
# Corrupt history lines (garbage, wrong length, a pruned bundle) are skipped, never deployed.
usable_target() { [[ "$1" =~ ^[0-9a-f]{12}$ ]] && [ -f "$BASE/releases/$1/docker-compose.yml" ] && ! in_list "$1" "$FAILED"; }
# newest known-good (health-verified, then replaced by a normal deploy) usable release other than $1; empty if none
newest_good_except() {
  [ -f "$KNOWN_GOOD" ] || return 0
  local s
  while read -r s; do
    [ -z "$s" ] || [ "$s" = "$1" ] && continue
    usable_target "$s" || continue
    echo "$s"; return 0
  done < <(tac "$KNOWN_GOOD")
}
# enforce the invariant: PREVIOUS_GOOD_SHA is empty or a usable, non-failed release other than the one running now
heal_previous() {
  local p cur
  p="$(cat "$BASE/PREVIOUS_GOOD_SHA" 2>/dev/null || true)"
  cur="$(cat "$BASE/CURRENT_SHA" 2>/dev/null || true)"
  if [ -n "$p" ] && { ! usable_target "$p" || [ "$p" = "$cur" ]; }; then
    echo "$(newest_good_except "$cur")" > "$BASE/PREVIOUS_GOOD_SHA"
    log "PREVIOUS_GOOD_SHA '$p' is failed, unusable or current -> '$(cat "$BASE/PREVIOUS_GOOD_SHA")'"
  fi
}

if in_list "$TAG" "$FAILED" && [ "${ALLOW_FAILED_TAG:-}" != "1" ]; then
  log "refused: $TAG is recorded in FAILED_SHAS (set ALLOW_FAILED_TAG=1 to retry it deliberately)"
  exit 2
fi

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

log "$MODE backend $TAG requested (current: ${PREV:-none})"
if rollout "$TAG"; then
  publish_proxy
  if [ -n "$PREV" ] && [ "$PREV" != "$TAG" ]; then
    if [ "$MODE" = "rollback" ]; then
      # the release we rolled back FROM is failed; the fallback becomes the next older known-good one
      add_to "$PREV" "$FAILED"
      echo "$(newest_good_except "$TAG")" > "$BASE/PREVIOUS_GOOD_SHA"
    elif in_list "$PREV" "$FAILED"; then
      :   # a release already recorded as failed never becomes the rollback target
    else
      echo "$PREV" > "$BASE/PREVIOUS_GOOD_SHA"
      add_to "$PREV" "$KNOWN_GOOD"
    fi
  fi
  echo "$TAG" > "$BASE/CURRENT_SHA"
  heal_previous
  log "$MODE backend $TAG OK (healthy); CURRENT_SHA=$TAG PREVIOUS_GOOD_SHA=$(cat "$BASE/PREVIOUS_GOOD_SHA" 2>/dev/null || echo none)"
  # keep the 10 most recent release bundles, never the current/previous ones nor the 3 newest known-good ones
  keep="$(cat "$BASE/CURRENT_SHA") $(cat "$BASE/PREVIOUS_GOOD_SHA" 2>/dev/null || true) $(tail -n 3 "$KNOWN_GOOD" 2>/dev/null | tr '\n' ' ')"
  ls -1t "$BASE/releases" 2>/dev/null | tail -n +11 | while read -r old; do
    case " $keep " in *" $old "*) ;; *) rm -rf "${BASE:?}/releases/$old" ;; esac
  done
  exit 0
fi

log "$MODE backend $TAG FAILED health gate"
add_to "$TAG" "$FAILED"
heal_previous
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
