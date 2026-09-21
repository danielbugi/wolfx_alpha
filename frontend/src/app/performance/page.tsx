'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Card, CardBody, CardHeader, Chip, Divider, Spinner, Button } from '@nextui-org/react';
import {
  performanceApi,
  PerformanceReport,
  EndpointPerfStat,
  RoutePerfStat,
  HealthStatus,
} from '@/services/api';

function statusColor(status: HealthStatus): string {
  switch (status) {
    case 'healthy':
      return 'bg-emerald-100 text-emerald-800';
    case 'warning':
      return 'bg-amber-100 text-amber-800';
    case 'critical':
      return 'bg-red-100 text-red-700';
    default:
      return 'bg-slate-100 text-slate-600';
  }
}

function StatusBadge({ status }: { status: HealthStatus }) {
  return (
    <Chip size="sm" className={`text-xs font-semibold uppercase ${statusColor(status)}`}>
      {status}
    </Chip>
  );
}

function ms(value: number | null | undefined): string {
  if (value == null) return '—';
  if (value >= 1000) return `${(value / 1000).toFixed(2)}s`;
  return `${value.toFixed(0)}ms`;
}

function EndpointTable({ rows, title, subtitle, emptyText }: {
  rows: { key: string; label: string; sub?: string; status: HealthStatus; avg_ms: number | null; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; max_ms: number | null; samples: number; errorRatePct?: number }[];
  title: string;
  subtitle: string;
  emptyText: string;
}) {
  return (
    <Card className="shadow-sm border border-slate-200">
      <CardHeader className="p-4 pb-2">
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-0">
        {rows.length === 0 ? (
          <p className="text-sm text-slate-400 p-4">{emptyText}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-50 text-slate-500 text-left">
                  <th className="px-3 py-2 font-medium">Route / Endpoint</th>
                  <th className="px-3 py-2 font-medium text-right">Samples</th>
                  <th className="px-3 py-2 font-medium text-right">Avg</th>
                  <th className="px-3 py-2 font-medium text-right">P50</th>
                  <th className="px-3 py-2 font-medium text-right">P95</th>
                  <th className="px-3 py-2 font-medium text-right">P99</th>
                  <th className="px-3 py-2 font-medium text-right">Max</th>
                  {rows.some((r) => r.errorRatePct != null) && (
                    <th className="px-3 py-2 font-medium text-right">Errors</th>
                  )}
                  <th className="px-3 py-2 font-medium text-right">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} className="border-t border-slate-100">
                    <td className="px-3 py-2">
                      <span className="font-mono text-slate-700">{r.label}</span>
                      {r.sub && <span className="ml-2 text-slate-400">{r.sub}</span>}
                    </td>
                    <td className="px-3 py-2 text-right text-slate-500 tabular-nums">{r.samples}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">{ms(r.avg_ms)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-700">{ms(r.p50_ms)}</td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold text-slate-800">{ms(r.p95_ms)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-600">{ms(r.p99_ms)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-slate-500">{ms(r.max_ms)}</td>
                    {rows.some((row) => row.errorRatePct != null) && (
                      <td className="px-3 py-2 text-right tabular-nums text-slate-500">
                        {r.errorRatePct != null ? `${r.errorRatePct}%` : '—'}
                      </td>
                    )}
                    <td className="px-3 py-2 text-right">
                      <StatusBadge status={r.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function DatabaseCard({ db }: { db: PerformanceReport['database'] }) {
  return (
    <Card className="shadow-sm border border-slate-200">
      <CardHeader className="p-4 pb-2">
        <h3 className="text-sm font-semibold text-slate-800">Database</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Connection cost isolated from query cost — every request in this backend pays the first
          number, since {db.pooling_active ? 'a connection pool is active' : 'there is currently no connection pooling'}.
        </p>
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-4 pt-3 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div className="bg-slate-50 rounded-lg p-3">
            <div className="flex items-center justify-between mb-1">
              <p className="text-xs text-slate-500">Connection acquire</p>
              <StatusBadge status={db.connection_status} />
            </div>
            <p className="text-xl font-bold text-slate-800">{ms(db.connection_acquire_ms)}</p>
            <p className="text-[10px] text-slate-400 mt-0.5">TCP + auth handshake, per connection</p>
          </div>
          <div className="bg-slate-50 rounded-lg p-3">
            <div className="flex items-center justify-between mb-1">
              <p className="text-xs text-slate-500">Sample query (total)</p>
              <StatusBadge status={db.sample_query_status} />
            </div>
            <p className="text-xl font-bold text-slate-800">{ms(db.sample_query_total_ms)}</p>
            <p className="text-[10px] text-slate-400 mt-0.5 truncate" title={db.sample_query_label}>
              {db.sample_query_label ?? '—'}
            </p>
          </div>
        </div>
        {db.pure_query_ms != null && db.connection_overhead_pct != null && (
          <div className="bg-indigo-50 rounded-lg p-3 flex items-center justify-between">
            <div>
              <p className="text-xs text-slate-600">
                Pure query time: <span className="font-semibold">{ms(db.pure_query_ms)}</span>
              </p>
              <p className="text-[10px] text-slate-500 mt-0.5">
                Connection overhead is {db.connection_overhead_pct}% of this sample&apos;s total time
                {!db.pooling_active && ' — a pool would remove nearly all of it'}.
              </p>
            </div>
          </div>
        )}
        {(db.connection_error || db.sample_query_error) && (
          <p className="text-xs text-red-500">{db.connection_error || db.sample_query_error}</p>
        )}
      </CardBody>
    </Card>
  );
}

export default function PerformancePage() {
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchReport = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await performanceApi.getReport();
      setReport(data);
    } catch {
      setError('Failed to load performance report — is the backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchReport();
  }, [fetchReport]);

  const endpointRows = (report?.backend_endpoints ?? []).map((e: EndpointPerfStat) => ({
    key: `${e.method} ${e.endpoint}`,
    label: e.endpoint,
    sub: e.method,
    status: e.status,
    avg_ms: e.avg_ms,
    p50_ms: e.p50_ms,
    p95_ms: e.p95_ms,
    p99_ms: e.p99_ms,
    max_ms: e.max_ms,
    samples: e.sample_count,
    errorRatePct: e.error_rate_pct,
  }));

  const routeRowsByMetric = (metric: string) =>
    (report?.frontend_routes ?? [])
      .filter((r: RoutePerfStat) => r.metric === metric)
      .map((r) => ({
        key: `${metric}-${r.route}`,
        label: r.route,
        status: r.status,
        avg_ms: r.avg_ms,
        p50_ms: r.p50_ms,
        p95_ms: r.p95_ms,
        p99_ms: r.p99_ms,
        max_ms: r.max_ms,
        samples: r.sample_count,
      }));

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-6xl space-y-4">
        <div className="p-4 bg-white rounded-lg shadow-sm border border-slate-200 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Performance</h1>
            <p className="text-slate-600 text-sm mt-1">
              Scored, tracked latency across the database, every backend endpoint, and every
              frontend route — where the app is actually slow, with numbers instead of guesses.
            </p>
            {report && (
              <p className="text-xs text-slate-400 mt-2">
                Last checked {new Date(report.generated_at).toLocaleTimeString()} · backend up{' '}
                {Math.round(report.backend_uptime_seconds)}s
                <span className="ml-2 text-slate-300">
                  (in-memory — resets on backend restart)
                </span>
              </p>
            )}
          </div>
          <div className="flex flex-col items-end gap-2 shrink-0">
            {report && <StatusBadge status={report.overall} />}
            <Button size="sm" className="bg-slate-700 text-white" onClick={fetchReport} isLoading={loading}>
              Refresh
            </Button>
          </div>
        </div>

        {loading && !report ? (
          <div className="flex justify-center py-16">
            <Spinner color="primary" />
          </div>
        ) : error ? (
          <p className="text-center text-red-500 text-sm py-16">{error}</p>
        ) : report ? (
          <>
            <DatabaseCard db={report.database} />

            <EndpointTable
              title="Backend Endpoints"
              subtitle="Real request latency, grouped by route template (e.g. /api/stock/{symbol}), slowest first"
              emptyText="No requests recorded yet — browse the app to generate traffic, then refresh."
              rows={endpointRows}
            />

            <EndpointTable
              title="Frontend Routes — Time to Data Visible"
              subtitle="From page mount to its first successful data fetch resolving (first load only, not manual refreshes)"
              emptyText="No page-load samples yet — visit the dashboard, System Health, or ML Stats pages, then refresh."
              rows={routeRowsByMetric('data_load_ms')}
            />

            <EndpointTable
              title="Frontend Routes — Initial Page Load (browser Navigation Timing)"
              subtitle="TTFB through DOMContentLoaded for the first hard load of a route this session"
              emptyText="No hard-load samples yet — a full page refresh (not client navigation) is needed to record this."
              rows={routeRowsByMetric('initial_load_ms')}
            />
          </>
        ) : null}
      </div>
    </div>
  );
}
