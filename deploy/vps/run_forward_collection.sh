#!/usr/bin/env bash
# /opt/donchian/scripts/run_forward_collection.sh
# COMMITTED BUT DORMANT: nothing installs this file, no unit is enabled, and even a unit that points at it refuses to run without the ARMING FILE below.
# The VPS copy is installed by an administrator (root SSH), exactly like every other wrapper here; neither CI nor CD can change /opt/donchian/scripts.
#
# One-shot `docker compose run --rm channel-sender -m forward_collection run --latest-completed --apply --with-sector-history-check --code-ref <sha>` at the
# mechanism image pinned in CURRENT_MECHANISM_SHA. Same shape as run_channel_sender.sh (flock, pinned image, one log line per run), but the target is a
# module, not a script path, so it needs PYTHONPATH=/app/mechanism (the channel-sender entrypoint is `python`). The runtime DB identity is whatever
# SENDER_DB_USER maps to (donchian_app in production); the collector needs SELECT+INSERT on the research tables only after migrations 24-31 and the roles script.
#
# ARMING FILE: /opt/donchian/FORWARD_COLLECTION_ARMED must exist, be a regular file, and contain EXACTLY the 12-hex tag in CURRENT_MECHANISM_SHA. It is
# created by hand at the activation step that enables the timer. Re-pinning the image disarms it until it is re-created, so a pin change can never
# silently start collecting with different code. Without it the wrapper exits 5 (refused) BEFORE any container or database is touched.
#
# `--with-sector-history-check` makes the collector VERIFY (read-only) that the fundamentals updater's sector recorder refreshed the session's universe in
# time (activation order: recorder first, capture next, timer last; see docs/operations/FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md).
#
# Exit statuses are the collector's own (forward_collection.contract): 0 complete | 2 incomplete | 3 another run holds the lock | 4 missed (the session can
# no longer be observed) | 5 refused (no trustworthy calendar / bad arguments / not armed). The unit's OnFailure= alert fires for 2/4/5 (3 alone is not an alert).
set -euo pipefail

BASE="${DONCHIAN_BASE:-/opt/donchian}"      # the override exists only so the refusal paths can be tested against a scratch directory
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/forward_collection_runs.log"
SHA_FILE="$BASE/CURRENT_MECHANISM_SHA"
ARMED_FILE="$BASE/FORWARD_COLLECTION_ARMED"

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- mechanism image not yet pinned" >&2; exit 5; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_MECHANISM_SHA is not a 12-hex sha: '$T'" >&2; exit 5; }
[ -f "$ARMED_FILE" ] || { echo "forward collection is not armed: $ARMED_FILE is absent (refusing before touching anything)" >&2; exit 5; }
[ "$(cat "$ARMED_FILE")" = "$T" ] || { echo "forward collection is armed for a different image than CURRENT_MECHANISM_SHA=$T (refusing)" >&2; exit 5; }

mkdir -p "$BASE/logs"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

cd "$COMPOSE_DIR"
exec 9>"$BASE/.forward_collection.lock"
flock -n 9 || { log "SKIP(locked): another forward-collection run is in progress"; exit 3; }

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

log "forward-collection starting (mechanism sha=$T)"
set +e
dc run --rm -e PYTHONPATH=/app/mechanism channel-sender -m forward_collection run --latest-completed --apply --with-sector-history-check --code-ref "$T" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e
log "forward-collection finished: exit $rc (mechanism sha=$T)"
exit "$rc"
