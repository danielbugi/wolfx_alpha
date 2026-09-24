#!/usr/bin/env bash
# /opt/donchian/scripts/nightly_backup.sh
#
# Nightly pg_dump of the VPS production database (docker exec pg_dump -Fc, inside the postgres
# container -- read-only, no writer needs to be running). Runs as root via systemd timer
# donchian-nightly-backup.timer.
#
#   /opt/donchian/backups/nightly/<UTC timestamp>/production.dump(.sha256)
#   retained: RETAIN_DAYS (default 14) most recent runs, older ones deleted after a new run succeeds
#   every run appends one line to /opt/donchian/logs/nightly_backup.log: OK or FAILED + reason
#
# Off-box copy is a SEPARATE leg (deploy/db/pull_nightly_backup.ps1, run from the admin machine,
# outside this VPS) -- a failure here must not depend on that machine being reachable, so this
# script never blocks on it.
set -uo pipefail

BASE=/opt/donchian
OUT="$BASE/backups/nightly"
LOG="$BASE/logs/nightly_backup.log"
RETAIN_DAYS="${RETAIN_DAYS:-14}"
C=donchian-screener-postgres-1
DB="${1:-trading_production}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
DIR="$OUT/$TS"

mkdir -p "$OUT" "$(dirname "$LOG")"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

fail() {
  log "FAILED db=$DB reason=$*"
  rm -rf "$DIR"
  exit 1
}

[ -n "$(docker ps -q -f name="^${C}\$" -f status=running)" ] || fail "postgres container not running"
mkdir -p "$DIR" || fail "cannot create $DIR"

docker exec "$C" pg_dump -U trading_user -d "$DB" -Fc -Z 6 -f /tmp/nightly.dump \
  || fail "pg_dump failed"
docker cp "$C:/tmp/nightly.dump" "$DIR/production.dump" || fail "docker cp failed"
docker exec "$C" rm -f /tmp/nightly.dump

# integrity: the archive's own table of contents must parse (cheap, catches truncation/corruption
# without a full restore). Via a file path inside the container, not stdin -- `pg_restore -l -`
# piped through `docker exec -i` does not reliably see stdin as a seekable-enough source here.
docker cp "$DIR/production.dump" "$C:/tmp/nightly_check.dump" || fail "docker cp for integrity check failed"
docker exec "$C" pg_restore -l /tmp/nightly_check.dump > /dev/null 2>"$DIR/.restore_list_err"
RC=$?
docker exec "$C" rm -f /tmp/nightly_check.dump
[ "$RC" -eq 0 ] || fail "pg_restore -l could not read the archive: $(cat "$DIR/.restore_list_err")"
rm -f "$DIR/.restore_list_err"

sha256sum "$DIR/production.dump" | awk '{print $1"  production.dump"}' > "$DIR/production.dump.sha256"
( cd "$DIR" && sha256sum -c production.dump.sha256 > /dev/null ) || fail "checksum self-check failed"

chmod 700 "$DIR"
chmod 600 "$DIR"/*
chown -R root:root "$DIR"

SIZE=$(stat -c %s "$DIR/production.dump")
log "OK db=$DB dir=$DIR bytes=$SIZE sha256=$(cut -d' ' -f1 "$DIR/production.dump.sha256")"

# retention: delete completed runs older than RETAIN_DAYS, only after this run succeeded
find "$OUT" -mindepth 1 -maxdepth 1 -type d -mtime "+$RETAIN_DAYS" -print -exec rm -rf {} \; \
  | while read -r d; do log "retention: removed $d"; done

exit 0
