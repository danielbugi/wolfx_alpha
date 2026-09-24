#!/usr/bin/env bash
# /opt/donchian/scripts/rollback.sh [backend]
# One-command rollback: redeploys the backend at PREVIOUS_GOOD_SHA (image AND that commit's Compose
# config, via deploy.sh). Application-level only -- never touches the database; schema changes are
# not assumed reversible (docs/devops/CD_DESIGN.md §6).
set -euo pipefail

SERVICE="${1:-backend}"
BASE=/opt/donchian

PREV_SHA="$(cat "$BASE/PREVIOUS_GOOD_SHA" 2>/dev/null || true)"
if [ -z "$PREV_SHA" ]; then
  echo "No PREVIOUS_GOOD_SHA recorded -- nothing to roll back to." >&2
  exit 1
fi

echo "Rolling back $SERVICE from $(cat "$BASE/CURRENT_SHA" 2>/dev/null || echo none) to $PREV_SHA"
exec "$BASE/scripts/deploy.sh" "$SERVICE" "$PREV_SHA"
