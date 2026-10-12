BEGIN READ ONLY;
SET LOCAL statement_timeout = '60s';
\echo '== D0 identity'
SELECT 'db', current_database(), 'read_only', current_setting('transaction_read_only'), 'server_utc', to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD"T"HH24:MI:SS"Z"');
\set ctes 'WITH lp AS (SELECT max(date) AS d FROM stock_prices), allsym AS (SELECT symbol, max(date) AS last_price, count(*) AS price_rows FROM stock_prices WHERE symbol IS NOT NULL GROUP BY symbol), inact AS (SELECT symbol FROM inactive_symbols)'
\echo '== D1 every stock_prices symbol that is NOT plain [A-Z0-9]+ : symbol|in_latest_session|inactive|last_price|price_rows|last_fund_row|latest_sector'
:ctes SELECT a.symbol, (a.last_price = lp.d) AS in_latest_session, (a.symbol IN (SELECT symbol FROM inact)) AS inactive, a.last_price, a.price_rows, (SELECT max(f.date) FROM daily_fundamentals f WHERE f.symbol = a.symbol) AS last_fund_row, (SELECT f.sector FROM daily_fundamentals f WHERE f.symbol = a.symbol ORDER BY f.date DESC LIMIT 1) AS latest_sector FROM allsym a CROSS JOIN lp WHERE a.symbol !~ '^[A-Z0-9]+$' ORDER BY a.symbol;
\echo '== D2 characters used outside [A-Z0-9] across ALL stock_prices symbols: char|symbols'
:ctes SELECT ch, count(*) FROM (SELECT regexp_split_to_table(regexp_replace(symbol, '[A-Z0-9]', '', 'g'), '') AS ch FROM allsym) t WHERE ch <> '' GROUP BY ch ORDER BY 2 DESC;
\echo '== D3 symbol-length distribution'
:ctes SELECT length(symbol), count(*) FROM allsym GROUP BY 1 ORDER BY 1;
\echo '== D4 same-company sibling spellings present (normalise by stripping . / - and compare): base|spellings'
:ctes SELECT regexp_replace(symbol, '[./-]', '', 'g') AS base, string_agg(symbol, ',' ORDER BY symbol) FROM allsym GROUP BY 1 HAVING count(*) > 1 ORDER BY 1;
\echo '== D5 lowercase / whitespace / non-ascii symbols'
:ctes SELECT symbol FROM allsym WHERE symbol ~ '[a-z[:space:]]' OR symbol ~ '[^ -~]';
\echo '== D6 the active updater set size and how many of it are non-plain'
:ctes SELECT 'updater_set', count(*), count(*) FILTER (WHERE symbol !~ '^[A-Z0-9]+$') FROM allsym WHERE symbol NOT IN (SELECT symbol FROM inact);
ROLLBACK;
