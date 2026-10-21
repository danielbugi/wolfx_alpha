select relname as table_name, n_live_tup as approx_rows from pg_stat_user_tables order by 2 desc
