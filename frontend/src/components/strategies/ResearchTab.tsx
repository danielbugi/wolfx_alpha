// File: frontend/src/components/strategies/ResearchTab.tsx
// What research data exists now vs. what Release B adds, straight from the backend's capability flags, lineage
// coverage and (when present) the research summary -- an unavailable item reads "Not collected — Release B", never a
// numeric 0.
import React from 'react';
import { clsx } from 'clsx';
import { CheckCircleIcon, ClockIcon } from '@heroicons/react/24/outline';
import type { Capabilities, CoverageEntry, ResearchSummary } from '@/services/strategyApi';
import { CAPABILITY_LABEL, describeCount, formatCount, formatSession, humanize, RELEASE_B_NOTE } from '@/lib/strategyFormat';
import { Section } from '@/components/strategies/ui';
import { AvailabilityNotice, CountCard, RateCard, ResearchLoadFailed } from '@/components/strategies/ResearchUi';

const COVERAGE_LABEL: Record<string, string> = {
  strategy_version: 'Strategy version',
  model_version: 'Model version',
  feature_set_version: 'Feature-set version',
  observation_id: 'Candidate observation link',
  feature_snapshot_id: 'Feature snapshot link',
};

// Once capture is live these two static flags (always "not collected" in the ledger endpoints) would be stale, so the
// research summary's dynamic cards replace them.
const CAPTURED_CAPABILITIES = new Set(['candidate_observations', 'feature_snapshots']);
const CAPTURED_COVERAGE = new Set(['observation_id', 'feature_snapshot_id']);

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

function FeatureSetBox({ research }: { research: ResearchSummary }) {
  const { current, registered } = research.feature_set;
  const cur = registered.find((f) => f.version === current) ?? null;
  return (
    <Section title="Current feature set" description="The versioned manifest every T0 snapshot is frozen against — provenance, not a model.">
      {current ? (
        <div className="space-y-2" data-testid="feature-set">
          <p className="font-mono text-lg font-semibold text-slate-800">{current}</p>
          {cur && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
              <div><dt className="text-slate-500">Features</dt><dd className="font-medium text-slate-800">{cur.feature_count === null ? '—' : formatCount(cur.feature_count)}</dd></div>
              <div><dt className="text-slate-500">Extends</dt><dd className="font-medium text-slate-800">{cur.extends ?? '—'}</dd></div>
              <div><dt className="text-slate-500">Manifest hash</dt><dd className="font-mono font-medium text-slate-800">{cur.manifest_hash.slice(0, 12)}</dd></div>
              <div><dt className="text-slate-500">Registered</dt><dd className="font-medium text-slate-800">{formatSession(cur.registered_at.slice(0, 10))}</dd></div>
            </dl>
          )}
          {cur?.description && <p className="text-xs text-slate-500">{cur.description}</p>}
          {registered.length > 1 && <p className="text-xs text-slate-400">Registered: {registered.map((f) => f.version).join(', ')}</p>}
        </div>
      ) : (
        <p className="text-xs text-slate-400" data-testid="feature-set">{RELEASE_B_NOTE}</p>
      )}
    </Section>
  );
}

function ForwardOutcomes({ research }: { research: ResearchSummary }) {
  return (
    <Section title="Forward outcomes" description="Not collected yet.">
      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4" data-testid="forward-outcomes" data-state={research.forward_outcomes.state}>
        <p className="text-sm font-semibold text-slate-700">Forward Outcomes — Not collected yet</p>
        <p className="mt-1 text-xs text-slate-500">
          {research.forward_outcomes.reason ?? 'Forward-outcome capture (migration 23, feature set fwd_v1) does not exist yet.'}
          {' '}Conditional questions — “average R for bullish breakouts with RVOL above 1.5, over how many candidates?” — are answered here only once
          outcomes are recorded against these snapshots. Nothing is estimated in the meantime.
        </p>
      </div>
    </Section>
  );
}

function ResearchCards({ research }: { research: ResearchSummary }) {
  const c = research.cards;
  const r = research.rates;
  return (
    <Section
      title="Captured research data"
      description={research.availability.state === 'ok' ? 'Counts across every captured session. Capture records candidates and market state — it makes no performance claim.' : undefined}
    >
      <div className="space-y-3">
        {research.availability.state !== 'ok' && <AvailabilityNotice availability={research.availability} />}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <CountCard label="Candidate observations" count={c.observations} />
          <CountCard label="Feature snapshots" count={c.snapshots} />
          <CountCard label="Bullish" count={c.bullish} />
          <CountCard label="Bearish" count={c.bearish} />
          <CountCard label="Guard passed" count={c.guard_passed} />
          <CountCard label="Guard rejected" count={c.guard_rejected} />
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <RateCard label="Missing feature rate" metric={r.missing_feature_rate} />
          <RateCard label="Snapshot coverage" metric={r.snapshot_coverage} />
          <RateCard label="Capture coverage" metric={r.capture_coverage} />
        </div>
      </div>
    </Section>
  );
}

export default function ResearchTab({ capabilities, coverage, research = null, researchError = null }: {
  capabilities: Capabilities;
  coverage: Record<string, CoverageEntry> | null;
  research?: ResearchSummary | null;
  researchError?: string | null;
}) {
  const live = !!research && research.availability.state === 'ok';
  const entries = Object.entries(capabilities).filter(([k]) => !(live && CAPTURED_CAPABILITIES.has(k)));
  const available = entries.filter(([, c]) => c.available);
  const pending = entries.filter(([, c]) => !c.available);
  const coverageEntries = coverage ? Object.entries(coverage).filter(([k]) => !(live && CAPTURED_COVERAGE.has(k))) : null;
  const linked = research?.funnel.stages.find((s) => s.key === 'ledger_signals');
  return (
    <div className="space-y-4">
      {research && <ResearchCards research={research} />}
      {!research && researchError && <ResearchLoadFailed message={researchError} />}
      {research && <FeatureSetBox research={research} />}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Lineage coverage" description="How many recorded signals carry each research identifier.">
          {coverageEntries ? (
            <ul className="divide-y divide-slate-100">
              {coverageEntries.map(([k, c]) => <CoverageRow key={k} name={k} c={c} />)}
              {live && linked && (
                <li className="flex items-center justify-between gap-3 py-2 text-sm" data-testid="lineage-linked">
                  <span className="text-slate-700">Ledger signals linked to a candidate{research?.funnel.session_date ? ` (${formatSession(research.funnel.session_date)})` : ''}</span>
                  {linked.state === 'ok' && linked.value !== null && linked.linked !== undefined
                    ? <span className="tabular-nums font-semibold text-slate-800">{formatCount(linked.linked)} <span className="font-normal text-slate-400">/ {formatCount(linked.value)}</span></span>
                    : <span className="text-xs text-slate-400">{describeCount(linked).note || '—'}</span>}
                </li>
              )}
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

      {research ? <ForwardOutcomes research={research} /> : (
        <Section title="Conditional analysis" description="Coming with Release B.">
          <p className="text-sm text-slate-600">
            Questions like “bullish breakouts in a strong sector with RVOL above 1.5 and top-decile relative strength — what was the average R, and over how many
            signals?” need a feature snapshot frozen at signal time. Release A records the signal, its trade plan and its outcome; Release B adds the T0 snapshot
            and candidate observations those breakdowns are built from. Nothing here is estimated in the meantime.
          </p>
        </Section>
      )}
    </div>
  );
}
