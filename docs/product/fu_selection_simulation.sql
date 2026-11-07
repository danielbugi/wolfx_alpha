-- docs/product/fu_selection_simulation.sql -- READ-ONLY export for mechanism/inflection/update_simulate.py (one JSON array of inflection_v2_sec_pit events).
-- PGOPTIONS='-c default_transaction_read_only=on' psql -d trading_production -At -f docs/product/fu_selection_simulation.sql > v2_events.json
\pset format unaligned
\pset tuples_only on
SELECT json_agg(x) FROM (
  SELECT e.id, e.symbol, e.event_type, e.severity, e.strength, e.fiscal_year, e.fiscal_quarter, e.quarter_end, e.available_at, e.availability_basis, e.data_quality, e.method_version,
         e.metrics, e.context, (e.first_detected_at AT TIME ZONE 'UTC') AS first_detected_utc, (e.first_detected_at AT TIME ZONE 'UTC')::date AS first_detected_on,
         co.company_name, sc.cik, f.quality_notes AS feature_notes, p.date AS profile_date, p.sector, p.industry, p.market_cap AS profile_market_cap
    FROM fundamental_inflection_event e
    LEFT JOIN LATERAL (SELECT c.company_name FROM companies c WHERE c.symbol = e.symbol LIMIT 1) co ON true
    LEFT JOIN sec_symbol_cik sc ON sc.symbol = e.symbol
    LEFT JOIN fundamental_feature f ON f.method_version = e.method_version AND f.symbol = e.symbol AND f.quarter_end = e.quarter_end
    LEFT JOIN LATERAL (SELECT d.date, d.sector, d.industry, d.market_cap FROM daily_fundamentals d WHERE d.symbol = e.symbol AND d.date <= e.available_at ORDER BY d.date DESC LIMIT 1) p ON true
   WHERE e.method_version = 'inflection_v2_sec_pit') x;
