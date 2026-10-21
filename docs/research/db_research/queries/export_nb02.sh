#!/usr/bin/env bash
# READ-ONLY export for notebook 02. Every session is forced read-only; no table is written.
# Auth, user, bot and session tables are deliberately NOT exported.
# usage: bash export_nb02.sh   (needs the deploy ssh key; run from this folder)
set -euo pipefail
KEY=${KEY:-$HOME/.ssh/donchian_deploy}; HOST=${HOST:-deploy@116.203.220.219}
OUT=../data; mkdir -p "$OUT"
run() { # name
  printf '\copy (%s) to stdout with csv header\n' "$(cat "nb02_$1.sql")" | \
  ssh -i "$KEY" "$HOST" 'docker exec -i donchian-screener-postgres-1 sh -c "PGOPTIONS=\"-c default_transaction_read_only=on\" psql -U \$POSTGRES_USER -d trading_production -q" | gzip -c' > "$OUT/nb02_$1.csv.gz"
  echo "$1 $(du -h "$OUT/nb02_$1.csv.gz" | cut -f1)"
}
NAMES=${NAMES:-"prices_ohlc earnings_calendar companies price_discontinuities index_prices sector_latest"}
for n in $NAMES; do run $n; done
