#!/usr/bin/env bash
# PROPOSED -- NOT INSTALLED ON THE VPS (docs/operations/BOT_DUAL_PIN.md). A library: `source` it, then call `compose_env_export`.
#
# Exports ONE image-tag variable per consumer, each read from THAT consumer's own pin file, so no command can retag a service it does not own:
#     BACKEND_IMAGE_TAG    <- /opt/donchian/CURRENT_SHA            (a deploy may pre-set it to the tag being rolled out)
#     BOT_IMAGE_TAG        <- /opt/donchian/CURRENT_BOT_SHA        (new: the bot's own pin; never taken from the environment)
#     MECHANISM_IMAGE_TAG  <- /opt/donchian/CURRENT_MECHANISM_SHA  (pipeline, collector, scan, notices; never taken from the environment)
#     FRONTEND_IMAGE_TAG   =  BACKEND_IMAGE_TAG                    (self-hosted fallback profile only)
# Every value must be exactly 12 lowercase hex characters; anything else (or a missing pin file) fails loudly with a non-zero status.
DONCHIAN_BASE="${DONCHIAN_BASE:-/opt/donchian}"

_compose_env_pin() {  # _compose_env_pin <file under $DONCHIAN_BASE> <label>
  local f="$DONCHIAN_BASE/$1" v
  [ -f "$f" ] || { echo "compose_env: missing pin file $f ($2)" >&2; return 1; }
  v="$(tr -d '[:space:]' < "$f")"
  [[ "$v" =~ ^[0-9a-f]{12}$ ]] || { echo "compose_env: $f is not a 12-hex sha: '$v'" >&2; return 1; }
  printf '%s' "$v"
}

compose_env_export() {
  local b
  if [ -n "${BACKEND_IMAGE_TAG:-}" ]; then
    b="$BACKEND_IMAGE_TAG"
    [[ "$b" =~ ^[0-9a-f]{12}$ ]] || { echo "compose_env: BACKEND_IMAGE_TAG is not a 12-hex sha: '$b'" >&2; return 1; }
  else
    b="$(_compose_env_pin CURRENT_SHA backend)" || return 1
  fi
  local bot mech
  bot="$(_compose_env_pin CURRENT_BOT_SHA bot)" || return 1
  mech="$(_compose_env_pin CURRENT_MECHANISM_SHA mechanism)" || return 1
  BACKEND_IMAGE_TAG="$b"
  BOT_IMAGE_TAG="$bot"
  MECHANISM_IMAGE_TAG="$mech"
  FRONTEND_IMAGE_TAG="$b"
  unset IMAGE_TAG        # the legacy shared variable must never leak into a compose call that now has per-service tags
  export BACKEND_IMAGE_TAG BOT_IMAGE_TAG MECHANISM_IMAGE_TAG FRONTEND_IMAGE_TAG
}
