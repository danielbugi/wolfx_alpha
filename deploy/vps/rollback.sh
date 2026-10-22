#!/usr/bin/env bash
# /opt/donchian/scripts/rollback.sh [backend]
# One-command rollback: redeploys the backend at PREVIOUS_GOOD_SHA (image AND that commit's Compose
# config, via deploy.sh). Application-level only -- never touches the database; schema changes are
# not assumed reversible (docs/devops/CD_DESIGN.md §6).
# Runs deploy.sh in DEPLOY_MODE=rollback: the release being left is recorded in FAILED_SHAS and
# PREVIOUS_GOOD_SHA moves to the next older known-good release, so a second rollback goes further back
# and never returns to a failed image (see the pointer notes at the top of deploy.sh).
set -euo pipefail

SERVICE="${1:-backend}"
BASE=/opt/donchian

PREV_SHA="$(cat "$BASE/PREVIOUS_GOOD_SHA" 2>/dev/null || true)"
if [ -z "$PREV_SHA" ]; then
  echo "No PREVIOUS_GOOD_SHA recorded -- nothing to roll back to." >&2
  exit 1
fi
if [ -f "$BASE/FAILED_SHAS" ] && grep -qx "$PREV_SHA" "$BASE/FAILED_SHAS"; then
  echo "refused: PREVIOUS_GOOD_SHA $PREV_SHA is recorded in FAILED_SHAS -- fix the pointer before rolling back." >&2
  exit 1
fi

echo "Rolling back $SERVICE from $(cat "$BASE/CURRENT_SHA" 2>/dev/null || echo none) to $PREV_SHA"
DEPLOY_MODE=rollback exec "$BASE/scripts/deploy.sh" "$SERVICE" "$PREV_SHA"
