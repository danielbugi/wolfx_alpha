#!/usr/bin/env bash
# deploy/db/pre_activation_backup.sh <label>
# A restore-checked, labelled production dump taken immediately before a production change. Run on the VPS as the deploy user (it needs docker):
#     ssh -i ~/.ssh/donchian_deploy deploy@<vps> 'bash -s -- forward-research' < deploy/db/pre_activation_backup.sh
# It is the procedure used before the Release B role switch (S9/S10), kept here so the activation does not depend on a scratch file.
#
# What it writes (only under /opt/donchian/backups/pre-release-b/<label>-<UTC timestamp>/, mode 700, files 600; never anywhere else):
#   production.dump        pg_dump -Fc of trading_production, taken by the postgres container's own POSTGRES_USER (the bootstrap identity, trading_user)
#   production.dump.sha256 its checksum, re-verified before the script ends
#   restore_list.txt       `pg_restore -l` of the dump: proves the archive is readable and lists every object
#   rowcounts.txt          exact row count of every public table at dump time (read-only SQL), the reference for "nothing was lost / nothing unexplained changed"
#   globals.sql            pg_dumpall --globals-only: roles and their grants. SECRET (password hashes): never copy it off the box, never commit it.
# It changes nothing in the database (the dump and the counts are read-only) and prints no secret. Exit status is non-zero if ANY self-check fails.
set -euo pipefail

LABEL="${1:-}"
[[ "$LABEL" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo "usage: pre_activation_backup.sh <label: lowercase letters, digits, dashes>" >&2; exit 2; }
BASE="${DONCHIAN_BASE:-/opt/donchian}"
PG="${PG_CONTAINER:-donchian-screener-postgres-1}"

umask 077
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BK="$BASE/backups/pre-release-b/$LABEL-$TS"
mkdir -p "$BK"
chmod 700 "$BK"
echo "BK=$BK"

docker exec "$PG" sh -c 'pg_dump -U "$POSTGRES_USER" -d trading_production -Fc -Z 6' > "$BK/production.dump" </dev/null
[ -s "$BK/production.dump" ] || { echo "FAIL: empty dump" >&2; exit 1; }
docker exec -i "$PG" pg_restore -l < "$BK/production.dump" > "$BK/restore_list.txt"
[ -s "$BK/restore_list.txt" ] || { echo "FAIL: the dump cannot be listed" >&2; exit 1; }
(cd "$BK" && sha256sum production.dump > production.dump.sha256 && sha256sum -c production.dump.sha256)

docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' "$PG" sh -c 'psql -U "$POSTGRES_USER" -d trading_production -X -At -F"|"' > "$BK/rowcounts.txt" <<'SQL'
select c.relname, (xpath('/row/c/text()', query_to_xml(format('select count(*) c from public.%I', c.relname), false, true, '')))[1]::text::bigint
from pg_class c where c.relnamespace='public'::regnamespace and c.relkind in ('r','p') order by 1;
SQL
[ -s "$BK/rowcounts.txt" ] || { echo "FAIL: no row counts" >&2; exit 1; }

tables_in_counts="$(wc -l < "$BK/rowcounts.txt")"
tables_in_dump="$(grep -c ' TABLE DATA ' "$BK/restore_list.txt" || true)"
[ "$tables_in_counts" = "$tables_in_dump" ] || { echo "FAIL: $tables_in_counts tables counted but $tables_in_dump TABLE DATA entries in the dump" >&2; exit 1; }

docker exec "$PG" sh -c 'pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$BK/globals.sql" </dev/null
chmod 600 "$BK"/*

echo "OK label=$LABEL dir=$BK tables=$tables_in_dump bytes=$(stat -c %s "$BK/production.dump") sha256=$(cut -c1-64 "$BK/production.dump.sha256")"
grep -E '^(signal_ledger|telegram_post_delivery|daily_fundamentals|candidate_observation)\|' "$BK/rowcounts.txt" | sed 's/^/COUNT|/'
