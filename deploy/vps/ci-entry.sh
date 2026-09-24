#!/usr/bin/env bash
# Forced command for the GitHub Actions deploy key. Installed as
#   command="/opt/donchian/scripts/ci-entry.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 ... github-actions-deploy
# in /home/deploy/.ssh/authorized_keys, so that key can do exactly one thing: deliver the release
# bundle (Compose files + production Caddyfile) for one commit on stdin, and deploy the backend at
# that commit's immutable image tag. Anything else is refused.
set -euo pipefail

read -r -a ARGS <<< "${SSH_ORIGINAL_COMMAND:-}"
if [ "${#ARGS[@]}" -ne 3 ] || [ "${ARGS[0]}" != "deploy" ] || [ "${ARGS[1]}" != "backend" ] \
   || ! [[ "${ARGS[2]}" =~ ^[0-9a-f]{12}$ ]]; then
  echo "refused: this key only accepts 'deploy backend <12-hex-sha>'" >&2
  exit 2
fi
TAG="${ARGS[2]}"
REL=/opt/donchian/releases/$TAG
TMP="$(mktemp -d /opt/donchian/releases/.incoming.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

# Only these exact member names are extracted, so a crafted archive cannot write anywhere else.
tar -xzf - -C "$TMP" --no-same-owner --no-same-permissions \
  docker-compose.yml docker-compose.prod.yml docker/Caddyfile.prod

rm -rf "$REL"
mv "$TMP" "$REL"
trap - EXIT
chmod 755 "$REL"

exec /opt/donchian/scripts/deploy.sh backend "$TAG"
