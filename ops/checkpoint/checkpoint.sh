#!/usr/bin/env bash
# Read-only post-cycle checkpoint with TWO SEPARATE VERDICTS. Template for the unattended checkpoint pattern used on the VPS (root-owned, hash-pinned bundle, run by a one-time
# transient timer as an unprivileged user). It never modifies production: every SQL statement runs with default_transaction_read_only=on, the collector runs WITHOUT --apply,
# it touches no flag, pin, unit, arming file, migration or data, and it never starts a pipeline.
#
#   Verdict A  FORWARD RESEARCH CORRECTNESS  (assert_forward_research.sql + shell-level health): sector history, candidate capture, scan heartbeat and lineage, units, DB errors.
#   Verdict B  COLLECTOR READINESS            (assert_collector_readiness.sql + the dry run): price/result fingerprint freshness, late ingestion after the scan, observe=dry_run|already_present.
# A fingerprint mismatch is a Verdict B fact only: it can never turn Verdict A into a failure, and a failing Verdict A is never hidden by a passing B.
# Exit: 0 A PASS and B READY | 1 REVIEW lines only | 2 pipeline not complete before the deadline | 3 Verdict A FAIL | 4 Verdict A PASS but Verdict B NOT READY | 5 bundle hash mismatch.
#
# Configuration (environment): CHECKPOINT_SESSION (required, YYYY-MM-DD), CHECKPOINT_DIR (bundle, default /opt/donchian/checkpoint), CHECKPOINT_OUT, CHECKPOINT_PIN,
# CHECKPOINT_FLAG_TIME, CHECKPOINT_BOUNDARY, CHECKPOINT_STRATEGY_ID, CHECKPOINT_PIPELINE_START, CHECKPOINT_LEGACY_{COUNT,STAMP_MD5,FROM,TO}, CHECKPOINT_PRICE_WRITER_TZ,
# CHECKPOINT_DEADLINE, CHECKPOINT_POLL_SECONDS, CHECKPOINT_SELFTEST=1 (skip the wait; installation test).
set -u
export LC_ALL=C PATH=/usr/sbin:/usr/bin:/sbin:/bin
SESSION="${CHECKPOINT_SESSION:?CHECKPOINT_SESSION is required}"
DIR="${CHECKPOINT_DIR:-/opt/donchian/checkpoint}"
OUT="${CHECKPOINT_OUT:-/home/deploy/checkpoint/reports}"
BASE="${DONCHIAN_BASE:-/opt/donchian}"
PIN_EXPECTED="${CHECKPOINT_PIN:?CHECKPOINT_PIN is required (the 12-hex production pin)}"
DEADLINE="${CHECKPOINT_DEADLINE:-}"
POLL="${CHECKPOINT_POLL_SECONDS:-600}"
SELFTEST="${CHECKPOINT_SELFTEST:-0}"
PG="${CHECKPOINT_PG:-donchian-screener-postgres-1}"
PIPELINE_START="${CHECKPOINT_PIPELINE_START:?CHECKPOINT_PIPELINE_START is required (UTC instant of the night's first pipeline attempt)}"

mkdir -p "$OUT"; TS=$(date -u +%Y%m%dT%H%M%SZ)
[ "$SELFTEST" = 1 ] && TAG=selftest || TAG=checkpoint
REPORT="$OUT/${TAG}_$TS.txt"; STATUS="$OUT/${TAG}_$TS.status"; DIAG="$OUT/${TAG}_${TS}_diag"
umask 027; mkdir -p "$DIAG"; exec > >(tee -a "$REPORT") 2>&1
say() { echo "$*"; }
qro()  { docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$PG" psql -U trading_user -d trading_production -X -At -v ON_ERROR_STOP=0 "$@"; }
qrof() { docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$PG" psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=0 < "$1"; }
subst() {   # fill the placeholders of an assertion file into a private copy
  sed -e "s|{{SESSION}}|$SESSION|g" -e "s|{{FLAG_TIME}}|${CHECKPOINT_FLAG_TIME:-1970-01-01 00:00:00+00}|g" -e "s|{{BOUNDARY}}|${CHECKPOINT_BOUNDARY:-$SESSION}|g" \
      -e "s|{{STRATEGY_ID}}|${CHECKPOINT_STRATEGY_ID:-1}|g" -e "s|{{PIPELINE_START}}|$PIPELINE_START|g" -e "s|{{LEGACY_COUNT}}|${CHECKPOINT_LEGACY_COUNT:-0}|g" \
      -e "s|{{LEGACY_STAMP_MD5}}|${CHECKPOINT_LEGACY_STAMP_MD5:-none}|g" -e "s|{{LEGACY_FROM}}|${CHECKPOINT_LEGACY_FROM:-1970-01-01 00:00:00}|g" \
      -e "s|{{LEGACY_TO}}|${CHECKPOINT_LEGACY_TO:-1970-01-01 00:00:01}|g" -e "s|{{PRICE_WRITER_TZ}}|${CHECKPOINT_PRICE_WRITER_TZ:-UTC}|g" "$1"
}
RANK_A=0; RANK_B=0; RANK_PIPE=0
bump_a() { RANK_A=$(( RANK_A > $1 ? RANK_A : $1 )); }          # 1 REVIEW, 3 FAIL
bump_b() { RANK_B=$(( RANK_B > $1 ? RANK_B : $1 )); }
A() { say "ASSERT|A|$1|$2${3:+ ($3)}"; case "$2" in FAIL) bump_a 3 ;; REVIEW) bump_a 1 ;; esac; }
B() { say "ASSERT|B|$1|$2${3:+ ($3)}"; case "$2" in FAIL) bump_b 3 ;; REVIEW) bump_b 1 ;; esac; }

say "CHECKPOINT|start_utc=$(date -u +%FT%TZ)|selftest=$SELFTEST|user=$(id -un)|session=$SESSION"
if ( cd "$DIR" && sha256sum -c SHA256SUMS ) > "$DIAG/integrity.txt" 2>&1; then sed 's/^/INTEGRITY|/' "$DIAG/integrity.txt"; say "INTEGRITY|bundle verified"
else sed 's/^/INTEGRITY|/' "$DIAG/integrity.txt"; say "INTEGRITY|REFUSED: bundle hash mismatch"; echo "exit_code=5" > "$STATUS"; sleep 1; exit 5; fi
say "READONLY-PROOF|$(qro -c "create temp table _probe(a int)" 2>&1 | head -1)"

# ---------------------------------------------------------------- wait (bounded) for the nightly pipeline
LOG="$BASE/logs/pipeline_runs.log"
pipeline_state() {
  local running last_start last_end
  running=$(systemctl is-active donchian-pipeline.service 2>/dev/null || true)
  if [ "$running" = active ] || [ "$running" = activating ]; then echo RUNNING; return; fi
  last_start=$(grep 'pipeline run starting' "$LOG" 2>/dev/null | tail -1 | cut -d' ' -f1); last_end=$(grep -E 'pipeline run (OK|FAILED)' "$LOG" 2>/dev/null | tail -1)
  if [ -z "$last_start" ] || ! [[ "$last_start" > "$PIPELINE_START" ]]; then echo NOT_STARTED; return; fi
  case "$last_end" in *"pipeline run OK"*"mechanism sha=$PIN_EXPECTED"*) [[ "$(echo "$last_end" | cut -d' ' -f1)" > "$last_start" ]] && { echo COMPLETE; return; } ;; esac
  echo FAILED_WAITING
}
PIPE=$(pipeline_state)
if [ "$SELFTEST" != 1 ]; then
  while [ "$PIPE" != COMPLETE ]; do
    say "PIPELINE|poll utc=$(date -u +%FT%TZ) state=$PIPE"
    [ -n "$DEADLINE" ] && [ "$(date -u +%s)" -ge "$(date -u -d "$DEADLINE" +%s)" ] && break
    sleep "$POLL"; PIPE=$(pipeline_state)
  done
fi
say "PIPELINE|final_state=$PIPE"
if [ "$PIPE" != COMPLETE ] && [ "$SELFTEST" != 1 ]; then
  say "VERDICT_A|NOT_EVALUATED|PIPELINE_NOT_COMPLETE"; say "VERDICT_B|NOT_EVALUATED|PIPELINE_NOT_COMPLETE"; echo "exit_code=2 pipeline=$PIPE" > "$STATUS"; sleep 1; exit 2
fi
[ "$PIPE" = COMPLETE ] && A "nightly_pipeline_completed_on_the_approved_pin" PASS "$PIN_EXPECTED"

# ---------------------------------------------------------------- state, units, errors (Verdict A)
[ "$(cat $BASE/CURRENT_MECHANISM_SHA 2>/dev/null)" = "$PIN_EXPECTED" ] && A "production_pin_unchanged" PASS || A "production_pin_unchanged" FAIL
[ ! -e "$BASE/FORWARD_COLLECTION_ARMED" ] && A "collector_arming_file_absent" PASS || A "collector_arming_file_absent" REVIEW "armed: expected only after an owner approval"
unit_ok() { local u="$1" label="$2" res; res=$(systemctl show "$u.service" -p Result --value 2>/dev/null); say "UNIT|$u|Result=$res"; [ "$res" = success ] && A "$label" PASS || A "$label" FAIL "Result=$res"; }
unit_ok donchian-pipeline pipeline_unit_succeeded; unit_ok donchian-signal-ledger-eval ledger_evaluator_succeeded; unit_ok donchian-postmarket-retry post_market_retry_succeeded
[ "$(docker inspect -f '{{.State.Health.Status}}' donchian-screener-backend-1 2>/dev/null)" = healthy ] && [ "$(docker inspect -f '{{.State.Health.Status}}' $PG 2>/dev/null)" = healthy ] && A "postgres_and_backend_healthy" PASS || A "postgres_and_backend_healthy" FAIL
S11="${CHECKPOINT_OBSERVER:-/home/deploy/release-b-s11/s11_ro.sh}"
if [ -x "$S11" ] && [ -n "${CHECKPOINT_PG_SINCE:-}" ]; then
  "$S11" pgerrors "$CHECKPOINT_PG_SINCE" </dev/null 2>&1 | cut -c1-200 | tee "$DIAG/pgerrors.txt"
  [ "$(sed -n 's/.*pattern=\[permission denied\] count=\([0-9]*\).*/\1/p' "$DIAG/pgerrors.txt" | head -1)" = 0 ] && A "no_permission_denied_errors" PASS || A "no_permission_denied_errors" FAIL
fi

# ---------------------------------------------------------------- the two assertion files (separate verdicts)
subst "$DIR/assert_forward_research.sql" > "$DIAG/assert_a.sql"; subst "$DIR/assert_collector_readiness.sql" > "$DIAG/assert_b.sql"
qrof "$DIAG/assert_a.sql" 2>&1 | cut -c1-320 | grep -v '^Output format\|^Tuples' | tee "$DIAG/assertions_a.txt"
qrof "$DIAG/assert_b.sql" 2>&1 | cut -c1-320 | grep -v '^Output format\|^Tuples' | tee "$DIAG/assertions_b.txt"
grep -q '^ASSERT|A|[^|]*|FAIL' "$DIAG/assertions_a.txt" && bump_a 3; grep -q '^ASSERT|A|[^|]*|REVIEW' "$DIAG/assertions_a.txt" && bump_a 1
grep -q '^ASSERT|B|[^|]*|FAIL' "$DIAG/assertions_b.txt" && bump_b 3; grep -q '^ASSERT|B|[^|]*|REVIEW' "$DIAG/assertions_b.txt" && bump_b 1
grep -q 'ERROR:' "$DIAG/assertions_a.txt" && { say "ASSERT|A|assertion_sql_executed_without_error|FAIL"; bump_a 3; }
grep -q 'ERROR:' "$DIAG/assertions_b.txt" && { say "ASSERT|B|assertion_sql_executed_without_error|FAIL"; bump_b 3; }
qro -c "select 'LATE_PRICE_ROW', symbol, date, created_at, updated_at from stock_prices where created_at > now() - interval '30 hours' or updated_at > now() - interval '30 hours' order by greatest(created_at, updated_at) desc limit 60" > "$DIAG/late_price_rows.txt" 2>&1

# ---------------------------------------------------------------- genuine collector dry run (Verdict B): no --apply, read-only session
if [ "$PIPE" = COMPLETE ] || [ "$SELFTEST" = 1 ]; then
  DRY="$DIAG/collector_dry_run_output.txt"
  ( cd $BASE/compose && IMAGE_TAG=$(cat $BASE/CURRENT_MECHANISM_SHA) timeout 1500 docker compose --project-directory $BASE/compose -f docker-compose.yml -f docker-compose.prod.yml --env-file $BASE/env/.env \
      run --rm -e PGOPTIONS='-c default_transaction_read_only=on' -e PYTHONPATH=/app/mechanism channel-sender -m forward_collection run --session "$SESSION" --with-sector-history-check --with-capture-check ) </dev/null > "$DRY" 2>&1
  RES=$(python3 - "$DRY" <<'PY'
import json, sys
t = open(sys.argv[1], encoding="utf-8", errors="replace").read(); i = t.find('{"applied"')
try:
    d = json.loads(t[i:t.rindex("}") + 1]); by = {s["name"]: s for s in d["steps"]}
    for n in ("capture_verify", "sector_history_verify", "observe", "labels", "verify"):
        if n in by: print("DRYRUN|step|%-22s %-18s %s" % (n, by[n]["outcome"], (by[n].get("error") or "")[:150]))
    print("RESULT|%s|%s|%s|%s" % (by["observe"]["outcome"], by["sector_history_verify"]["outcome"], by["capture_verify"]["outcome"], d["applied"]))
except Exception:
    print("RESULT|none|none|none|?")
PY
)
  echo "$RES" | grep '^DRYRUN' ; IFS='|' read -r _ O_OUT SH_OUT CV_OUT APPLIED <<< "$(echo "$RES" | grep '^RESULT')"
  case "$O_OUT" in dry_run|already_present) B "collector_dry_run_observe_is_dry_run_or_already_present" PASS "$O_OUT" ;; *) B "collector_dry_run_observe_is_dry_run_or_already_present" FAIL "observe=$O_OUT" ;; esac
  case "$SH_OUT" in dry_run|ok|already_present) B "collector_sector_history_dependency_satisfied" PASS "$SH_OUT" ;; *) B "collector_sector_history_dependency_satisfied" FAIL "$SH_OUT" ;; esac
  case "$CV_OUT" in dry_run|ok|already_present) B "collector_capture_dependency_satisfied" PASS "$CV_OUT" ;; *) B "collector_capture_dependency_satisfied" FAIL "$CV_OUT" ;; esac
  [ "$APPLIED" = False ] && B "dry_run_wrote_nothing" PASS || B "dry_run_wrote_nothing" FAIL "applied=$APPLIED"
fi

# ---------------------------------------------------------------- the two verdicts, kept apart
word() { case "$1" in 0) echo PASS ;; 1) echo REVIEW ;; *) echo FAIL ;; esac; }
say "VERDICT_A|$(word $RANK_A)|forward research correctness"
if [ "$RANK_B" -ge 3 ]; then WB=NOT_READY; elif [ "$RANK_B" -eq 1 ]; then WB=REVIEW; else WB=READY; fi
say "VERDICT_B|$WB|collector readiness"
if [ "$RANK_A" -ge 3 ]; then CODE=3; elif [ "$RANK_B" -ge 3 ]; then CODE=4; elif [ "$RANK_A" -eq 1 ] || [ "$RANK_B" -eq 1 ]; then CODE=1; else CODE=0; fi
say "UNCHANGED|pin=$(cat $BASE/CURRENT_MECHANISM_SHA 2>/dev/null) arming=$([ -e $BASE/FORWARD_COLLECTION_ARMED ] && echo PRESENT || echo absent)"
echo "verdict_a=$(word $RANK_A) verdict_b=$WB exit_code=$CODE finished_utc=$(date -u +%FT%TZ) report=$REPORT" > "$STATUS"
ln -sfn "$REPORT" "$OUT/latest_${TAG}.txt"; ln -sfn "$STATUS" "$OUT/latest_${TAG}.status"; ln -sfn "$DIAG" "$OUT/latest_${TAG}_diag"
sleep 2; exit "$CODE"
