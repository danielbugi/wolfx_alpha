#!/usr/bin/env bash
# /opt/donchian/scripts/firstlight1_updateonly.sh
#
# VPS equivalent of the Windows FirstLight-1-UpdatePrices task
# (run_first_light_morning.ps1 -UpdateOnly -To prod): a 05:00 Israel price safety-net ahead of the
# 23:45 pipeline's own run. Same three steps, same order, same fail/continue behavior as the PS1:
#   1. trading-day gate (key digest:prod) -- skip entirely if nothing new
#   2. market index updater -- failure is a WARNING only (continues)
#   3. daily price updater -- failure ABORTS (no snapshot from stale data)
#   4. send_daily_digest.py --snapshot-only -- saves today's snapshot even though nothing is posted
set -uo pipefail
BASE=/opt/donchian
RUN="$BASE/scripts/run_channel_sender.sh"
LOG="$BASE/logs/channel_sender_runs.log"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

"$RUN" mechanism/shared/market_calendar.py gate --key digest:prod
gate=$?
if [ "$gate" -eq 3 ]; then
  log "firstlight1_updateonly SKIPPED: no new session for digest:prod"
  exit 0
fi
if [ "$gate" -ne 0 ]; then
  log "firstlight1_updateonly WARNING: trading-day gate itself failed (exit $gate) - continuing (fails open)"
fi

"$RUN" mechanism/data_updaters/market_index_updater.py \
  || log "firstlight1_updateonly WARNING: index update failed - market card will show n/a for index tiles; continuing"

"$RUN" mechanism/data_updaters/daily_data_updater.py
rc=$?
if [ "$rc" -ne 0 ]; then
  log "firstlight1_updateonly ABORT: price update failed (exit $rc), nothing snapshotted from stale data"
  exit "$rc"
fi

"$RUN" mechanism/alerts/send_daily_digest.py --snapshot-only
rc=$?
[ "$rc" -eq 0 ] || log "firstlight1_updateonly WARNING: snapshot step exited $rc (bot keeps showing the previous session)"
log "firstlight1_updateonly finished"
exit 0
