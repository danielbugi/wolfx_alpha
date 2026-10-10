BEGIN READ ONLY;
SET LOCAL statement_timeout = '60s';
\set ctes 'WITH lp AS (SELECT max(date) AS d FROM stock_prices), u_session AS (SELECT DISTINCT s.symbol FROM stock_prices s, lp WHERE s.date = lp.d AND s.symbol NOT IN (SELECT symbol FROM inactive_symbols)), u_updater AS (SELECT DISTINCT symbol FROM stock_prices WHERE symbol IS NOT NULL AND symbol <> \'\' AND symbol NOT IN (SELECT symbol FROM inactive_symbols)), latest AS (SELECT DISTINCT ON (f.symbol) f.symbol, f.date, f.sector, f.created_at FROM daily_fundamentals f JOIN u_updater USING (symbol) ORDER BY f.symbol, f.date DESC), latest_sector AS (SELECT DISTINCT ON (f.symbol) f.symbol, f.date AS sector_date, f.sector FROM daily_fundamentals f JOIN u_updater USING (symbol) WHERE f.sector IS NOT NULL AND btrim(f.sector) <> \'\' AND lower(btrim(f.sector)) <> \'unknown\' ORDER BY f.symbol, f.date DESC)'
\echo '== A3 universes'
:ctes SELECT 'latest_price_session', d FROM lp;
:ctes SELECT 'u_session_symbols', count(*) FROM u_session;
:ctes SELECT 'u_updater_symbols', count(*) FROM u_updater;
SELECT 'inactive_symbols', count(*) FROM inactive_symbols;
\echo '== A4 age of the latest fundamentals row (latest_price_session - row date): bucket|updater_set|session_universe'
:ctes SELECT CASE WHEN l.symbol IS NULL THEN 'no_row' WHEN (lp.d - l.date) <= 0 THEN '0d' WHEN (lp.d - l.date) = 1 THEN '1d' WHEN (lp.d - l.date) <= 4 THEN '2-4d' WHEN (lp.d - l.date) <= 10 THEN '5-10d' WHEN (lp.d - l.date) <= 30 THEN '11-30d' ELSE '>30d' END AS bucket, count(*), count(*) FILTER (WHERE us.symbol IS NOT NULL) FROM u_updater up CROSS JOIN lp LEFT JOIN latest l ON l.symbol = up.symbol LEFT JOIN u_session us ON us.symbol = up.symbol GROUP BY 1 ORDER BY 1;
\echo '== A4b session universe coverage'
:ctes SELECT 'session_universe_with_row_on_latest_session', count(*) FROM u_session us JOIN latest l USING (symbol), lp WHERE l.date = lp.d;
:ctes SELECT 'session_universe_with_row_within_4d', count(*) FROM u_session us JOIN latest l USING (symbol), lp WHERE lp.d - l.date <= 4;
:ctes SELECT 'session_universe_with_row_within_30d', count(*) FROM u_session us JOIN latest l USING (symbol), lp WHERE lp.d - l.date <= 30;
:ctes SELECT 'session_universe_with_NO_fundamentals_row', count(*) FROM u_session us LEFT JOIN latest l USING (symbol) WHERE l.symbol IS NULL;
\echo '== A5 sector availability on the LATEST row (session universe)'
:ctes SELECT CASE WHEN l.symbol IS NULL THEN 'no_row' WHEN l.sector IS NULL THEN 'null' WHEN btrim(l.sector) = '' THEN 'blank' WHEN lower(btrim(l.sector)) = 'unknown' THEN 'unknown_label' ELSE 'available' END, count(*) FROM u_session us LEFT JOIN latest l USING (symbol) GROUP BY 1 ORDER BY 1;
\echo '== A6 sector EVIDENCE age = latest_price_session - date of the latest row with a real sector (session universe)'
:ctes SELECT CASE WHEN ls.symbol IS NULL THEN 'never_had_a_sector' WHEN (lp.d - ls.sector_date) <= 4 THEN '<=4d' WHEN (lp.d - ls.sector_date) <= 30 THEN '5-30d' ELSE '>30d(stale)' END, count(*) FROM u_session us CROSS JOIN lp LEFT JOIN latest_sector ls USING (symbol) GROUP BY 1 ORDER BY 1;
\echo 'sector evidence >30d or never (symbol|sector_date)'
:ctes SELECT us.symbol, ls.sector_date FROM u_session us CROSS JOIN lp LEFT JOIN latest_sector ls USING (symbol) WHERE ls.symbol IS NULL OR lp.d - ls.sector_date > 30 ORDER BY ls.sector_date NULLS FIRST, us.symbol LIMIT 60;
\echo '== A7 distinct latest sector values (session universe)'
:ctes SELECT l.sector, count(*) FROM u_session us JOIN latest l USING (symbol) GROUP BY 1 ORDER BY 2 DESC;
\echo '== A8 symbols whose LATEST row has NULL/blank sector (session universe): symbol|latest_row_date'
:ctes SELECT us.symbol, l.date FROM u_session us LEFT JOIN latest l USING (symbol) WHERE l.symbol IS NULL OR l.sector IS NULL OR btrim(l.sector) = '' ORDER BY us.symbol LIMIT 80;
\echo '== A9 rows stamped on the latest date created_at after 00:00Z next day? (cadence vs PIT window): hour-of-day (UTC) of created_at for the latest 3 dates'
SELECT date, min(created_at AT TIME ZONE 'Asia/Jerusalem' AT TIME ZONE 'UTC'), max(created_at AT TIME ZONE 'Asia/Jerusalem' AT TIME ZONE 'UTC') FROM daily_fundamentals WHERE date >= (SELECT max(date) FROM daily_fundamentals) - 3 GROUP BY date ORDER BY date DESC;
\echo '== A10 trading-day gaps: stock_prices dates in the last 21 days vs fundamentals dates'
SELECT p.date, (SELECT count(*) FROM daily_fundamentals f WHERE f.date = p.date) AS fundamentals_rows FROM (SELECT DISTINCT date FROM stock_prices WHERE date >= (SELECT max(date) FROM stock_prices) - 21) p ORDER BY p.date DESC;
ROLLBACK;
