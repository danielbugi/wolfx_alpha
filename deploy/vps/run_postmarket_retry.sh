#!/usr/bin/env bash
# /opt/donchian/scripts/run_postmarket_retry.sh
#
# The bounded post-market freshness retry (donchian-postmarket-retry.timer, ~23:45-06:00 Israel, every ~20
# min). Deliberately light: `docker compose run --rm channel-sender mechanism/alerts/publish_post_market.py`
# -- market index update + daily price update + freshness check + the four post-market posts (per-kind
# idempotency, mechanism/alerts/post_delivery.py) -- never the heavy weekly/monthly/fundamentals/quarterly/
# screener/ML stages (those live only in automation_pipeline.sh / donchian-pipeline.timer). Once a session's
# four posts are all delivered, every later invocation this same night is a fast no-op (post_delivery's claim
# finds everything already 'sent' and skips it) -- nothing here needs its own "stop after success" logic.
set -uo pipefail

BASE=/opt/donchian
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/postmarket_retry_runs.log"
SHA_FILE="$BASE/CURRENT_MECHANISM_SHA"

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- mechanism image not yet pinned" >&2; exit 1; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_MECHANISM_SHA is not a 12-hex sha: '$T'" >&2; exit 1; }

mkdir -p "$BASE/logs"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

cd "$COMPOSE_DIR"
exec 9>"$BASE/.postmarket_retry.lock"
flock -n 9 || { log "SKIP: another post-market retry is already in progress"; exit 0; }

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

log "post-market retry starting (mechanism sha=$T)"
if dc run --rm channel-sender mechanism/alerts/publish_post_market.py --send --to prod; then
  log "post-market retry OK (mechanism sha=$T)"
  exit 0
else
  rc=$?
  log "post-market retry exited $rc (mechanism sha=$T) -- a real post-send failure, or stale data (exit 0 expected there; check the log above)"
  exit "$rc"
fi
