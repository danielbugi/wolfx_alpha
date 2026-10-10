-- VERDICT B -- COLLECTOR READINESS for one session. READ-ONLY. Independent of Verdict A: a stale scan can never turn a forward-research result into a failure.
-- Placeholders: {{SESSION}}; {{PRICE_WRITER_TZ}} the ONE explicit timezone in which the price writers stamp stock_prices.created_at/updated_at (they are `timestamp` columns with no zone;
-- production's updater session is UTC, verified against its own log line); the scan's finished_at is a timestamptz, so the comparison below converts the naive price stamp
-- with that explicit zone and never relies on the session TimeZone of the connection.
\pset tuples_only on
\pset format unaligned
-- the two fingerprints are checked INDEPENDENTLY
select 'ASSERT|B|price_fingerprint_fresh (a complete scan matches the prices as stored now)|' || case when exists (
    select 1 from price_discontinuity_scan s, research_price_input_fingerprint(date '{{SESSION}}') f
    where s.session_date = date '{{SESSION}}' and s.status = 'complete' and s.input_fingerprint = f.fingerprint and s.n_price_rows = f.n_price_rows and s.n_symbols = f.n_symbols) then 'PASS'
    else 'FAIL (the prices changed after every complete scan; the strict check would refuse to create a snapshot from this evidence)' end;
select 'ASSERT|B|result_fingerprint_fresh (a complete scan matches the discontinuity table as stored now)|' || case when exists (
    select 1 from price_discontinuity_scan s, research_discontinuity_result_fingerprint() r
    where s.session_date = date '{{SESSION}}' and s.status = 'complete' and s.result_fingerprint = r.fingerprint and s.n_discontinuities = r.n_rows) then 'PASS' else 'FAIL' end;
select 'ASSERT|B|one_complete_scan_matches_both_fingerprints_and_finished_before_the_cutoff|' || case when exists (
    select 1 from price_discontinuity_scan s, research_price_input_fingerprint(date '{{SESSION}}') f, research_discontinuity_result_fingerprint() r
    where s.session_date = date '{{SESSION}}' and s.status = 'complete' and s.input_fingerprint = f.fingerprint and s.result_fingerprint = r.fingerprint
      and s.finished_at <= (((date '{{SESSION}}' + 1)::timestamp + interval '12 hours') at time zone 'America/New_York')) then 'PASS' else 'FAIL' end;
-- late ingestion after the scan completed, in ONE explicit timezone
select 'ASSERT|B|price_rows_created_or_changed_after_the_first_complete_scan=' || count(*) || ' symbols=' || coalesce(string_agg(distinct p.symbol, ',' order by p.symbol), 'none') || '|' || case when count(*) = 0 then 'PASS' else 'FAIL (late ingestion)' end
  from stock_prices p, (select min(finished_at) f from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete') s
  where p.date <= date '{{SESSION}}' and s.f is not null
    and ((p.created_at at time zone '{{PRICE_WRITER_TZ}}') > s.f or (p.updated_at at time zone '{{PRICE_WRITER_TZ}}') > s.f);
select 'ASSERT|B|a_complete_scan_finished_after_the_last_price_write (the evidence covers the final price state)|' || case when (
    select max(finished_at) from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete')
    >= (select max(greatest(p.created_at, p.updated_at) at time zone '{{PRICE_WRITER_TZ}}') from stock_prices p where p.date <= date '{{SESSION}}') then 'PASS' else 'FAIL' end;
select 'ASSERT|B|price_rows_in_scan_vs_now: scan=' || coalesce((select max(n_price_rows)::text from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete'), 'none')
    || ' live=' || (select count(*) from stock_prices where date <= date '{{SESSION}}') || '|INFO';
select 'ASSERT|B|latest_price_write_utc=' || coalesce((select max(greatest(p.created_at, p.updated_at) at time zone '{{PRICE_WRITER_TZ}}' at time zone 'UTC')::text from stock_prices p where p.date <= date '{{SESSION}}'), 'none')
    || ' first_complete_scan_utc=' || coalesce((select (min(finished_at) at time zone 'UTC')::text from price_discontinuity_scan where session_date = date '{{SESSION}}' and status = 'complete'), 'none') || '|INFO';
