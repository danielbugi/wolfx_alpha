-- Removes the one non-session price bar found in production (audit 2026-11-02, read-only) and the indicator row derived from it.
--   stock_prices          CBL 2026-02-16 (Presidents' Day): open=high=low=close=33.6709, volume 0, created 2026-09-18 08:50 by the Tiingo backfill
--   technical_indicators  CBL 2026-02-16
-- NOT EXECUTED. Needs owner approval. DRY RUN BY DEFAULT: the transaction ends in ROLLBACK; change the last line to COMMIT only after reviewing
-- the output. Every DELETE matches the exact audited values, and the script refuses (raises) if the counts differ from the audit.
--
--   ssh ... 'docker exec -i donchian-screener-postgres-1 psql -U $POSTGRES_USER -d trading_production -v ON_ERROR_STOP=1' < cleanup_stray_session_bars_2026-11-02.sql
--
-- Before running: take the usual pre-change backup (deploy/db), and deploy the write-time guard (fix/price-bar-session-guard) first, otherwise
-- a later backfill can write the same bar again.
\set ON_ERROR_STOP 1
BEGIN;

-- 1. what will be removed (kept in the session output as the record of the change)
SELECT 'stock_prices' AS t, symbol, date, open, high, low, close, adj_close, volume, created_at FROM stock_prices WHERE symbol = 'CBL' AND date = '2026-02-16';
SELECT 'technical_indicators' AS t, * FROM technical_indicators WHERE symbol = 'CBL' AND date = '2026-02-16';

-- 2. preflight: the audit found exactly one off-calendar date in stock_prices, with exactly one row
DO $$
DECLARE n_dates int; n_rows int;
BEGIN
  SELECT count(*) INTO n_dates FROM (SELECT date FROM stock_prices GROUP BY date HAVING count(*) < 500) d;
  SELECT count(*) INTO n_rows FROM stock_prices WHERE date = '2026-02-16';
  IF n_dates <> 1 OR n_rows <> 1 THEN
    RAISE EXCEPTION 'state differs from the 2026-11-02 audit (thin dates %, rows on 2026-02-16 %): re-audit before cleaning', n_dates, n_rows;
  END IF;
END $$;

-- 3. remove, matching the exact audited bar
WITH d AS (
  DELETE FROM stock_prices
  WHERE symbol = 'CBL' AND date = '2026-02-16' AND volume = 0 AND open = 33.6709 AND high = 33.6709 AND low = 33.6709 AND close = 33.6709
  RETURNING 1)
SELECT count(*) AS stock_prices_deleted FROM d;
WITH d AS (DELETE FROM technical_indicators WHERE symbol = 'CBL' AND date = '2026-02-16' RETURNING 1)
SELECT count(*) AS technical_indicators_deleted FROM d;

-- 4. verify
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM stock_prices WHERE date = '2026-02-16') OR EXISTS (SELECT 1 FROM technical_indicators WHERE date = '2026-02-16') THEN
    RAISE EXCEPTION 'rows remain on 2026-02-16';
  END IF;
END $$;

ROLLBACK;   -- dry run. Replace with COMMIT after review and approval.
