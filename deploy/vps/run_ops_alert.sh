#!/usr/bin/env bash
# /opt/donchian/scripts/run_ops_alert.sh <job-name>
#
# The body of an OnFailure= alert service (first user: the research collector's alert unit). systemd 251+ hands the alert service the failed
# job's outcome as MONITOR_SERVICE_RESULT / MONITOR_EXIT_CODE / MONITOR_EXIT_STATUS / MONITOR_UNIT (production runs systemd 255).
#
# It does three things, in this order, and nothing else:
#   1. ALWAYS appends one line to $BASE/logs/ops_alerts.log (a durable record that does not depend on Telegram, the image or the journal);
#   2. once per job, UTC day and exit status (a stamp file under $BASE/.ops_alert/), asks the pinned mechanism image to send ONE message to the
#      owner's PRIVATE chat with the bot (mechanism/alerts/ops_failure_alert.py; never a channel; it also needs OPS_ALERTS_ENABLED=1 in .env);
#   3. exits 1 if that delivery failed, so the alert service itself shows as failed instead of hiding the problem.
# It never retries, restarts, re-arms or changes the failed job, and it reads no database. The detail it forwards is built here from one 12-hex
# file (CURRENT_MECHANISM_SHA), never from log contents.
#
# Test overrides: DONCHIAN_BASE (a scratch directory) and OPS_ALERT_SENDER (a stub instead of run_channel_sender.sh).
set -uo pipefail

BASE="${DONCHIAN_BASE:-/opt/donchian}"
LOG="$BASE/logs/ops_alerts.log"
STAMPS="$BASE/.ops_alert"
SENDER="${OPS_ALERT_SENDER:-$BASE/scripts/run_channel_sender.sh}"

JOB="${1:-${MONITOR_UNIT:-unknown}}"
JOB="${JOB%.service}"
[[ "$JOB" =~ ^[A-Za-z0-9@_.-]{1,80}$ ]] || { echo "run_ops_alert.sh: refusing an unexpected job name" >&2; exit 2; }
safe() { printf '%s' "${1:-}" | tr -cd 'A-Za-z0-9_.-' | cut -c1-40; }
RESULT="$(safe "${MONITOR_SERVICE_RESULT:-unknown}")"
STATUS="$(safe "${MONITOR_EXIT_STATUS:-}")"
CODE="$(safe "${MONITOR_EXIT_CODE:-}")"

mkdir -p "$BASE/logs" "$STAMPS"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; echo "$*"; }

# The only detail forwarded: the pinned image tag (12 hex). The job's arming state is deliberately NOT read here: only the job's own wrapper may
# refer to its arming file (pinned by test_dormant_units.py); the message explains what each exit status means instead.
PINNED="$(safe "$(cat "$BASE/CURRENT_MECHANISM_SHA" 2>/dev/null)")"
DETAIL="pinned mechanism image ${PINNED:-unknown}"

log "ALERT job=$JOB result=$RESULT code=$CODE status=${STATUS:-none} ${DETAIL}"

STAMP="$STAMPS/$JOB.$(date -u +%F).${STATUS:-none}"
if [ -e "$STAMP" ]; then
  log "ALERT job=$JOB: already alerted today for status ${STATUS:-none}; not sent again"
  exit 0
fi

if "$SENDER" mechanism/alerts/ops_failure_alert.py --unit "$JOB" --result "$RESULT" --exit-status "$STATUS" --detail "$DETAIL" --send; then
  : > "$STAMP"
  log "ALERT job=$JOB: handed to the owner alert (sent, or a dry run if OPS_ALERTS_ENABLED is not 1)"
  exit 0
else
  rc=$?
  log "ALERT job=$JOB: DELIVERY FAILED (sender exit $rc); the line above is the only record"
  exit 1
fi
