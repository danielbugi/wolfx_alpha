#!/usr/bin/env bash
# READ-ONLY extract for the SEC point-in-time history study. Every query runs in a session forced read-only (default_transaction_read_only=on) on the production database, through the
# deploy SSH key; output is gzipped on the server and written under data/ (git-ignored). Same column layout as research/strategy_lab/extract_sec_facts.py, so inflection.runner's
# loader reads it unchanged.
#
#   bash mechanism/research/sec_pit_history/extract.sh            (from the repo root; ~2.8M fact rows)
set -euo pipefail
KEY=${KEY:-$HOME/.ssh/donchian_deploy}; HOST=${HOST:-deploy@116.203.220.219}
OUT="$(cd "$(dirname "$0")" && pwd)/data/sec"
mkdir -p "$OUT"

copy() {   # $1 = output file, stdin = one COPY ... TO STDOUT statement
    ssh -i "$KEY" -o ConnectTimeout=20 "$HOST" \
        'docker exec -i donchian-screener-postgres-1 sh -c "PGOPTIONS=\"-c default_transaction_read_only=on -c statement_timeout=900000\" psql -U \$POSTGRES_USER -d trading_production -q -v ON_ERROR_STOP=1" | gzip -c -n' > "$1"
}

SYMBOLS="SELECT DISTINCT ON (m.cik) m.cik, m.symbol FROM sec_symbol_cik m JOIN (SELECT DISTINCT cik FROM sec_quarter) q ON q.cik = m.cik
         JOIN (SELECT symbol, COUNT(*) AS n FROM stock_prices GROUP BY symbol) p ON p.symbol = m.symbol ORDER BY m.cik, p.n DESC, LENGTH(m.symbol), m.symbol"

copy "$OUT/sec_facts.csv.gz" <<SQL
COPY (WITH s AS ($SYMBOLS)
      SELECT s.symbol, f.metric, f.tag, f.unit, f.value::text, f.period_start, f.period_end, f.fy, f.fp, f.form, f.accession, f.filed_date
      FROM sec_fact f JOIN s ON s.cik = f.cik ORDER BY s.symbol, f.metric, f.period_end, f.filed_date, f.accession) TO STDOUT WITH (FORMAT csv, HEADER)
SQL
copy "$OUT/sec_filings.csv.gz" <<SQL
COPY (WITH s AS ($SYMBOLS)
      SELECT fl.accession, (fl.accepted_at AT TIME ZONE 'America/New_York')::date AS accepted_date_et, to_char(fl.accepted_at AT TIME ZONE 'America/New_York', 'HH24:MI:SS') AS accepted_time_et,
             fl.form, fl.is_amendment
      FROM sec_filing fl JOIN s ON s.cik = fl.cik WHERE fl.accepted_at IS NOT NULL ORDER BY fl.accession) TO STDOUT WITH (FORMAT csv, HEADER)
SQL
copy "$OUT/symbols.csv.gz" <<SQL
COPY ($SYMBOLS) TO STDOUT WITH (FORMAT csv, HEADER)
SQL
# the sector label the live run uses: the latest stored daily_fundamentals label (NOT point in time; documented as a limitation)
copy "$OUT/sectors.csv.gz" <<SQL
COPY (SELECT DISTINCT ON (symbol) symbol, sector FROM daily_fundamentals WHERE sector IS NOT NULL AND sector <> '' ORDER BY symbol, date DESC) TO STDOUT WITH (FORMAT csv, HEADER)
SQL
# the production inflection_v2_sec_pit events of the first full run, to check that the offline reconstruction reproduces them
copy "$OUT/prod_v2_events.csv.gz" <<SQL
COPY (SELECT symbol, quarter_end, event_type, strength, severity, available_at, availability_basis, data_quality FROM fundamental_inflection_event
      WHERE method_version = 'inflection_v2_sec_pit' ORDER BY symbol, quarter_end, event_type) TO STDOUT WITH (FORMAT csv, HEADER)
SQL
# the universe the coverage gate measures: live price symbols, and whether each is mapped / ingested
copy "$OUT/universe.csv.gz" <<SQL
COPY (WITH u AS (SELECT DISTINCT symbol FROM stock_prices WHERE symbol NOT IN (SELECT symbol FROM inactive_symbols)),
           q AS (SELECT DISTINCT cik FROM sec_quarter WHERE metric = 'revenue')
      SELECT u.symbol, c.cik, (q.cik IS NOT NULL) AS has_revenue_quarters, st.last_status FROM u LEFT JOIN sec_symbol_cik c ON c.symbol = u.symbol LEFT JOIN q ON q.cik = c.cik
      LEFT JOIN sec_fundamentals_state st ON st.cik = c.cik ORDER BY 1) TO STDOUT WITH (FORMAT csv, HEADER)
SQL
ls -la "$OUT"
