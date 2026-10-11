#!/usr/bin/env bash
# R1-R3 first-scheduled-scan confirmation. READ-ONLY and unattended: scan evidence, then the corrected checkpoint (Verdict A and Verdict B kept apart).
# Runs as an unprivileged user from a root-owned, hash-pinned bundle (/opt/donchian/checkpoint-r13). It writes only its own report files.
# It never changes production: every SQL statement is read-only, the collector dry run has no --apply, no unit/flag/pin/arming file/data is touched.
set -u
export LC_ALL=C PATH="${CHECKPOINT_PATH_PREFIX:+$CHECKPOINT_PATH_PREFIX:}/usr/sbin:/usr/bin:/sbin:/bin"      # the optional prefix exists only so tests can put stub binaries first
DIR="${R13_DIR:-/opt/donchian/checkpoint-r13}"
BASE="${R13_BASE:-/opt/donchian}"
OUT="${R13_OUT:-/home/deploy/checkpoint-r13/reports}"
PG=donchian-screener-postgres-1
SESSION="${R13_SESSION:-2026-10-09}"
PIN="${R13_PIN:-5c511899505a}"
SELFTEST="${CHECKPOINT_SELFTEST:-0}"
mkdir -p "$OUT"; umask 027
TS=$(date -u +%Y%m%dT%H%M%SZ); [ "$SELFTEST" = 1 ] && TAG=selftest || TAG=confirm
REPORT="$OUT/r13_${TAG}_$TS.txt"; STATUS="$OUT/r13_${TAG}_$TS.status"
exec > >(tee -a "$REPORT") 2>&1
say() { echo "$*"; }
qro() { docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$PG" psql -U trading_user -d trading_production -X -At -F'|' "$@"; }
EV_FAIL=0
ev() { say "EVID|$1|$2${3:+|$3}"; if [ "$2" = FAIL ]; then EV_FAIL=1; fi; return 0; }

say "R13|start_utc=$(date -u +%FT%TZ)|selftest=$SELFTEST|user=$(id -un)|session=$SESSION|pin=$PIN"
if ! ( cd "$DIR" && sha256sum -c SHA256SUMS ) >/dev/null 2>&1; then say "R13|REFUSED: bundle hash mismatch"; echo "exit_code=5" > "$STATUS"; exit 5; fi
say "R13|bundle verified: $(cd $DIR && sha256sum SHA256SUMS | cut -c1-16)"

say "== SCAN SERVICE"
for p in Result ExecMainStatus ExecMainStartTimestamp ExecMainExitTimestamp; do say "SCANSVC|$p=$(systemctl show donchian-discontinuity-scan.service -p $p --value)"; done
say "SCANSVC|timer=$(systemctl is-enabled donchian-discontinuity-scan.timer)/$(systemctl is-active donchian-discontinuity-scan.timer)"
LOG=$BASE/logs/discontinuity_scan_runs.log
{ [ -f "$LOG" ] && tail -n 14 "$LOG" || echo "(no scan log yet)"; } 2>&1 | cut -c1-200 | sed 's/^/SCANLOG|/'
if [ "$SELFTEST" != 1 ]; then
  [ "$(systemctl show donchian-discontinuity-scan.service -p Result --value)" = success ] && [ "$(systemctl show donchian-discontinuity-scan.service -p ExecMainStatus --value)" = 0 ] && ev scan_service_exit_status_0 PASS || ev scan_service_exit_status_0 FAIL
  START=$(systemctl show donchian-discontinuity-scan.service -p ExecMainStartTimestamp --value)
  S_EPOCH=$(date -u -d "$START" +%s 2>/dev/null || echo 0)
  HM=$(date -u -d "$START" +%H:%M 2>/dev/null || echo none)
  case "$HM" in 03:20|03:21|04:20|04:21) ev scan_started_at_the_scheduled_06_20_jerusalem_window PASS "$START" ;; *) ev scan_started_at_the_scheduled_06_20_jerusalem_window REVIEW "start=$START (06:20 Asia/Jerusalem is 03:20Z in summer, 04:20Z in winter)" ;; esac
  grep -q "locks held" "$LOG" && ev scan_log_records_retry_and_pipeline_locks_held PASS || ev scan_log_records_retry_and_pipeline_locks_held FAIL
  ! grep -qE "REFUSED|SKIP" "$LOG" && ev scan_not_refused_or_skipped PASS || ev scan_not_refused_or_skipped FAIL "$(grep -E 'REFUSED|SKIP' $LOG | tail -1 | cut -c1-120)"
  # no overlap with the post-market retry: the retry runs logged around the scan must have finished before the scan started
  RLOG=$BASE/logs/postmarket_retry_runs.log
  LAST_RETRY_START=$(grep 'post-market retry starting' "$RLOG" | tail -1 | cut -d' ' -f1); LAST_RETRY_END=$(grep -E 'post-market retry (OK|exited)' "$RLOG" | tail -1 | cut -d' ' -f1)
  SCAN_LOG_START=$(grep 'discontinuity scan starting' "$LOG" | head -1 | cut -d' ' -f1)
  say "TIMELINE|last_retry_start=$LAST_RETRY_START last_retry_end=$LAST_RETRY_END scan_log_start=$SCAN_LOG_START"
  { [ -n "$SCAN_LOG_START" ] && [ -n "$LAST_RETRY_END" ] && ! [[ "$LAST_RETRY_START" > "$SCAN_LOG_START" ]] && ! [[ "$LAST_RETRY_END" > "$SCAN_LOG_START" ]]; } && ev scan_started_after_the_final_retry_finished_no_overlap PASS || ev scan_started_after_the_final_retry_finished_no_overlap REVIEW "see TIMELINE"
fi

say "== SCAN ROWS, FINGERPRINTS AND INTEGRITY (read-only SQL)"
qro <<SQL | sed 's/^/SQL|/'
select 'scan_row', id, session_date, status, left(input_fingerprint,12), left(result_fingerprint,12), n_price_rows, n_symbols, n_discontinuities, finished_at, code_ref,
  (input_fingerprint = (select fingerprint from research_price_input_fingerprint(session_date))) as input_matches_db_now,
  (result_fingerprint = (select fingerprint from research_discontinuity_result_fingerprint())) as result_matches_db_now
from price_discontinuity_scan order by id;
select 'dataset', (select count(*) from ml_breakout_dataset_v2), c.relfilenode, s.n_tup_ins, s.n_tup_upd, s.n_tup_del from pg_class c join pg_stat_user_tables s on s.relid=c.oid where c.relname='ml_breakout_dataset_v2';
select 'disc_all', count(*), md5(string_agg(symbol||'|'||date||'|'||kind||'|'||detected_at::text, ',' order by symbol,date,kind)) from price_discontinuities;
select 'disc_legacy_1040', count(*), md5(string_agg(symbol||'|'||date||'|'||kind||'|'||detected_at::text, ',' order by symbol,date,kind)) from price_discontinuities where detected_at between timestamp '2026-10-08 03:08:00' and timestamp '2026-10-08 03:12:00';
select 'prices', max(date), count(*) from stock_prices;
SQL
if [ "$SELFTEST" != 1 ]; then
  R=$(qro -c "select id, status, session_date, (input_fingerprint = (select fingerprint from research_price_input_fingerprint(session_date))), (result_fingerprint = (select fingerprint from research_discontinuity_result_fingerprint())), code_ref from price_discontinuity_scan order by id desc limit 1")
  IFS='|' read -r SID SST SSD SIN SRE SCR <<< "$R"
  say "LATEST_SCAN|id=$SID status=$SST session=$SSD input_ok_now=$SIN result_ok_now=$SRE code_ref=$SCR"
  [ "$SSD" = "$SESSION" ] && [ "$SST" = complete ] && ev scan_targets_the_latest_completed_session PASS "$SSD" || ev scan_targets_the_latest_completed_session FAIL "$SSD/$SST"
  [ "$SIN" = t ] && [ "$SRE" = t ] && ev latest_scan_fingerprints_match_the_database_now PASS || ev latest_scan_fingerprints_match_the_database_now FAIL
  case "$SCR" in *scan_only@v1) ev latest_scan_written_by_the_scan_only_path PASS ;; *) ev latest_scan_written_by_the_scan_only_path FAIL "$SCR" ;; esac
  D=$(qro -c "select count(*)||'|'||c.relfilenode||'|'||s.n_tup_ins||'|'||s.n_tup_upd||'|'||s.n_tup_del from pg_class c join pg_stat_user_tables s on s.relid=c.oid, ml_breakout_dataset_v2 d where c.relname='ml_breakout_dataset_v2' group by c.relfilenode, s.n_tup_ins, s.n_tup_upd, s.n_tup_del")
  [ "$D" = "600584|17065|7778944|0|7178360" ] && ev ml_dataset_untouched_same_rows_file_and_dml_counters PASS "$D" || ev ml_dataset_untouched_same_rows_file_and_dml_counters REVIEW "now=$D baseline=600584|17065|7778944|0|7178360 (a weekday pipeline run legitimately rebuilds it)"
  L=$(qro -c "select count(*)||'|'||md5(string_agg(symbol||'|'||date||'|'||kind||'|'||detected_at::text, ',' order by symbol,date,kind)) from price_discontinuities where detected_at between timestamp '2026-10-08 03:08:00' and timestamp '2026-10-08 03:12:00'")
  [ "$L" = "1040|4fd34882fe0914910b7736892bfb5732" ] && ev all_1040_legacy_first_detected_stamps_unchanged PASS || ev all_1040_legacy_first_detected_stamps_unchanged FAIL "$L"
  A=$(qro -c "select count(*)||'|'||md5(string_agg(symbol||'|'||date||'|'||kind||'|'||detected_at::text, ',' order by symbol,date,kind)) from price_discontinuities")
  [ "$A" = "1041|f06fc739755e2b1399892a9d32d4a47c" ] && ev all_1041_first_detected_stamps_unchanged PASS || ev all_1041_first_detected_stamps_unchanged REVIEW "$A"
fi
# ---- POST-ACTIVATION block (R13_POST=1): the FIRST scheduled collector execution for the session, read-only
if [ "${R13_POST:-0}" = 1 ]; then
  say "== COLLECTOR EXECUTION (post-activation), target session $SESSION"
  CLOG=$BASE/logs/forward_collection_runs.log
  HIST="${R13_HIST_SESSION:-2026-10-09}"
  for p in Result ExecMainStatus ExecMainStartTimestamp ExecMainExitTimestamp; do say "COLLSVC|$p=$(systemctl show donchian-forward-collection.service -p $p --value)"; done
  say "COLLSVC|timer=$(systemctl is-enabled donchian-forward-collection.timer)/$(systemctl is-active donchian-forward-collection.timer) armed=$([ -e $BASE/FORWARD_COLLECTION_ARMED ] && cat $BASE/FORWARD_COLLECTION_ARMED || echo absent)"
  RUNS=$(python3 "$DIR/r13_runs.py" "$CLOG" "$SESSION"); echo "$RUNS" | sed 's/^/COLLRUN|/'
  NT=$(echo "$RUNS" | sed -n 's/^TARGET_COUNT=//p'); FIRST=$(echo "$RUNS" | sed -n 's/^FIRST=//p'); LATER=$(echo "$RUNS" | sed -n 's/^LATER=//p')
  IFS='|' read -r F_START F_EXIT F_VERDICT F_OBS <<< "$FIRST"
  if [ "${NT:-0}" -ge 1 ] && [ "$F_EXIT" = 0 ] && [ "$F_VERDICT" = COMPLETE ] && [ "$F_OBS" = ok ]; then ev first_collector_run_for_the_session_exit_0_complete_and_it_created_the_snapshot PASS "start=$F_START"; else ev first_collector_run_for_the_session_exit_0_complete_and_it_created_the_snapshot FAIL "runs=$NT first=$FIRST"; fi
  if [ -z "$LATER" ]; then ev recovery_run_is_complete_and_idempotent REVIEW "no later run for the session yet"
  else
    bad=0
    while IFS='|' read -r L_START L_EXIT L_VERDICT L_OBS; do [ -n "$L_START" ] || continue; { [ "$L_EXIT" = 0 ] && [ "$L_VERDICT" = COMPLETE ] && [ "$L_OBS" = already_present ]; } || bad=1; done <<< "$LATER"
    if [ $bad = 0 ]; then ev recovery_run_is_complete_and_idempotent PASS "later runs: $(echo "$LATER" | wc -l), observe=already_present"; else ev recovery_run_is_complete_and_idempotent FAIL "$LATER"; fi
    SECOND=$(echo "$LATER" | head -1 | cut -d'|' -f1)
    LAST_WRITE=$(qro -c "select max(t) < timestamptz '$SECOND' from (select captured_at t from universe_snapshot where session_date='$SESSION' and provenance='observed' union all select captured_at from market_snapshot where session_date='$SESSION' and provenance='observed' union all select captured_at from sector_snapshot where session_date='$SESSION' and provenance='observed' union all select created_at from stock_relative_strength where session_date='$SESSION' and provenance='observed') q")
    if [ "$LAST_WRITE" = t ]; then ev recovery_run_wrote_nothing_all_evidence_stamps_predate_it PASS "recovery start=$SECOND"; else ev recovery_run_wrote_nothing_all_evidence_stamps_predate_it FAIL "$LAST_WRITE"; fi
  fi
  OTHERS=$(echo "$RUNS" | grep '^OTHER=' | cut -d= -f2- | tr '\n' ';'); say "COLLRUN|earlier_runs_for_other_sessions=${OTHERS:-none} (expected: the expired session $HIST was refused as missed, exit 4)"
  HS=$(qro -c "select (select count(*) from universe_snapshot where session_date='$HIST' and provenance='observed')+(select count(*) from market_snapshot where session_date='$HIST' and provenance='observed')+(select count(*) from sector_snapshot where session_date='$HIST' and provenance='observed')+(select count(*) from stock_relative_strength where session_date='$HIST' and provenance='observed')")
  if [ "$HS" = 0 ]; then ev expired_session_has_no_snapshot_written PASS "$HIST"; else ev expired_session_has_no_snapshot_written FAIL "$HIST rows=$HS"; fi
  case "$(systemctl show donchian-forward-collection.service -p ExecMainStatus --value)" in 0|3) ev last_collector_service_exit_0_or_3 PASS ;; *) ev last_collector_service_exit_0_or_3 FAIL "$(systemctl show donchian-forward-collection.service -p ExecMainStatus --value)" ;; esac
  SN=$(qro -c "select (select count(*) from universe_snapshot where session_date='$SESSION' and provenance='observed')||'|'||(select count(*) from market_snapshot where session_date='$SESSION' and provenance='observed')||'|'||(select count(*) from sector_snapshot where session_date='$SESSION' and provenance='observed')||'|'||(select count(*) from stock_relative_strength where session_date='$SESSION' and provenance='observed')||'|'||coalesce((select n_symbols from universe_snapshot where session_date='$SESSION' and provenance='observed'),0)")
  IFS='|' read -r NU NM NS NR NN <<< "$SN"; say "SNAPSHOT|universe=$NU market=$NM sectors=$NS rs_rows=$NR n_symbols=$NN"
  if [ "$NU" = 1 ] && [ "$NM" = 1 ] && [ "${NS:-0}" -gt 0 ] && [ "${NN:-0}" -gt 0 ] && [ "$NR" = "$((NN * 3))" ]; then ev committed_snapshot_complete_one_universe_one_market_sectors_and_3_horizons_of_rs PASS "$SN"; else ev committed_snapshot_complete_one_universe_one_market_sectors_and_3_horizons_of_rs FAIL "$SN"; fi
  WIN=$(qro -c "select bool_and(t >= (date '$SESSION' + time '13:00') at time zone 'America/New_York' and t < (date '$SESSION' + 1 + time '12:00') at time zone 'America/New_York') from (select captured_at t from universe_snapshot where session_date='$SESSION' and provenance='observed' union all select captured_at from market_snapshot where session_date='$SESSION' and provenance='observed' union all select captured_at from sector_snapshot where session_date='$SESSION' and provenance='observed' union all select created_at from stock_relative_strength where session_date='$SESSION' and provenance='observed') q")
  if [ "$WIN" = t ]; then ev snapshot_stamped_by_the_database_inside_the_allowed_window PASS; else ev snapshot_stamped_by_the_database_inside_the_allowed_window FAIL "$WIN"; fi
  LIN=$(qro -c "select (select count(*) from market_snapshot m join universe_snapshot u on u.id = m.universe_snapshot_id where m.session_date='$SESSION' and m.provenance='observed' and u.session_date = m.session_date)||'|'||(select count(*) from sector_snapshot s join market_snapshot m on m.id = s.market_snapshot_id where m.session_date='$SESSION' and s.session_date = m.session_date)||'|'||(select count(*) from sector_snapshot where session_date='$SESSION' and provenance='observed')")
  IFS='|' read -r LM LS LT <<< "$LIN"
  if [ "$LM" = 1 ] && [ "$LS" = "$LT" ] && [ "${LT:-0}" -gt 0 ]; then ev snapshot_lineage_market_points_at_the_universe_and_every_sector_row_at_the_market PASS "$LIN"; else ev snapshot_lineage_market_points_at_the_universe_and_every_sector_row_at_the_market FAIL "$LIN"; fi
fi
if [ "${R13_POST:-0}" != 1 ]; then ev collector_still_disabled_and_unarmed "$( [ "$(systemctl is-enabled donchian-forward-collection.timer)" = disabled ] && [ "$(systemctl is-active donchian-forward-collection.timer)" = inactive ] && [ ! -e $BASE/FORWARD_COLLECTION_ARMED ] && echo PASS || echo FAIL )"; fi
ev bot_and_backend_images_unchanged "$( [ "$(docker inspect -f '{{.Config.Image}}' donchian-screener-bot-1)" = ghcr.io/danielbugi/wolfx_alpha-mechanism:e3feb64d3e86 ] && [ "$(docker inspect -f '{{.Config.Image}}' donchian-screener-backend-1)" = ghcr.io/danielbugi/wolfx_alpha-backend:963ab19318b7 ] && echo PASS || echo FAIL )"
say "EVID_VERDICT|$([ $EV_FAIL = 0 ] && echo PASS || echo FAIL)"

say "== CORRECTED CHECKPOINT (Verdict A and Verdict B, separate)"
export CHECKPOINT_SESSION="$SESSION" CHECKPOINT_PIN="$PIN" CHECKPOINT_DIR="$DIR" CHECKPOINT_OUT="$OUT/checkpoint" CHECKPOINT_PG="$PG" \
  CHECKPOINT_PIPELINE_START="${CHECKPOINT_PIPELINE_START:-2026-10-09T20:00:00Z}" CHECKPOINT_FLAG_TIME="2026-10-08 08:55:00+00" CHECKPOINT_BOUNDARY="${R13_BOUNDARY:-2026-10-09}" CHECKPOINT_STRATEGY_ID=1 \
  CHECKPOINT_LEGACY_COUNT=1040 CHECKPOINT_LEGACY_STAMP_MD5=4fd34882fe0914910b7736892bfb5732 CHECKPOINT_LEGACY_FROM="2026-10-08 03:08:00" CHECKPOINT_LEGACY_TO="2026-10-08 03:12:00" \
  CHECKPOINT_PRICE_WRITER_TZ=UTC CHECKPOINT_DEADLINE="${CHECKPOINT_DEADLINE:-}" CHECKPOINT_POLL_SECONDS=300 CHECKPOINT_SELFTEST="$SELFTEST" CHECKPOINT_PG_SINCE=
bash "$DIR/checkpoint.sh"; CODE=$?
say "R13|checkpoint_exit=$CODE evidence_verdict=$([ $EV_FAIL = 0 ] && echo PASS || echo FAIL) finished_utc=$(date -u +%FT%TZ)"
echo "checkpoint_exit=$CODE evidence=$([ $EV_FAIL = 0 ] && echo PASS || echo FAIL) finished_utc=$(date -u +%FT%TZ) report=$REPORT" > "$STATUS"
ln -sfn "$REPORT" "$OUT/latest_r13_${TAG}.txt"; ln -sfn "$STATUS" "$OUT/latest_r13_${TAG}.status"
sleep 2; exit "$CODE"
