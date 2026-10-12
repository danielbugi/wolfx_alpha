A=cfd83f72f960; ENVF=/opt/donchian/env/.env; IMG=ghcr.io/danielbugi/wolfx_alpha-mechanism:$A
docker run --rm -i --network donchian-screener_app_net --env-file "$ENVF" -e DB_HOST=postgres -e PYTHONPATH=/app:/app/mechanism -v /opt/donchian/compose/breakout_results:/res:ro --entrypoint sh $IMG -c 'mkdir -p /tmp/ov && tar xf - -C /tmp/ov && cd /app && python /tmp/ov/recon_ledger.py 2>&1 | tail -20'
