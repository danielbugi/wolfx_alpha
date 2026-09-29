// File: frontend/src/components/strategies/OverviewTab.tsx
import React from 'react';
import type { DataHealth, StrategySummary } from '@/services/strategyApi';
import { formatCount, formatSession } from '@/lib/strategyFormat';
import { Btn } from '@/components/telegram/ui';
import { DistributionBar, HealthBadge, MetricCard, Section, StatTile } from '@/components/strategies/ui';

export default function OverviewTab({ summary, health, onOpenTab }: {
  summary: StrategySummary;
  health: DataHealth | null;
  onOpenTab: (tab: 'performance' | 'health' | 'signals') => void;
}) {
  const t = summary.tracking;
  const p = summary.performance;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile emphasis label="Total signals" value={formatCount(t.total_signals)} />
        <StatTile emphasis label="Open" value={formatCount(t.open)} hint={t.held ? `${formatCount(t.normally_open)} progressing · ${formatCount(t.held)} held` : 'tracked forward each session'} />
        <StatTile emphasis label="Resolved" value={formatCount(t.resolved)} hint="stop, target or time exit reached" />
        <StatTile emphasis label="Held" value={formatCount(t.held)} hint="paused for review (split suspect)" />
      </div>

      <Section title="Direction" description="Signal counts by breakout direction. A count imbalance describes what the screener found, not how either side performs.">
        <DistributionBar
          ariaLabel={`Bullish ${t.bullish}, bearish ${t.bearish}`}
          segments={[
            { key: 'bullish', label: 'Bullish', count: t.bullish, color: 'bg-emerald-600' },
            { key: 'bearish', label: 'Bearish', count: t.bearish, color: 'bg-red-600' },
          ]}
        />
      </Section>

      <Section
        title="Performance"
        description="Resolved signals only. Open and held signals never count toward a rate."
        actions={<Btn size="sm" variant="ghost" onClick={() => onOpenTab('performance')}>Details →</Btn>}
      >
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard label="Win rate" metric={p.win_rate} kind="rate" />
          <MetricCard label="Average R" metric={p.average_r} kind="r" signed />
          <MetricCard label="Median R" metric={p.median_r} kind="r" signed />
          <MetricCard label="Trading sessions held" metric={p.average_holding_bars} kind="sessions" />
        </div>
      </Section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Lifecycle" description="Where every recorded signal stands now.">
          <DistributionBar
            ariaLabel={`Open ${t.normally_open}, held ${t.held}, resolved ${t.resolved}`}
            segments={[
              { key: 'open', label: 'Open', count: t.normally_open, color: 'bg-indigo-600' },
              { key: 'held', label: 'Held', count: t.held, color: 'bg-amber-600' },
              { key: 'resolved', label: 'Resolved', count: t.resolved, color: 'bg-cyan-600' },
            ]}
          />
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <dt className="text-slate-500">Signals this week</dt>
            <dd className="text-right font-medium tabular-nums text-slate-800">{formatCount(t.signals_this_week)}</dd>
            <dt className="text-slate-500">Signals this month</dt>
            <dd className="text-right font-medium tabular-nums text-slate-800">{formatCount(t.signals_this_month)}</dd>
            <dt className="text-slate-500">Latest market session</dt>
            <dd className="text-right font-medium text-slate-800">{formatSession(summary.reference_session)}</dd>
          </dl>
        </Section>

        <Section
          title="Data health"
          actions={<Btn size="sm" variant="ghost" onClick={() => onOpenTab('health')}>Details →</Btn>}
        >
          {health ? (
            <div className="space-y-2 text-sm">
              <HealthBadge status={health.status} />
              {health.issues.length === 0 ? (
                <p className="text-xs text-slate-500">No held signals, no stale or blocked price data, and 0 integrity violations.</p>
              ) : (
                <ul className="list-disc pl-4 text-xs text-slate-700">
                  {health.issues.map((i) => <li key={i}>{i}</li>)}
                </ul>
              )}
            </div>
          ) : (
            <p className="text-xs text-slate-400">Data health is unavailable right now.</p>
          )}
        </Section>
      </div>
    </div>
  );
}
