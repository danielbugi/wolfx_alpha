// File: frontend/src/components/strategies/ResearchUi.tsx
// Building blocks shared by every Release B view. A research number is shown ONLY when the backend measured it; an
// unmeasured one reads "—" with the reason (never 0, never 0%), and a capture status is always a named, coloured chip.
import React from 'react';
import { clsx } from 'clsx';
import type { Availability, CaptureStatus, CountMetric, Metric } from '@/services/strategyApi';
import { CAPTURE_STATUS_META, describeCount, describeMetric, NOT_COLLECTED_NOTE } from '@/lib/strategyFormat';
import { WarningBanner } from '@/components/strategies/ui';

export function CaptureBadge({ status, className }: { status: CaptureStatus; className?: string }) {
  const m = CAPTURE_STATUS_META[status];
  return (
    <span data-capture-status={status}
          className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold tracking-wide', m.chip, className)}>
      <span className={clsx('h-1.5 w-1.5 rounded-full', m.dot)} aria-hidden />
      {m.label}
    </span>
  );
}

const TONE_TEXT = { ok: 'text-slate-800', empty: 'text-slate-300', unavailable: 'text-slate-300' } as const;

/** A backend count (observations, snapshots, ...) by its state. */
export function CountCard({ label, count, hint }: { label: string; count: CountMetric | undefined; hint?: string }) {
  const d = describeCount(count);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3" data-count-state={count?.state ?? 'not_available'}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={clsx('mt-1 text-2xl font-semibold tabular-nums', TONE_TEXT[d.tone])}>{d.text}</p>
      <p className="mt-0.5 text-xs text-slate-400">{d.note || hint}</p>
    </div>
  );
}

/** A backend rate. Unavailable → "Not collected — Release B"; no data → the backend's reason; otherwise value and N. */
export function RateCard({ label, metric }: { label: string; metric: Metric | undefined }) {
  const m = metric;
  const measured = !!m && (m.state === 'ok' || m.state === 'preliminary') && m.value !== null;
  const d = m && measured ? describeMetric(m, 'rate') : null;
  const text = d ? d.text : '—';
  const note = d ? d.note : m?.state === 'no_data' ? m.reason ?? 'No data yet' : NOT_COLLECTED_NOTE;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3" data-metric-state={m?.state ?? 'not_available'}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className={clsx('mt-1 text-2xl font-semibold tabular-nums', measured ? 'text-slate-800' : 'text-slate-300')}>{text}</p>
      <p className={clsx('mt-0.5 text-xs', d?.tone === 'preliminary' ? 'text-amber-700' : measured ? 'text-slate-500' : 'text-slate-400')}>{note}</p>
    </div>
  );
}

/** Why there is nothing to show: research tables not installed yet vs. installed but capture never ran. */
export function AvailabilityNotice({ availability }: { availability: Availability }) {
  if (availability.state === 'ok') return null;
  const notInstalled = availability.state === 'not_available';
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4" data-availability={availability.state}>
      <p className="text-sm font-semibold text-slate-700">{notInstalled ? NOT_COLLECTED_NOTE : 'Capture has not run for this strategy'}</p>
      <p className="mt-1 text-xs text-slate-500">{availability.reason}</p>
    </div>
  );
}

export function ResearchLoadFailed({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <WarningBanner title="Research data could not be loaded">
      <p>{message} Nothing below is estimated.</p>
      {onRetry && <button type="button" onClick={onRetry} className="mt-1 text-xs font-medium underline">Try again</button>}
    </WarningBanner>
  );
}
