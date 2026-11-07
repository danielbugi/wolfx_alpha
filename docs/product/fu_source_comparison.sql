-- docs/product/fu_source_comparison.sql -- READ-ONLY queries behind FUNDAMENTAL_UPDATES_SOURCE_COMPARISON.md (inflection_v1 vs inflection_v2_sec_pit).
-- Run in a forced read-only session:  PGOPTIONS='-c default_transaction_read_only=on' psql -d trading_production -f docs/product/fu_source_comparison.sql
-- Results recorded 2026-11-07 (production, read-only).

\echo === 1. coverage per version
SELECT method_version, count(*) events, count(DISTINCT symbol) companies, count(DISTINCT (symbol, quarter_end)) company_quarters, min(quarter_end), max(quarter_end),
       min(first_detected_at)::date first_run, max(first_detected_at)::date last_detect
  FROM fundamental_inflection_event GROUP BY 1;
\echo === feature rows (company-quarters computed) per version
SELECT method_version, count(*) rows, count(DISTINCT symbol) companies, min(quarter_end), max(quarter_end), max(computed_at)::date FROM fundamental_feature GROUP BY 1;
\echo === feature source per version
SELECT method_version, source, count(*) FROM fundamental_feature GROUP BY 1,2 ORDER BY 1,2;
\echo === events by quarter-end month and version
SELECT to_char(date_trunc('month', quarter_end), 'YYYY-MM') m, count(*) FILTER (WHERE method_version='inflection_v1') v1, count(*) FILTER (WHERE method_version='inflection_v2_sec_pit') v2
  FROM fundamental_inflection_event WHERE quarter_end >= '2025-01-01' GROUP BY 1 ORDER BY 1;
\echo === 2. event types per version
SELECT event_type, count(*) FILTER (WHERE method_version='inflection_v1') v1, count(*) FILTER (WHERE method_version='inflection_v2_sec_pit') v2 FROM fundamental_inflection_event GROUP BY 1 ORDER BY 1;
\echo === availability basis per version
SELECT method_version, availability_basis, count(*) FROM fundamental_inflection_event GROUP BY 1,2 ORDER BY 1,3 DESC;
\echo === 3. overlap, window: quarter ends 2026-03-01..2026-08-31 (both versions computed), excluding limited_repricing; match = same symbol + type, quarter end within 10 days
WITH v1 AS (SELECT * FROM fundamental_inflection_event WHERE method_version='inflection_v1' AND quarter_end BETWEEN '2026-03-01' AND '2026-08-31' AND event_type <> 'improvement_with_limited_repricing'),
     v2 AS (SELECT * FROM fundamental_inflection_event WHERE method_version='inflection_v2_sec_pit' AND quarter_end BETWEEN '2026-03-01' AND '2026-08-31' AND event_type <> 'improvement_with_limited_repricing'),
     m AS (SELECT v1.id id1, v2.id id2 FROM v1 JOIN v2 ON v1.symbol = v2.symbol AND v1.event_type = v2.event_type AND abs(v1.quarter_end - v2.quarter_end) <= 10)
SELECT (SELECT count(*) FROM v1) v1_events, (SELECT count(*) FROM v2) v2_events, (SELECT count(DISTINCT id1) FROM m) v1_matched, (SELECT count(DISTINCT id2) FROM m) v2_matched;

\echo === why unmatched v1 events have no v2 event (v2 feature row for that company-quarter?)
WITH v1 AS (SELECT * FROM fundamental_inflection_event WHERE method_version='inflection_v1' AND quarter_end BETWEEN '2026-03-01' AND '2026-08-31' AND event_type <> 'improvement_with_limited_repricing'),
     u AS (SELECT v1.* FROM v1 WHERE NOT EXISTS (SELECT 1 FROM fundamental_inflection_event v2 WHERE v2.method_version='inflection_v2_sec_pit' AND v2.symbol=v1.symbol AND v2.event_type=v1.event_type AND abs(v2.quarter_end-v1.quarter_end)<=10))
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM fundamental_feature f WHERE f.method_version='inflection_v2_sec_pit' AND f.symbol=u.symbol) THEN 'company not in v2 (no SEC data)'
            WHEN NOT EXISTS (SELECT 1 FROM fundamental_feature f WHERE f.method_version='inflection_v2_sec_pit' AND f.symbol=u.symbol AND abs(f.quarter_end-u.quarter_end)<=10) THEN 'quarter not computed in v2'
            WHEN EXISTS (SELECT 1 FROM fundamental_inflection_event e WHERE e.method_version='inflection_v2_sec_pit' AND e.symbol=u.symbol AND abs(e.quarter_end-u.quarter_end)<=10) THEN 'v2 has other event types for the quarter'
            ELSE 'v2 computed the quarter, no event (thresholds / different figures)' END reason, count(*)
  FROM u GROUP BY 1 ORDER BY 2 DESC;

\echo === why unmatched v2 events have no v1 event
WITH v2 AS (SELECT * FROM fundamental_inflection_event WHERE method_version='inflection_v2_sec_pit' AND quarter_end BETWEEN '2026-03-01' AND '2026-08-31' AND event_type <> 'improvement_with_limited_repricing'),
     u AS (SELECT v2.* FROM v2 WHERE NOT EXISTS (SELECT 1 FROM fundamental_inflection_event v1 WHERE v1.method_version='inflection_v1' AND v1.symbol=v2.symbol AND v1.event_type=v2.event_type AND abs(v1.quarter_end-v2.quarter_end)<=10))
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM fundamental_feature f WHERE f.method_version='inflection_v1' AND f.symbol=u.symbol) THEN 'company not in v1'
            WHEN NOT EXISTS (SELECT 1 FROM fundamental_feature f WHERE f.method_version='inflection_v1' AND f.symbol=u.symbol AND abs(f.quarter_end-u.quarter_end)<=10) THEN 'quarter not computed in v1'
            WHEN EXISTS (SELECT 1 FROM fundamental_inflection_event e WHERE e.method_version='inflection_v1' AND e.symbol=u.symbol AND abs(e.quarter_end-u.quarter_end)<=10) THEN 'v1 has other event types for the quarter'
            ELSE 'v1 computed the quarter, no event' END reason, count(*)
  FROM u GROUP BY 1 ORDER BY 2 DESC;

\echo === matched pairs by type
SELECT v1.event_type, count(*) pairs FROM fundamental_inflection_event v1 JOIN fundamental_inflection_event v2 ON v2.method_version='inflection_v2_sec_pit' AND v1.symbol=v2.symbol AND v1.event_type=v2.event_type AND abs(v1.quarter_end-v2.quarter_end)<=10
 WHERE v1.method_version='inflection_v1' AND v1.quarter_end BETWEEN '2026-03-01' AND '2026-08-31' GROUP BY 1 ORDER BY 1;
\echo === v2-only events where v1 computed the quarter: by type
WITH v2 AS (SELECT * FROM fundamental_inflection_event WHERE method_version='inflection_v2_sec_pit' AND quarter_end BETWEEN '2026-03-01' AND '2026-08-31' AND event_type <> 'improvement_with_limited_repricing')
SELECT event_type, count(*) FROM v2 WHERE NOT EXISTS (SELECT 1 FROM fundamental_inflection_event v1 WHERE v1.method_version='inflection_v1' AND v1.symbol=v2.symbol AND v1.event_type=v2.event_type AND abs(v1.quarter_end-v2.quarter_end)<=10)
  AND EXISTS (SELECT 1 FROM fundamental_feature f WHERE f.method_version='inflection_v1' AND f.symbol=v2.symbol AND abs(f.quarter_end-v2.quarter_end)<=10) GROUP BY 1 ORDER BY 2 DESC;
\echo === v1-only events where v2 computed the quarter: by type
WITH v1 AS (SELECT * FROM fundamental_inflection_event WHERE method_version='inflection_v1' AND quarter_end BETWEEN '2026-03-01' AND '2026-08-31' AND event_type <> 'improvement_with_limited_repricing')
SELECT event_type, count(*) FROM v1 WHERE NOT EXISTS (SELECT 1 FROM fundamental_inflection_event v2 WHERE v2.method_version='inflection_v2_sec_pit' AND v2.symbol=v1.symbol AND v2.event_type=v1.event_type AND abs(v1.quarter_end-v2.quarter_end)<=10)
  AND EXISTS (SELECT 1 FROM fundamental_feature f WHERE f.method_version='inflection_v2_sec_pit' AND f.symbol=v1.symbol AND abs(f.quarter_end-v1.quarter_end)<=10) GROUP BY 1 ORDER BY 2 DESC;
\echo === matched pairs: figure agreement (relative difference of the headline metric) and date gap v2 - v1
WITH p AS (SELECT v1.event_type, v1.metrics m1, v2.metrics m2, v2.available_at - v1.available_at gap, v1.availability_basis b1
             FROM fundamental_inflection_event v1 JOIN fundamental_inflection_event v2 ON v2.method_version='inflection_v2_sec_pit' AND v1.symbol=v2.symbol AND v1.event_type=v2.event_type AND abs(v1.quarter_end-v2.quarter_end)<=10
            WHERE v1.method_version='inflection_v1' AND v1.quarter_end BETWEEN '2026-03-01' AND '2026-08-31' AND v1.event_type <> 'improvement_with_limited_repricing'),
     k AS (SELECT event_type, gap, b1, CASE event_type WHEN 'margin_expansion' THEN 'op_margin' WHEN 'operating_leverage_inflection' THEN 'rev_yoy' WHEN 'profitability_turnaround' THEN 'net_income'
                  WHEN 'fcf_turnaround' THEN 'free_cash_flow' WHEN 'revenue_acceleration' THEN 'rev_yoy' END key, m1, m2 FROM p),
     d AS (SELECT event_type, gap, b1, (m1->>key)::float a, (m2->>key)::float b FROM k)
SELECT event_type, count(*) n,
       round(100.0*avg((abs(a-b) <= 0.01*greatest(abs(a),abs(b),1e-9))::int)::numeric,1) pct_within_1pct,
       round(100.0*avg((abs(a-b) <= 0.05*greatest(abs(a),abs(b),1e-9))::int)::numeric,1) pct_within_5pct,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) med_gap_days, percentile_cont(0.1) WITHIN GROUP (ORDER BY gap) p10_gap, percentile_cont(0.9) WITHIN GROUP (ORDER BY gap) p90_gap,
       count(*) FILTER (WHERE gap < 0) v2_earlier
  FROM d WHERE a IS NOT NULL AND b IS NOT NULL GROUP BY 1 ORDER BY 1;
\echo === date gap by v1 basis
WITH p AS (SELECT v2.available_at - v1.available_at gap, v1.availability_basis b1 FROM fundamental_inflection_event v1 JOIN fundamental_inflection_event v2 ON v2.method_version='inflection_v2_sec_pit' AND v1.symbol=v2.symbol AND v1.event_type=v2.event_type AND abs(v1.quarter_end-v2.quarter_end)<=10
            WHERE v1.method_version='inflection_v1' AND v1.quarter_end BETWEEN '2026-03-01' AND '2026-08-31')
SELECT b1, count(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) med, min(gap), max(gap), count(*) FILTER (WHERE gap < 0) v2_earlier, count(*) FILTER (WHERE gap > 30) v2_30d_later FROM p GROUP BY 1;
\echo === 4. point-in-time checks per version
SELECT method_version, count(*) events,
       count(*) FILTER (WHERE (first_detected_at AT TIME ZONE 'UTC')::date < available_at) detected_before_public_date,
       count(*) FILTER (WHERE available_at - quarter_end > 104) public_gt_104d_after_quarter,
       count(*) FILTER (WHERE available_at < quarter_end) public_before_quarter_end,
       count(*) FILTER (WHERE availability_basis LIKE 'assumed%' OR availability_basis LIKE '%assumed%' OR availability_basis = 'first_seen') unverified_date,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY available_at - quarter_end) med_days_to_public
  FROM fundamental_inflection_event GROUP BY 1;
\echo === v1 earnings_calendar events where the SEC date is >7 days EARLIER (v1 date suspect)
SELECT count(*) FROM fundamental_inflection_event v1 JOIN fundamental_inflection_event v2 ON v2.method_version='inflection_v2_sec_pit' AND v1.symbol=v2.symbol AND v1.event_type=v2.event_type AND abs(v1.quarter_end-v2.quarter_end)<=10
 WHERE v1.method_version='inflection_v1' AND v1.availability_basis='earnings_calendar' AND v2.available_at < v1.available_at - 7;
\echo === 5. live detection latency (events public on or after the first run of each version)
SELECT e.method_version, count(*) n,
       count(*) FILTER (WHERE (first_detected_at AT TIME ZONE 'UTC')::date >= available_at) fresh_detections,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (first_detected_at AT TIME ZONE 'UTC')::date - available_at) med_days, max((first_detected_at AT TIME ZONE 'UTC')::date - available_at) max_days
  FROM fundamental_inflection_event e JOIN (SELECT method_version, min(first_detected_at)::date start FROM fundamental_inflection_event GROUP BY 1) s USING (method_version)
 WHERE e.available_at >= s.start GROUP BY 1;
\echo === current season (quarter ends >= 2026-09-01)
SELECT method_version, count(*) events, count(DISTINCT symbol) companies FROM fundamental_inflection_event WHERE quarter_end >= '2026-09-01' GROUP BY 1;
SELECT method_version, count(*) feature_rows FROM fundamental_feature WHERE quarter_end >= '2026-09-01' GROUP BY 1;
\echo === 6. filing traceability: v2 feature rows with an accession for the quarter
SELECT count(*) features, count(*) FILTER (WHERE quality_notes::text ~ 'prov:q=\d{10}-\d{2}-\d{6}') with_accession FROM fundamental_feature WHERE method_version='inflection_v2_sec_pit';
SELECT count(*) events, count(*) FILTER (WHERE f.quality_notes::text ~ 'prov:q=\d{10}-\d{2}-\d{6}') events_with_accession
  FROM fundamental_inflection_event e LEFT JOIN fundamental_feature f ON f.method_version=e.method_version AND f.symbol=e.symbol AND f.quarter_end=e.quarter_end WHERE e.method_version='inflection_v2_sec_pit';
\echo === v1 source freshness
SELECT column_name FROM information_schema.columns WHERE table_name='quarterly_fundamentals' AND column_name ~ 'date|quarter|period|fetched|updated' ORDER BY 1;
SELECT method_version, symbol, quarter_end, available_at - quarter_end lag, availability_basis FROM fundamental_inflection_event WHERE method_version='inflection_v2_sec_pit' AND available_at - quarter_end > 104 ORDER BY lag DESC;
SELECT max(quarter) latest_quarter, max(updated_at) last_update, count(DISTINCT symbol) FILTER (WHERE quarter >= '2026-09-01') symbols_with_sep_quarter FROM quarterly_fundamentals;
