'use client';

// File: frontend/src/app/strategies/page.tsx
// Strategy Intelligence: every registered strategy (from GET /api/strategies, never hard-coded), with overview,
// performance, direction analysis, the signal explorer, data health and research state. The selected strategy and tab
// live in the URL (?strategy=&version=&tab=) so a view can be linked. All numbers come from the backend.
import React, { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { strategyApi, toApiError } from '@/services/strategyApi';
import type { ApiError, DataHealth, StrategyListItem, StrategySummary } from '@/services/strategyApi';
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
import DataHealthTab from '@/components/strategies/DataHealthTab';
import ResearchTab from '@/components/strategies/ResearchTab';

const TABS: SideTab[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'performance', label: 'Performance' },
  { id: 'directions', label: 'Direction analysis' },
  { id: 'signals', label: 'Signals' },
  { id: 'health', label: 'Data health' },
  { id: 'research', label: 'Research / ML' },
];
type Tab = 'overview' | 'performance' | 'directions' | 'signals' | 'health' | 'research';
const isTab = (t: string | null): t is Tab => !!t && TABS.some((x) => x.id === t);

function StrategyHeader({ strategy, summary, strategies, onSelect, onRefresh, refreshing }: {
  strategy: StrategyListItem;
  summary: StrategySummary | null;
  strategies: StrategyListItem[];
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
          <p className="text-xs font-medium uppercase tracking-wider text-slate-400">Strategy intelligence</p>
          <h1 className="mt-0.5 text-2xl font-bold uppercase tracking-wide text-slate-800">{strategy.display_name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-slate-700">Version {strategy.version}</span>
            <span className={live ? 'inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700' : 'inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500'}>
              {live && <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />}
              {live ? 'LIVE' : 'No signals yet'}
            </span>
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

function StrategiesView() {
  const { markLoaded } = usePagePerf('/strategies');
  const pathname = usePathname();
  const params = useSearchParams();

  const [strategies, setStrategies] = useState<StrategyListItem[] | null>(null);
  const [listError, setListError] = useState<ApiError | null>(null);
  const [summary, setSummary] = useState<StrategySummary | null>(null);
  const [summaryError, setSummaryError] = useState<ApiError | null>(null);
  const [health, setHealth] = useState<DataHealth | null>(null);
  const [healthError, setHealthError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  const tabParam = params.get('tab');
  const tab: Tab = isTab(tabParam) ? tabParam : 'overview';

  const loadList = useCallback(async () => {
    try {
      setStrategies(await strategyApi.list());
      setListError(null);
    } catch (e) {
      setListError(toApiError(e));
    }
  }, []);

  useEffect(() => { loadList(); }, [loadList]);

  const selected = useMemo(() => {
    if (!strategies || strategies.length === 0) return null;
    const key = params.get('strategy');
    const version = params.get('version');
    return strategies.find((s) => s.key === key && s.version === version)
      ?? strategies.find((s) => s.key === key)
      ?? strategies.find((s) => s.tracking.tracking_status === 'tracking')
      ?? strategies[0];
  }, [strategies, params]);

  // A URL-only change: window.history.replaceState keeps useSearchParams in sync without a server round trip
  // (router.replace would refetch the route's payload on every tab click).
  const setQuery = useCallback((next: { strategy?: StrategyListItem; tab?: Tab }) => {
    const q = new URLSearchParams(params.toString());
    if (next.strategy) {
      q.set('strategy', next.strategy.key);
      q.set('version', next.strategy.version);
    }
    if (next.tab) q.set('tab', next.tab);
    window.history.replaceState(null, '', `${pathname}?${q.toString()}`);
  }, [params, pathname]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([strategyApi.summary(selected.key, selected.version), strategyApi.dataHealth(selected.key, selected.version)])
      .then(([s, h]) => {
        if (cancelled) return;
        if (s.status === 'fulfilled') { setSummary(s.value); setSummaryError(null); } else { setSummary(null); setSummaryError(toApiError(s.reason)); }
        if (h.status === 'fulfilled') { setHealth(h.value); setHealthError(null); } else { setHealth(null); setHealthError(toApiError(h.reason)); }
        markLoaded();
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected, refreshTick, markLoaded]);

  const refresh = useCallback(() => {
    loadList();
    setRefreshTick((n) => n + 1);
  }, [loadList]);

  if (listError && !strategies) {
    return <ErrorAlert title="Could not load strategies" message={listError.message} onRetry={loadList} />;
  }
  if (!strategies) return <LoadingSpinner className="py-24" />;
  if (!selected) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <EmptyState title="No strategy is registered yet">Strategies appear here once they are registered in the strategies table.</EmptyState>
      </div>
    );
  }

  // After switching strategy, a response for the previous one must never be shown under the new header.
  const summaryMatches = summary && summary.strategy.key === selected.key && summary.strategy.version === selected.version;
  const currentHealth = health && health.strategy.key === selected.key && health.strategy.version === selected.version ? health : null;

  return (
    <div className="space-y-4">
      <StrategyHeader strategy={selected} summary={summaryMatches ? summary : null} strategies={strategies}
                      onSelect={(s) => setQuery({ strategy: s })} onRefresh={refresh} refreshing={loading} />
      <div className="flex flex-col gap-4 md:flex-row">
        <SideTabs tabs={TABS} active={tab} onChange={(id) => setQuery({ tab: id as Tab })} />
        <div className="min-w-0 flex-1">
          {tab === 'signals' ? (
            <SignalsTab key={`${selected.key}/${selected.version}`} strategyKey={selected.key} version={selected.version} />
          ) : tab === 'health' ? (
            currentHealth ? <DataHealthTab health={currentHealth} />
              : healthError && !loading ? <ErrorAlert title="Could not load data health" message={healthError.message} onRetry={refresh} />
              : <LoadingSpinner className="py-16" />
          ) : summaryError && !summaryMatches ? (
            <ErrorAlert title="Could not load this strategy" message={summaryError.message} onRetry={refresh} />
          ) : !summaryMatches ? (
            <LoadingSpinner className="py-16" />
          ) : tab === 'overview' ? (
            <OverviewTab summary={summary} health={currentHealth} onOpenTab={(t) => setQuery({ tab: t })} />
          ) : tab === 'performance' ? (
            <PerformanceTab summary={summary} />
          ) : tab === 'directions' ? (
            <DirectionsTab summary={summary} />
          ) : (
            <ResearchTab capabilities={summary.capabilities} coverage={currentHealth?.coverage ?? null} />
          )}
        </div>
      </div>
    </div>
  );
}

export default function StrategiesPage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto max-w-7xl p-4">
        <Suspense fallback={<LoadingSpinner className="py-24" />}>
          <StrategiesView />
        </Suspense>
      </div>
    </div>
  );
}
