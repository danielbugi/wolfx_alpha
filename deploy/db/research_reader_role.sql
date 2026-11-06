-- deploy/db/research_reader_role.sql -- a dedicated SELECT-only login role for the Research Missions source connection (RESEARCH_SOURCE_DSN).
--
-- REVIEW ONLY: not applied anywhere in production until the owner approves this exact SQL.
-- Apply (as the database superuser, inside the postgres container):
--     docker exec -i donchian-screener-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/research_reader_role.sql
-- Then set the password INTERACTIVELY (it is never written to a file, a script or shell history):
--     docker exec -it donchian-screener-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d trading_production -c "\password research_reader"'
-- Verify: deploy/db/research_reader_verify.sql. Disable at once: ALTER ROLE research_reader NOLOGIN. Remove: deploy/db/research_reader_rollback.sql.
--
-- What the role can do: log in (password, scram-sha-256; pg_hba already requires it for every non-loopback client), read the 8 tables the research
-- sources read (mechanism/research_missions/sources.py), and nothing else:
--   * SELECT on exactly 8 tables; no other table grant exists (production grants nothing to PUBLIC on tables, verified 2026-11-06);
--   * no INSERT/UPDATE/DELETE/TRUNCATE anywhere, no sequence, no function beyond PUBLIC defaults, no CREATE on the schema or the database;
--   * not a superuser, cannot create roles or databases, no replication, no RLS bypass, member of no role (NOINHERIT as a second guard);
--   * sessions default to read-only with a 60 s statement timeout, 60 s idle-in-transaction timeout and a 256 MB temp-file cap;
--   * at most 2 concurrent connections; the login expires on 2027-01-31 unless renewed.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_reader') THEN
        RAISE EXCEPTION 'research_reader already exists: verify it with research_reader_verify.sql instead of re-creating it';
    END IF;
END $$;

CREATE ROLE research_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 2 VALID UNTIL '2027-01-31 00:00:00+00';

ALTER ROLE research_reader SET default_transaction_read_only = on;
ALTER ROLE research_reader SET statement_timeout = '60s';
ALTER ROLE research_reader SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE research_reader SET lock_timeout = '5s';
ALTER ROLE research_reader SET temp_file_limit = '256MB';

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO research_reader', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO research_reader;
GRANT SELECT ON TABLE
    public.companies,                     -- company names
    public.daily_fundamentals,            -- universe by industry, sector, market cap
    public.fundamental_inflection_event,  -- inflection events (mission entry and context)
    public.inactive_symbols,              -- universe exclusions
    public.news_items,                    -- headlines (internal only)
    public.sec_quarter,                   -- SEC point-in-time revenue quarters
    public.sec_symbol_cik,                -- symbol -> CIK for the SEC quarters
    public.stock_prices                   -- price and return state
TO research_reader;
