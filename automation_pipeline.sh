#!/bin/bash
# automation_pipeline.sh - Daily Trading Data Pipeline
# Runs complete data update and screening pipeline
# Usage: ./automation_pipeline.sh

set -e  # Exit on any error
set -o pipefail  # A failing python step must fail the pipeline even piped through tee

# Configuration
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/pipeline_$(date +%Y%m%d_%H%M%S).log"

# Always run against the project's own venv, never whatever "python" happens
# to resolve to on PATH — that ambiguity is what let this drift out of sync
# with the pinned dependencies in the first place. See CLAUDE.md / MILESTONES.md.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/Scripts/python.exe"
if [ ! -f "$PYTHON" ]; then
    echo "ERROR: venv not found at $PYTHON — run: python -m venv .venv && .venv/Scripts/python.exe -m pip install -r mechanism/requirements.txt -r backend/requirements.txt -r ml_training/requirements.txt" >&2
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Logging function
log() {
    echo -e "$1" | tee -a "$LOG_FILE"
}

# Error handling function
handle_error() {
    log "${RED}ERROR: Pipeline failed at step: $1${NC}"
    log "${RED}Check log file: $LOG_FILE${NC}"
    exit 1
}

# Success function
log_success() {
    log "${GREEN}SUCCESS: $1${NC}"
}

# Start pipeline
log "${BLUE}========================================${NC}"
log "${BLUE}TRADING DATA AUTOMATION PIPELINE${NC}"
log "${BLUE}Started: $TIMESTAMP${NC}"
log "${BLUE}========================================${NC}"

START_TIME=$(date +%s)

# Trading-day gate (added 2026-09-21). Only run when a US session has COMPLETED that
# this pipeline has not already finished -- weekends, NYSE holidays and an already-done
# session skip the whole run (no ~3,000-symbol fetches). Compares the newest completed
# session on the NYSE calendar (Alpaca calendar, cached in data/) with the one recorded
# in data/session_state.json, which is written only at the very end of a SUCCESSFUL run,
# so a run that failed half-way is retried on the next invocation. See
# mechanism/shared/market_calendar.py. Override: ./automation_pipeline.sh --force  (or FORCE=1).
# A gate that itself breaks (exit code other than 0/3) fails OPEN: the pipeline runs.
GATE_ARGS=(--key pipeline)
if [ "${FORCE:-0}" = "1" ]; then GATE_ARGS+=(--force); fi
for arg in "$@"; do
    if [ "$arg" = "--force" ]; then GATE_ARGS+=(--force); fi
done
GATE_RC=0
GATE_OUT=$("$PYTHON" mechanism/shared/market_calendar.py gate "${GATE_ARGS[@]}") || GATE_RC=$?
SESSION_DATE=""
if [ "$GATE_RC" -eq 3 ]; then
    log "${YELLOW}SKIPPED: ${GATE_OUT}${NC}"
    exit 0
elif [ "$GATE_RC" -eq 0 ]; then
    SESSION_DATE="$GATE_OUT"
    log "${BLUE}Trading-day gate: new US session ${SESSION_DATE} to process${NC}"
else
    log "${YELLOW}WARNING: trading-day gate failed (exit ${GATE_RC}) - running anyway${NC}"
fi

# Run one pipeline step, streaming its output live to this terminal AND the
# log file (instead of the old `>> "$LOG_FILE" 2>&1`, which sent everything
# straight to the file and left the bash window showing nothing but the
# "Step N/5: Running..." line until the process exited several minutes
# later). Each updater now also draws its own live progress bar + a log
# line per symbol -- see ProgressBar in mechanism/shared/utils.py -- so this
# is where that becomes visible.
TOTAL_STEPS=8

run_step() {
    local step_num="$1" step_label="$2"; shift 2
    log "${YELLOW}Step ${step_num}/${TOTAL_STEPS}: Running ${step_label}...${NC}"
    if "$PYTHON" "$@" 2>&1 | tee -a "$LOG_FILE"; then
        log_success "${step_label} completed"
    else
        handle_error "${step_label}"
    fi
}

# Step 1: Market Index / Macro Data Update
# Added 2026-09-19, part of the dashboard data-representation review (see
# CLAUDE.md). Fetches S&P 500/Nasdaq/Russell 2000/Dow/VIX/10Y yield/Gold/
# Crude/DXY/BTC -- independent of the stock universe, so it runs first and
# fast (~10s once backfilled).
run_step 1 "market index updater" mechanism/data_updaters/market_index_updater.py

# Step 2: Daily Data Update
run_step 2 "daily data updater" mechanism/data_updaters/daily_data_updater.py

# Step 3: Weekly Data Update
run_step 3 "weekly data updater" mechanism/data_updaters/weekly_data_updater.py

# Step 4: Monthly Data Update
run_step 4 "monthly data updater" mechanism/data_updaters/monthly_data_updater.py

# Step 5: Daily Fundamentals Update
# Added 2026-09-19 -- previously manual-only (see CLAUDE.md §7.4). Unlike
# the quarterly updater below, this one has no staleness filter and isn't
# supposed to: market_cap/PE/PB/sector are price-derived and meant to
# refresh every trading day, so get_symbols_to_update() intentionally
# re-fetches the full active-symbol universe here (~25 min at its
# 0.5s/symbol rate-limit delay across ~3,000 symbols -- this is the
# slowest step in the pipeline by design, not a bug).
run_step 5 "daily fundamentals updater" mechanism/data_updaters/fundamentals_updater.py

# Step 6: Sector Performance Snapshot
# Added 2026-09-19, same session as step 1 -- persists one row/sector/day
# into sector_performance_daily so the dashboard's sector heatmap can grow a
# trend-chart view; previously this aggregate was only ever computed live,
# per-request, in backend/services/market_service.py. Runs after step 5 so
# sector tags are same-day fresh, and after step 2 so there's a same-day
# close to compare against prev_close.
run_step 6 "sector performance snapshot" mechanism/data_updaters/sector_performance_snapshot.py

# Step 7: Quarterly Fundamentals Update
# Added 2026-09-18 -- previously this was never part of the automated
# pipeline at all (manual/ad hoc only). Safe to run daily now:
# get_symbols_to_update() only actually calls the API for symbols not
# checked in the last 25 days (or with no data yet), so a normal day is a
# handful of requests, not ~3,000 -- see CLAUDE.md §4/§9 for the staleness
# logic. The script itself always exits 0 even when some symbols fail
# (closed-end funds with no real quarterly financials are expected, not a
# pipeline error), so this step won't abort the pipeline over that.
run_step 7 "quarterly fundamentals updater" mechanism/data_updaters/quarterly_fundamentals_updater.py

# Step 8: Multi-timeframe Screening
run_step 8 "multi-timeframe screener" mechanism/screeners/multi_timeframe_screener.py

# Record the session as done (only reached when every step above succeeded -- set -e).
if [ -n "$SESSION_DATE" ]; then
    "$PYTHON" mechanism/shared/market_calendar.py mark --key pipeline --session "$SESSION_DATE" | tee -a "$LOG_FILE"
fi

# Calculate total time
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
MINUTES=$((DURATION / 60))
SECONDS=$((DURATION % 60))

# Final success message
log "${BLUE}========================================${NC}"
log "${GREEN}PIPELINE COMPLETED SUCCESSFULLY!${NC}"
log "${GREEN}Total time: ${MINUTES}m ${SECONDS}s${NC}"
log "${GREEN}Log saved to: $LOG_FILE${NC}"
log "${BLUE}========================================${NC}"

# Show quick summary of results
if [ -f "frontend_data/latest_multi_timeframe_ml_enhanced.json" ]; then
    log "${BLUE}Latest screening results available at:${NC}"
    log "frontend_data/latest_multi_timeframe_ml_enhanced.json"
fi

log "Pipeline completed at: $(date +"%Y-%m-%d %H:%M:%S")"
