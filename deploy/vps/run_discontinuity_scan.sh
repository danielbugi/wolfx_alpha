#!/usr/bin/env bash
# /opt/donchian/scripts/run_discontinuity_scan.sh
# COMMITTED, NOT INSTALLED: nothing installs this file or enables its timer; an administrator does, after an owner approval (docs/operations/DISCONTINUITY_SCAN_AND_COLLECTOR_TIMING.md).
#
# The night's single price-discontinuity SCAN. It records the append-only `price_discontinuity_scan` evidence (migration 32) over the price state that is FINAL for the
# session, i.e. after every scheduled price writer has finished:
#   * the pipeline (23:45 / 01:00 Asia/Jerusalem), the post-market retry (23:45, then every 20 min 00:00-05:40, then 06:00 Asia/Jerusalem; it loads late-arriving bars),
#     and the 05:00 price safety net. The retry is the last scheduled writer; its final run starts at 06:00 and takes 9-16 minutes.
# It runs `build_dataset.py --scan-only --session latest-completed`: ONE execution, never a loop. The ML dataset is not read, rebuilt, truncated or altered; the original
# first-detected timestamps of the discontinuity rows are preserved; the target session must be the latest completed market session on the calendar; the database
# recomputes both fingerprints at insert and refuses the row if the prices changed while the scan ran (that run is recorded as a failed scan and exits non-zero).
#
# Coordination: it refuses to run before the final retry's SCHEDULED start (exit 6), then takes the retry's own lock (waiting for a running retry up to
# SCAN_RETRY_WAIT_SECONDS) and the pipeline's lock, and HOLDS both for the whole scan, so no retry or pipeline run can write prices underneath it (the retry wrapper
# skips when its lock is held). Exit 7 = a writer still held a lock after the wait bound (nothing was scanned).
# Exit statuses: 0 recorded (or an identical scan already existed) | 1 the database refused the scan evidence | 3 target session is not the latest completed / price data
# not at the session | 5 no trustworthy market calendar | 6 outside the safe window (the retry window 23:45-06:00) | 7 a price writer still running | 8 another scan is running | 9 setup problem.
set -uo pipefail

BASE="${DONCHIAN_BASE:-/opt/donchian}"      # the override exists only so the refusal paths can be tested against a scratch directory
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/discontinuity_scan_runs.log"
SHA_FILE="$BASE/CURRENT_MECHANISM_SHA"
NOT_BEFORE_LOCAL="${SCAN_NOT_BEFORE_LOCAL:-06:01}"          # one minute after the final post-market retry's scheduled start (06:00 Asia/Jerusalem)
NOT_AFTER_LOCAL="${SCAN_NOT_AFTER_LOCAL:-23:44}"            # the retry window reopens at 23:45 (first post-market retry of the next night)
RETRY_WAIT_SECONDS="${SCAN_RETRY_WAIT_SECONDS:-1020}"        # 17 min: the longest observed retry run is 16 min
PIPELINE_WAIT_SECONDS="${SCAN_PIPELINE_WAIT_SECONDS:-300}"
NOW_LOCAL="${SCAN_NOW_LOCAL:-$(TZ=Asia/Jerusalem date +%H:%M)}"

mkdir -p "$BASE/logs" 2>/dev/null || true
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- mechanism image not yet pinned" >&2; exit 9; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_MECHANISM_SHA is not a 12-hex sha: '$T'" >&2; exit 9; }

if [[ "$NOW_LOCAL" < "$NOT_BEFORE_LOCAL" || "$NOW_LOCAL" > "$NOT_AFTER_LOCAL" ]]; then
  log "REFUSED(retry window): $NOW_LOCAL Asia/Jerusalem is outside $NOT_BEFORE_LOCAL-$NOT_AFTER_LOCAL; the post-market retry runs 23:45-06:00 and may start and write prices"
  exit 6
fi

cd "$COMPOSE_DIR" 2>/dev/null || { echo "missing compose dir $COMPOSE_DIR" >&2; exit 9; }
exec 7>"$BASE/.discontinuity_scan.lock"
flock -n 7 || { log "SKIP(locked): another discontinuity scan is in progress"; exit 8; }
exec 9>"$BASE/.postmarket_retry.lock"
flock -w "$RETRY_WAIT_SECONDS" 9 || { log "REFUSED(retry still running): the post-market retry lock was not free within ${RETRY_WAIT_SECONDS}s; nothing was scanned"; exit 7; }
exec 8>"$BASE/.pipeline.lock"
flock -w "$PIPELINE_WAIT_SECONDS" 8 || { log "REFUSED(pipeline still running): the pipeline lock was not free within ${PIPELINE_WAIT_SECONDS}s; nothing was scanned"; exit 7; }

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

log "discontinuity scan starting (mechanism sha=$T, retry+pipeline locks held)"
set +e
dc run --rm --entrypoint python pipeline ml_training/data_preparation/build_dataset.py --scan-only --session latest-completed 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e
log "discontinuity scan finished: exit $rc (mechanism sha=$T)"
exit "$rc"
