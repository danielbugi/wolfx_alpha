// File: frontend/src/components/strategies/DirectionsTab.tsx
import React from 'react';
import { clsx } from 'clsx';
import type { DirectionBlock, Metric, StrategySummary } from '@/services/strategyApi';
import { describeMetric, formatCount } from '@/lib/strategyFormat';
import type { MetricKind } from '@/lib/strategyFormat';
import { DirectionTag, Section } from '@/components/strategies/ui';

type Row =
  | { label: string; count: (d: DirectionBlock) => number }
  | { label: string; metric: (d: DirectionBlock) => Metric; kind: MetricKind };

const ROWS: Row[] = [
  { label: 'Signals', count: (d) => d.signals },
  { label: 'Open', count: (d) => d.normally_open },
  { label: 'Held', count: (d) => d.held },
  { label: 'Resolved', count: (d) => d.resolved },
  { label: 'Winners', count: (d) => d.winners },
  { label: 'Stopped', count: (d) => d.stopped },
  { label: 'Expired', count: (d) => d.expired },
  { label: 'Ambiguous', count: (d) => d.ambiguous },
  { label: 'Win rate', metric: (d) => d.win_rate, kind: 'rate' },
  { label: 'Average R', metric: (d) => d.average_r, kind: 'r' },
  { label: 'Median R', metric: (d) => d.median_r, kind: 'r' },
  { label: 'Avg trading sessions held', metric: (d) => d.average_holding_bars, kind: 'sessions' },
  { label: 'Stop rate', metric: (d) => d.stop_rate, kind: 'rate' },
  { label: 'Expiry rate', metric: (d) => d.expiry_rate, kind: 'rate' },
  { label: 'Target 1 rate', metric: (d) => d.target_rates.target1, kind: 'rate' },
  { label: 'Target 2 rate', metric: (d) => d.target_rates.target2, kind: 'rate' },
  { label: 'Target 3 rate', metric: (d) => d.target_rates.target3, kind: 'rate' },
];

function MetricCell({ m, kind }: { m: Metric; kind: MetricKind }) {
  const d = describeMetric(m, kind);
  return (
    <td className="px-3 py-2 text-right">
      <span className={clsx('tabular-nums', d.tone === 'ok' || d.tone === 'preliminary' ? 'font-semibold text-slate-800' : 'text-slate-300')}>{d.text}</span>
      {(d.tone === 'ok' || d.tone === 'preliminary') && (
        <span className={clsx('block text-[10px]', d.tone === 'preliminary' ? 'text-amber-700' : 'text-slate-400')}>{d.note}</span>
      )}
    </td>
  );
}

export default function DirectionsTab({ summary }: { summary: StrategySummary }) {
  const { bullish, bearish } = summary.directions;
  const noResolved = bullish.resolved === 0 && bearish.resolved === 0;
  return (
    <Section title="Bullish vs bearish" description="The same canonical definitions, split by breakout direction. Rates carry their own sample size.">
      {noResolved && (
        <p className="mb-3 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
          No resolved signals in either direction yet — performance rows show “—” until outcomes exist. Signal counts are not a performance comparison.
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[20rem] text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500">
              <th className="px-3 py-2 text-left font-medium">Metric</th>
              <th className="px-3 py-2 text-right font-medium"><DirectionTag direction="bullish" /></th>
              <th className="px-3 py-2 text-right font-medium"><DirectionTag direction="bearish" /></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {ROWS.map((r) => (
              <tr key={r.label} className={'metric' in r ? 'bg-slate-50/40' : undefined}>
                <th scope="row" className="px-3 py-2 text-left text-xs font-medium text-slate-600">{r.label}</th>
                {'count' in r ? (
                  <>
                    <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-800">{formatCount(r.count(bullish))}</td>
                    <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-800">{formatCount(r.count(bearish))}</td>
                  </>
                ) : (
                  <>
                    <MetricCell m={r.metric(bullish)} kind={r.kind} />
                    <MetricCell m={r.metric(bearish)} kind={r.kind} />
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
