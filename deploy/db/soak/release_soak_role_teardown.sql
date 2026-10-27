-- Removes the temporary soak role after the soak (also run it if the soak is abandoned). Run as the bootstrap/owner login:
--   psql -v ON_ERROR_STOP=1 -d trading_production -f release_soak_role_teardown.sql
-- Then delete /root/fl-release-soak.env and confirm: SELECT count(*) FROM pg_roles WHERE rolname = 'fl_release_soak';  -- 0
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'fl_release_soak';
DROP OWNED BY fl_release_soak;      -- the role owns nothing; this drops its grants
DROP ROLE fl_release_soak;
