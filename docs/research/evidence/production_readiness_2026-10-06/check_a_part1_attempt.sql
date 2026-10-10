BEGIN READ ONLY;
SET LOCAL statement_timeout = '60s';
\echo '== A0 identity'
SELECT 'db', current_database(), 'role', current_user, 'read_only', current_setting('transaction_read_only'), 'server_utc', to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD"T"HH24:MI:SS"Z"'), 'tz', current_setting('TimeZone');
\echo '== A1 table-level freshness'
SELECT 'stock_prices_max_date', max(date) FROM stock_prices;
SELECT 'daily_fundamentals_max_date', max(date), 'max_created_at', max(created_at), 'max_updated_at', max(updated_at), 'rows', count(*), 'symbols', count(DISTINCT symbol) FROM daily_fundamentals;
\echo '== A2 per-date cadence (last 21 fundamentals dates): date|rows|rows_with_sector|min_created|max_created|max_updated'
SELECT date, count(*), count(*) FILTER (WHERE sector IS NOT NULL AND btrim(sector) <> ''), min(created_at), max(created_at), max(updated_at)
FROM daily_fundamentals WHERE date >= (SELECT max(date) FROM daily_fundamentals) - 21 GROUP BY date ORDER BY date DESC;
\echo '== A3 universes'
CREATE TEMP TABLE lp AS SELECT max(date) AS d FROM stock_prices;
CREATE TEMP TABLE u_session AS SELECT DISTINCT s.symbol FROM stock_prices s, lp WHERE s.date = lp.d AND s.symbol NOT IN (SELECT symbol FROM inactive_symbols);
CREATE TEMP TABLE u_updater AS SELECT DISTINCT symbol FROM stock_prices WHERE symbol IS NOT NULL AND symbol <> '' AND symbol NOT IN (SELECT symbol FROM inactive_symbols);
SELECT 'latest_price_session', d FROM lp;
SELECT 'u_session_symbols', count(*) FROM u_session;
SELECT 'u_updater_symbols(updater target set)', count(*) FROM u_updater;
SELECT 'inactive_symbols', count(*) FROM inactive_symbols;
\echo '== A4 latest fundamentals row per symbol (updater target set)'
CREATE TEMP TABLE latest AS
  SELECT DISTINCT ON (f.symbol) f.symbol, f.date, f.sector, f.created_at, f.updated_at FROM daily_fundamentals f JOIN u_updater USING (symbol) ORDER BY f.symbol, f.date DESC;
CREATE TEMP TABLE latest_sector AS
  SELECT DISTINCT ON (f.symbol) f.symbol, f.date AS sector_date, f.sector FROM daily_fundamentals f JOIN u_updater USING (symbol)
  WHERE f.sector IS NOT NULL AND btrim(f.sector) <> '' AND lower(btrim(f.sector)) <> 'unknown' ORDER BY f.symbol, f.date DESC;
\echo 'age_bucket|updater_set_symbols|session_universe_symbols  (age = latest_price_session - date of the latest fundamentals row)'
SELECT CASE WHEN l.symbol IS NULL THEN 'no_row' WHEN (lp.d - l.date) <= 0 THEN '0d' WHEN (lp.d - l.date) = 1 THEN '1d' WHEN (lp.d - l.date) <= 4 THEN '2-4d'
            WHEN (lp.d - l.date) <= 10 THEN '5-10d' WHEN (lp.d - l.date) <= 30 THEN '11-30d' ELSE '>30d' END AS bucket,
       count(*), count(*) FILTER (WHERE us.symbol IS NOT NULL)
FROM u_updater up CROSS JOIN lp LEFT JOIN latest l ON l.symbol = up.symbol LEFT JOIN u_session us ON us.symbol = up.symbol GROUP BY 1 ORDER BY 1;
\echo 'row dated exactly the latest_price_session'
SELECT 'session_universe_with_fundamentals_row_on_latest_session', count(*) FROM u_session us JOIN latest l USING (symbol), lp WHERE l.date = lp.d;
SELECT 'session_universe_with_fundamentals_row_within_4d', count(*) FROM u_session us JOIN latest l USING (symbol), lp WHERE lp.d - l.date <= 4;
SELECT 'session_universe_with_fundamentals_row_within_30d', count(*) FROM u_session us JOIN latest l USING (symbol), lp WHERE lp.d - l.date <= 30;
\echo '== A5 sector availability on the LATEST row (session universe)'
SELECT CASE WHEN l.symbol IS NULL THEN 'no_row' WHEN l.sector IS NULL THEN 'null' WHEN btrim(l.sector) = '' THEN 'blank' WHEN lower(btrim(l.sector)) = 'unknown' THEN 'unknown_label' ELSE 'available' END, count(*)
FROM u_session us LEFT JOIN latest l USING (symbol) GROUP BY 1 ORDER BY 1;
\echo '== A6 sector EVIDENCE age = latest_price_session - date of the latest row that has a real sector (session universe)'
SELECT CASE WHEN ls.symbol IS NULL THEN 'never_had_a_sector' WHEN (lp.d - ls.sector_date) <= 4 THEN '<=4d' WHEN (lp.d - ls.sector_date) <= 30 THEN '5-30d' ELSE '>30d(stale)' END, count(*)
FROM u_session us CROSS JOIN lp LEFT JOIN latest_sector ls USING (symbol) GROUP BY 1 ORDER BY 1;
\echo 'oldest sector evidence examples (symbol|sector_date)'
SELECT us.symbol, ls.sector_date FROM u_session us CROSS JOIN lp LEFT JOIN latest_sector ls USING (symbol) WHERE ls.symbol IS NULL OR lp.d - ls.sector_date > 30 ORDER BY ls.sector_date NULLS FIRST, us.symbol LIMIT 40;
\echo '== A7 distinct latest sector values (session universe)'
SELECT l.sector, count(*) FROM u_session us JOIN latest l USING (symbol) GROUP BY 1 ORDER BY 2 DESC;
\echo '== A8 NULL-sector symbols on the latest row (session universe): symbol|latest_date'
SELECT us.symbol, l.date FROM u_session us LEFT JOIN latest l USING (symbol) WHERE l.symbol IS NULL OR l.sector IS NULL OR btrim(l.sector) = '' ORDER BY us.symbol LIMIT 80;
ROLLBACK;
