-- Verifies the soak role, as a superuser/owner login (read-only catalog queries) and then AS the role.
-- Usage: psql -v ON_ERROR_STOP=1 -d trading_production -f release_soak_role_verify.sql
-- Expected: every line starts with PASS. Any FAIL = do not start the soak.

CREATE TEMP TABLE _v (ok boolean, what text);

INSERT INTO _v SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit),
  'role has no superuser/createdb/createrole/replication/bypassrls/inherit' FROM pg_roles WHERE rolname = 'fl_release_soak';
INSERT INTO _v SELECT count(*) = 0, 'role is a member of no role (no inherited privileges)'
  FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = 'fl_release_soak';
INSERT INTO _v SELECT count(*) = 0, 'role owns nothing' FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = 'fl_release_soak';
INSERT INTO _v SELECT count(*) = 0,
  'no write privilege (INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER) on any table, via role or PUBLIC'
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,
       unnest(ARRAY['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p(priv)
  WHERE n.nspname = 'public' AND c.relkind IN ('r','v','m','p') AND has_table_privilege('fl_release_soak', c.oid, p.priv);
INSERT INTO _v SELECT NOT has_schema_privilege('fl_release_soak', 'public', 'CREATE'), 'no CREATE on schema public';
INSERT INTO _v SELECT NOT has_database_privilege('fl_release_soak', current_database(), 'CREATE'), 'no CREATE on the database';
INSERT INTO _v SELECT count(*) = 0, 'no privilege on any sensitive table'
  FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname IN (
    'dashboard_users','dashboard_sessions','dashboard_login_challenges','dashboard_auth_audit','bot_access','bot_audit',
    'bot_chart_cache','bot_invites','bot_requests','bot_settings','bot_tracked','bot_user_settings','bot_users',
    'bot_watchlist','telegram_messages','telegram_control_audit','telegram_post_delivery')
    AND has_table_privilege('fl_release_soak', c.oid, 'SELECT');
INSERT INTO _v SELECT count(*) = 0, 'cannot EXECUTE any SECURITY DEFINER function'
  FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef AND p.pronargs > 0
    AND has_function_privilege('fl_release_soak', p.oid, 'EXECUTE');
INSERT INTO _v SELECT coalesce(bool_and(has_table_privilege('fl_release_soak', c.oid, 'SELECT')), false) AND count(*) = 3,
  'can SELECT the data tables (stock_prices, technical_indicators, earnings_calendar)'
  FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relname IN ('stock_prices','technical_indicators','earnings_calendar');
SELECT CASE WHEN ok THEN 'PASS ' ELSE 'FAIL ' END || what FROM _v;
SELECT CASE WHEN bool_and(ok) THEN 'PASS catalog audit' ELSE 'FAIL catalog audit' END FROM _v;

-- Behaviour AS the role, with the session read-only default deliberately switched OFF, so only privileges stop it.
-- Each statement must fail with "permission denied".
\set ON_ERROR_STOP off
SET ROLE fl_release_soak;
SET default_transaction_read_only = off;
SET transaction_read_only = off;
\echo 'EXPECT-DENIED (every line below must be an ERROR: permission denied ...)'
INSERT INTO stock_prices (symbol) VALUES ('ZZZZ');
UPDATE stock_prices SET symbol = symbol WHERE false;
DELETE FROM stock_prices WHERE false;
TRUNCATE stock_prices;
CREATE TABLE public.fl_soak_probe (x int);
ALTER TABLE stock_prices ADD COLUMN fl_soak_probe int;
DROP TABLE stock_prices;
CREATE INDEX fl_soak_probe_idx ON stock_prices (symbol);
SELECT * FROM dashboard_users LIMIT 1;
SELECT * FROM bot_users LIMIT 1;
SELECT research_capture_set_state(1, 'active', current_date, 'probe');
GRANT SELECT ON stock_prices TO PUBLIC;
CREATE ROLE fl_soak_probe_role;
\echo 'EXPECT-ALLOWED (reads only)'
SELECT 'PASS read stock_prices, rows=' || count(*) FROM stock_prices;
SELECT 'PASS read latest_stock_data view, rows=' || count(*) FROM latest_stock_data;
\echo 'KNOWN, HARMLESS: PUBLIC may create session-local TEMP tables (gone at disconnect, visible to no other session)'
CREATE TEMP TABLE fl_soak_temp_probe (x int);
RESET ROLE;
