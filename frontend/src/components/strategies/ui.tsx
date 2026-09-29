// File: frontend/src/components/strategies/ui.tsx
// Building blocks for /strategies, in the app's existing light slate style (white bordered cards, emerald = bullish /
// target, red = bearish / stop). Plain Tailwind: this app does not load NextUI's Tailwind plugin.
import React from 'react';
import { clsx } from 'clsx';
import { ExclamationTriangleIcon, InboxIcon } from '@heroicons/react/24/outline';
import type { Direction, Metric, SignalStatus } from '@/services/strategyApi';
import { describeMetric, NO_RESOLVED_NOTE, STATUS_LABEL } from '@/lib/strategyFormat';
import type { MetricKind } from '@/lib/strategyFormat';

export function Section({ title, description, actions, children, className }: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={clsx('rounded-lg border border-slate-200 bg-white shadow-sm', className)}>
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
        </div>
        {actions}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

/** A plain count: a real 0 is shown as 0 (it is measured), null as —. */
export function StatTile({ label, value, hint, emphasis = false }: { label: string; value: string; hint?: string; emphasis?: boolean }) {
  return (
    <div className={clsx('rounded-lg p-3', emphasis ? 'border border-slate-200 bg-white' : 'bg-slate-50')}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={clsx('mt-1 font-semibold tabular-nums text-slate-800', emphasis ? 'text-3xl' : 'text-xl')}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

const TONE_NOTE: Record<string, string> = {
  ok: 'text-slate-500',
  preliminary: 'text-amber-700',
  empty: 'text-slate-400',
  unavailable: 'text-slate-400',
};

/** A backend Metric, rendered strictly by its state: no_data → "—", preliminary → value + "Preliminary · N", ok → value + N. */
export function MetricCard({ label, metric, kind, emptyNote = NO_RESOLVED_NOTE, signed = false }: {
  label: string;
  metric: Metric;
  kind: MetricKind;
  emptyNote?: string;
  /** colour a signed value (R) green/red once it is a real number */
  signed?: boolean;
}) {
  const d = describeMetric(metric, kind, emptyNote);
  const valueColor = signed && metric.value !== null && (d.tone === 'ok' || d.tone === 'preliminary')
    ? metric.value > 0 ? 'text-emerald-700' : metric.value < 0 ? 'text-red-700' : 'text-slate-800'
    : d.tone === 'ok' || d.tone === 'preliminary' ? 'text-slate-800' : 'text-slate-300';
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3" data-metric-state={metric.state}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={clsx('mt-1 text-2xl font-semibold tabular-nums', valueColor)}>{d.text}</p>
      <p className={clsx('mt-0.5 text-xs', TONE_NOTE[d.tone])}>{d.note}</p>
    </div>
  );
}

export interface Segment {
  key: string;
  label: string;
  count: number;
  /** a Tailwind bg-* class from the validated palettes (see DistributionBar) */
  color: string;
}

/**
 * A part-to-whole bar with its legend underneath. Colours were run through the dataviz palette validator
 * (direction: emerald-600/red-600; lifecycle: indigo-600/amber-600/cyan-600; outcomes: emerald/red/violet-600) and every
 * segment is also named with its count in text, so identity never rests on colour alone. Widths are proportional to
 * the backend's counts; no percentage is derived or displayed here.
 */
export function DistributionBar({ segments, ariaLabel }: { segments: Segment[]; ariaLabel: string }) {
  const total = segments.reduce((s, x) => s + x.count, 0);
  return (
    <div>
      {total > 0 && (
        <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full" role="img" aria-label={ariaLabel}>
          {segments.filter((s) => s.count > 0).map((s) => (
            <div key={s.key} className={clsx('h-full first:rounded-l-full last:rounded-r-full', s.color)}
                 style={{ flexGrow: s.count, flexBasis: 0, minWidth: '4px' }} title={`${s.label}: ${s.count}`} />
          ))}
        </div>
      )}
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        {segments.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span className={clsx('inline-block h-2.5 w-2.5 rounded-sm', s.color)} aria-hidden />
            {s.label} <span className="font-semibold tabular-nums text-slate-800">{s.count.toLocaleString('en-US')}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DirectionTag({ direction }: { direction: Direction }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 text-xs font-medium',
      direction === 'bullish' ? 'text-emerald-700' : 'text-red-700')}>
      <span aria-hidden>{direction === 'bullish' ? '▲' : '▼'}</span>
      {direction === 'bullish' ? 'Bullish' : 'Bearish'}
    </span>
  );
}

const STATUS_CHIP: Record<SignalStatus, string> = {
  open: 'bg-slate-100 text-slate-700',
  stopped: 'bg-red-50 text-red-700',
  target1: 'bg-emerald-50 text-emerald-700',
  target2: 'bg-emerald-50 text-emerald-700',
  target3: 'bg-emerald-50 text-emerald-700',
  expired: 'bg-violet-50 text-violet-700',
};

export function StatusChip({ status, held = false, ambiguous = false }: { status: SignalStatus; held?: boolean; ambiguous?: boolean }) {
  if (held) {
    return <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">Held</span>;
  }
  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium', STATUS_CHIP[status])}>
      {STATUS_LABEL[status]}{ambiguous && <span title="Stop and target on the same bar" aria-label="ambiguous">*</span>}
    </span>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center">
      <InboxIcon className="h-8 w-8 text-slate-300" aria-hidden />
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {children && <div className="max-w-md text-xs text-slate-500">{children}</div>}
    </div>
  );
}

export function WarningBanner({ tone = 'warning', title, children }: { tone?: 'warning' | 'danger'; title: string; children?: React.ReactNode }) {
  return (
    <div role="alert" className={clsx('flex gap-3 rounded-lg border p-3',
      tone === 'danger' ? 'border-red-200 bg-red-50 text-red-800' : 'border-amber-200 bg-amber-50 text-amber-900')}>
      <ExclamationTriangleIcon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
      <div className="text-sm">
        <p className="font-semibold">{title}</p>
        {children && <div className="mt-1 text-xs">{children}</div>}
      </div>
    </div>
  );
}

export function HealthBadge({ status }: { status: 'healthy' | 'attention' | 'violation' }) {
  const cls = status === 'healthy' ? 'bg-emerald-50 text-emerald-700' : status === 'attention' ? 'bg-amber-100 text-amber-800' : 'bg-red-100 text-red-700';
  const label = status === 'healthy' ? 'Healthy' : status === 'attention' ? 'Needs attention' : 'Integrity violation';
  return (
    <span className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold', cls)}>
      <span className={clsx('h-1.5 w-1.5 rounded-full', status === 'healthy' ? 'bg-emerald-500' : status === 'attention' ? 'bg-amber-500' : 'bg-red-500')} aria-hidden />
      {label}
    </span>
  );
}
