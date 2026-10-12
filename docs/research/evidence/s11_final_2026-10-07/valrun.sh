A=cfd83f72f960; ENVF=/opt/donchian/env/.env; IMG=ghcr.io/danielbugi/wolfx_alpha-mechanism:$A
docker run --rm -i --network donchian-screener_app_net --env-file "$ENVF" -e DB_HOST=postgres -e PYTHONPATH=/app:/app/mechanism -v "$ENVF:$ENVF:ro" --entrypoint sh $IMG -c '
mkdir -p /tmp/ov && tar xf - -C /tmp/ov && cd /app
V() { echo "#### VAL $*"; python /tmp/ov/validate_release_b.py "$@" 2>&1 | cut -c1-300; }
V schema --expect exact --capture inactive --ml-models any
V roles --expect present
V config --env-file /opt/donchian/env/.env --expect-guards set --expect-capture unset --check-boundary-vs-db
V connections --forbid-user trading_user
V guards --session 2026-10-05 --expect active --log /tmp/ov/g1005.log
V guards --session 2026-10-06 --expect active --log /tmp/ov/g1006.log
V delivery --session 2026-10-05
V delivery --session 2026-10-06
'
