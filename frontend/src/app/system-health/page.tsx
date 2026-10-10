'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Card,
  CardBody,
  CardHeader,
  Chip,
  Divider,
  Button,
} from '@nextui-org/react';
import { systemHealthApi, mlStatsApi, SystemHealthReport, MLStatsReport, HealthSection, HealthCheck, HealthStatus } from '@/services/api';
import MLStatsPanel from '@/components/health/MLStatsPanel';
import { usePagePerf } from '@/hooks/usePagePerf';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import SegmentedControl from '@/components/ui/SegmentedControl';

type ViewMode = 'overview' | 'basic' | 'ml';

const VIEWS: { key: ViewMode; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'basic', label: 'Basic System Health' },
  { key: 'ml', label: 'ML Health & Accuracy' },
];

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

function statusDot(status: HealthStatus): string {
  switch (status) {
    case 'healthy':
      return 'bg-emerald-500';
    case 'warning':
      return 'bg-amber-500';
    case 'critical':
      return 'bg-red-500';
    default:
      return 'bg-slate-400';
  }
}

function StatusBadge({ status }: { status: HealthStatus }) {
  return (
    <Chip size="sm" className={`text-xs font-semibold uppercase ${statusColor(status)}`}>
      {status}
    </Chip>
  );
}

function CheckRow({ check }: { check: HealthCheck }) {
  return (
    <div className="flex items-start justify-between py-2.5 px-1 border-b border-slate-100 last:border-0">
      <div className="flex items-start gap-2.5">
        <span className={`mt-1.5 h-2 w-2 rounded-full shrink-0 ${statusDot(check.status)}`} />
        <div>
          <p className="text-sm font-medium text-slate-700">{check.label}</p>
          {check.detail && <p className="text-xs text-slate-500 mt-0.5">{check.detail}</p>}
          {check.error && <p className="text-xs text-red-500 mt-0.5">Error: {check.error}</p>}
        </div>
      </div>
      <StatusBadge status={check.status} />
    </div>
  );
}

function SectionCard({ title, subtitle, section }: { title: string; subtitle: string; section: HealthSection }) {
  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardHeader className="flex items-center justify-between p-4 pb-2">
        <div>
          <h3 className="text-[15px] font-semibold text-slate-900">{title}</h3>
          <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>
        </div>
        <StatusBadge status={section.overall} />
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-4 pt-1">
        {section.checks.map((c) => (
          <CheckRow key={c.label} check={c} />
        ))}
      </CardBody>
    </Card>
  );
}

function CoverageCard({ coverage }: { coverage: NonNullable<SystemHealthReport['universe_coverage']> }) {
  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardHeader className="flex items-center justify-between p-4 pb-2">
        <div>
          <h3 className="text-[15px] font-semibold text-slate-900">Universe Coverage</h3>
          <p className="text-xs text-slate-500 mt-0.5">Russell 3000 expansion progress</p>
        </div>
        <StatusBadge status={coverage.overall} />
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-4 pt-3 space-y-3">
        <div className="grid grid-cols-2 gap-3 text-center">
          <div className="bg-slate-50 rounded-lg p-2.5">
            <p className="text-lg font-bold text-slate-800">{coverage.total_symbols?.toLocaleString() ?? '—'}</p>
            <p className="text-[11px] text-slate-500">Total symbols</p>
          </div>
          <div className="bg-slate-50 rounded-lg p-2.5">
            <p className="text-lg font-bold text-slate-800">{coverage.active_symbols?.toLocaleString() ?? '—'}</p>
            <p className="text-[11px] text-slate-500">Active symbols</p>
          </div>
        </div>
        <div>
          <div className="flex justify-between text-xs text-slate-500 mb-1">
            <span>Quarterly fundamentals coverage</span>
            <span className="font-semibold text-slate-700">{coverage.quarterly_fundamentals_coverage_pct?.toFixed(1)}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
            <div className="h-full bg-accent" style={{ width: `${coverage.quarterly_fundamentals_coverage_pct ?? 0}%` }} />
          </div>
        </div>
        <div>
          <div className="flex justify-between text-xs text-slate-500 mb-1">
            <span>10-year price history coverage</span>
            <span className="font-semibold text-slate-700">{coverage.ten_year_history_coverage_pct?.toFixed(1)}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
            <div className="h-full bg-indigo-500" style={{ width: `${coverage.ten_year_history_coverage_pct ?? 0}%` }} />
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function SectorBreakdownCard({ sectors }: { sectors: NonNullable<SystemHealthReport['universe_coverage']['sector_breakdown']> }) {
  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardHeader className="p-4 pb-2">
        <h3 className="text-[15px] font-semibold text-slate-900">Sector Breakdown</h3>
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-4 pt-3">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {sectors.map((s) => (
            <div key={s.sector} className="flex justify-between text-xs bg-slate-50 rounded px-2.5 py-1.5">
              <span className="text-slate-600">{s.sector}</span>
              <span className="font-semibold text-slate-800">{s.n}</span>
            </div>
          ))}
        </div>
      </CardBody>
    </Card>
  );
}

export default function SystemHealthPage() {
  const { markLoaded } = usePagePerf('/system-health');
  const [view, setView] = useState<ViewMode>('overview');

  const [report, setReport] = useState<SystemHealthReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [mlReport, setMlReport] = useState<MLStatsReport | null>(null);
  const [mlLoading, setMlLoading] = useState(true);
  const [mlError, setMlError] = useState<string | null>(null);

  const fetchReport = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await systemHealthApi.getReport();
      setReport(data);
      markLoaded();
    } catch {
      setError('Failed to load system health report — is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [markLoaded]);

  const fetchMlReport = useCallback(async () => {
    try {
      setMlLoading(true);
      setMlError(null);
      const data = await mlStatsApi.getReport();
      setMlReport(data);
    } catch {
      setMlError('Failed to load ML stats — is the backend running?');
    } finally {
      setMlLoading(false);
    }
  }, []);

  const refreshAll = useCallback(() => {
    fetchReport();
    fetchMlReport();
  }, [fetchReport, fetchMlReport]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  const coverage = report?.universe_coverage;
  const sectors = coverage?.sector_breakdown;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-5xl space-y-4">
        <div className="px-1 pt-2 pb-1">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="large-title">System Health</h1>
              <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
                Production-readiness across pipeline freshness, data quality, ML model health, and universe
                coverage — four different failure modes a single metric would miss.
              </p>
              {report && (
                <p className="text-xs text-slate-400 mt-2">
                  Last checked {new Date(report.generated_at).toLocaleTimeString()}
                </p>
              )}
            </div>
            <div className="flex flex-col items-end gap-2 shrink-0">
              {report && view === 'overview' && <StatusBadge status={report.overall} />}
              <Button size="sm" className="rounded-full bg-slate-900 px-4 font-medium text-white" onClick={refreshAll} isLoading={loading || mlLoading}>
                Refresh
              </Button>
            </div>
          </div>

          <div className="mt-5">
            <SegmentedControl
              aria-label="Health view"
              size="md"
              options={VIEWS.map((v) => ({ value: v.key, label: v.label }))}
              value={view}
              onChange={setView}
            />
          </div>
        </div>

        {/* Overview: everything, exactly as the combined report reads */}
        {view === 'overview' && (
          loading && !report ? (
            <div className="flex justify-center py-16">
              <LoadingSpinner />
            </div>
          ) : error ? (
            <ErrorAlert title="Couldn’t load the system health report" message={error} onRetry={fetchReport} className="my-4" />
          ) : report ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <SectionCard
                  title="Pipeline Freshness"
                  subtitle="Is daily/weekly/monthly data actually current?"
                  section={report.pipeline_freshness}
                />
                <SectionCard
                  title="Data Quality"
                  subtitle="Missing values, price anomalies, fundamentals coverage"
                  section={report.data_quality}
                />
                <SectionCard
                  title="ML Model Health"
                  subtitle="Model staleness, prediction activity, outcome evaluation"
                  section={report.ml_health}
                />
                {coverage && <CoverageCard coverage={coverage} />}
              </div>
              {sectors && sectors.length > 0 && <SectorBreakdownCard sectors={sectors} />}
            </>
          ) : null
        )}

        {/* Basic System Health: operational data pipeline only, no ML */}
        {view === 'basic' && (
          loading && !report ? (
            <div className="flex justify-center py-16">
              <LoadingSpinner />
            </div>
          ) : error ? (
            <ErrorAlert title="Couldn’t load the system health report" message={error} onRetry={fetchReport} className="my-4" />
          ) : report ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <SectionCard
                  title="Pipeline Freshness"
                  subtitle="Is daily/weekly/monthly data actually current?"
                  section={report.pipeline_freshness}
                />
                <SectionCard
                  title="Data Quality"
                  subtitle="Missing values, price anomalies, fundamentals coverage"
                  section={report.data_quality}
                />
                {coverage && <CoverageCard coverage={coverage} />}
              </div>
              {sectors && sectors.length > 0 && <SectorBreakdownCard sectors={sectors} />}
            </>
          ) : null
        )}

        {/* ML Health & Accuracy: operational ML checks + full model/accuracy detail */}
        {view === 'ml' && (
          <>
            {loading && !report ? (
              <div className="flex justify-center py-8">
                <LoadingSpinner />
              </div>
            ) : error ? (
              <ErrorAlert title="Couldn’t load the system health report" message={error} onRetry={fetchReport} className="my-4" />
            ) : report ? (
              <SectionCard
                title="ML Model Health"
                subtitle="Model staleness, prediction activity, outcome evaluation"
                section={report.ml_health}
              />
            ) : null}

            {mlLoading && !mlReport ? (
              <div className="flex justify-center py-16">
                <LoadingSpinner />
              </div>
            ) : mlError ? (
              <ErrorAlert title="Couldn’t load ML health" message={mlError} onRetry={fetchMlReport} className="my-4" />
            ) : mlReport ? (
              <MLStatsPanel report={mlReport} />
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
