#!/usr/bin/env bash
# Reproducible visual check: headless-Chrome screenshots of the dashboard routes.
#
#   frontend/scripts/screenshots.sh OUT_DIR [route ...]
#
# Needs `next dev` (or `next start`) already running, and the backend on :8000.
# Pages are loaded via the `localhost` origin on purpose: the backend's CORS
# allow-list is keyed on localhost, so http://127.0.0.1:<port> gives "Failed to
# fetch" on every panel (FRONTEND_FIX_MILESTONES.md FM4.7).
#
# Env overrides: BASE_URL (default http://localhost:3001), CHROME (path to
# chrome.exe), WIDTHS (default "1440 820 390"), BUDGET_MS (default 15000 —
# virtual time Chrome waits for data fetches to settle before capturing).
set -euo pipefail

OUT_DIR="${1:?usage: screenshots.sh OUT_DIR [route ...]}"
shift || true
ROUTES=("$@")
[ ${#ROUTES[@]} -eq 0 ] && ROUTES=(/ /screener /strategy /alerts /stock/AAPL)

BASE_URL="${BASE_URL:-http://localhost:3001}"
WIDTHS="${WIDTHS:-1440 820 390}"
BUDGET_MS="${BUDGET_MS:-15000}"
HEIGHT_FOR() { case "$1" in 1440) echo 2400 ;; 820) echo 2400 ;; *) echo 3200 ;; esac; }

CHROME="${CHROME:-}"
if [ -z "$CHROME" ]; then
  for c in "/c/Program Files/Google/Chrome/Application/chrome.exe" \
           "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"; do
    [ -x "$c" ] && CHROME="$c" && break
  done
fi
[ -n "$CHROME" ] || { echo "Chrome not found; set CHROME=/path/to/chrome" >&2; exit 1; }

mkdir -p "$OUT_DIR"
# Windows Chrome needs a Windows-style path; cygpath ships with Git Bash.
win() { cygpath -w "$1"; }
PROFILE="$(mktemp -d)"   # throwaway profile: never touches the user's own Chrome
trap 'rm -rf "$PROFILE"' EXIT

for route in "${ROUTES[@]}"; do
  slug="$(echo "$route" | sed 's#^/##; s#/#_#g; s#[^A-Za-z0-9._-]#-#g')"
  [ -z "$slug" ] && slug="home"
  for w in $WIDTHS; do
    out="$OUT_DIR/${slug}_${w}.png"
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars \
      --user-data-dir="$(win "$PROFILE")" \
      --window-size="$w,$(HEIGHT_FOR "$w")" \
      --virtual-time-budget="$BUDGET_MS" \
      --screenshot="$(win "$out")" \
      "$BASE_URL$route" >/dev/null 2>&1 || true
    if [ -s "$out" ]; then echo "ok    $out"; else echo "FAIL  $route @ $w" >&2; fi
  done
done
