-- TEMPORARY read-only role for the isolated backend soak (docs/operations/PRODUCT_RELEASE_PLAN_2026-10-28.md §2b).
-- NOT to be run before Stage 2 is COMPLETE and the owner has approved it. Run as the bootstrap/owner login, once:
--   psql -v ON_ERROR_STOP=1 -v soak_pw="$SOAK_PW" -d trading_production -f release_soak_role_setup.sql
-- The password is passed as a psql variable (never written into this file or the shell history of a real value).
-- A role change, not a migration: nothing in the schema or any existing role's privileges is altered.

CREATE ROLE fl_release_soak LOGIN PASSWORD :'soak_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 6;

-- second layer only (a session can override it); the enforcement is the absence of any write privilege below
ALTER ROLE fl_release_soak SET default_transaction_read_only = on;
ALTER ROLE fl_release_soak SET statement_timeout = '60s';
ALTER ROLE fl_release_soak SET idle_in_transaction_session_timeout = '60s';

GRANT CONNECT ON DATABASE trading_production TO fl_release_soak;
GRANT USAGE ON SCHEMA public TO fl_release_soak;

-- SELECT only, on every table and view that exists now ...
GRANT SELECT ON ALL TABLES IN SCHEMA public TO fl_release_soak;

-- ... except the credential, session, member and message tables the data endpoints never read (the soak overrides auth)
REVOKE ALL ON
  dashboard_users, dashboard_sessions, dashboard_login_challenges, dashboard_auth_audit,
  bot_access, bot_audit, bot_chart_cache, bot_invites, bot_requests, bot_settings, bot_tracked, bot_user_settings,
  bot_users, bot_watchlist, telegram_messages, telegram_control_audit, telegram_post_delivery
FROM fl_release_soak;
-- No sequence, function, type or default-privilege grant is made. Write-capable SECURITY DEFINER functions are
-- already restricted to their owner and donchian_research_admin (verified 2026-10-27), so PUBLIC cannot call them.
