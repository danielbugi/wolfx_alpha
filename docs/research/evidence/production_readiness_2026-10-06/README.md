# Slice 12 - read-only production evidence (2026-10-06)

Four owner-approved read-only checks. No secret appears in any file here. Nothing was written to production: the DB checks ran inside `BEGIN READ ONLY`
with `default_transaction_read_only=on` (the first attempt tried a temporary table and was REFUSED by the read-only guard - kept in
`check_a_part1_attempt.*` for transparency), then `ROLLBACK`; the vendor dry run ran in a throw-away container (`--rm`, no env file, no volume, no database).

| Check | When (UTC) | Target | Files |
|---|---|---|---|
| A  daily_fundamentals freshness | 2026-10-06 20:13:52Z and 20:14:18Z | host `ubuntu-8gb-nbg1-2` (116.203.220.219), DB `trading_production`, session user `trading_user` (the bootstrap identity), read-only | `check_a_part1_attempt.*`, `check_a_main.*` |
| B  DATA_PROVIDER | 20:13:04Z and 20:19:15Z | `/opt/donchian/env/.env` (the one line `DATA_PROVIDER=`), the running `donchian-screener-bot-1` container's effective value, the tracked compose wiring, `donchian-pipeline.service` ExecStart | recorded in `LAB_SLICE12_PRODUCTION_READINESS.md` (no other variable was read) |
| C  VPS-network yfinance dry run | 20:18:27Z - 20:18:40Z | image `ghcr.io/danielbugi/wolfx_alpha-mechanism:cfd83f72f960` (CURRENT_MECHANISM_SHA) run with `--rm`; the two lab files (`vendor_symbols.py`, `sector_history_recorder.py`) were streamed in over stdin | `check_c_dry_run.json` (the non-mutating dry run, after translation), `check_c_raw_untranslated.json` (what the UNtranslated dotted forms return, for contrast), `check_c_raw_probe.py` |
| D  dotted-symbol production universe | 20:14:56Z | same DB, read-only | `check_d.sql`, `check_d.out` |

Timestamps in `daily_fundamentals.created_at` are `timestamp without time zone`; the analysis treats them as UTC (see the Slice 12 doc for why).
