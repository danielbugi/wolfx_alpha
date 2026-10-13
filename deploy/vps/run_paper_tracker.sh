#!/usr/bin/env bash
# /opt/donchian/scripts/run_paper_tracker.sh [dry-run|run]
# COMMITTED BUT DORMANT: nothing installs this file, no unit is enabled, and `run` refuses without BOTH its own pin and its own arming file (below). The VPS copy would be installed by an administrator
# (root SSH) like every other wrapper here; neither CI nor CD can change /opt/donchian/scripts.
#
# One-shot `docker compose run --rm channel-sender -m research.paper_runner <mode> --next ...`: ONE trading session (the one right after the newest processed session) for all five registered strategy
# variants, in one transaction. It is the forward PAPER tracker: it opens no order, calls no broker, sends nothing, and writes only the three migration-36 tables.
#
# ITS OWN PIN: /opt/donchian/CURRENT_PAPER_SHA (12 hex) names the mechanism image this job runs. It is deliberately NOT CURRENT_MECHANISM_SHA: the collector, pipeline and notices keep their pin and arming file;
# this wrapper never reads or writes them. (An image only needs to exist in ghcr: building one is the mechanism CD's build-and-push, which deploys nothing.)
# ITS OWN ARMING FILE: /opt/donchian/PAPER_TRACKER_ARMED must exist, be a regular file and contain EXACTLY the pin. Re-pinning disarms it. `dry-run` is read-only and needs only the pin.
#
# Exit statuses: 0 processed or already processed | 2 input not ready yet (the timer's later fire retries) | 3 another run holds the lock (the wrapper's own) | 4 refused (a gap, a changed scope, a registry
# mismatch, a corrupted snapshot: a human looks) | 5 refused: not armed / bad pin | 6 up to date (the next session is not loaded yet) | 7 usage | 8 database connection failed. Only 0, 2, 3, 6 are success to systemd.
set -euo pipefail

MODE="${1:-run}"
[ "$MODE" = run ] || [ "$MODE" = dry-run ] || { echo "usage: run_paper_tracker.sh [dry-run|run]" >&2; exit 5; }
BASE="${DONCHIAN_BASE:-/opt/donchian}"      # the override exists only so the refusal paths can be tested against a scratch directory
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/paper_tracker_runs.log"
SHA_FILE="$BASE/CURRENT_PAPER_SHA"
ARMED_FILE="$BASE/PAPER_TRACKER_ARMED"

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- no image pinned for the paper tracker" >&2; exit 5; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_PAPER_SHA is not a 12-hex sha: '$T'" >&2; exit 5; }
if [ "$MODE" = run ]; then
  [ -f "$ARMED_FILE" ] || { echo "the paper tracker is not armed: $ARMED_FILE is absent (refusing before touching anything)" >&2; exit 5; }
  [ "$(cat "$ARMED_FILE")" = "$T" ] || { echo "the paper tracker is armed for a different image than CURRENT_PAPER_SHA=$T (refusing)" >&2; exit 5; }
fi

mkdir -p "$BASE/logs"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

cd "$COMPOSE_DIR"
exec 9>"$BASE/.paper_tracker.lock"
flock -n 9 || { log "SKIP(locked): another paper-tracker run is in progress"; exit 3; }

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

log "paper-tracker $MODE starting (image sha=$T)"
set +e
if [ "$MODE" = run ]; then
  dc run --rm -e PYTHONPATH=/app/mechanism -v "$ARMED_FILE:/armed/PAPER_TRACKER_ARMED:ro" channel-sender -m research.paper_runner run --next \
     --confirm-production trading_production --arming-file /armed/PAPER_TRACKER_ARMED --code-ref "$T" 2>&1 | tee -a "$LOG"
else
  dc run --rm -e PYTHONPATH=/app/mechanism channel-sender -m research.paper_runner dry-run --next --code-ref "$T" 2>&1 | tee -a "$LOG"
fi
rc=${PIPESTATUS[0]}
set -e
log "paper-tracker $MODE finished: exit $rc (image sha=$T)"
exit "$rc"
