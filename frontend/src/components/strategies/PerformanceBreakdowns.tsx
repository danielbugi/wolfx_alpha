// File: frontend/src/components/strategies/PerformanceBreakdowns.tsx
// Strategy-neutral performance breakdowns, served by the generic API (GET /api/strategies/{key}/{version}/performance and
// .../performance/breakdowns). Nothing here is computed: every value, N and state comes from mechanism/strategy_analytics.
// Missing information is shown as missing -- a metric or dimension in state 'not_available' / 'no_data' renders "—" with
// its reason, never 0, and what the ledger cannot support yet is listed with what it needs instead of being hidden.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { strategyApi, toApiError } from '@/services/strategyApi';
import type { Metric, PerfDimension, PerfGroup, PerformanceBreakdowns, PerformanceOverview } from '@/services/strategyApi';
import type { MetricKind } from '@/lib/strategyFormat';
import { describeMetric, formatCount } from '@/lib/strategyFormat';
import ErrorAlert from '@/components/common/ErrorAlert';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { EmptyState, Section } from '@/components/strategies/ui';

const COLUMNS: Array<{ key: keyof PerfGroup; label: string; kind: MetricKind; signed?: boolean }> = [
  { key: 'win_rate', label: 'Win rate', kind: 'rate' },
  { key: 'average_r', label: 'Avg R', kind: 'r', signed: true },
  { key: 'median_r', label: 'Median R', kind: 'r', signed: true },
  { key: 'sum_r', label: 'Sum R', kind: 'r', signed: true },
  { key: 'profit_factor', label: 'Profit factor', kind: 'ratio' },
  { key: 'average_holding_bars', label: 'Avg sessions held', kind: 'sessions' },
  { key: 'average_mae_r', label: 'Avg MAE', kind: 'magnitude_r' },
];

const humanize = (k: string) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** One metric cell. The value comes from the backend's state alone; the reason (or N) rides along as a tooltip + caption. */
export function MetricCell({ metric, kind, signed = false }: { metric: Metric; kind: MetricKind; signed?: boolean }) {
  const d = describeMetric(metric, kind, 'No resolved signals');
  const tone = d.tone === 'ok' || d.tone === 'preliminary';
  const color = signed && tone && metric.value !== null ? (metric.value > 0 ? 'text-emerald-700' : metric.value < 0 ? 'text-red-700' : 'text-slate-800')
    : tone ? 'text-slate-800' : 'text-slate-300';
  const caption = d.tone === 'preliminary' ? `prelim · N=${metric.n}` : d.tone === 'ok' ? `N=${metric.n}` : d.tone === 'unavailable' ? 'n/a' : '';
  return (
    <td className="px-3 py-2 text-right align-top tabular-nums" data-metric-state={metric.state} title={metric.reason ?? d.note}>
      <span className={clsx('block text-sm font-medium', color)}>{d.text}</span>
      {caption && <span className={clsx('block text-[10px]', d.tone === 'preliminary' ? 'text-amber-700' : 'text-slate-400')}>{caption}</span>}
    </td>
  );
}

function DimensionTable({ dimension, minSample }: { dimension: PerfDimension; minSample: number }) {
  if (dimension.state === 'not_available') {
    return (
      <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-600" data-dimension-state="not_available">
        <p className="font-medium text-slate-700">{dimension.label} — not available</p>
        <p className="mt-1 text-xs text-slate-500">Requires {dimension.requires}.</p>
      </div>
    );
  }
  if (dimension.state === 'no_data' || dimension.groups.length === 0) {
    return (
      <div data-dimension-state="no_data">
        <EmptyState title={`No signals to group by ${dimension.label.toLowerCase()} yet`}>Groups appear once this strategy has recorded signals.</EmptyState>
      </div>
    );
  }
  return (
    <div data-dimension-state="ok">
      <div className="overflow-x-auto rounded-md border border-slate-200">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2 font-medium">{dimension.label}</th>
              <th className="px-3 py-2 text-right font-medium">Signals</th>
              <th className="px-3 py-2 text-right font-medium">Open</th>
              <th className="px-3 py-2 text-right font-medium">Held</th>
              <th className="px-3 py-2 text-right font-medium">Resolved</th>
              {COLUMNS.map((c) => <th key={c.key} className="px-3 py-2 text-right font-medium">{c.label}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {dimension.groups.map((g) => (
              <tr key={g.bucket} data-bucket={g.bucket}>
                <th scope="row" className="px-3 py-2 font-medium text-slate-800">{g.bucket}</th>
                <td className="px-3 py-2 text-right tabular-nums">{formatCount(g.signals)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCount(g.open)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCount(g.held)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatCount(g.resolved)}</td>
                {COLUMNS.map((c) => <MetricCell key={c.key} metric={g[c.key] as Metric} kind={c.kind} signed={c.signed} />)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        Rates and averages are over resolved signals only. Groups with fewer than {minSample} resolved signals are marked preliminary.
        {dimension.truncated ? ' Showing the first groups only; the list was truncated by the API.' : ''}
      </p>
    </div>
  );
}

export default function PerformanceBreakdownsPanel({ strategyKey, version }: { strategyKey: string; version: string }) {
  const [overview, setOverview] = useState<PerformanceOverview | null>(null);
  const [breakdowns, setBreakdowns] = useState<PerformanceBreakdowns | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<string | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([strategyApi.performanceOverview(strategyKey, version), strategyApi.performanceBreakdowns(strategyKey, version)])
      .then(([o, b]) => {
        if (cancelled) return;
        // An older backend without the generic API (404) or any failure: say so, never fall back to invented numbers.
        if (o.status === 'fulfilled' && b.status === 'fulfilled') {
          setOverview(o.value);
          setBreakdowns(b.value);
          setError(null);
        } else {
          setOverview(null);
          setBreakdowns(null);
          const failed = o.status === 'rejected' ? o.reason : (b as PromiseRejectedResult).reason;
          setError(toApiError(failed).message);
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [strategyKey, version]);

  useEffect(() => { setActive(null); return load(); }, [load]);

  const dimensions = useMemo(() => (breakdowns ? Object.values(breakdowns.dimensions) : []), [breakdowns]);
  const available = dimensions.filter((d) => d.state !== 'not_available');
  const missing = dimensions.filter((d) => d.state === 'not_available');
  const current = available.find((d) => d.dimension === active) ?? available[0] ?? null;

  if (loading && !breakdowns) return <LoadingSpinner className="py-10" />;
  if (error || !breakdowns || !overview) {
    return <ErrorAlert title="Could not load the performance breakdowns" message={error ?? 'No response.'} onRetry={load} />;
  }
  const rules = overview.exit_rules;
  const unavailableMetrics = Object.entries(overview.unavailable_metrics);

  return (
    <div className="space-y-4" data-testid="performance-breakdowns">
      <Section title="Breakdowns" description={`Resolved-signal performance grouped by one dimension at signal time. Definitions ${breakdowns.definitions_version}.`}>
        {available.length === 0 ? (
          <EmptyState title="No breakdowns available" />
        ) : (
          <>
            <div role="tablist" aria-label="Breakdown dimension" className="mb-3 flex flex-wrap gap-1.5">
              {available.map((d) => (
                <button key={d.dimension} type="button" role="tab" aria-selected={current?.dimension === d.dimension}
                        onClick={() => setActive(d.dimension)}
                        className={clsx('rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                          current?.dimension === d.dimension ? 'border-slate-800 bg-slate-800 text-white' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300')}>
                  {d.label}
                </button>
              ))}
            </div>
            {current && <DimensionTable dimension={current} minSample={breakdowns.min_sample_size} />}
          </>
        )}
      </Section>

      <Section title="Not yet measurable" description="Declared by the API as unavailable. These are not zero: the stored data cannot support them yet.">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Dimensions</h4>
            <ul className="space-y-1.5 text-xs" data-testid="unavailable-dimensions">
              {missing.map((d) => (
                <li key={d.dimension} className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
                  <span className="font-medium text-slate-700">{d.label}</span>
                  <span className="block text-slate-500">Requires {d.requires}</span>
                </li>
              ))}
              {missing.length === 0 && <li className="text-slate-400">None.</li>}
            </ul>
          </div>
          <div>
            <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Metrics</h4>
            <ul className="space-y-1.5 text-xs" data-testid="unavailable-metrics">
              {unavailableMetrics.map(([k, m]) => (
                <li key={k} className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
                  <span className="font-medium text-slate-700">{humanize(k)}</span>
                  <span className="block text-slate-500">{m.reason}</span>
                </li>
              ))}
              {unavailableMetrics.length === 0 && <li className="text-slate-400">None.</li>}
            </ul>
          </div>
        </div>
      </Section>

      <Section title="Exit rules" description="The rules this strategy version declares. R is measured against its stop distance.">
        {rules.state === 'declared' ? (
          <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4" data-exit-rules="declared">
            <div><dt className="text-slate-500">Stop</dt><dd className="font-medium text-slate-800">{rules.stop_atr_mult} × ATR</dd></div>
            <div><dt className="text-slate-500">Targets</dt><dd className="font-medium text-slate-800">{rules.target_atr_mults.map((t) => `${t} × ATR`).join(' · ')}</dd></div>
            <div><dt className="text-slate-500">Expiry</dt><dd className="font-medium text-slate-800">{rules.expiry_bars} sessions</dd></div>
            <div><dt className="text-slate-500">1R</dt><dd className="font-medium text-slate-800">{rules.r_unit}</dd></div>
          </dl>
        ) : (
          <p className="text-xs text-slate-500" data-exit-rules="not_available">Not available — {rules.reason}.</p>
        )}
      </Section>
    </div>
  );
}
