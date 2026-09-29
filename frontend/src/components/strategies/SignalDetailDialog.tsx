// File: frontend/src/components/strategies/SignalDetailDialog.tsx
import React, { useEffect, useState } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import Dialog from '@/components/common/Dialog';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { strategyApi, toApiError } from '@/services/strategyApi';
import type { ApiError, SignalDetail } from '@/services/strategyApi';
import { FLAG_LABEL, formatPrice, formatR, formatSession, formatUtcTimestamp, LIFECYCLE_LABEL, RELEASE_B_NOTE, STATUS_LABEL } from '@/lib/strategyFormat';
import { Btn } from '@/components/telegram/ui';
import { DirectionTag, StatusChip } from '@/components/strategies/ui';

function Field({ label, children, muted = false }: { label: string; children: React.ReactNode; muted?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={muted ? 'text-right text-xs text-slate-400' : 'text-right font-medium tabular-nums text-slate-800'}>{children}</dd>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      <dl className="divide-y divide-slate-100">{children}</dl>
    </div>
  );
}

function Body({ d }: { d: SignalDetail }) {
  const lc = d.lifecycle;
  const plan = d.trade_plan;
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <Block title="Trade plan">
        <Field label="Entry">{formatPrice(plan.entry_price)}</Field>
        <Field label="Stop (−1R)">{formatPrice(plan.stop_price)}</Field>
        <Field label="Target 1 (+1R)">{formatPrice(plan.target1_price)}</Field>
        <Field label="Target 2 (+2R)">{formatPrice(plan.target2_price)}</Field>
        <Field label="Target 3 (+3R)">{formatPrice(plan.target3_price)}</Field>
        <Field label="ATR">{formatPrice(plan.atr)}</Field>
        <Field label="Risk per share (1R)">
          {formatPrice(plan.risk_per_share)}
          {plan.risk_pct_of_entry !== null && <span className="ml-1 text-xs font-normal text-slate-400">{(plan.risk_pct_of_entry * 100).toFixed(1)}% of entry</span>}
        </Field>
      </Block>

      <Block title="Lifecycle">
        <Field label="State">{LIFECYCLE_LABEL[lc.state]}</Field>
        <Field label="Status">{STATUS_LABEL[lc.status]}</Field>
        <Field label="Outcome" muted={lc.outcome_r === null}>
          {lc.outcome_r === null ? 'Not resolved yet' : <span className={lc.outcome_r > 0 ? 'text-emerald-700' : lc.outcome_r < 0 ? 'text-red-700' : ''}>{formatR(lc.outcome_r)}</span>}
        </Field>
        <Field label="Resolution session" muted={!lc.resolved_date}>{lc.resolved_date ? formatSession(lc.resolved_date) : 'Not resolved yet'}</Field>
        <Field label="Trading sessions held" muted={lc.bars_held === null}>{lc.bars_held ?? 'Counted at resolution'}</Field>
        <Field label="Max adverse excursion" muted={lc.mae_r === null}>{lc.mae_r === null ? 'Not measured yet' : `${lc.mae_r.toFixed(2)}R adverse`}</Field>
        <Field label="Last evaluated session">{formatSession(lc.last_evaluated_date)}</Field>
        <Field label="Forward sessions with prices">{lc.forward_bars_available}</Field>
        <Field label="Resolution flag" muted={!lc.resolution_flag}>{lc.resolution_flag ? FLAG_LABEL[lc.resolution_flag] ?? lc.resolution_flag : 'None'}</Field>
        <Field label="Evaluation flag" muted={!lc.evaluation_flag}>{lc.evaluation_flag ? FLAG_LABEL[lc.evaluation_flag] ?? lc.evaluation_flag : 'None'}</Field>
      </Block>

      <Block title="Context at signal time">
        <Field label="Grade" muted={!d.context.quality_grade}>{d.context.quality_grade ?? 'Not recorded'}</Field>
        <Field label="Sector" muted={!d.context.sector}>{d.context.sector ?? 'Not recorded'}</Field>
        <Field label="Model version" muted={!d.context.model_version}>{d.context.model_version ?? 'Not scored (no validated model)'}</Field>
        <Field label="Feature-set version" muted={!d.context.feature_set_version}>{d.context.feature_set_version ?? RELEASE_B_NOTE}</Field>
      </Block>

      <Block title="Provenance">
        <Field label="Ledger ID">#{d.id}</Field>
        <Field label="Strategy">{d.strategy.display_name} {d.identity.strategy_version}</Field>
        <Field label="Recorded">{formatUtcTimestamp(d.provenance.ledger_created_at)}</Field>
        <Field label="Observation ID" muted={d.provenance.observation_id === null}>{d.provenance.observation_id ?? RELEASE_B_NOTE}</Field>
        <Field label="Feature snapshot ID" muted={d.provenance.feature_snapshot_id === null}>{d.provenance.feature_snapshot_id ?? RELEASE_B_NOTE}</Field>
      </Block>

      <div className="rounded-lg border border-slate-200 p-3 md:col-span-2">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Timeline</h3>
        <ol className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
          {d.timeline.map((e, i) => (
            <li key={`${e.event}-${i}`} className="flex items-center gap-2">
              {i > 0 && <span className="text-slate-300" aria-hidden>→</span>}
              <span className="rounded bg-slate-100 px-2 py-0.5">
                <span className="font-medium text-slate-700">{formatSession(e.date)}</span> · {timelineLabel(e.event)}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function timelineLabel(event: string): string {
  if (event === 'signal') return 'Signal';
  if (event === 'last_evaluated') return 'Last evaluated';
  if (event.startsWith('held:')) return `Held — ${FLAG_LABEL[event.slice(5)] ?? event.slice(5)}`;
  return STATUS_LABEL[event as keyof typeof STATUS_LABEL] ?? event;
}

export default function SignalDetailDialog({ strategyKey, version, signalId, onClose }: {
  strategyKey: string;
  version: string;
  signalId: number | null;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<SignalDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (signalId === null) return;
    let cancelled = false;
    setDetail(null);
    setError(null);
    strategyApi.signal(strategyKey, version, signalId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => { if (!cancelled) setError(toApiError(e)); });
    return () => { cancelled = true; };
  }, [strategyKey, version, signalId, attempt]);

  return (
    <Dialog open={signalId !== null} onClose={onClose} labelledBy="signal-detail-title">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div>
          <h2 id="signal-detail-title" className="flex flex-wrap items-center gap-2 text-lg font-semibold text-slate-800">
            {detail ? <SymbolHoverLink symbol={detail.identity.symbol} /> : 'Signal'}
            {detail && <DirectionTag direction={detail.identity.direction} />}
            {detail && <StatusChip status={detail.lifecycle.status} held={detail.lifecycle.state === 'held'} ambiguous={!!detail.lifecycle.resolution_flag} />}
          </h2>
          {detail && (
            <p className="mt-0.5 text-xs text-slate-500">
              {detail.strategy.display_name} {detail.identity.strategy_version} · signal on {formatSession(detail.identity.signal_date)}
            </p>
          )}
        </div>
        <Btn variant="ghost" size="icon" aria-label="Close" onClick={onClose}><XMarkIcon className="h-5 w-5" /></Btn>
      </div>
      <div className="overflow-y-auto p-4">
        {error ? (
          <ErrorAlert title="Could not load this signal" message={error.message} onRetry={() => setAttempt((n) => n + 1)} />
        ) : detail ? (
          <Body d={detail} />
        ) : (
          <LoadingSpinner className="py-12" />
        )}
      </div>
    </Dialog>
  );
}
