// File: frontend/src/components/strategies/CandidateDetailDialog.tsx
// One captured candidate. "Strategy context" is what the strategy itself recorded (candidate_observation); "T0 market
// snapshot" is the frozen, strategy-independent market state (feature_snapshot). They are loaded and shown separately,
// so a snapshot problem never hides the strategy's own record. Lineage: Candidate → Snapshot → Ledger signal.
import React, { useEffect, useState } from 'react';
import { clsx } from 'clsx';
import { ChevronRightIcon, XMarkIcon } from '@heroicons/react/24/outline';
import Dialog from '@/components/common/Dialog';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { strategyApi, toApiError } from '@/services/strategyApi';
import type { ApiError, CandidateDetail, SnapshotDetail } from '@/services/strategyApi';
import {
  formatCount, formatFeatureValue, formatNumber, formatPrice, formatR, formatSession, formatUtcTimestamp, GUARD_LABEL, GUARD_REASON_LABEL,
  humanize, LIFECYCLE_LABEL, STATUS_LABEL,
} from '@/lib/strategyFormat';
import { candidateClassLabel, extensionRows } from '@/lib/strategyExtensions';
import type { ExtensionRow } from '@/lib/strategyExtensions';
import { Btn } from '@/components/telegram/ui';
import { DirectionTag } from '@/components/strategies/ui';

function Field({ label, children, muted = false }: { label: string; children: React.ReactNode; muted?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={muted ? 'text-right text-xs text-slate-400' : 'text-right font-medium tabular-nums text-slate-800'}>{children}</dd>
    </div>
  );
}

function Block({ title, subtitle, children, testId }: { title: string; subtitle?: string; children: React.ReactNode; testId?: string }) {
  return (
    <section className="rounded-lg border border-slate-200 p-3" aria-label={title} data-testid={testId}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {subtitle && <p className="mb-1 text-[11px] text-slate-400">{subtitle}</p>}
      <div className="mt-1 divide-y divide-slate-100">{children}</div>
    </section>
  );
}

function extensionValue(row: ExtensionRow): string {
  const v = row.value;
  if (v === null || v === undefined) return 'Not recorded';
  if (row.kind === 'price' && typeof v === 'number') return formatPrice(v);
  if (row.kind === 'date' && typeof v === 'string') return formatSession(v);
  if (typeof v === 'number') return formatNumber(v, 2);
  return typeof v === 'object' ? JSON.stringify(v) : String(v);
}

function Banner({ c, s }: { c: CandidateDetail; s: SnapshotDetail | null }) {
  const missing = s ? s.missing_features : null;
  const partial = (s?.snapshot_status ?? c.snapshot.snapshot_status) === 'partial';
  const count = missing ? missing.length : c.snapshot.missing_count;
  return (
    <dl className="grid grid-cols-2 gap-3 rounded-lg bg-slate-50 p-3 text-xs sm:grid-cols-4" data-testid="candidate-banner">
      <div><dt className="text-slate-500">Feature set</dt><dd className="font-mono text-sm font-semibold text-slate-800">{c.snapshot.feature_set_version}</dd></div>
      <div><dt className="text-slate-500">Session date</dt><dd className="text-sm font-semibold text-slate-800">{formatSession(c.identity.session_date)}</dd></div>
      <div><dt className="text-slate-500">Bar date</dt><dd className="text-sm font-semibold text-slate-800">{formatSession(c.identity.bar_date)}</dd></div>
      <div>
        <dt className="text-slate-500">Missing features</dt>
        <dd className={clsx('text-sm font-semibold', partial ? 'text-amber-800' : 'text-slate-800')} data-testid="missing-features">
          {count === null || count === undefined ? '—' : formatCount(count)}{partial && <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px]">PARTIAL</span>}
        </dd>
      </div>
    </dl>
  );
}

function Lineage({ c, onOpenSignal }: { c: CandidateDetail; onOpenSignal: (id: number) => void }) {
  const sig = c.lineage.signal;
  const step = 'rounded-lg border px-3 py-1.5 text-xs';
  return (
    <ol className="flex flex-wrap items-center gap-2" aria-label="Lineage" data-testid="candidate-lineage">
      <li className={clsx(step, 'border-slate-300 bg-white')}>
        <span className="block text-[10px] uppercase tracking-wide text-slate-400">Candidate</span>
        <span className="font-semibold text-slate-800">#{c.lineage.observation_id}</span>
      </li>
      <ChevronRightIcon className="h-4 w-4 text-slate-300" aria-hidden />
      <li className={clsx(step, 'border-slate-300 bg-white')}>
        <span className="block text-[10px] uppercase tracking-wide text-slate-400">Feature snapshot</span>
        <span className="font-semibold text-slate-800">#{c.lineage.snapshot_id}</span>
      </li>
      <ChevronRightIcon className="h-4 w-4 text-slate-300" aria-hidden />
      {sig ? (
        <li className={clsx(step, 'border-indigo-200 bg-indigo-50')}>
          <span className="block text-[10px] uppercase tracking-wide text-indigo-400">Ledger signal</span>
          <button type="button" className="font-semibold text-indigo-700 hover:underline" onClick={() => onOpenSignal(sig.id)}>
            #{sig.id} · {STATUS_LABEL[sig.status]} · {LIFECYCLE_LABEL[sig.lifecycle]}
            {sig.outcome_r !== null && ` · ${formatR(sig.outcome_r)}`}
          </button>
        </li>
      ) : (
        <li className={clsx(step, 'border-dashed border-slate-300 bg-slate-50 text-slate-500')}>
          <span className="block text-[10px] uppercase tracking-wide text-slate-400">Ledger signal</span>
          Not in the signal ledger
        </li>
      )}
    </ol>
  );
}

function StrategyContext({ c }: { c: CandidateDetail }) {
  const x = c.strategy_context;
  const ext = extensionRows(c.strategy.key, x.extension);
  return (
    <Block title="Strategy context" subtitle="Recorded by the strategy at detection (candidate observation)" testId="strategy-context">
      <Field label="Candidate class">{candidateClassLabel(c.strategy.key, x.candidate_class)}</Field>
      <Field label="Triggered">{x.triggered ? 'Yes' : 'No — near the channel'}</Field>
      <Field label="Entry close" muted={x.levels.entry_close === null}>{x.levels.entry_close === null ? 'Not recorded' : formatPrice(x.levels.entry_close)}</Field>
      <Field label="Channel high (prior)" muted={x.levels.channel_high_prev === null}>{x.levels.channel_high_prev === null ? 'Not recorded' : formatPrice(x.levels.channel_high_prev)}</Field>
      <Field label="Channel low (prior)" muted={x.levels.channel_low_prev === null}>{x.levels.channel_low_prev === null ? 'Not recorded' : formatPrice(x.levels.channel_low_prev)}</Field>
      <Field label="Breakout distance" muted={x.breakout_dist_atr === null}>{x.breakout_dist_atr === null ? 'Not recorded' : `${formatNumber(x.breakout_dist_atr, 2)} ATR`}</Field>
      <Field label="Distance to channel" muted={x.distance_to_channel_pct === null}>{x.distance_to_channel_pct === null ? 'Not recorded' : `${formatNumber(x.distance_to_channel_pct, 2)}%`}</Field>
      <Field label="Universe guard">
        {GUARD_LABEL[x.guard.status]}
        {x.guard.reasons.length > 0 && <span className="ml-1 text-xs font-normal text-red-700">({x.guard.reasons.map((r) => GUARD_REASON_LABEL[r] ?? humanize(r)).join(', ')})</span>}
      </Field>
      <Field label="Alignment score" muted={x.alignment_score === null}>{x.alignment_score === null ? 'Not recorded' : formatNumber(x.alignment_score, 0)}</Field>
      <Field label="Grade" muted={!x.quality_grade}>{x.quality_grade ?? 'Not recorded'}</Field>
      <Field label="Combined score" muted={x.combined_score === null}>{x.combined_score === null ? 'Not recorded' : formatNumber(x.combined_score, 1)}</Field>
      <Field label="Session rank" muted={x.session_rank === null}>{x.session_rank === null ? 'Not ranked' : `#${formatCount(x.session_rank)}`}</Field>
      <Field label="Selected">{x.selected ? 'Yes' : 'No'}</Field>
      <Field label="Model" muted={!x.model.version}>
        {x.model.version ? `${x.model.version}${x.model.score !== null ? ` · ${formatNumber(x.model.score, 3)}` : ''}` : 'Not scored (no validated model)'}
      </Field>
      {x.screener_defaults.length > 0 && (
        <Field label="Screener defaults" muted>{x.screener_defaults.map(humanize).join(', ')}</Field>
      )}
      {ext.map((r) => <Field key={r.key} label={r.label} muted={r.value === null || r.value === undefined}>{extensionValue(r)}</Field>)}
    </Block>
  );
}

function SnapshotPanel({ s, error, loading, onRetry }: { s: SnapshotDetail | null; error: ApiError | null; loading: boolean; onRetry: () => void }) {
  return (
    <section className="rounded-lg border border-slate-200 p-3" aria-label="T0 market snapshot" data-testid="t0-snapshot">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">T0 market snapshot</h3>
      <p className="mb-2 text-[11px] text-slate-400">Frozen at the session close; independent of any strategy (feature snapshot)</p>
      {error ? (
        <ErrorAlert title="Could not load the snapshot" message={error.message} onRetry={onRetry} />
      ) : loading || !s ? (
        <LoadingSpinner className="py-8" />
      ) : (
        <div className="space-y-3">
          {s.missing_features.length > 0 && (
            <p className="rounded bg-amber-50 px-2 py-1.5 text-xs text-amber-900" data-testid="missing-feature-list">
              <span className="font-semibold">{formatCount(s.missing_features.length)} missing:</span> {s.missing_features.join(', ')}
            </p>
          )}
          {s.groups.map((g) => (
            <div key={g.key} data-group={g.key}>
              <h4 className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{g.label}</h4>
              <dl className="divide-y divide-slate-100">
                {g.items.map((f) => (
                  <Field key={f.name} label={humanize(f.name)} muted={f.missing || f.value === null}>
                    <span title={f.definition ?? undefined}>{formatFeatureValue(f.value, f.unit)}</span>
                  </Field>
                ))}
              </dl>
            </div>
          ))}
          <p className="break-all text-[11px] text-slate-400">
            {s.feature_set.version} · manifest {s.feature_set.manifest_hash.slice(0, 12)} · hash {s.provenance.content_hash.slice(0, 12)}
            {s.provenance.code_ref ? ` · code ${s.provenance.code_ref.slice(0, 12)}` : ''} · captured {formatUtcTimestamp(s.provenance.captured_at)}
          </p>
        </div>
      )}
    </section>
  );
}

export default function CandidateDetailDialog({ strategyKey, version, candidateId, onClose, onOpenSignal }: {
  strategyKey: string;
  version: string;
  candidateId: number | null;
  onClose: () => void;
  onOpenSignal: (signalId: number) => void;
}) {
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [snap, setSnap] = useState<SnapshotDetail | null>(null);
  const [snapError, setSnapError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [snapAttempt, setSnapAttempt] = useState(0);

  useEffect(() => {
    if (candidateId === null) return;
    let cancelled = false;
    setDetail(null); setError(null); setSnap(null); setSnapError(null);
    strategyApi.candidate(strategyKey, version, candidateId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => { if (!cancelled) setError(toApiError(e)); });
    return () => { cancelled = true; };
  }, [strategyKey, version, candidateId, attempt]);

  const snapshotId = detail?.snapshot.id ?? null;
  useEffect(() => {
    if (snapshotId === null) return;
    let cancelled = false;
    setSnap(null); setSnapError(null);
    strategyApi.snapshot(strategyKey, version, snapshotId)
      .then((d) => { if (!cancelled) setSnap(d); })
      .catch((e) => { if (!cancelled) setSnapError(toApiError(e)); });
    return () => { cancelled = true; };
  }, [strategyKey, version, snapshotId, snapAttempt]);

  return (
    <Dialog open={candidateId !== null} onClose={onClose} labelledBy="candidate-detail-title">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div>
          <h2 id="candidate-detail-title" className="flex flex-wrap items-center gap-2 text-lg font-semibold text-slate-800">
            {detail ? <SymbolHoverLink symbol={detail.identity.symbol} /> : 'Candidate'}
            {detail && <DirectionTag direction={detail.identity.direction} />}
            {detail && <span className="text-sm font-normal text-slate-500">{candidateClassLabel(detail.strategy.key, detail.strategy_context.candidate_class)}</span>}
          </h2>
          {detail && <p className="mt-0.5 text-xs text-slate-500">{detail.strategy.display_name} {detail.identity.strategy_version} · captured {formatUtcTimestamp(detail.capture.captured_at)}</p>}
        </div>
        <Btn variant="ghost" size="icon" aria-label="Close" onClick={onClose}><XMarkIcon className="h-5 w-5" /></Btn>
      </div>
      <div className="space-y-3 overflow-y-auto p-4">
        {error ? (
          <ErrorAlert title="Could not load this candidate" message={error.message} onRetry={() => setAttempt((n) => n + 1)} />
        ) : !detail ? (
          <LoadingSpinner className="py-12" />
        ) : (
          <>
            <Banner c={detail} s={snap} />
            <Lineage c={detail} onOpenSignal={onOpenSignal} />
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <StrategyContext c={detail} />
              <SnapshotPanel s={snap} error={snapError} loading={!snapError && !snap} onRetry={() => setSnapAttempt((n) => n + 1)} />
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
