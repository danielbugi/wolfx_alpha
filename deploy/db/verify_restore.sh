#!/usr/bin/env bash
# verify_restore.sh <backup_dir> [backend_image_tag]
#
# Proves a production backup (made by deploy/db/backup_production.py) is complete and usable,
# WITHOUT touching the running staging/production stack, its network or its volumes:
#   1. sha256 of the dump matches
#   2. restore into a DISPOSABLE Postgres (same major version as the source) on an --internal Docker
#      network: no published ports, no outbound internet, its own throwaway volume
#   3. exact per-table row counts == the counts taken in the dump's own snapshot
#   4. schema object counts (tables, views, indexes, constraints by type) and sequence positions match;
#      no NOT VALID constraints
#   5. application compatibility: the deployed backend image (immutable tag) runs against the copy --
#      container health, /api/health on real data, and the full auth smoke test (auth_smoke.sh)
# Everything created is removed at the end; a report is written next to the dump.
#
# Run as root on the VPS: bash verify_restore.sh /opt/donchian/backups/prod/<ts>
set -euo pipefail

DIR="${1:?usage: verify_restore.sh <backup_dir> [backend_image_tag]}"
TAG="${2:-$(cat /opt/donchian/CURRENT_SHA)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SMOKE="${SMOKE:-/opt/donchian/scripts/auth_smoke.sh}"
[ -f "$SMOKE" ] || SMOKE="$HERE/../vps/auth_smoke.sh"
REPORT="$DIR/verify_report.txt"
cd "$DIR"

exec > >(tee "$REPORT") 2>&1
echo "== restore verification $(date -u +%FT%TZ)  backup=$DIR  backend=$TAG"

sha256sum -c production.dump.sha256

inv() { python3 -c "import json,sys; d=json.load(open('inventory.json')); print(eval(sys.argv[1], {}, {'d': d}))" "$1"; }
DB="$(inv "d['database']")"
MAJOR="$(inv "d['server_version'].split('.')[0]")"
echo "source: db=$DB server=$(inv "d['server_version']") tables=$(inv "d['tables']") rows=$(inv "sum(d['row_counts'].values())") snapshot=$(inv "d['snapshot']")"

ID="rv-$(date +%s)"
NET="$ID-net"; PG="$ID-pg"; BE="$ID-backend"; VOL="$ID-data"
PW="$(openssl rand -hex 24)"
cleanup() {
  docker rm -f "$BE" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
  echo "cleanup: removed $BE $PG $NET $VOL"
}
trap cleanup EXIT

docker network create --internal "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" -v "$VOL:/var/lib/postgresql/data" \
  -e POSTGRES_DB="$DB" -e POSTGRES_USER=trading_user -e POSTGRES_PASSWORD="$PW" "postgres:$MAJOR-alpine" >/dev/null
for _ in $(seq 1 60); do docker exec "$PG" pg_isready -U trading_user -d "$DB" >/dev/null 2>&1 && break; sleep 2; done
sleep 2

q() { docker exec "$PG" psql -X -U trading_user -d "$DB" -tA -v ON_ERROR_STOP=1 -c "$1"; }

echo "== pg_restore (single transaction, exit on first error)"
docker cp production.dump "$PG:/tmp/production.dump"
docker exec "$PG" pg_restore -U trading_user -d "$DB" --no-owner --no-privileges --exit-on-error --single-transaction /tmp/production.dump
docker exec "$PG" rm -f /tmp/production.dump
echo "restore OK"

echo "== exact row counts vs the dump's own snapshot"
FAIL=0
while IFS=$'\t' read -r tbl expected; do
  schema="${tbl%%.*}"; name="${tbl#*.}"
  got="$(q "SELECT count(*) FROM \"$schema\".\"$name\"")"
  if [ "$got" = "$expected" ]; then printf 'ok   %-45s %12s\n' "$tbl" "$got"
  else printf 'DIFF %-45s expected %s got %s\n' "$tbl" "$expected" "$got"; FAIL=1; fi
done < <(python3 -c "import json; [print(f'{k}\t{v}') for k,v in json.load(open('inventory.json'))['row_counts'].items()]")

echo "== schema objects"
cmp_eq() { if [ "$2" = "$3" ]; then echo "ok   $1 = $2"; else echo "DIFF $1 source=$2 restored=$3"; FAIL=1; fi; }
S="pg_catalog','information_schema"
cmp_eq tables "$(inv "d['tables']")" "$(q "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('$S')")"
cmp_eq views "$(inv "d['views']")" "$(q "SELECT count(*) FROM pg_views WHERE schemaname NOT IN ('$S')")"
cmp_eq indexes "$(inv "d['indexes']")" "$(q "SELECT count(*) FROM pg_indexes WHERE schemaname NOT IN ('$S')")"
for t in p f u c x; do
  cmp_eq "constraints[$t]" "$(inv "d['constraints_by_type'].get('$t', 0)")" \
    "$(q "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname NOT IN ('$S') AND contype='$t'")"
done
cmp_eq "NOT VALID constraints" 0 "$(q "SELECT count(*) FROM pg_constraint WHERE NOT convalidated")"
while IFS=$'\t' read -r seq expected; do
  got="$(q "SELECT last_value FROM pg_sequences WHERE schemaname||'.'||sequencename = '$seq'")"
  [ "$got" = "$expected" ] || { echo "DIFF sequence $seq expected $expected got $got"; FAIL=1; }
done < <(python3 -c "import json; [print(f'{k}\t{\"\" if v is None else v}') for k,v in json.load(open('inventory.json'))['sequences'].items()]")
echo "sequences checked: $(inv "len(d['sequences'])")"

[ "$FAIL" = 0 ] || { echo "RESULT: DATA VERIFICATION FAILED"; exit 1; }

echo "== application compatibility: backend ghcr.io/danielbugi/wolfx_alpha-backend:$TAG on the restored copy"
docker run -d --name "$BE" --network "$NET" \
  -e DB_HOST="$PG" -e DB_PORT=5432 -e DB_NAME="$DB" -e DB_USER=trading_user -e DB_PASSWORD="$PW" \
  -e JWT_SECRET="$(openssl rand -hex 48)" -e TELEGRAM_CONTROL_TOKEN="$(openssl rand -hex 32)" \
  -e ENVIRONMENT=restore-verify -e ALLOWED_ORIGINS=http://localhost -e PROD_SENDING_ENABLED=0 \
  -e TELEGRAM_BOT_TOKEN=000000000:INVALID-RESTORE-VERIFY -e API_HOST=0.0.0.0 \
  "ghcr.io/danielbugi/wolfx_alpha-backend:$TAG" >/dev/null
for _ in $(seq 1 60); do
  st="$(docker inspect -f '{{.State.Health.Status}}' "$BE")"
  [ "$st" = healthy ] && break; [ "$st" = unhealthy ] && break; sleep 3
done
echo "container health: $st"
[ "$st" = healthy ] || { docker logs --tail 40 "$BE"; echo "RESULT: BACKEND NOT HEALTHY ON RESTORED DATA"; exit 1; }
docker exec "$BE" python -c "
import json, urllib.request
h = json.load(urllib.request.urlopen('http://localhost:8000/api/health', timeout=60))
db = h['components']['database']
print('api/health:', h['status'], '| database:', db.get('status'), '| stock_prices rows:', db.get('stock_prices_count'))
assert db.get('status') == 'healthy' and (db.get('stock_prices_count') or 0) > 0
"
bash "$SMOKE" "$BE"
echo "RESULT: RESTORE VERIFIED (data exact, schema matches, backend $TAG healthy + auth OK on the copy)"
