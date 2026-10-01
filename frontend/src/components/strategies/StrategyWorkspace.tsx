'use client';

// File: frontend/src/components/strategies/StrategyWorkspace.tsx
// The Quant Lab strategy control center. One workspace renders ANY registered strategy (from GET /api/strategies, never
// hard-coded): overview + candidate funnel, performance, direction analysis, signals, the Release B candidate explorer,
// data health (with capture health) and research state. /strategies shows the default strategy with a selector;
// /strategies/[strategyKey] pins one. The strategy, version, tab and open candidate live in the URL so a view can be
// linked. All numbers come from the backend; strategy-specific presentation lives in lib/strategyExtensions.ts.
import React, { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { strategyApi, toApiError } from '@/services/strategyApi';
import type { ApiError, CaptureRunsResponse, DataHealth, ResearchSummary, StrategyListItem, StrategySummary } from '@/services/strategyApi';
import { formatSession } from '@/lib/strategyFormat';
import { usePagePerf } from '@/hooks/usePagePerf';
import ErrorAlert from '@/components/common/ErrorAlert';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { SideTabs } from '@/components/telegram/SideTabs';
import type { SideTab } from '@/components/telegram/SideTabs';
import { Btn } from '@/components/telegram/ui';
import { EmptyState } from '@/components/strategies/ui';
import OverviewTab from '@/components/strategies/OverviewTab';
import PerformanceTab from '@/components/strategies/PerformanceTab';
import DirectionsTab from '@/components/strategies/DirectionsTab';
import SignalsTab from '@/components/strategies/SignalsTab';
import CandidatesTab from '@/components/strategies/CandidatesTab';
import DataHealthTab from '@/components/strategies/DataHealthTab';
import CaptureHealthPanel from '@/components/strategies/CaptureHealthPanel';
import ResearchTab from '@/components/strategies/ResearchTab';
import { CaptureBadge } from '@/components/strategies/ResearchUi';

const TABS: SideTab[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'performance', label: 'Performance' },
  { id: 'directions', label: 'Direction analysis' },
  { id: 'signals', label: 'Signals' },
  { id: 'candidates', label: 'Candidates' },
  { id: 'health', label: 'Data health' },
  { id: 'research', label: 'Research / ML' },
];
type Tab = 'overview' | 'performance' | 'directions' | 'signals' | 'candidates' | 'health' | 'research';
const isTab = (t: string | null): t is Tab => !!t && TABS.some((x) => x.id === t);

function StrategyHeader({ strategy, summary, research, strategies, pinned, onSelect, onRefresh, refreshing }: {
  strategy: StrategyListItem;
  summary: StrategySummary | null;
  research: ResearchSummary | null;
  strategies: StrategyListItem[];
  pinned: boolean;
  onSelect: (s: StrategyListItem) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const tracking = summary?.tracking ?? null;
  const live = (tracking?.tracking_status ?? strategy.tracking.tracking_status) === 'tracking';

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
            {pinned && <Link href="/strategies" className="mr-2 normal-case tracking-normal text-slate-500 hover:underline">← All strategies</Link>}
            Strategy intelligence
          </p>
          <h1 className="mt-0.5 text-2xl font-bold uppercase tracking-wide text-slate-800">{strategy.display_name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-700">Version {strategy.version}</span>
            <span className={live ? 'inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700' : 'inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500'}>
              {live && <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />}
              {live ? 'LIVE' : 'No signals yet'}
            </span>
            {research?.latest_session && (
              <span className="inline-flex items-center gap-1.5 text-xs text-slate-500" data-testid="header-capture">
                Capture {formatSession(research.latest_session.session_date)} <CaptureBadge status={research.latest_session.capture_status} />
              </span>
            )}
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Tracking since <span className="font-medium text-slate-700">{formatSession(tracking?.first_tracked_session ?? strategy.tracking.first_tracked_session)}</span>
            <span className="mx-1.5 text-slate-300">·</span>
            Latest signal session <span className="font-medium text-slate-700">{formatSession(tracking?.latest_tracked_session ?? strategy.tracking.latest_tracked_session)}</span>
            {summary?.reference_session && (
              <>
                <span className="mx-1.5 text-slate-300">·</span>
                Latest market session <span className="font-medium text-slate-700">{formatSession(summary.reference_session)}</span>
              </>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-end gap-2">
          {strategies.length > 1 && (
            <label className="flex flex-col gap-1 text-xs text-slate-500">
              Strategy
              <select
                className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm text-slate-700 focus:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300"
                value={`${strategy.key}/${strategy.version}`}
                onChange={(e) => {
                  const next = strategies.find((s) => `${s.key}/${s.version}` === e.target.value);
                  if (next) onSelect(next);
                }}
              >
                {strategies.map((s) => <option key={`${s.key}/${s.version}`} value={`${s.key}/${s.version}`}>{s.display_name} {s.version}</option>)}
              </select>
            </label>
          )}
          <Btn variant="primary" size="sm" onClick={onRefresh} disabled={refreshing}>{refreshing ? 'Refreshing…' : 'Refresh'}</Btn>
        </div>
      </div>
    </div>
  );
}

const sameStrategy = (x: { strategy: { key: string; version: string } } | null, s: StrategyListItem) =>
  !!x && x.strategy.key === s.key && x.strategy.version === s.version;

function WorkspaceView({ pinnedKey, onNavigateStrategy }: { pinnedKey?: string; onNavigateStrategy?: (s: StrategyListItem) => void }) {
  const { markLoaded } = usePagePerf('/strategies');
  const pathname = usePathname();
  const params = useSearchParams();

  const [strategies, setStrategies] = useState<StrategyListItem[] | null>(null);
  const [listError, setListError] = useState<ApiError | null>(null);
  const [summary, setSummary] = useState<StrategySummary | null>(null);
  const [summaryError, setSummaryError] = useState<ApiError | null>(null);
  const [health, setHealth] = useState<DataHealth | null>(null);
  const [healthError, setHealthError] = useState<ApiError | null>(null);
  const [research, setResearch] = useState<ResearchSummary | null>(null);
  const [researchError, setResearchError] = useState<string | null>(null);
  const [capture, setCapture] = useState<CaptureRunsResponse | null>(null);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  const tabParam = params.get('tab');
  const tab: Tab = isTab(tabParam) ? tabParam : 'overview';
  const candidateParam = Number(params.get('candidate'));
  const candidateId = Number.isInteger(candidateParam) && candidateParam > 0 ? candidateParam : null;

  const loadList = useCallback(async () => {
    try {
      setStrategies(await strategyApi.list());
      setListError(null);
    } catch (e) {
      setListError(toApiError(e));
    }
  }, []);

  useEffect(() => { loadList(); }, [loadList]);

  // Pinned route: the key is the path segment, the version comes from ?version= (else the tracking one). Never a
  // fallback to a different strategy -- an unknown key is reported, not silently replaced.
  const unknownPinned = !!pinnedKey && !!strategies && strategies.length > 0 && !strategies.some((s) => s.key === pinnedKey);
  const selected = useMemo(() => {
    if (!strategies || strategies.length === 0) return null;
    const key = pinnedKey ?? params.get('strategy');
    const version = params.get('version');
    if (pinnedKey) {
      const ofKey = strategies.filter((s) => s.key === pinnedKey);
      return ofKey.find((s) => s.version === version) ?? ofKey.find((s) => s.tracking.tracking_status === 'tracking') ?? ofKey[0] ?? null;
    }
    return strategies.find((s) => s.key === key && s.version === version)
      ?? strategies.find((s) => s.key === key)
      ?? strategies.find((s) => s.tracking.tracking_status === 'tracking')
      ?? strategies[0];
  }, [strategies, params, pinnedKey]);

  // A URL-only change: window.history.replaceState keeps useSearchParams in sync without a server round trip
  // (router.replace would refetch the route's payload on every tab click).
  const setQuery = useCallback((next: { strategy?: StrategyListItem; tab?: Tab; candidate?: number | null }) => {
    const q = new URLSearchParams(params.toString());
    if (next.strategy) {
      if (!pinnedKey) q.set('strategy', next.strategy.key);
      q.set('version', next.strategy.version);
    }
    if (next.tab) { q.set('tab', next.tab); q.delete('candidate'); }
    if (next.candidate !== undefined) { if (next.candidate === null) q.delete('candidate'); else q.set('candidate', String(next.candidate)); }
    window.history.replaceState(null, '', `${pathname}?${q.toString()}`);
  }, [params, pathname, pinnedKey]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    // Research is optional: an older backend (no /research) or a failure must never take the ledger views down.
    Promise.allSettled([
      strategyApi.summary(selected.key, selected.version),
      strategyApi.dataHealth(selected.key, selected.version),
      (async () => strategyApi.researchSummary(selected.key, selected.version))(),
    ])
      .then(([s, h, r]) => {
        if (cancelled) return;
        if (s.status === 'fulfilled') { setSummary(s.value); setSummaryError(null); } else { setSummary(null); setSummaryError(toApiError(s.reason)); }
        if (h.status === 'fulfilled') { setHealth(h.value); setHealthError(null); } else { setHealth(null); setHealthError(toApiError(h.reason)); }
        if (r.status === 'fulfilled' && r.value) { setResearch(r.value); setResearchError(null); } else { setResearch(null); setResearchError(r.status === 'rejected' ? toApiError(r.reason).message : 'No response.'); }
        markLoaded();
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected, refreshTick, markLoaded]);

  const onHealthTab = tab === 'health';
  useEffect(() => {
    if (!selected || !onHealthTab) return;
    let cancelled = false;
    setCapture(null);
    setCaptureError(null);
    (async () => strategyApi.captureRuns(selected.key, selected.version, 30))()
      .then((d) => { if (!cancelled) setCapture(d); })
      .catch((e) => { if (!cancelled) setCaptureError(toApiError(e).message); });
    return () => { cancelled = true; };
  }, [selected, onHealthTab, refreshTick]);

  const refresh = useCallback(() => {
    loadList();
    setRefreshTick((n) => n + 1);
  }, [loadList]);

  if (listError && !strategies) {
    return <ErrorAlert title="Could not load strategies" message={listError.message} onRetry={loadList} />;
  }
  if (!strategies) return <LoadingSpinner className="py-24" />;
  if (unknownPinned || !selected) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        {unknownPinned
          ? <EmptyState title="Unknown strategy">No strategy “{pinnedKey}” is registered. <Link href="/strategies" className="font-medium underline">See all strategies</Link>.</EmptyState>
          : <EmptyState title="No strategy is registered yet">Strategies appear here once they are registered in the strategies table.</EmptyState>}
      </div>
    );
  }

  // After switching strategy, a response for the previous one must never be shown under the new header.
  const summaryMatches = summary && sameStrategy(summary, selected);
  const currentHealth = health && sameStrategy(health, selected) ? health : null;
  const currentResearch = research && sameStrategy(research, selected) ? research : null;
  const currentCapture = capture && sameStrategy(capture, selected) ? capture : null;
  const researchProblem = currentResearch ? null : researchError;

  return (
    <div className="space-y-4">
      <StrategyHeader strategy={selected} summary={summaryMatches ? summary : null} research={currentResearch} strategies={strategies}
                      pinned={!!pinnedKey}
                      onSelect={(s) => (onNavigateStrategy ? onNavigateStrategy(s) : setQuery({ strategy: s }))}
                      onRefresh={refresh} refreshing={loading} />
      <div className="flex flex-col gap-4 md:flex-row">
        <SideTabs tabs={TABS} active={tab} onChange={(id) => setQuery({ tab: id as Tab })} />
        <div className="min-w-0 flex-1">
          {tab === 'signals' ? (
            <SignalsTab key={`${selected.key}/${selected.version}`} strategyKey={selected.key} version={selected.version} />
          ) : tab === 'candidates' ? (
            <CandidatesTab key={`${selected.key}/${selected.version}`} strategyKey={selected.key} version={selected.version}
                           strategyName={selected.display_name} candidateId={candidateId} onCandidateChange={(id) => setQuery({ candidate: id })} />
          ) : tab === 'health' ? (
            <div className="space-y-4">
              <CaptureHealthPanel data={currentCapture} error={captureError} onRetry={refresh} />
              {currentHealth ? <DataHealthTab health={currentHealth} />
                : healthError && !loading ? <ErrorAlert title="Could not load data health" message={healthError.message} onRetry={refresh} />
                : <LoadingSpinner className="py-16" />}
            </div>
          ) : summaryError && !summaryMatches ? (
            <ErrorAlert title="Could not load this strategy" message={summaryError.message} onRetry={refresh} />
          ) : !summaryMatches ? (
            <LoadingSpinner className="py-16" />
          ) : tab === 'overview' ? (
            <OverviewTab summary={summary} health={currentHealth} research={currentResearch} researchError={researchProblem}
                         onOpenTab={(t) => setQuery({ tab: t })} />
          ) : tab === 'performance' ? (
            <PerformanceTab summary={summary} />
          ) : tab === 'directions' ? (
            <DirectionsTab summary={summary} />
          ) : (
            <ResearchTab capabilities={summary.capabilities} coverage={currentHealth?.coverage ?? null} research={currentResearch} researchError={researchProblem} />
          )}
        </div>
      </div>
    </div>
  );
}

export default function StrategyWorkspace({ pinnedKey, onNavigateStrategy }: { pinnedKey?: string; onNavigateStrategy?: (s: StrategyListItem) => void }) {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto max-w-7xl p-4">
        <Suspense fallback={<LoadingSpinner className="py-24" />}>
          <WorkspaceView pinnedKey={pinnedKey} onNavigateStrategy={onNavigateStrategy} />
        </Suspense>
      </div>
    </div>
  );
}
