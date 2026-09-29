// File: frontend/src/components/strategies/ResearchTab.tsx
// What research data exists now vs. what Release B adds, straight from the backend's capability flags and lineage
// coverage -- an unavailable capability reads "Not collected — Release B", never a numeric 0.
import React from 'react';
import { clsx } from 'clsx';
import { CheckCircleIcon, ClockIcon } from '@heroicons/react/24/outline';
import type { Capabilities, CoverageEntry } from '@/services/strategyApi';
import { CAPABILITY_LABEL, formatCount, humanize, RELEASE_B_NOTE } from '@/lib/strategyFormat';
import { Section } from '@/components/strategies/ui';

const COVERAGE_LABEL: Record<string, string> = {
  strategy_version: 'Strategy version',
  model_version: 'Model version',
  feature_set_version: 'Feature-set version',
  observation_id: 'Candidate observation link',
  feature_snapshot_id: 'Feature snapshot link',
};

function CoverageRow({ name, c }: { name: string; c: CoverageEntry }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2 text-sm">
      <span className="text-slate-700">{COVERAGE_LABEL[name] ?? humanize(name)}</span>
      {c.collected ? (
        <span className="tabular-nums font-semibold text-slate-800">
          {formatCount(c.count)} <span className="font-normal text-slate-400">/ {formatCount(c.total)}</span>
        </span>
      ) : (
        <span className="text-xs text-slate-400">{c.release ? `Not collected — Release ${c.release}` : RELEASE_B_NOTE}</span>
      )}
    </li>
  );
}

export default function ResearchTab({ capabilities, coverage }: { capabilities: Capabilities; coverage: Record<string, CoverageEntry> | null }) {
  const entries = Object.entries(capabilities);
  const available = entries.filter(([, c]) => c.available);
  const pending = entries.filter(([, c]) => !c.available);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Lineage coverage" description="How many recorded signals carry each research identifier.">
          {coverage ? (
            <ul className="divide-y divide-slate-100">
              {Object.entries(coverage).map(([k, c]) => <CoverageRow key={k} name={k} c={c} />)}
            </ul>
          ) : (
            <p className="text-xs text-slate-400">Coverage is unavailable right now (data health could not be loaded).</p>
          )}
          {coverage?.model_version && coverage.model_version.collected && coverage.model_version.count === 0 && (
            <p className="mt-2 text-xs text-slate-500">No validated model is scoring live signals yet, so no signal carries a model version.</p>
          )}
        </Section>

        <Section title="Research capabilities" description="What the ledger collects today, and what arrives with Release B.">
          <ul className="space-y-1.5">
            {[...available, ...pending].map(([k, c]) => (
              <li key={k} className="flex items-start justify-between gap-3 text-sm">
                <span className={clsx('flex items-center gap-2', c.available ? 'text-slate-700' : 'text-slate-500')}>
                  {c.available
                    ? <CheckCircleIcon className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden />
                    : <ClockIcon className="h-4 w-4 shrink-0 text-slate-400" aria-hidden />}
                  {CAPABILITY_LABEL[k] ?? humanize(k)}
                </span>
                <span className="text-right text-xs text-slate-400">
                  {c.available
                    ? `Collected${c.release ? ` · Release ${c.release}` : ''}`
                    : c.release ? `Not collected — Release ${c.release}` : 'Not persisted yet'}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      </div>

      <Section title="Conditional analysis" description="Coming with Release B.">
        <p className="text-sm text-slate-600">
          Questions like “bullish breakouts in a strong sector with RVOL above 1.5 and top-decile relative strength — what was the average R, and over how many
          signals?” need a feature snapshot frozen at signal time. Release A records the signal, its trade plan and its outcome; Release B adds the T0 snapshot
          and candidate observations those breakdowns are built from. Nothing here is estimated in the meantime.
        </p>
      </Section>
    </div>
  );
}
