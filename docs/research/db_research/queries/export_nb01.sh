#!/usr/bin/env bash
# READ-ONLY export for notebook 01 (plain CSV; notebook 01 reads data/<name>.csv).
# Every session is forced read-only (default_transaction_read_only=on); no table is written.
# Auth, user, bot and session tables are deliberately NOT exported.
# usage: bash export_nb01.sh   (needs the deploy ssh key; run from this folder)
set -euo pipefail
KEY=${KEY:-$HOME/.ssh/donchian_deploy}; HOST=${HOST:-deploy@116.203.220.219}
OUT=../data; mkdir -p "$OUT"
for f in nb01_*.sql; do
  name=${f#nb01_}; name=${name%.sql}
  printf '\copy (%s) to stdout with csv header\n' "$(cat "$f")" | \
  ssh -i "$KEY" "$HOST" 'docker exec -i donchian-screener-postgres-1 sh -c "PGOPTIONS=\"-c default_transaction_read_only=on\" psql -U \$POSTGRES_USER -d trading_production -q"' > "$OUT/$name.csv"
  echo "$name $(wc -l < "$OUT/$name.csv") lines"
done
