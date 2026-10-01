// File: frontend/src/components/strategies/CaptureHealthPanel.tsx
// Release B capture health: one row of candidate_capture_run per session, as the backend classified it. COMPLETE,
// PARTIAL, FAILED, RUNNING and DISABLED are distinct named colours; a PARTIAL run is never styled as healthy and never
// hidden behind a green "OK". Before migration 22 the whole panel is one honest "Not collected — Release B" notice.
import React from 'react';
import { clsx } from 'clsx';
import type { CaptureRun, CaptureRunsResponse } from '@/services/strategyApi';
import { CAPTURE_STATUS_META, describeMetric, formatCount, formatRuntime, formatSession, NOT_COLLECTED_NOTE } from '@/lib/strategyFormat';
import { Section, WarningBanner } from '@/components/strategies/ui';
import { AvailabilityNotice, CaptureBadge } from '@/components/strategies/ResearchUi';

const COUNTER_ROWS: { key: keyof CaptureRun['counters']; label: string; attention?: boolean }[] = [
  { key: 'candidates', label: 'Candidates' },
  { key: 'captured', label: 'Captured' },
  { key: 'already_captured', label: 'Already captured' },
  { key: 'stale_skipped', label: 'Stale skipped' },
  { key: 'snapshot_skipped', label: 'Snapshot skipped', attention: true },
  { key: 'invalid_skipped', label: 'Invalid', attention: true },
  { key: 'guard_rejected', label: 'Guard rejected' },
  { key: 'hash_drift', label: 'Hash drift' },
  { key: 'snapshot_drift', label: 'Snapshot drift' },
];

function missingText(run: CaptureRun): string {
  const m = run.missing_features;
  if (!m) return '—';
  const d = describeMetric(m.rate, 'rate', 'No snapshots');
  return d.text === '—' ? '—' : `${d.text} · ${formatCount(m.missing_slots)} of ${formatCount(m.total_slots)} slots`;
}

function LatestRun({ run, overallReason }: { run: CaptureRun; overallReason: string | null }) {
  const status = run.health.status;
  const meta = CAPTURE_STATUS_META[status];
  const skipped = Object.entries(run.skipped_symbols ?? {});
  return (
    <div className={clsx('rounded-lg border p-4', meta.panel)} data-testid="capture-latest" data-capture-status={status}>
      <div className="flex flex-wrap items-center gap-3">
        <CaptureBadge status={status} className="text-sm" />
        <span className="text-sm font-semibold text-slate-800">Session {formatSession(run.session_date)}</span>
        <span className="text-xs text-slate-500">
          feature set <span className="font-mono">{run.feature_set_version ?? '—'}</span> · run #{run.id ?? '—'}
          {run.run_attempts > 1 ? ` · ${run.run_attempts} attempts` : ''}
        </span>
      </div>

      {(run.health.reasons.length > 0 || (status === 'disabled' && overallReason)) && (
        <ul className={clsx('mt-2 list-disc pl-5 text-sm font-medium', meta.text)}>
          {run.health.reasons.map((r) => <li key={r}>{r}</li>)}
          {run.health.reasons.length === 0 && overallReason && <li>{overallReason}</li>}
        </ul>
      )}
      {run.error && <p className="mt-2 break-words rounded bg-white/70 px-2 py-1 font-mono text-xs text-red-700">{run.error}</p>}

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-3 lg:grid-cols-5" data-testid="capture-counters">
        {COUNTER_ROWS.map(({ key, label, attention }) => {
          const v = run.counters[key];
          return (
            <div key={key} className="flex items-baseline justify-between gap-2">
              <dt className="text-slate-600">{label}</dt>
              <dd className={clsx('tabular-nums', v === null ? 'text-slate-300' : attention && v > 0 ? 'font-bold text-amber-800' : 'font-semibold text-slate-800')}>
                {v === null ? '—' : formatCount(v)}
              </dd>
            </div>
          );
        })}
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-slate-600">Missing-feature rate</dt>
          <dd className="font-semibold tabular-nums text-slate-800">{missingText(run)}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-slate-600">Runtime</dt>
          <dd className="font-semibold tabular-nums text-slate-800">{formatRuntime(run.runtime_seconds)}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-slate-600">Ledger signals</dt>
          <dd className="font-semibold tabular-nums text-slate-800">{run.ledger_signals === undefined ? '—' : formatCount(run.ledger_signals)}</dd>
        </div>
      </dl>

      {run.health.notes.length > 0 && (
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-slate-600" data-testid="capture-notes">
          {run.health.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      {skipped.length > 0 && (
        <details className="mt-3 text-xs text-slate-600">
          <summary className="cursor-pointer font-medium">{formatCount(run.skipped_symbol_count)} skipped symbol{run.skipped_symbol_count === 1 ? '' : 's'}</summary>
          <ul className="mt-1 grid grid-cols-1 gap-x-4 sm:grid-cols-2">
            {skipped.map(([sym, why]) => <li key={sym}><span className="font-mono font-semibold">{sym}</span> — {why}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

function History({ runs }: { runs: CaptureRun[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-xs" aria-label="Capture history">
        <thead className="text-slate-500">
          <tr>
            {['Session', 'Status', 'Candidates', 'Captured', 'Skipped', 'Guard rej.', 'Missing', 'Runtime'].map((h, i) => (
              <th key={h} className={clsx('px-2 py-1.5 font-medium', i >= 2 && 'text-right')}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {runs.map((r) => {
            const c = r.counters;
            const skipped = c.snapshot_skipped === null && c.invalid_skipped === null ? null : (c.snapshot_skipped ?? 0) + (c.invalid_skipped ?? 0);
            return (
              <tr key={`${r.session_date}-${r.id ?? 'none'}`} data-capture-status={r.health.status}>
                <td className="px-2 py-1.5 font-medium text-slate-800">{formatSession(r.session_date)}</td>
                <td className="px-2 py-1.5"><CaptureBadge status={r.health.status} /></td>
                <td className="px-2 py-1.5 text-right tabular-nums">{c.candidates === null ? '—' : formatCount(c.candidates)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{c.captured === null ? '—' : formatCount(c.captured)}</td>
                <td className={clsx('px-2 py-1.5 text-right tabular-nums', skipped ? 'font-bold text-amber-800' : '')}>{skipped === null ? '—' : formatCount(skipped)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{c.guard_rejected === null ? '—' : formatCount(c.guard_rejected)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{r.missing_features ? describeMetric(r.missing_features.rate, 'rate', 'No snapshots').text : '—'}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{formatRuntime(r.runtime_seconds)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function CaptureHealthPanel({ data, error, onRetry }: { data: CaptureRunsResponse | null; error?: string | null; onRetry?: () => void }) {
  if (!data) {
    return (
      <Section title="Candidate capture" description="Release B records every strategy candidate and its T0 feature snapshot once per session.">
        {error ? (
          <WarningBanner title="Capture health could not be loaded">
            <p>{error} Nothing is estimated.</p>
            {onRetry && <button type="button" onClick={onRetry} className="mt-1 text-xs font-medium underline">Try again</button>}
          </WarningBanner>
        ) : <p className="text-xs text-slate-400">Loading capture health…</p>}
      </Section>
    );
  }
  const { availability, overall, latest, history } = data;
  const meta = CAPTURE_STATUS_META[overall.status];
  return (
    <Section
      title="Candidate capture"
      description="One run per session. A run is COMPLETE only when every candidate was captured with its snapshot; anything less is PARTIAL or FAILED and says so."
      actions={<CaptureBadge status={overall.status} />}
    >
      <div className="space-y-4" data-testid="capture-panel" data-overall={overall.status}>
        {availability.state !== 'ok' ? (
          <>
            <AvailabilityNotice availability={availability} />
            {history.length > 0 && (
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Recent sessions</h3>
                <History runs={history} />
              </div>
            )}
          </>
        ) : (
          <>
            {overall.status !== 'complete' && overall.reason && (
              <p className={clsx('text-sm font-medium', meta.text)} role="status">{overall.reason}</p>
            )}
            {latest ? <LatestRun run={latest} overallReason={overall.reason} /> : (
              <p className="text-xs text-slate-500">{NOT_COLLECTED_NOTE}</p>
            )}
            {history.length > 0 && (
              <div>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Recent sessions</h3>
                <History runs={history} />
              </div>
            )}
          </>
        )}
        {data.activation?.active_from && (
          <p className="text-xs text-slate-500" data-testid="capture-activation">
            Capture expected from {formatSession(data.activation.active_from)}
            {data.activation.pre_activation_sessions ? ` · ${formatCount(data.activation.pre_activation_sessions)} earlier session(s) predate Release B capture and are not counted as missing.` : ''}
          </p>
        )}
      </div>
    </Section>
  );
}
