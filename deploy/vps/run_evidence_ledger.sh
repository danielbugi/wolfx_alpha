#!/usr/bin/env bash
# /opt/donchian/scripts/run_evidence_ledger.sh [capture|report]
# COMMITTED BUT DORMANT: nothing installs this file, no unit is enabled, and `capture` refuses without BOTH its own pin and its own arming file (below). The VPS copy is
# installed by an administrator (root SSH) like every other wrapper here; neither CI nor CD can change /opt/donchian/scripts.
#
# One-shot `docker compose run --rm channel-sender mechanism/evidence_ledger/cli.py capture --latest-completed --store /ledger/ledger.jsonl` (docs/research/EVIDENCE_LEDGER.md):
# the Opportunity Intelligence briefs of the latest COMPLETED session on the market calendar, appended to the append-only, hash-chained ledger file
# /opt/donchian/evidence_ledger/ledger.jsonl (first capture wins; a re-run of a captured night appends nothing). The database is read READ ONLY (the job's
# session is read-only and the reads run inside a read-only transaction); it writes no table, sends nothing and calls no broker. `report` is read-only too and
# prints the summary only.
#
# ITS OWN PIN: /opt/donchian/CURRENT_EVIDENCE_LEDGER_SHA (12 hex) names the mechanism image this job runs. It is deliberately NOT CURRENT_MECHANISM_SHA: the
# pipeline, collector and notices keep their pin and arming file; this wrapper never reads or writes them, so arming the ledger never moves the pipeline image.
# ITS OWN ARMING FILE: /opt/donchian/EVIDENCE_LEDGER_ARMED must exist, be a regular file and contain EXACTLY the pin. Re-pinning disarms it.
#
# Exit statuses (the CLI's own): 0 captured / nothing new | 2 the session's prices are not in the database yet (the later fire retries) | 3 another capture holds
# the lock | 5 refused (not armed, bad pin, no trustworthy calendar) | 8 database connection failed | 9 the ledger file is corrupt (nothing appended; a human looks).
set -euo pipefail

MODE="${1:-capture}"
[ "$MODE" = capture ] || [ "$MODE" = report ] || { echo "usage: run_evidence_ledger.sh [capture|report]" >&2; exit 5; }
BASE="${DONCHIAN_BASE:-/opt/donchian}"      # the override exists only so the refusal paths can be tested against a scratch directory
COMPOSE_DIR="$BASE/compose"
ENV_FILE="$BASE/env/.env"
LOG="$BASE/logs/evidence_ledger_runs.log"
SHA_FILE="$BASE/CURRENT_EVIDENCE_LEDGER_SHA"
ARMED_FILE="$BASE/EVIDENCE_LEDGER_ARMED"
LEDGER_DIR="$BASE/evidence_ledger"

[ -f "$SHA_FILE" ] || { echo "missing $SHA_FILE -- no image pinned for the evidence ledger" >&2; exit 5; }
T="$(cat "$SHA_FILE")"
[[ "$T" =~ ^[0-9a-f]{12}$ ]] || { echo "CURRENT_EVIDENCE_LEDGER_SHA is not a 12-hex sha: '$T'" >&2; exit 5; }
if [ "$MODE" = capture ]; then
  [ -f "$ARMED_FILE" ] || { echo "the evidence ledger is not armed: $ARMED_FILE is absent (refusing before touching anything)" >&2; exit 5; }
  [ "$(cat "$ARMED_FILE")" = "$T" ] || { echo "the evidence ledger is armed for a different image than CURRENT_EVIDENCE_LEDGER_SHA=$T (refusing)" >&2; exit 5; }
fi
[ -d "$LEDGER_DIR" ] || { echo "missing $LEDGER_DIR -- created by the administrator at activation" >&2; exit 5; }

mkdir -p "$BASE/logs"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

cd "$COMPOSE_DIR"
exec 9>"$BASE/.evidence_ledger_run.lock"
flock -n 9 || { log "SKIP(locked): another evidence-ledger run is in progress"; exit 3; }

dc() {
  IMAGE_TAG="$T" docker compose --project-directory "$COMPOSE_DIR" \
    -f "$COMPOSE_DIR/docker-compose.yml" -f "$COMPOSE_DIR/docker-compose.prod.yml" \
    --env-file "$ENV_FILE" "$@"
}

log "evidence-ledger $MODE starting (image sha=$T)"
set +e
if [ "$MODE" = capture ]; then
  dc run --rm -e PGOPTIONS="-c default_transaction_read_only=on" -v "$LEDGER_DIR:/ledger" channel-sender \
     mechanism/evidence_ledger/cli.py capture --latest-completed --store /ledger/ledger.jsonl 2>&1 | tee -a "$LOG"
else
  dc run --rm -e PGOPTIONS="-c default_transaction_read_only=on" -v "$LEDGER_DIR:/ledger:ro" channel-sender \
     mechanism/evidence_ledger/cli.py report --store /ledger/ledger.jsonl 2>&1 | tee -a "$LOG"
fi
rc=${PIPESTATUS[0]}
set -e
log "evidence-ledger $MODE finished: exit $rc (image sha=$T)"
exit "$rc"
