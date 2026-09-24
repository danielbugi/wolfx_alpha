#!/bin/bash
# automation_pipeline.sh - Daily Trading Data Pipeline
# Order: index + price update -> freshness check -> CHANNEL POSTS (digest + card + buttons, momentum
# board, the day's rotating post) -> weekly/monthly/fundamentals/screener/ML -> session mark -> a retry
# of the channel posts (a no-op when they already went out). Sunday: only the weekly recap post.
# Usage: ./automation_pipeline.sh   (--force or FORCE=1 to bypass the trading-day gate)
# Scheduled (Task Scheduler "DonchianScreenerDailyPipeline", 2026-09-24) at 23:45 Israel time with a
# 01:00 backup trigger. 23:45 relies on MARKET_SETTLE_MINUTES=30 in .env: the gate counts a session as
# complete 30 min after the 16:00 ET close (22:30/23:30 Israel depending on the DST offset), so a trigger
# before that sees "nothing new yet" and skips -- re-derive that math before moving it earlier. A vendor
# bar that is not published yet fails the freshness check after step 2 (nothing posted, session unmarked)
# and the 01:00 trigger retries; while a run is going, the 01:00 trigger is ignored (IgnoreNew).
# FirstLight-1 (05:00, a lightweight price-only safety net) is unchanged. FirstLight-2 (the fixed
# 06:00 digest send) is DISABLED -- this script sends the posts itself right after it confirms fresh
# data, instead of racing a separate fixed clock time (which is what silently failed on 2026-09-23).

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
    # Sunday (Israel) has no session but is the day of the weekly recap post -- previously sent only by
    # run_first_light_morning.ps1 -Send (FirstLight-2, disabled). send_channel_posts.py picks the recap on a
    # Sunday by itself and has its own once-per-week gate (recap:prod), so the 01:00 and 23:45 Sunday
    # triggers send it once. Warn-only, like the other channel posts.
    if [ "$(date +%u)" = "7" ]; then
        log "${YELLOW}Sunday: sending the weekly recap post...${NC}"
        if "$PYTHON" mechanism/alerts/send_channel_posts.py --send --to prod 2>&1 | tee -a "$LOG_FILE"; then
            log_success "weekly recap step finished"
        else
            log "${RED}WARNING: weekly recap failed. Send manually: python mechanism/alerts/send_channel_posts.py --send --to prod${NC}"
        fi
    fi
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
TOTAL_STEPS=13

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

# Freshness check (2026-09-24). The run starts 45 min after the US close (23:45 Israel,
# MARKET_SETTLE_MINUTES=30). A vendor that has not published the day's bar yet returns the older bars
# WITHOUT an error, so step 2 "succeeds" with nothing new. Stop here instead: nothing is posted on stale
# data, the session stays unmarked, and the 01:00 backup trigger re-runs it. Skipped when there is no
# gate session (--force / a gate that failed open) because there is no session date to check against.
if [ -n "$SESSION_DATE" ]; then
    log "${YELLOW}Checking that ${SESSION_DATE} prices actually landed...${NC}"
    if "$PYTHON" mechanism/data_updaters/check_price_freshness.py --session "$SESSION_DATE" 2>&1 | tee -a "$LOG_FILE"; then
        log_success "price data is fresh"
    else
        handle_error "price freshness check (the ${SESSION_DATE} bar is not in yet - the 01:00 backup run will retry)"
    fi
fi

# Channel posts (2026-09-24): the FULL morning set -- digest with the market card + buttons, then the
# momentum board + the day's rotating post (send_channel_posts.py) -- the same set run_first_light_morning.ps1
# -Send sends (FirstLight-2, now disabled). Sent as soon as the prices are confirmed fresh: the posts read
# stock_prices, market_index_prices (step 1), digest_stocks (written by the digest itself) and each stock's
# latest known sector / market cap from daily_fundamentals (the previous day's refresh is fine for a sector
# and a "< $2B" tag), NOT the weekly/monthly/fundamentals/screener/ML steps below -- those take ~2 h and no
# post reads them. Warn-only: a Telegram problem must not fail the data/ML pipeline. Called again at the
# end of the run as a retry; both senders dedupe per session (data/session_state.json digest:prod /
# posts:prod), so a second call after a success posts nothing.
send_channel_posts() {
    local label="$1"
    log "${YELLOW}${label}: sending the channel digest (card + buttons)...${NC}"
    if "$PYTHON" mechanism/alerts/send_daily_digest.py --image --buttons --send --to prod 2>&1 | tee -a "$LOG_FILE"; then
        log_success "channel digest step finished"
        if "$PYTHON" mechanism/alerts/send_channel_posts.py --send --to prod 2>&1 | tee -a "$LOG_FILE"; then
            log_success "momentum board + daily post step finished"
        else
            log "${RED}WARNING: board / daily post failed. Send manually: python mechanism/alerts/send_channel_posts.py --send --to prod${NC}"
        fi
    else
        log "${RED}WARNING: channel digest send failed. Send manually: python mechanism/alerts/send_daily_digest.py --image --buttons --send --to prod${NC}"
    fi
}
send_channel_posts "Channel posts"

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

# Step 7: Earnings Calendar Update
# Added 2026-09-22 (DATA_ML_MILESTONES.md M2) -- batch-fetches each symbol's earnings report dates
# (past + upcoming) into earnings_calendar. Runs before step 8 (quarterly fundamentals) because that
# step's staleness gate now reads this table: a symbol is only re-fetched once a known report date has
# actually passed, not on a blind timer. Also feeds the channel's "who reports today" post (M3). Cheap
# after the first full run -- get_symbols_to_update() only refetches a symbol whose known calendar has
# gone stale (its last known date is in the past) or was never fetched.
run_step 7 "earnings calendar updater" mechanism/data_updaters/earnings_calendar_updater.py

# Step 8: Quarterly Fundamentals Update
# Added 2026-09-18 -- previously this was never part of the automated
# pipeline at all (manual/ad hoc only). Safe to run daily now:
# get_symbols_to_update() only actually calls the API for symbols with a real
# earnings-calendar report date since the last check (or, lacking that
# coverage, not checked in the last 25 days) -- see CLAUDE.md §4/§9 and
# DATA_ML_MILESTONES.md M2 for the staleness logic. The script itself always
# exits 0 even when some symbols fail (closed-end funds with no real
# quarterly financials are expected, not a pipeline error), so this step
# won't abort the pipeline over that.
run_step 8 "quarterly fundamentals updater" mechanism/data_updaters/quarterly_fundamentals_updater.py

# Step 9: Multi-timeframe Screening
run_step 9 "multi-timeframe screener" mechanism/screeners/multi_timeframe_screener.py

# Steps 10-12: ML dataset rebuild + daily retrain (added 2026-09-22, per user decision -- see
# DATA_ML_MILESTONES.md M1). Runs every night so any feature/label change is evaluated against the
# honest promotion gate the very next cycle; a day that does not clear the gate is not an error --
# momentum_predictor.py always exits 0 on a gate miss (it only writes a candidate report), and only
# exits non-zero on an actual data problem (e.g. an empty dataset) -- see run_step's normal failure
# handling below, which is intentionally NOT bypassed here. --replace does a full rebuild (~5 min for
# ~595k rows), not an incremental one -- see ml_training/data_preparation/build_dataset.py.
run_step 10 "ML dataset rebuild" ml_training/data_preparation/build_dataset.py --replace
run_step 11 "ML training (momentum target)" ml_training/models/momentum_predictor.py --target momentum
run_step 12 "ML training (plan_profit target)" ml_training/models/momentum_predictor.py --target plan_profit

# Record the session as done (only reached when every step above succeeded -- set -e).
if [ -n "$SESSION_DATE" ]; then
    "$PYTHON" mechanism/shared/market_calendar.py mark --key pipeline --session "$SESSION_DATE" | tee -a "$LOG_FILE"
fi

# Step 13: Send the channel digest -- only reached once every data/ML step above has actually
# succeeded (set -e) and the session above is marked processed, so the send is tied to confirmed
# fresh data rather than a fixed clock time. send_daily_digest.py has its own dedup
# (data/session_state.json's digest:<target> key) and staleness abort, so re-running this pipeline
# the same day is safe -- it will not double-post.
# Deliberately NOT run through run_step(): a digest-send problem (e.g. a Telegram outage) should
# not make Task Scheduler report the whole nightly data/ML pipeline as failed when steps 1-12 and
# the session mark above already succeeded -- so this warns and continues rather than exiting 1.
# 2026-09-24: now a RETRY of the channel posts sent right after step 5 (posts nothing if those went out).
send_channel_posts "Step 13/${TOTAL_STEPS} (retry)"

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
