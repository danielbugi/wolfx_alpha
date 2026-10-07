BEGIN READ ONLY;
select 'LEDGER_BASELINE_POP|count_and_immutable_fp', count(*) || '|' || coalesce(md5(string_agg(concat_ws('|',id,symbol,signal_date,direction,entry_price,atr,stop_price,target1_price,target2_price,target3_price,sector,quality_grade,strategy_id,strategy_version,feature_set_version,model_version,observation_id,feature_snapshot_id,created_at), E'\n' order by id)),'-') from signal_ledger where signal_date <= '2026-10-02';
select 'LEDGER_BASELINE_POP|mutable_fp', coalesce(md5(string_agg(concat_ws('|',id,status,outcome_r,mae_r,resolved_date,bars_held,last_evaluated_date,resolution_flag,evaluation_flag), E'\n' order by id)),'-') from signal_ledger where signal_date <= '2026-10-02';
select 'DELIVERY_BASELINE_POP|count_rowhash', count(*) || '|' || coalesce(md5(string_agg(t::text, '' order by t::text)),'-') from telegram_post_delivery t where market_session <= '2026-10-02';
select 'DELIVERY_ALL_BY_SESSION', string_agg(market_session||':'||c, ' ' order by market_session) from (select market_session, count(*) c from telegram_post_delivery group by 1) x;
select 'LEDGER_MODEL_VERSION_ALL_NEW_ROWS(signal_date>2026-10-02)', coalesce(model_version,'<NULL>'), count(*) from signal_ledger where signal_date > '2026-10-02' group by 2 order by 2;
select 'LEDGER_MODEL_VERSION_ALL_ROWS', coalesce(model_version,'<NULL>'), count(*) from signal_ledger group by 2 order by 2;
select 'LEDGER_UNKNOWN_BY_GRADE_NEW', quality_grade, coalesce(model_version,'<NULL>'), count(*) from signal_ledger where signal_date > '2026-10-02' group by 2,3 order by 2,3;
ROLLBACK;
