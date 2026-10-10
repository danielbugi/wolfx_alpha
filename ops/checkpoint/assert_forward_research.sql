-- VERDICT A -- FORWARD RESEARCH CORRECTNESS for one session. READ-ONLY (run with default_transaction_read_only=on). Placeholders are substituted by the runner:
--   {{SESSION}} the session under test | {{FLAG_TIME}} the instant the recorder flag was set (timestamptz) | {{BOUNDARY}} the capture boundary session
--   {{STRATEGY_ID}} the enabled strategy | {{PIPELINE_START}} the start of the nightly pipeline window (timestamptz) | {{LEGACY_COUNT}} {{LEGACY_STAMP_MD5}} {{LEGACY_FROM}} {{LEGACY_TO}} the original
--   discontinuity stamps (rows, md5 over symbol|date|kind|detected_at, and the stamp window, in the discontinuity writer's own timezone).
-- Every line is ASSERT|A|<name>|PASS | FAIL[...] | REVIEW[...] | INFO. Nothing here looks at whether the CURRENT prices still match the scan: that is Verdict B.
\pset tuples_only on
\pset format unaligned
-- ===================================================================== scan heartbeat and lineage (historical validity, not freshness)
select 'ASSERT|A|complete_scan_exists_for_session|' || case when exists (select 1 from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete' and newest_bar = date '{{SESSION}}') then 'PASS' else 'FAIL' end;
select 'ASSERT|A|scan_completed_before_the_operational_cutoff (noon New York next day)|' || case when (select min(finished_at) from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete')
    <= (((date '{{SESSION}}' + 1)::timestamp + interval '12 hours') at time zone 'America/New_York') then 'PASS' else 'FAIL' end;
select 'ASSERT|A|scan_completion_is_after_the_session_close_and_database_stamped|' || case when (select min(finished_at) from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete')
    > ((date '{{SESSION}}'::timestamp + interval '16 hours') at time zone 'America/New_York') then 'PASS' else 'FAIL' end;
select 'ASSERT|A|scan_fingerprints_are_well_formed_and_counts_positive|' || case when not exists (select 1 from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete'
    and (input_fingerprint !~ '^[0-9a-f]{64}$' or result_fingerprint !~ '^[0-9a-f]{64}$' or n_price_rows <= 0 or n_symbols <= 0)) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|no_failed_scan_rows_for_session|' || case when not exists (select 1 from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'failed') then 'PASS'
    else 'REVIEW (failed: ' || (select string_agg(failure_reason, ',') from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'failed') || ')' end;
select 'ASSERT|A|scan_rows_for_session complete=' || count(*) filter (where status = 'complete') || ' failed=' || count(*) filter (where status = 'failed') || '|INFO' from price_discontinuity_scan where session_date = date '{{SESSION}}';
select 'ASSERT|A|original_first_detected_stamps_preserved|' || case when count(*) = {{LEGACY_COUNT}} and md5(string_agg(symbol || '|' || date || '|' || kind || '|' || detected_at::text, ',' order by symbol, date, kind)) = '{{LEGACY_STAMP_MD5}}'
    then 'PASS' else 'REVIEW (rows in the original stamp window=' || count(*) || ' of {{LEGACY_COUNT}}; a genuine documented price correction would explain a difference; nothing is modified here)' end
  from price_discontinuities where detected_at between timestamp '{{LEGACY_FROM}}' and timestamp '{{LEGACY_TO}}';
-- ===================================================================== sector history: authoritative chain, diagnostic chains, integrity
select 'ASSERT|A|authoritative_yfinance_info_observations_written|' || case when (select count(*) from sector_observation where source = 'yfinance_info') > 0 then 'PASS (' || (select count(*) from sector_observation where source = 'yfinance_info') || ' rows)'
    else 'FAIL (no yfinance_info observation: polls alone are not sector recording)' end;
select 'ASSERT|A|authoritative_coverage_of_the_fundamentals_universe (distinct yfinance_info symbols >= 90% of the newest fundamentals date)|' || case when (select count(distinct symbol) from sector_observation where source = 'yfinance_info')
    >= 0.9 * (select count(distinct symbol) from daily_fundamentals where date = (select max(date) from daily_fundamentals)) then 'PASS' else 'FAIL (' || (select count(distinct symbol) from sector_observation where source = 'yfinance_info') || ' of '
    || (select count(distinct symbol) from daily_fundamentals where date = (select max(date) from daily_fundamentals)) || ')' end;
select 'ASSERT|A|observation_sources_are_known (yfinance_info authoritative, tiingo_meta documented diagnostic)|' || case when not exists (select 1 from sector_observation where source not in ('yfinance_info', 'tiingo_meta')) then 'PASS'
    else 'REVIEW (unknown source: ' || (select string_agg(distinct source, ',') from sector_observation where source not in ('yfinance_info', 'tiingo_meta')) || ')' end;
select 'ASSERT|A|diagnostic_chains sources=' || coalesce(string_agg(source || ':' || n, ' ' order by source), 'none') || '|INFO' from (select source, count(*) n from sector_observation where source <> 'yfinance_info' group by 1) x;
select 'ASSERT|A|genuine_successful_authoritative_polls (state sector or no_sector)|' || case when exists (select 1 from sector_poll where source = 'yfinance_info' and response_state in ('sector', 'no_sector')) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|failed_or_invalid_authoritative_polls_share_is_small|' || case when (select count(*) from sector_poll where source = 'yfinance_info') = 0 then 'FAIL'
    when (select count(*) filter (where response_state in ('request_failed', 'invalid_response'))::numeric / count(*) from sector_poll where source = 'yfinance_info') <= 0.05 then 'PASS'
    else 'REVIEW (' || (select count(*) filter (where response_state in ('request_failed', 'invalid_response')) from sector_poll where source = 'yfinance_info') || ' of ' || (select count(*) from sector_poll where source = 'yfinance_info') || ')' end;
select 'ASSERT|A|every_stored_value_hash_equals_the_database_recomputed_hash|' || case when (select count(*) from sector_observation) = 0 then 'FAIL (nothing to verify)' when not exists (
    select 1 from sector_observation where value_hash <> research_sector_row_hash(symbol, source, seq, sector, no_sector_reason, sector_raw, captured_at, provenance, raw_payload_hash, prev_value_hash)) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|hash_chains_contiguous_and_linked_per_symbol_and_source|' || case when (select count(*) from sector_observation) = 0 then 'FAIL (no chain)' when not exists (
    select 1 from (select symbol, source, seq, prev_value_hash, lag(value_hash) over (partition by symbol, source order by seq) as exp_prev, lag(seq) over (partition by symbol, source order by seq) as prev_seq from sector_observation) c
    where (seq = 1 and prev_value_hash is not null) or (seq > 1 and (prev_value_hash is distinct from exp_prev or prev_seq <> seq - 1))) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|chains_start_at_seq_1|' || case when not exists (select 1 from (select symbol, source, min(seq) m from sector_observation group by 1, 2) x where m <> 1) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|no_duplicate_observations_or_repeated_consecutive_sector|' || case when not exists (select 1 from (select sector, lag(sector) over (partition by symbol, source order by seq) as prev_sector, seq from sector_observation) q where seq > 1 and sector is not distinct from prev_sector)
    and not exists (select 1 from sector_observation group by symbol, source, seq having count(*) > 1) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|every_observation_and_poll_symbol_is_a_symbol_of_the_price_universe (canonical, slash symbols such as BRK/A included)|' || case when not exists (
    select 1 from (select symbol from sector_observation union select symbol from sector_poll) q where not exists (select 1 from stock_prices p where p.symbol = q.symbol)) then 'PASS'
    else 'FAIL (not in the price universe: ' || (select string_agg(symbol, ',') from (select symbol from sector_observation union select symbol from sector_poll) q where not exists (select 1 from stock_prices p where p.symbol = q.symbol)) || ')' end;
select 'ASSERT|A|no_vendor_request_form_stored_as_a_symbol (BRK-B, BF-B, BRK-A)|' || case when not exists (select 1 from sector_observation where symbol in ('BRK-B', 'BF-B', 'BRK-A'))
    and not exists (select 1 from sector_poll where symbol in ('BRK-B', 'BF-B', 'BRK-A')) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|share_classes_present_under_their_canonical_symbol (BRK.B, BF.B, BRK/A)|' || case when (select count(*) from sector_observation where source = 'yfinance_info' and symbol in ('BRK.B', 'BF.B', 'BRK/A')) = 3 then 'PASS'
    else 'REVIEW (found ' || (select count(*) from sector_observation where source = 'yfinance_info' and symbol in ('BRK.B', 'BF.B', 'BRK/A')) || ' of 3)' end;
select 'ASSERT|A|no_retroactive_observation (none before the recorder flag time)|' || case when not exists (select 1 from sector_observation where captured_at < timestamptz '{{FLAG_TIME}}') then 'PASS' else 'FAIL' end;
select 'ASSERT|A|observations_written_during_this_pipeline_night|' || case when not exists (select 1 from sector_observation where captured_at < timestamptz '{{PIPELINE_START}}') then 'PASS'
    else 'REVIEW (' || (select count(*) from sector_observation where captured_at < timestamptz '{{PIPELINE_START}}') || ' observations are older than this night)' end;
select 'ASSERT|A|observation_effective_session_equals_the_utc_date_of_captured_at|' || case when not exists (select 1 from sector_observation where effective_session <> (captured_at at time zone 'UTC')::date) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|poll_to_observation_links_are_consistent|' || case when not exists (select 1 from sector_poll p left join sector_observation o on o.id = p.observation_id where p.observation_id is not null and (o.id is null or o.symbol <> p.symbol or o.source <> p.source)) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|no_duplicate_polls_per_run_symbol_source|' || case when not exists (select 1 from sector_poll group by run_id, symbol, source having count(*) > 1) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|no_sector_reconstruction_rows_no_backfill|' || case when (select count(*) from sector_reconstruction) = 0 then 'PASS' else 'FAIL' end;
-- ===================================================================== candidate capture (boundary {{BOUNDARY}}, strategy {{STRATEGY_ID}})
select 'ASSERT|A|capture_boundary_row_unchanged|' || case when exists (select 1 from research_capture_activation where strategy_id = {{STRATEGY_ID}} and state = 'enabled' and effective_from_session = date '{{BOUNDARY}}') then 'PASS' else 'FAIL' end;
select 'ASSERT|A|capture_run_complete_for_enabled_strategy_and_session|' || case when exists (select 1 from candidate_capture_run where strategy_id = {{STRATEGY_ID}} and session_date = date '{{SESSION}}' and status = 'complete' and run_finished_at is not null and error is null)
    then 'PASS' else 'FAIL (' || coalesce((select string_agg(status || ':' || coalesce(error, ''), ',') from candidate_capture_run where strategy_id = {{STRATEGY_ID}} and session_date = date '{{SESSION}}'), 'no run for the session') || ')' end;
select 'ASSERT|A|capture_run_row=' || coalesce((select id || ':' || status || ' candidates=' || candidates || ' captured=' || captured || ' already=' || already_captured || ' stale=' || stale_skipped || ' guard_rejected=' || guard_rejected
    || ' hash_drift=' || hash_drift || ' snapshot_drift=' || snapshot_drift from candidate_capture_run where strategy_id = {{STRATEGY_ID}} and session_date = date '{{SESSION}}' order by id desc limit 1), 'none') || '|INFO';
select 'ASSERT|A|no_capture_run_observation_or_snapshot_before_the_boundary|' || case when not exists (select 1 from candidate_capture_run where session_date < date '{{BOUNDARY}}') and not exists (select 1 from candidate_observation where session_date < date '{{BOUNDARY}}')
    and not exists (select 1 from feature_snapshot where session_date < date '{{BOUNDARY}}') then 'PASS' else 'FAIL' end;
select 'ASSERT|A|only_the_enabled_strategy_and_session_were_captured|' || case when not exists (select 1 from candidate_observation where session_date >= date '{{BOUNDARY}}' and (strategy_id <> {{STRATEGY_ID}} or session_date <> date '{{SESSION}}')) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|observation_rows_equal_the_run_captured_count|' || case when exists (select 1 from candidate_capture_run r where r.strategy_id = {{STRATEGY_ID}} and r.session_date = date '{{SESSION}}' and r.status = 'complete'
    and r.captured = (select count(*) from candidate_observation o where o.capture_run_id = r.id)) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|every_observation_links_to_a_snapshot_of_the_same_symbol_and_session|' || case when (select count(*) from candidate_observation where session_date = date '{{SESSION}}') = 0 then 'REVIEW (no candidate observations)'
    when not exists (select 1 from candidate_observation o left join feature_snapshot s on s.id = o.snapshot_id where o.session_date = date '{{SESSION}}' and (s.id is null or s.symbol <> o.symbol or s.session_date <> o.session_date)) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|every_observation_links_to_the_capture_run_of_its_session_and_strategy|' || case when not exists (select 1 from candidate_observation o left join candidate_capture_run r on r.id = o.capture_run_id
    where o.session_date = date '{{SESSION}}' and (r.id is null or r.session_date <> o.session_date or r.strategy_id <> o.strategy_id)) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|capture_times_are_database_stamped_after_the_session_close|' || case when (select count(*) from candidate_observation where session_date = date '{{SESSION}}') = 0 then 'REVIEW'
    when not exists (select 1 from candidate_observation where session_date = date '{{SESSION}}' and captured_at <= ((date '{{SESSION}}'::timestamp + interval '16 hours') at time zone 'America/New_York')) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|capture_run_has_no_drift_or_skips|' || case when exists (select 1 from candidate_capture_run where strategy_id = {{STRATEGY_ID}} and session_date = date '{{SESSION}}' and coalesce(hash_drift, 0) = 0 and coalesce(snapshot_drift, 0) = 0
    and coalesce(snapshot_skipped, 0) = 0 and coalesce(invalid_skipped, 0) = 0) then 'PASS' else 'REVIEW' end;
-- candidate guard semantics: the capture records the WHOLE pre-guard funnel as research evidence; only guard-passing, eligible breakouts are signals
select 'ASSERT|A|funnel session=' || count(*) || ' breakouts=' || count(*) filter (where triggered) || ' near_misses=' || count(*) filter (where not triggered) || ' guard_passed=' || count(*) filter (where passed_guard is true)
    || ' guard_rejected=' || count(*) filter (where passed_guard is false) || ' guard_not_evaluated=' || count(*) filter (where passed_guard is null) || ' tracked_intent=' || count(*) filter (where tracked_intent) || '|INFO'
  from candidate_observation where session_date = date '{{SESSION}}';
select 'ASSERT|A|guard_rejected_candidates_are_kept_as_evidence_but_never_signals|' || case when not exists (select 1 from candidate_observation o where o.session_date = date '{{SESSION}}' and o.passed_guard is false
    and (o.tracked_intent or o.guard_reasons is null or exists (select 1 from signal_ledger l where l.observation_id = o.id))) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|near_misses_are_never_tracked_signals|' || case when not exists (select 1 from candidate_observation o where o.session_date = date '{{SESSION}}' and not o.triggered and (o.tracked_intent or exists (select 1 from signal_ledger l where l.observation_id = o.id))) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|every_tracked_intent_is_a_new_ledger_row_or_an_already_open_position (position invariant)|' || case when not exists (
    select 1 from candidate_observation o where o.session_date = date '{{SESSION}}' and o.tracked_intent and not exists (select 1 from signal_ledger l where l.observation_id = o.id)
    and not exists (select 1 from signal_ledger l where l.symbol = o.symbol and l.direction = o.direction and l.strategy_id = o.strategy_id and l.signal_date < o.session_date)) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|ledger_rows_for_session=' || count(*) || ' with_observation_id=' || count(*) filter (where observation_id is not null) || ' with_snapshot_id=' || count(*) filter (where feature_snapshot_id is not null) || '|'
    || case when count(*) = 0 then 'REVIEW' when count(*) filter (where observation_id is not null) = count(*) then 'PASS' else 'FAIL' end from signal_ledger where signal_date = date '{{SESSION}}';
select 'ASSERT|A|no_duplicate_ledger_rows|' || case when not exists (select 1 from signal_ledger group by symbol, direction, strategy_id, signal_date having count(*) > 1) then 'PASS' else 'FAIL' end;
-- ===================================================================== guards, grants, immutability
select 'ASSERT|A|scan_table_immutable_triggers_enable_always|' || case when (select count(*) from pg_trigger where tgrelid = 'price_discontinuity_scan'::regclass and not tgisinternal and tgenabled = 'A') = 3 then 'PASS' else 'FAIL' end;
select 'ASSERT|A|every_research_trigger_is_enable_always|' || case when (select count(*) from pg_trigger t join pg_class c on c.oid = t.tgrelid join pg_proc p on p.oid = t.tgfoid where not t.tgisinternal and c.relnamespace = 'public'::regnamespace
    and p.proname like 'research\_%' and t.tgenabled <> 'A') = 0 then 'PASS' else 'FAIL' end;
select 'ASSERT|A|runtime_role_privileges_on_scan_and_sector_tables_select_insert_only|' || case when to_regrole('donchian_app') is null then 'REVIEW (role donchian_app not found in this database)' when not exists (select 1 from unnest(array['price_discontinuity_scan', 'sector_observation', 'sector_poll', 'sector_reconstruction']) t
    where not has_table_privilege('donchian_app', t, 'SELECT') or not has_table_privilege('donchian_app', t, 'INSERT') or has_table_privilege('donchian_app', t, 'UPDATE') or has_table_privilege('donchian_app', t, 'DELETE')
    or has_table_privilege('donchian_app', t, 'TRUNCATE')) then 'PASS' else 'FAIL' end;
select 'ASSERT|A|sector_helper_grants_runtime_only (app yes, public no, admin group no)|' || case when to_regrole('donchian_app') is null or to_regrole('donchian_research_admin') is null then 'REVIEW (role not found in this database)' when has_function_privilege('donchian_app', 'research_sector_enc(text)', 'EXECUTE')
    and has_function_privilege('donchian_app', 'research_sector_row_hash(text,text,integer,text,text,text,timestamptz,text,text,text)', 'EXECUTE') and not has_function_privilege('public', 'research_sector_enc(text)', 'EXECUTE')
    and not has_function_privilege('public', 'research_sector_row_hash(text,text,integer,text,text,text,timestamptz,text,text,text)', 'EXECUTE') and not has_function_privilege('donchian_research_admin', 'research_sector_enc(text)', 'EXECUTE')
    and not has_function_privilege('donchian_research_admin', 'research_sector_row_hash(text,text,integer,text,text,text,timestamptz,text,text,text)', 'EXECUTE') then 'PASS' else 'FAIL' end;
