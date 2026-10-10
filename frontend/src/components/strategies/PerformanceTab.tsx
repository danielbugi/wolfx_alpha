// File: frontend/src/components/strategies/PerformanceTab.tsx
import React from 'react';
import { clsx } from 'clsx';
import type { Metric, StrategySummary } from '@/services/strategyApi';
import { describeMetric, formatCount } from '@/lib/strategyFormat';
import { EmptyState, MetricCard, Section, StatTile } from '@/components/strategies/ui';
import PerformanceBreakdownsPanel from '@/components/strategies/PerformanceBreakdowns';

interface OutcomeRow {
  key: string;
  label: string;
  count: number;
  share: Metric | null;
  color: string;
  note?: string;
}

/** Horizontal bars, one per terminal outcome, each named with its count and the backend's share (with its N). Bar
 * length is relative to the largest count on screen -- a reading aid, not a statistic. */
function OutcomeBars({ rows }: { rows: OutcomeRow[] }) {
  const max = Math.max(...rows.map((r) => r.count), 1);
  return (
    <ul className="space-y-2">
      {rows.map((r) => {
        const share = r.share ? describeMetric(r.share, 'rate') : null;
        return (
          <li key={r.key} className="grid grid-cols-[7.5rem_1fr_auto] items-center gap-3 text-xs sm:grid-cols-[9rem_1fr_7rem]">
            <span className="text-slate-600">
              {r.label}
              {r.note && <span className="block text-[10px] text-slate-400">{r.note}</span>}
            </span>
            <span className="h-2.5 rounded-full bg-slate-100">
              {r.count > 0 && <span className={clsx('block h-full rounded-full', r.color)} style={{ width: `${(r.count / max) * 100}%`, minWidth: '4px' }} />}
            </span>
            <span className="text-right tabular-nums text-slate-800">
              <span className="font-semibold">{formatCount(r.count)}</span>
              {share && share.tone !== 'empty' && <span className="ml-1 text-slate-400">{share.text}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export default function PerformanceTab({ summary }: { summary: StrategySummary }) {
  const { key, version } = summary.strategy;
  const p = summary.performance;
  const o = summary.outcomes;
  const exp = o.terminal.expired;
  return (
    <div className="space-y-4">
      <Section title="Performance" description="Every rate is over resolved signals only. A winner is a target reached before the stop; an expiry is not a win, but its real R counts in average and median R.">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard label="Win rate" metric={p.win_rate} kind="rate" />
          <MetricCard label="Average R" metric={p.average_r} kind="r" signed />
          <MetricCard label="Median R" metric={p.median_r} kind="r" signed />
          <MetricCard label="Stop rate" metric={p.stop_rate} kind="rate" />
          <MetricCard label="Expiry rate" metric={p.expiry_rate} kind="rate" />
          <MetricCard label="Avg trading sessions held" metric={p.average_holding_bars} kind="sessions" />
          <MetricCard label="Median trading sessions held" metric={p.median_holding_bars} kind="sessions" />
          <MetricCard label="Average MAE (adverse)" metric={p.average_mae_r} kind="magnitude_r" />
          <MetricCard label="Average MFE (favourable)" metric={p.average_mfe_r} kind="magnitude_r" />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Resolved" value={formatCount(p.resolved_n)} />
          <StatTile label="Winners" value={formatCount(p.winners)} />
          <StatTile label="Stopped" value={formatCount(p.stopped)} hint={p.ambiguous ? `${formatCount(p.ambiguous)} ambiguous` : undefined} />
          <StatTile label="Expired" value={formatCount(p.expired)} hint={p.expired ? `${formatCount(p.expired_positive)} positive · ${formatCount(p.expired_negative)} negative` : undefined} />
        </div>
      </Section>

      <Section title="Outcome distribution" description="How resolved signals ended. The trade exits at the first target touched.">
        {o.resolved_n === 0 ? (
          <EmptyState title="Outcome analysis will appear after signals begin resolving.">
            Signals resolve when price reaches the stop or a target, or after 20 trading sessions.
          </EmptyState>
        ) : (
          <div className="space-y-4">
            <OutcomeBars rows={[
              { key: 't1', label: 'Target 1 (+1R)', count: o.terminal.target1.count, share: o.terminal.target1.share, color: 'bg-emerald-600' },
              { key: 't2', label: 'Target 2 (+2R)', count: o.terminal.target2.count, share: o.terminal.target2.share, color: 'bg-emerald-600' },
              { key: 't3', label: 'Target 3 (+3R)', count: o.terminal.target3.count, share: o.terminal.target3.share, color: 'bg-emerald-600' },
              { key: 'stopped', label: 'Stopped (−1R)', count: o.terminal.stopped.count, share: o.terminal.stopped.share, color: 'bg-red-600',
                note: o.ambiguous.count ? `incl. ${formatCount(o.ambiguous.count)} ambiguous same-bar` : undefined },
              { key: 'exp_pos', label: 'Expired, positive', count: exp.positive_count, share: null, color: 'bg-violet-600' },
              { key: 'exp_neg', label: 'Expired, negative', count: exp.negative_count, share: null, color: 'bg-violet-600' },
              { key: 'exp_flat', label: 'Expired, flat', count: exp.flat_count, share: null, color: 'bg-violet-600' },
            ]} />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <MetricCard label="Expired: average R" metric={exp.average_r} kind="r" signed emptyNote="No expired signals yet" />
              <MetricCard label="Ambiguous share of stops" metric={o.ambiguous.share_of_stopped} kind="rate" emptyNote="No stopped signals yet" />
              <MetricCard label="Reached target 1 or higher" metric={o.target_milestones.reached_target1.rate} kind="rate" />
            </div>
            <p className="text-xs text-slate-400">{o.milestone_semantics}</p>
          </div>
        )}
      </Section>

      <PerformanceBreakdownsPanel key={`${key}/${version}`} strategyKey={key} version={version} />
    </div>
  );
}
