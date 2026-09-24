#!/usr/bin/env bash
# auth_smoke.sh <backend-container> [--keep]
#
# End-to-end authentication check against a RUNNING backend container, from inside it (no published
# port needed): creates a throwaway Owner account, logs in with its password, reads the emailed 2FA
# code from the server log (only works where SMTP_HOST is unset -- staging / disposable restores,
# never production), exchanges it for an access token, then proves:
#   - protected endpoints return 401 without a token and 200 with it
#   - a wrong password is refused
# The throwaway account and its sessions are deleted afterwards (unless --keep).
#
# Use only against staging or a disposable restored copy -- it writes one user row.
set -euo pipefail

C="${1:?usage: auth_smoke.sh <backend-container> [--keep]}"
KEEP="${2:-}"
EMAIL="smoke-$(date +%s)-$RANDOM@example.com"
PASS="$(openssl rand -base64 24)"

if docker exec "$C" printenv SMTP_HOST 2>/dev/null | grep -q .; then
  echo "refused: SMTP_HOST is set in $C (a real mail setup) -- this check reads the 2FA code from logs" >&2
  exit 2
fi

printf '%s\n%s\n' "$PASS" "$PASS" \
  | docker exec -i -w /app/backend "$C" python scripts/create_user.py --email "$EMAIL" --role owner >/dev/null 2>&1

cleanup() {
  [ "$KEEP" = "--keep" ] && return
  # sessions/challenges cascade; the audit trail is kept (its user_id becomes NULL)
  docker exec "$C" python -c "
import os, sys, psycopg2
c = psycopg2.connect(host=os.environ['DB_HOST'], port=os.environ.get('DB_PORT', '5432'), dbname=os.environ['DB_NAME'],
                     user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'])
with c, c.cursor() as cur:
    cur.execute('DELETE FROM dashboard_users WHERE email = %s', (sys.argv[1],))
    print('deleted throwaway account rows:', cur.rowcount)
c.close()
" "$EMAIL" || true
}
trap cleanup EXIT

SINCE="$(date -u +%FT%TZ)"
PY_CALL='
import json, sys, urllib.request, urllib.error
BASE = "http://localhost:8000"
def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")
'

# stage 1: wrong password refused, right password -> 2FA challenge
CHALLENGE="$(docker exec -i "$C" python -c "$PY_CALL
email, pw = sys.argv[1], sys.argv[2]
s, _ = call(\"POST\", \"/api/auth/login\", {\"email\": email, \"password\": pw + \"x\"})
assert s == 401, f\"wrong password returned {s}\"
s, ch = call(\"POST\", \"/api/auth/login\", {\"email\": email, \"password\": pw})
assert s == 200 and ch.get(\"stage\") == \"2fa_required\", (s, ch)
print(ch[\"challenge_token\"])
" "$EMAIL" "$PASS")"
echo "PASS wrong password refused (401); correct password -> 2FA challenge"

# the code is only in the server log because SMTP is unset here (checked above)
CODE=""
for _ in $(seq 1 10); do
  CODE="$(docker logs --since "$SINCE" "$C" 2>&1 | grep -F "login code for $EMAIL is" | tail -1 | grep -oE '[0-9]{6}' | tail -1 || true)"
  [ -n "$CODE" ] && break
  sleep 1
done
[ -n "$CODE" ] || { echo "FAIL could not find the 2FA code in the server log" >&2; exit 1; }

# stage 2: code -> tokens; protected endpoints 401 without / 200 with the token
docker exec -i "$C" python -c "$PY_CALL
ch, code = sys.argv[1], sys.argv[2]
s, _ = call(\"POST\", \"/api/auth/verify-2fa\", {\"challenge_token\": ch, \"code\": \"000000\" if code != \"000000\" else \"111111\"})
assert s == 401, f\"wrong 2FA code returned {s}\"
print(\"PASS wrong 2FA code refused (401)\")
s, tok = call(\"POST\", \"/api/auth/verify-2fa\", {\"challenge_token\": ch, \"code\": code})
assert s == 200 and tok.get(\"access_token\"), (s, tok)
print(\"PASS 2FA code -> access token (role %s)\" % tok[\"user\"][\"role\"])
at = tok[\"access_token\"]
s, me = call(\"GET\", \"/api/auth/me\", token=at)
assert s == 200, s
for path in (\"/api/screener/market-overview\", \"/api/momentum-board\", \"/api/dashboard/main-page-data\",
             \"/api/market/indices\", \"/api/system-health/\", \"/api/auth/users\"):
    s0, _ = call(\"GET\", path)
    s1, body = call(\"GET\", path, token=at)
    assert s0 == 401, (path, \"no token\", s0)
    assert s1 == 200, (path, \"with token\", s1, str(body)[:300])
    print(\"PASS %-32s 401 without token, 200 with token\" % path)
s, _ = call(\"POST\", \"/api/auth/logout\", {\"refresh_token\": tok[\"refresh_token\"]}, token=at)
print(\"PASS logout (%s)\" % s)
" "$CHALLENGE" "$CODE"
echo "AUTH SMOKE: ALL PASSED"
