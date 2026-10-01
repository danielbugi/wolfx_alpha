// File: frontend/src/components/strategies/ResearchFunnel.tsx
// The Quant Lab funnel for the latest captured session -- Evaluated → Candidates → Guard Passed → Qualified/Ranked →
// Selected → Ledger Signals -- plus the capture status line. Every figure is the backend's; a stage it did not
// measure shows "—" and why, never 0.
import React from 'react';
import { clsx } from 'clsx';
import { ChevronRightIcon } from '@heroicons/react/24/outline';
import type { ResearchSummary } from '@/services/strategyApi';
import { CAPTURE_STATUS_META, describeCount, formatSession } from '@/lib/strategyFormat';
import { Btn } from '@/components/telegram/ui';
import { Section } from '@/components/strategies/ui';
import { AvailabilityNotice, CaptureBadge, CountCard, RateCard } from '@/components/strategies/ResearchUi';

export default function ResearchFunnel({ research, onOpenTab }: {
  research: ResearchSummary;
  onOpenTab?: (tab: 'candidates' | 'health' | 'research') => void;
}) {
  const ok = research.availability.state === 'ok';
  const session = research.latest_session;
  return (
    <Section
      title="Candidate funnel"
      description={ok && research.funnel.session_date
        ? `Latest captured session ${formatSession(research.funnel.session_date)}. Candidates are recorded before the universe guards drop anything.`
        : 'From the strategy’s own detection to the signal ledger, one captured session at a time.'}
      actions={onOpenTab && ok ? <Btn size="sm" variant="ghost" onClick={() => onOpenTab('candidates')}>Explore candidates →</Btn> : undefined}
    >
      <div className="space-y-4">
        {!ok && <AvailabilityNotice availability={research.availability} />}

        <ol className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:flex xl:items-stretch xl:gap-1" aria-label="Candidate funnel">
          {research.funnel.stages.map((stage, i) => {
            const d = describeCount(stage, { reasonFirst: ok });
            return (
              <React.Fragment key={stage.key}>
                {i > 0 && <ChevronRightIcon className="hidden h-4 w-4 shrink-0 self-center text-slate-300 xl:block" aria-hidden />}
                <li data-stage={stage.key} data-count-state={stage.state}
                    className={clsx('min-w-0 rounded-lg border p-3 xl:flex-1', d.tone === 'ok' ? 'border-slate-200 bg-white' : 'border-dashed border-slate-200 bg-slate-50')}>
                  <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{stage.label}</p>
                  <p className={clsx('mt-1 text-2xl font-semibold tabular-nums', d.tone === 'ok' ? 'text-slate-800' : 'text-slate-300')}>{d.text}</p>
                  {d.note && <p className="mt-0.5 text-xs text-slate-400">{d.note}</p>}
                  {stage.linked !== undefined && d.tone === 'ok' && (
                    <p className="mt-0.5 text-xs text-slate-500">{stage.linked.toLocaleString('en-US')} linked to a candidate</p>
                  )}
                </li>
              </React.Fragment>
            );
          })}
        </ol>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 rounded-lg bg-slate-50 px-3 py-2 text-xs sm:grid-cols-3 lg:grid-cols-4" data-testid="capture-status-line">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Latest captured session</dt>
            <dd className="font-medium text-slate-800">{session ? formatSession(session.session_date) : '—'}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Capture status</dt>
            <dd>{session ? <CaptureBadge status={session.capture_status} /> : <span className="text-slate-400">{research.availability.state === 'not_available' ? CAPTURE_STATUS_META.not_available.label.toLowerCase() : 'not run'}</span>}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Feature set</dt>
            <dd className="font-mono font-medium text-slate-800">{research.feature_set.current ?? '—'}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-slate-500">Observations / snapshots</dt>
            <dd className="font-medium tabular-nums text-slate-800">{describeCount(research.cards.observations).text} / {describeCount(research.cards.snapshots).text}</dd>
          </div>
        </dl>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <RateCard label="Guard pass rate" metric={research.rates.guard_pass_rate} />
          <RateCard label="Selected rate" metric={research.rates.selected_rate} />
          <CountCard label="Candidate observations" count={research.cards.observations} hint="all captured sessions" />
          <CountCard label="Feature snapshots" count={research.cards.snapshots} hint="all captured sessions" />
        </div>
      </div>
    </Section>
  );
}
