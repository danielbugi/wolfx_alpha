#!/usr/bin/env bash
# PROPOSED -- NOT INSTALLED ON THE VPS (docs/operations/BOT_DUAL_PIN.md). A library for deploy.sh: `source` it.
#
# A backend deploy may recreate the backend (and, on a Caddyfile change, the reverse proxy). Anything else in the compose project (the bot, postgres) must come out of the deploy exactly as it went in.
#   guard_snapshot <file>            record "name image-id created started" of every project container except *-backend-* and *-reverse-proxy-*
#   guard_assert_unchanged <file>    take it again; if it differs, say exactly what changed and return 1 (the caller logs and stops: it does NOT roll anything back by itself)
GUARD_PROJECT="${GUARD_PROJECT:-donchian-screener}"

guard_snapshot() {
  local out="$1" n
  : > "$out"
  docker ps -a --filter "label=com.docker.compose.project=$GUARD_PROJECT" --format '{{.Names}}' | sort | while read -r n; do
    case "$n" in *-backend-*|*-reverse-proxy-*) continue ;; esac
    docker inspect -f '{{.Name}} {{.Image}} {{.Created}} {{.State.StartedAt}}' "$n" >> "$out"
  done
}

guard_assert_unchanged() {
  local before="$1"
  guard_snapshot "$before.after"
  if ! diff -u "$before" "$before.after" >/dev/null; then
    echo "BLAST-RADIUS GUARD: a container other than backend/reverse-proxy changed during the deploy:" >&2
    diff -u "$before" "$before.after" >&2 || true
    return 1
  fi
}
