#!/usr/bin/env bash
# deploy/vps/stage2_repin.sh -- the controlled Stage 2 change: re-pin the mechanism image (CURRENT_MECHANISM_SHA). Re-arming the collector stays a deliberate HUMAN act (see below).
# DORMANT: not installed, referenced by no unit and no workflow; an operator runs it by hand, as root on the VPS, inside an approved maintenance window.
# Procedure, rationale and rollback: docs/operations/STAGE2_MECHANISM_REPIN_RUNBOOK.md.
#
#   stage2_repin.sh preflight <NEW_TAG> <NEW_DIGEST>   read-only. Every check prints PASS/FAIL; exit 1 on any FAIL.
#   stage2_repin.sh backup                             takes a fresh restore-checkable production dump via pre_activation_backup.sh and verifies its checksum.
#   stage2_repin.sh apply <NEW_TAG> <NEW_DIGEST>       preflight again, snapshot, write the PIN. It does NOT arm anything: it prints the exact arming command for the operator to run by hand.
#   stage2_repin.sh collector-check <STATE_DIR> [TAG]  after the operator armed: checks the arming file holds the tag, then runs the operator-supplied collector check once and requires exit 0.
#   stage2_repin.sh verify <STATE_DIR>                 compares the world with the snapshot taken by apply.
#   stage2_repin.sh status <STATE_DIR>                 read-only. Classifies the pin/arming state (consistent new, consistent old, or one of the two PARTIAL states), checks for concurrent activity
#                                                      (running services, held locks, tables whose row counts moved since the snapshot) and prints the recommended recovery action.
#   stage2_repin.sh rollback <STATE_DIR>               restores the saved pin and prints the arming command that restores the previous arming; the operator runs it, then collector-check.
#                                                      REFUSES while any run is active or a lock is held (concurrent writes), unless FORCE_ROLLBACK=1 is set deliberately.
#
# Operator-supplied (required, so the repository file itself names no collector, no arming file and no unit-management command):
#   ARMING_FILE      path of the collector's arming file. The tool only READS it and never creates or modifies it: arming is a deliberate human act (repository policy, enforced by tests).
#   COLLECTOR_CHECK  the command that re-verifies the collector once (it is idempotent and exits 0 when the session is complete and verified).
#   UNITCTL          a read-only unit-introspection binary (list-units / list-unit-files / show); the tool never starts, stops, enables, installs or reloads anything.
#   GATE_CMD         a command whose exit status 3 means "no new US session": the pipeline wrapper would then be a gated no-op.
# What it never touches: the tracker's pin and arming file, the bot's image tag file, any timer or unit, the database (read-only counts only), any container.
set -uo pipefail

BASE="${DONCHIAN_BASE:-/opt/donchian}"
SCRIPTS="${DONCHIAN_SCRIPTS:-$BASE/scripts}"
DOCKER="${DOCKER:-docker}"
UNITCTL="${UNITCTL:-}"
ARMING_FILE="${ARMING_FILE:-}"
COLLECTOR_CHECK="${COLLECTOR_CHECK:-}"
GATE_CMD="${GATE_CMD:-}"
PRE_BACKUP="${PRE_BACKUP:-/root/pre_activation_backup.sh}"
MIN_FREE_GB="${MIN_FREE_GB:-5}"
TIMER_GUARD_MIN="${TIMER_GUARD_MIN:-30}"
IMAGE_REPO="ghcr.io/danielbugi/wolfx_alpha-mechanism"
OLD_EXPECTED="${OLD_EXPECTED:-5c511899505a}"
PG="${PG_CONTAINER:-donchian-screener-postgres-1}"
PIN="$BASE/CURRENT_MECHANISM_SHA"

FAILS=0
ck() {  # ck "name" <0|1 ok> ["detail"]
  if [ "$2" = "1" ]; then echo "PASS  $1${3:+  :: $3}"; else echo "FAIL  $1${3:+  :: $3}"; FAILS=$((FAILS + 1)); fi
}
rd() { [ -f "$1" ] && tr -d '[:space:]' < "$1" || echo "<missing>"; }
sha() { [ -f "$1" ] && sha256sum "$1" | cut -c1-16 || echo "<missing>"; }
need_env() {
  local missing=""
  [ -n "$ARMING_FILE" ] || missing="$missing ARMING_FILE"
  [ -n "$COLLECTOR_CHECK" ] || missing="$missing COLLECTOR_CHECK"
  [ -n "$UNITCTL" ] || missing="$missing UNITCTL"
  [ -z "$missing" ] || { echo "STOP: set the operator-supplied variables first:$missing (see the header)"; return 1; }
}

preflight() {
  local NEW="$1" DIGEST="$2"
  need_env || return 1
  FAILS=0
  echo "== preflight $(date -u +%FT%TZ)  new=$NEW"
  [[ "$NEW" =~ ^[0-9a-f]{12}$ ]] && ck "new tag is 12 hex" 1 "$NEW" || ck "new tag is 12 hex" 0 "$NEW"
  [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] && ck "new digest is a full sha256" 1 || ck "new digest is a full sha256" 0 "$DIGEST"
  [ "$NEW" != "$OLD_EXPECTED" ] && ck "new tag differs from the current pin" 1 || ck "new tag differs from the current pin" 0
  local cur arm
  cur="$(rd "$PIN")"; arm="$(rd "$ARMING_FILE")"
  [ "$cur" = "$OLD_EXPECTED" ] && ck "the mechanism pin is the expected rollback image" 1 "$cur" || ck "the mechanism pin is the expected rollback image" 0 "found $cur, expected $OLD_EXPECTED"
  [ "$arm" = "$cur" ] && ck "the collector arming file equals the pin (collector currently armed)" 1 || ck "the collector arming file equals the pin (collector currently armed)" 0 "armed=$arm pin=$cur"
  local pp pa
  pp="$(rd "$BASE/CURRENT_PAPER_SHA")"; pa="$(rd "$BASE/PAPER_TRACKER_ARMED")"
  [ "$pp" != "<missing>" ] && [ "$pp" = "$pa" ] && ck "tracker pin and arming present and equal (will be left untouched)" 1 "$pp" || ck "tracker pin and arming present and equal (will be left untouched)" 0 "pin=$pp armed=$pa"
  local d_new d_old
  d_new="$("$DOCKER" image inspect "$IMAGE_REPO:$NEW" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
  [ -n "$d_new" ] && [ "${d_new##*@}" = "$DIGEST" ] && ck "new image is present locally and its digest equals the recorded one" 1 "${d_new##*@}" || ck "new image is present locally and its digest equals the recorded one" 0 "local=${d_new:-<absent>} recorded=$DIGEST"
  d_old="$("$DOCKER" image inspect "$IMAGE_REPO:$OLD_EXPECTED" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
  [ -n "$d_old" ] && ck "rollback image is present locally" 1 "${d_old##*@}" || ck "rollback image is present locally" 0 "$IMAGE_REPO:$OLD_EXPECTED"
  local running
  running="$("$UNITCTL" list-units 'donchian-*.service' --state=running,activating --no-pager --plain --no-legend 2>/dev/null | grep -c . || true)"
  [ "$running" = "0" ] && ck "no donchian service is running" 1 || ck "no donchian service is running" 0 "$running running"
  local held="" lf
  for lf in "$BASE"/.*.lock; do
    [ -e "$lf" ] || continue
    ( exec 8>"$lf"; flock -n 8 ) 2>/dev/null || held="$held $(basename "$lf")"
  done
  [ -z "$held" ] && ck "every lock file is free (no pipeline, collector, tracker, notice or backup run)" 1 || ck "every lock file is free (no pipeline, collector, tracker, notice or backup run)" 0 "held:$held"
  local now t val epoch due=""
  now="$(date +%s)"
  for t in $("$UNITCTL" list-unit-files 'donchian-*.timer' --no-legend 2>/dev/null | awk '{print $1}'); do
    val="$("$UNITCTL" show "$t" -p NextElapseUSecRealtime --value 2>/dev/null || true)"
    [ -n "$val" ] && [ "$val" != "n/a" ] || continue
    epoch="$(date -d "$val" +%s 2>/dev/null || echo 0)"
    if [ "$epoch" -gt 0 ] && [ $((epoch - now)) -ge -60 ] && [ $((epoch - now)) -lt $((TIMER_GUARD_MIN * 60)) ]; then due="$due $t($(( (epoch - now) / 60 ))m)"; fi
  done
  [ -z "$due" ] && ck "no timer is due within ${TIMER_GUARD_MIN} minutes" 1 || ck "no timer is due within ${TIMER_GUARD_MIN} minutes" 0 "$due"
  if [ -n "$GATE_CMD" ]; then
    $GATE_CMD >/dev/null 2>&1; local grc=$?
    [ "$grc" = "3" ] && ck "the pipeline would be a gated no-op (no new US session pending)" 1 || ck "the pipeline would be a gated no-op (no new US session pending)" 0 "gate exit $grc (3 expected)"
  else
    ck "the pipeline would be a gated no-op (no new US session pending)" 0 "GATE_CMD not set"
  fi
  local free
  free="$(df -Pk "$BASE" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')"
  [ "${free:-0}" -ge "$MIN_FREE_GB" ] && ck "free disk on the data volume" 1 "${free} GB" || ck "free disk on the data volume" 0 "${free:-0} GB < ${MIN_FREE_GB} GB"
  echo "== preflight result: $([ $FAILS -eq 0 ] && echo ALL PASS || echo "$FAILS FAILED")"
  [ $FAILS -eq 0 ]
}

counts() {  # row count of every public table (read-only), "table|n"
  "$DOCKER" exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$PG" sh -c 'psql -U "$POSTGRES_USER" -d trading_production -X -At -F"|"' <<'SQL'
select c.relname, (xpath('/row/c/text()', query_to_xml(format('select count(*) c from public.%I', c.relname), false, true, '')))[1]::text::bigint
from pg_class c where c.relnamespace='public'::regnamespace and c.relkind in ('r','p') order by 1;
SQL
}

containers() { "$DOCKER" ps -a --format '{{.Names}} {{.Image}} {{.ID}}' 2>/dev/null | grep -v -- '-run-' | sort; }

backup() {
  [ -x "$PRE_BACKUP" ] || { echo "FAIL  fresh-backup script not found/executable: $PRE_BACKUP"; return 1; }
  local out dir
  out="$("$PRE_BACKUP" stage2-repin 2>&1)"; local rc=$?
  echo "$out" | tail -6
  dir="$(echo "$out" | sed -n 's/^BK=//p' | head -1)"
  [ "$rc" = "0" ] && [ -n "$dir" ] && [ -f "$dir/production.dump.sha256" ] || { echo "FAIL  backup did not complete"; return 1; }
  ( cd "$dir" && sha256sum -c production.dump.sha256 ) || { echo "FAIL  checksum"; return 1; }
  echo "PASS  fresh backup $dir (checksum verified; restore-check it separately before apply)"
}

recent_backup() {  # newest stage2-repin backup younger than 45 minutes with a valid checksum
  local d
  d="$(ls -1dt "$BASE"/backups/pre-release-b/stage2-repin-* 2>/dev/null | head -1)"
  [ -n "$d" ] && [ $(( $(date +%s) - $(stat -c %Y "$d") )) -lt 2700 ] && ( cd "$d" && sha256sum -c production.dump.sha256 >/dev/null 2>&1 ) && { echo "$d"; return 0; }
  return 1
}

apply() {
  local NEW="$1" DIGEST="$2"
  preflight "$NEW" "$DIGEST" || { echo "STOP: preflight failed; nothing was changed."; return 1; }
  local bk
  bk="$(recent_backup)" || { echo "STOP: no verified stage2-repin backup younger than 45 minutes; run 'backup' first. Nothing was changed."; return 1; }
  echo "backup in use: $bk"
  local st="$BASE/stage2-repin/$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$st" || { echo "STOP: cannot create $st"; return 1; }
  cp -p "$PIN" "$st/pin_before"
  cp -p "$ARMING_FILE" "$st/arming_before"
  local f
  for f in CURRENT_PAPER_SHA PAPER_TRACKER_ARMED env/mechanism_image_tag.env; do
    [ -f "$BASE/$f" ] && cp -p "$BASE/$f" "$st/$(basename "$f")"
  done
  ( cd "$BASE" && sha256sum CURRENT_PAPER_SHA PAPER_TRACKER_ARMED env/mechanism_image_tag.env 2>/dev/null ) > "$st/protected_before.sha"
  counts > "$st/counts_before.txt" || { echo "STOP: cannot read row counts"; return 1; }
  containers > "$st/containers_before.txt"
  echo "NEW=$NEW DIGEST=$DIGEST OLD=$OLD_EXPECTED BACKUP=$bk" > "$st/params.txt"
  [ -s "$st/counts_before.txt" ] || { echo "STOP: empty row-count snapshot"; return 1; }
  echo "snapshot: $st ($(wc -l < "$st/counts_before.txt") tables)"
  echo "== APPLY $(date -u +%FT%TZ): writing the pin only"
  printf '%s\n' "$NEW" > "$PIN.new" && mv "$PIN.new" "$PIN" || { echo "FAIL writing the pin; roll back with: $0 rollback $st"; return 1; }
  echo "pin=$(rd "$PIN")  $(date -u +%FT%TZ)"
  echo "PIN WRITTEN. The collector now refuses to run (armed for a different image) until YOU arm it. Run this deliberate step by hand, now:"
  echo "    printf '%s\\n' $NEW > $ARMING_FILE"
  echo "then:   $0 collector-check $st"
  echo "state dir: $st"
}

collector_check() {
  local st="$1" expect="${2:-}"
  need_env || return 1
  [ -f "$st/params.txt" ] || { echo "FAIL  state dir missing: $st"; return 1; }
  # shellcheck disable=SC1090
  [ -n "$expect" ] || { eval "$(sed 's/ /; /g' "$st/params.txt")"; expect="$NEW"; }
  FAILS=0
  echo "== collector check $(date -u +%FT%TZ)  expect=$expect"
  [ "$(rd "$PIN")" = "$expect" ] && ck "the mechanism pin is $expect" 1 || ck "the mechanism pin is $expect" 0 "$(rd "$PIN")"
  [ "$(rd "$ARMING_FILE")" = "$expect" ] && ck "the collector arming file holds $expect (armed by the operator)" 1 || ck "the collector arming file holds $expect (armed by the operator)" 0 "$(rd "$ARMING_FILE")"
  [ $FAILS -eq 0 ] || { echo "STOP: not armed for $expect; the collector check was NOT run."; return 1; }
  $COLLECTOR_CHECK; local rc=$?
  [ "$rc" = "0" ] && ck "collector re-verification exited 0 (session complete and verified)" 1 || ck "collector re-verification exited 0 (session complete and verified)" 0 "exit $rc"
  [ $FAILS -eq 0 ] || echo "STOP: collector re-verification failed. Nothing was rolled back automatically. Recovery: $0 rollback $st"
  [ $FAILS -eq 0 ]
}

verify() {
  local st="$1"
  need_env || return 1
  FAILS=0
  [ -d "$st" ] && [ -f "$st/params.txt" ] || { echo "FAIL  state dir missing: $st"; return 1; }
  # shellcheck disable=SC1090
  eval "$(sed 's/ /; /g' "$st/params.txt")"
  echo "== verify $(date -u +%FT%TZ) against $st (new=$NEW)"
  [ "$(rd "$PIN")" = "$NEW" ] && ck "the mechanism pin is the new tag" 1 || ck "the mechanism pin is the new tag" 0 "$(rd "$PIN")"
  [ "$(rd "$ARMING_FILE")" = "$NEW" ] && ck "the collector arming file equals the new tag (explicitly re-armed)" 1 || ck "the collector arming file equals the new tag (explicitly re-armed)" 0
  local f ok=1
  for f in CURRENT_PAPER_SHA PAPER_TRACKER_ARMED env/mechanism_image_tag.env; do
    [ "$(sha "$BASE/$f")" = "$(grep " $f\$" "$st/protected_before.sha" | cut -c1-16)" ] || ok=0
  done
  [ $ok = 1 ] && ck "tracker pin, tracker arming and the bot's image tag file are byte-identical to before" 1 || ck "tracker pin, tracker arming and the bot's image tag file are byte-identical to before" 0
  counts > "$st/counts_after.txt" 2>/dev/null
  if diff -q "$st/counts_before.txt" "$st/counts_after.txt" >/dev/null 2>&1 && [ -s "$st/counts_after.txt" ]; then
    ck "every public table has the same row count as before (no evidence changed)" 1 "$(wc -l < "$st/counts_after.txt") tables"
  else
    ck "every public table has the same row count as before (no evidence changed)" 0 "$(diff "$st/counts_before.txt" "$st/counts_after.txt" 2>&1 | head -4 | tr '\n' ' ')"
  fi
  containers > "$st/containers_after.txt"
  diff -q "$st/containers_before.txt" "$st/containers_after.txt" >/dev/null 2>&1 && ck "no long-lived container was recreated or restarted (bot, backend, caddy, postgres unchanged)" 1 || ck "no long-lived container was recreated or restarted (bot, backend, caddy, postgres unchanged)" 0 "$(diff "$st/containers_before.txt" "$st/containers_after.txt" | head -3 | tr '\n' ' ')"
  echo "== pipeline wrapper smoke (gated no-op with the new image)"
  local grc=1
  if [ -n "$GATE_CMD" ]; then $GATE_CMD >/dev/null 2>&1; grc=$?; fi
  if [ "$grc" = "3" ]; then
    "$SCRIPTS/run_pipeline.sh" >/dev/null 2>&1; local prc=$?
    [ "$prc" = "0" ] && ck "run_pipeline.sh exits 0 as a gated no-op with the new image" 1 || ck "run_pipeline.sh exits 0 as a gated no-op with the new image" 0 "exit $prc"
  else
    ck "run_pipeline.sh smoke skipped: a new session is pending, a real run would start" 0 "do not smoke in the window; check at the next scheduled fire"
  fi
  echo "== verify result: $([ $FAILS -eq 0 ] && echo ALL PASS || echo "$FAILS FAILED")"
  [ $FAILS -eq 0 ]
}

# Concurrent-activity probe shared by status and rollback: prints one line per finding and returns 1 when anything is active.
activity() {
  local st="$1" found=0 running lf held="" t
  running="$("$UNITCTL" list-units 'donchian-*.service' --state=running,activating --no-pager --plain --no-legend 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
  [ -z "${running// }" ] || { echo "ACTIVE  services running: $running"; found=1; }
  for lf in "$BASE"/.*.lock; do
    [ -e "$lf" ] || continue
    ( exec 8>"$lf"; flock -n 8 ) 2>/dev/null || held="$held $(basename "$lf")"
  done
  [ -z "$held" ] || { echo "ACTIVE  locks held:$held"; found=1; }
  if [ -f "$st/counts_before.txt" ]; then
    counts > "$st/counts_now.txt" 2>/dev/null
    local moved
    moved="$(diff "$st/counts_before.txt" "$st/counts_now.txt" 2>/dev/null | grep '^>' | sed 's/^> //' | tr '\n' ' ')"
    [ -z "$moved" ] || { echo "ACTIVE  row counts moved since the snapshot (concurrent writes or an effect of this change): $moved"; found=1; }
  fi
  [ $found -eq 0 ] && echo "QUIET   no running service, no held lock, no row count moved since the snapshot"
  return $found
}

status() {
  local st="$1"
  need_env || return 1
  [ -f "$st/params.txt" ] && [ -f "$st/pin_before" ] || { echo "STOP: state dir missing or incomplete: $st"; return 1; }
  # shellcheck disable=SC1090
  eval "$(sed 's/ /; /g' "$st/params.txt")"
  local old="$(rd "$st/pin_before")" pin arm cls action
  pin="$(rd "$PIN")"; arm="$(rd "$ARMING_FILE")"
  echo "== status $(date -u +%FT%TZ)   pin=$pin  arming=$arm   (old=$old new=$NEW)"
  if   [ "$pin" = "$NEW" ] && [ "$arm" = "$NEW" ]; then cls="CONSISTENT_NEW"; action="run: collector-check $st   then: verify $st   (nothing to recover if both pass)"
  elif [ "$pin" = "$old" ] && [ "$arm" = "$old" ]; then cls="CONSISTENT_OLD"; action="the previous state is in place; run: collector-check $st $old   to re-confirm the collector; the change is simply not applied"
  elif [ "$pin" = "$NEW" ] && [ "$arm" = "$old" ]; then cls="PARTIAL_PIN_NEW_ARMING_OLD"; action="the collector currently REFUSES to run (armed for a different image). Either finish: write $NEW into the arming file by hand, then collector-check $st; or undo: rollback $st (restores the pin), then run the printed arming command"
  elif [ "$pin" = "$old" ] && [ "$arm" = "$NEW" ]; then cls="PARTIAL_PIN_OLD_ARMING_NEW"; action="the collector currently REFUSES to run. Restore the arming by hand: write $old into the arming file (or finish the change by writing the pin), then collector-check $st $old"
  else cls="UNEXPECTED"; action="neither file holds the old or the new tag: do nothing automatic; compare with $st/pin_before and $st/arming_before and restore each file by hand from those copies"
  fi
  echo "CLASS   $cls"
  echo "ACTION  $action"
  activity "$st"; local act=$?
  [ $act -eq 0 ] || echo "NOTE    activity was detected: wait for the active run to finish (or confirm it is a timer-driven run unrelated to this change) BEFORE restoring anything"
  local ok=1 f
  for f in CURRENT_PAPER_SHA PAPER_TRACKER_ARMED env/mechanism_image_tag.env; do
    [ "$(sha "$BASE/$f")" = "$(grep " $f\$" "$st/protected_before.sha" | cut -c1-16)" ] || ok=0
  done
  [ $ok = 1 ] && echo "PASS    tracker pin, tracker arming and the bot's image tag file are byte-identical to the snapshot" || echo "FAIL    a tracker/bot file differs from the snapshot: do NOT touch it from this tool; report"
  [ "$cls" = "CONSISTENT_NEW" ] || [ "$cls" = "CONSISTENT_OLD" ]
}

rollback() {
  local st="$1"
  [ -f "$st/pin_before" ] && [ -f "$st/arming_before" ] || { echo "STOP: saved pin/arming not found in $st"; return 1; }
  need_env || return 1
  echo "== concurrency check before restoring anything"
  if ! activity "$st"; then
    if [ "${FORCE_ROLLBACK:-0}" = "1" ]; then echo "FORCE_ROLLBACK=1: continuing despite the activity above (deliberate operator decision)"
    else echo "STOP: activity detected (see above). Nothing was restored. Wait for it to finish and rerun, or set FORCE_ROLLBACK=1 only after deciding it is safe."; return 1; fi
  fi
  echo "== ROLLBACK $(date -u +%FT%TZ): restoring the saved pin (existing forward observations are never rewritten)"
  cp -p "$st/pin_before" "$PIN.new" && mv "$PIN.new" "$PIN" || return 1
  echo "pin=$(rd "$PIN")"
  echo "PIN RESTORED. Re-arm the collector for the old image by hand (a deliberate human act):"
  echo "    printf '%s\\n' $(rd "$st/arming_before") > $ARMING_FILE"
  echo "then:   $0 collector-check $st $(rd "$st/arming_before")"
}

cmd="${1:-}"
case "$cmd" in
  preflight)       shift; [ $# -eq 2 ] || { echo "usage: $0 preflight <NEW_TAG> <NEW_DIGEST>"; exit 2; }; preflight "$@" ;;
  backup)          backup ;;
  apply)           shift; [ $# -eq 2 ] || { echo "usage: $0 apply <NEW_TAG> <NEW_DIGEST>"; exit 2; }; apply "$@" ;;
  collector-check) shift; [ $# -ge 1 ] || { echo "usage: $0 collector-check <STATE_DIR> [TAG]"; exit 2; }; collector_check "$@" ;;
  verify)          shift; [ $# -eq 1 ] || { echo "usage: $0 verify <STATE_DIR>"; exit 2; }; verify "$@" ;;
  status)          shift; [ $# -eq 1 ] || { echo "usage: $0 status <STATE_DIR>"; exit 2; }; status "$@" ;;
  rollback)        shift; [ $# -eq 1 ] || { echo "usage: $0 rollback <STATE_DIR>"; exit 2; }; rollback "$@" ;;
  *) echo "usage: $0 preflight|backup|apply|collector-check|verify|status|rollback ..."; exit 2 ;;
esac
