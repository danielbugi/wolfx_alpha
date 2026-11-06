-- deploy/db/research_reader_rollback.sql -- remove the research_reader role (deploy/db/research_reader_role.sql). Owns no objects, so nothing else changes.
--     docker exec -i donchian-screener-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/research_reader_rollback.sql
-- Immediate disable without removal (keeps the grants for review): ALTER ROLE research_reader NOLOGIN;

ALTER ROLE research_reader NOLOGIN;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'research_reader';
DO $$
BEGIN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM research_reader', current_database());
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM research_reader;
REVOKE ALL ON SCHEMA public FROM research_reader;
DROP ROLE research_reader;
SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_reader') THEN 'STILL PRESENT' ELSE 'research_reader removed' END AS result;
