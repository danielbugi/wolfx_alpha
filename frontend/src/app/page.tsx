'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Card,
  CardBody,
  CardHeader,
  Button,
  Chip,
} from '@nextui-org/react';
import Link from 'next/link';
import { ResponsiveContainer, Treemap } from 'recharts';
import { apiClient, screenerApi, marketApi, MainPageData, MarketOverview, MarketIndexEntry, SectorHistoryEntry } from '@/services/api';
import { usePagePerf } from '@/hooks/usePagePerf';
import { useTopProgress } from '@/lib/topProgress';
import { DashboardSkeleton } from '@/components/ui/Skeleton';
import ErrorAlert from '@/components/common/ErrorAlert';
import MarketIndicesStrip from '@/components/dashboard/MarketIndicesStrip';
import SectorTrendChart from '@/components/dashboard/SectorTrendChart';
import MarketMovers from '@/components/dashboard/MarketMovers';
import WatchlistCard from '@/components/dashboard/WatchlistCard';
import EarningsCard from '@/components/earnings/EarningsCard';
import SummaryTiles from '@/components/dashboard/SummaryTiles';
import CommoditiesCard, { COMMODITY_SYMBOLS } from '@/components/dashboard/CommoditiesCard';
import SegmentedControl from '@/components/ui/SegmentedControl';
import DataStamp from '@/components/common/DataStamp';
import MarketIntelligencePanel from '@/components/marketIntel/MarketIntelligencePanel';
import { ResolvedTheme, useResolvedTheme } from '@/lib/theme';

function timeAgo(date: Date | null): string {
  if (!date) return '—';
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

// Same thresholds in both themes; the dark set is deeper so the heatmap doesn't glare on a dark page.
const SECTOR_PALETTE = {
  light: { strongUp: '#34c759', up: '#b4ecc3', down: '#ffc2bd', strongDown: '#ff6961', label: '#1d1d1f', sub: '#3a3a3c', gap: '#fff' },
  dark: { strongUp: '#248a3d', up: '#1f4a2b', down: '#5a2826', strongDown: '#c0392f', label: '#f5f5f7', sub: '#d1d1d6', gap: '#1c1c1e' },
} as const;

function sectorColor(perf: number, theme: ResolvedTheme): string {
  const p = SECTOR_PALETTE[theme];
  if (perf >= 1) return p.strongUp;
  if (perf >= 0) return p.up;
  if (perf >= -1) return p.down;
  return p.strongDown;
}

const SECTOR_ABBR: Record<string, string> = {
  Technology: 'Tech',
  Healthcare: 'Health',
  'Financial Services': 'Financials',
  'Consumer Cyclical': 'Cons Cycl',
  'Consumer Defensive': 'Cons Def',
  'Communication Services': 'Comm Svcs',
  'Real Estate': 'Real Est',
  'Basic Materials': 'Materials',
};

/** Shrink/abbreviate a sector name to fit inside a treemap box of the given
 * pixel width, so every box shows some label instead of hiding small ones. */
function fitSectorLabel(name: string, width: number, fontSize: number): string {
  const short = SECTOR_ABBR[name] || name;
  const maxChars = Math.max(3, Math.floor(width / (fontSize * 0.62)));
  if (short.length <= maxChars) return short;
  return `${short.slice(0, Math.max(3, maxChars - 1))}…`;
}

export default function DashboardPage() {
  const { markLoaded } = usePagePerf('/');
  const theme = useResolvedTheme();
  const palette = SECTOR_PALETTE[theme];
  const { start: startProgress, done: doneProgress } = useTopProgress();

  // `initialLoading` gates the full-page state (true only until the first
  // successful fetch ever lands). Every fetch after that is a background
  // `refreshing` pass that keeps the current page mounted — see the UX audit,
  // finding F1: this used to reuse the same flag and blank the whole
  // dashboard on every 5-minute auto-refresh.
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData] = useState<MainPageData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);
  const [, forceTick] = useState(0);
  const hasLoadedOnceRef = useRef(false);

  const [marketOverview, setMarketOverview] = useState<MarketOverview | null>(null);
  const [marketOverviewError, setMarketOverviewError] = useState(false);

  const [marketIndices, setMarketIndices] = useState<MarketIndexEntry[]>([]);
  const [marketIndicesLoading, setMarketIndicesLoading] = useState(true);
  const [marketIndicesError, setMarketIndicesError] = useState(false);

  const [sectorView, setSectorView] = useState<'heatmap' | 'trend'>('heatmap');
  const [sectorHistory, setSectorHistory] = useState<SectorHistoryEntry[]>([]);

  const [selectedSector, setSelectedSector] = useState<string | null>(null);

  // The Momentum Board and Momentum Leaders moved to the internal /momentum page (signal output, not product content).

  const fetchData = useCallback(async () => {
    const isFirstLoad = !hasLoadedOnceRef.current;
    try {
      if (isFirstLoad) setInitialLoading(true);
      else {
        setRefreshing(true);
        startProgress();
      }
      setError(null);
      const response = await apiClient.get<MainPageData>('/api/dashboard/main-page-data');
      setData(response.data);
      setLastUpdatedAt(new Date());
      hasLoadedOnceRef.current = true;
      markLoaded();
    } catch (err) {
      // On a background refresh, keep showing the last good data instead of
      // replacing it with an error — only a first-load failure is fatal.
      if (isFirstLoad) {
        setError(err instanceof Error ? err.message : 'Failed to load data');
      }
    } finally {
      if (isFirstLoad) setInitialLoading(false);
      else {
        setRefreshing(false);
        doneProgress();
      }
    }
  }, [startProgress, doneProgress, markLoaded]);

  const fetchMarketOverview = useCallback(async () => {
    try {
      const overview = await screenerApi.getMarketOverview();
      setMarketOverview(overview);
      setMarketOverviewError(false);
    } catch {
      // Keep the last good overview on screen; just flag it as stale so the
      // section isn't presented as up to date when a refresh actually failed.
      setMarketOverviewError(true);
    }
  }, []);

  const fetchMarketIndices = useCallback(async () => {
    try {
      const result = await marketApi.getIndices();
      setMarketIndices(result.indices);
      setMarketIndicesError(false);
    } catch {
      setMarketIndicesError(true);
    } finally {
      setMarketIndicesLoading(false);
    }
  }, []);

  const fetchSectorHistory = useCallback(async () => {
    try {
      const result = await marketApi.getSectorHistory(90);
      setSectorHistory(result.sectors);
    } catch {
      // Trend view just shows its own "no data" state; not worth a
      // dedicated error banner for a secondary view of data the heatmap
      // above already surfaced.
    }
  }, []);

  useEffect(() => {
    fetchData();
    fetchMarketOverview();
    fetchMarketIndices();
    fetchSectorHistory();
    const interval = setInterval(() => {
      fetchData();
      fetchMarketOverview();
      fetchMarketIndices();
      fetchSectorHistory();
    }, 5 * 60 * 1000);
    // Tick once a minute just to re-render the "updated Xm ago" label.
    const clock = setInterval(() => forceTick((t) => t + 1), 60 * 1000);
    return () => {
      clearInterval(interval);
      clearInterval(clock);
    };
  }, [fetchData, fetchMarketOverview, fetchMarketIndices, fetchSectorHistory]);

  if (initialLoading) {
    return <DashboardSkeleton />;
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
        <Card className="max-w-md w-full border border-black/[0.05] shadow-lg">
          <CardBody className="p-6">
            <ErrorAlert
              title="Connection Error"
              message={error}
              onRetry={fetchData}
            />
            <p className="text-xs text-slate-500 mt-3 text-center">The market data service did not respond. Please try again shortly.</p>
          </CardBody>
        </Card>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-7xl">

        {/* Market Indices Strip -- real S&P 500/Nasdaq/Russell/VIX/etc. data,
            placed above everything else: traders orient top-down (macro
            regime -> sector -> single name), not the other way around. */}
        <MarketIndicesStrip
          indices={marketIndices.filter((i) => !(COMMODITY_SYMBOLS as readonly string[]).includes(i.symbol))}
          loading={marketIndicesLoading}
          error={marketIndicesError}
        />

        {/* Header */}
        <div className="px-1 pt-2 pb-1 flex flex-wrap justify-between items-end gap-x-6 gap-y-3 mb-6">
          <div>
            <h1 className="large-title">Market overview</h1>
            <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
              Breadth, sectors and the day&apos;s movers across the US equity universe.
            </p>
            <DataStamp className="mt-1" asOf={data.market_summary?.last_updated ?? null} source="end-of-day prices" />
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-xs text-slate-500 flex items-center justify-end gap-1.5">
                {refreshing && (
                  <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
                )}
                {refreshing ? 'Refreshing…' : 'Last updated'}
              </p>
              <p className="text-xs font-medium text-slate-700">
                {refreshing ? timeAgo(lastUpdatedAt) : `${timeAgo(lastUpdatedAt)} · ${lastUpdatedAt?.toLocaleTimeString() ?? ''}`}
              </p>
            </div>
            <Button
              color="primary"
              size="sm"
              onClick={fetchData}
              isLoading={refreshing}
              className="rounded-full bg-slate-900 px-4 font-medium text-white"
            >
              {refreshing ? 'Refreshing' : 'Refresh'}
            </Button>
          </div>
        </div>

        {/* Market Overview v2: regime, breadth, returns and sector leadership from the observed daily snapshot */}
        <MarketIntelligencePanel priceDate={data.market_summary?.last_updated ?? null} />

        {/* First read: breadth, market mood, sector leaders */}
        <SummaryTiles overview={marketOverview} indices={marketIndices} />

        {/* Watchlist -- stocks starred on their stock page (this browser); renders nothing when empty */}
        <WatchlistCard />

        {/* Earnings -- yesterday / today / tomorrow / this week, as New York sessions */}
        <EarningsCard />

        {/* Sectors and commodities side by side */}
        <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="min-w-0 lg:col-span-2">
          {/* Market Overview */}
          {!marketOverview && marketOverviewError && (
            <ErrorAlert
              title="Market overview unavailable"
              message="Couldn't load sector performance and market breadth."
              onRetry={fetchMarketOverview}
            />
          )}
          {marketOverview && (
            <Card className="h-full border border-black/[0.05] shadow-sm">
              <CardHeader className="px-5 pt-5 pb-3 flex items-center justify-between flex-wrap gap-2">
                <h3 className="text-[17px] font-semibold text-slate-900">Market Overview <span className="font-normal text-slate-400">Today</span></h3>
                <div className="flex items-center gap-2">
                  {marketOverviewError && (
                    <Chip size="sm" className="bg-amber-100 text-amber-800 text-[10px]">
                      showing stale data — last refresh failed
                    </Chip>
                  )}
                  <SegmentedControl
                    aria-label="Sector view"
                    options={[
                      { value: 'heatmap', label: 'Heatmap' },
                      { value: 'trend', label: 'Trend' },
                    ]}
                    value={sectorView}
                    onChange={setSectorView}
                  />
                </div>
              </CardHeader>
              <CardBody className="px-5 pb-5 pt-1">
                <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-5">
                  <div className="bg-slate-50 rounded-xl p-4">
                    <p className="text-xs font-medium text-slate-500 mb-1">Advance/Decline</p>
                    <p className="text-xl font-semibold tracking-[-0.01em] tabular-nums text-slate-900">
                      {marketOverview.market_breadth.gainers} / {marketOverview.market_breadth.losers}
                    </p>
                    <p className="text-xs text-slate-500">ratio {marketOverview.market_breadth.advance_decline_ratio.toFixed(2)}</p>
                  </div>
                  <div className="bg-slate-50 rounded-xl p-4">
                    <p className="text-xs font-medium text-slate-500 mb-1">Unusual Volume</p>
                    <p className="text-xl font-semibold tracking-[-0.01em] tabular-nums text-slate-900">{marketOverview.market_breadth.unusual_volume_count}</p>
                    <p className="text-xs text-slate-500">stocks</p>
                  </div>
                  <div className="bg-slate-50 rounded-xl p-4">
                    <p className="text-xs font-medium text-slate-500 mb-1">Overbought / Oversold</p>
                    <p className="text-xl font-semibold tracking-[-0.01em] tabular-nums text-slate-900">
                      <span className="text-red-600">{marketOverview.market_breadth.overbought_count}</span>
                      {' / '}
                      <span className="text-emerald-600">{marketOverview.market_breadth.oversold_count}</span>
                    </p>
                    <p className="text-xs text-slate-500">RSI &gt;70 / &lt;30</p>
                  </div>
                  <div className="bg-slate-50 rounded-xl p-4">
                    <p className="text-xs font-medium text-slate-500 mb-1">Avg RSI / Vol Ratio</p>
                    <p className="text-xl font-semibold tracking-[-0.01em] tabular-nums text-slate-900">
                      {marketOverview.market_indicators.avg_rsi.toFixed(0)} / {marketOverview.market_indicators.avg_volume_ratio.toFixed(2)}x
                    </p>
                    <p className="text-xs text-slate-500">across {marketOverview.market_breadth.total_stocks} stocks</p>
                  </div>
                </div>

                <div className="flex justify-between items-center mb-2">
                  <p className="text-xs text-slate-500">
                    {sectorView === 'heatmap'
                      ? 'Sector performance today: box size = number of stocks, colour = average change. Click a sector to highlight it.'
                      : 'Sector performance: 90-day cumulative return. Click a sector to isolate it.'}
                  </p>
                  {selectedSector && (
                    <div className="flex items-center gap-2">
                      <Chip
                        size="sm"
                        className="bg-accent text-white text-xs cursor-pointer"
                        onClose={() => setSelectedSector(null)}
                      >
                        {selectedSector}
                      </Chip>
                      {/* Internal tool: the selected sector filters the internal momentum lists on /momentum. */}
                      <Link
                        href={`/momentum?sector=${encodeURIComponent(selectedSector)}`}
                        title="Open the internal momentum view filtered to this sector"
                        className="text-xs font-medium text-accent hover:underline"
                      >
                        Internal view →
                      </Link>
                    </div>
                  )}
                </div>
                {sectorView === 'trend' ? (
                  <SectorTrendChart
                    sectors={sectorHistory}
                    selectedSector={selectedSector}
                    onSelectSector={setSelectedSector}
                  />
                ) : (
                <div className="overflow-hidden rounded-xl">
                <ResponsiveContainer width="100%" height={280}>
                  <Treemap
                    data={marketOverview.sector_performance.map((s) => ({
                      name: s.sector,
                      size: Math.max(s.stock_count, 1),
                      stock_count: s.stock_count,
                      avg_performance: s.avg_performance,
                    }))}
                    dataKey="size"
                    stroke={palette.gap}
                    isAnimationActive={false}
                    content={(node) => {
                      // Treemap hands back our own datum's fields on the node (see the
                      // `data` mapping above); recharts types them as an index signature.
                      const { x, y, width, height, name } = node;
                      const avgPerformance: number | undefined = node.avg_performance;
                      const stockCount: number | undefined = node.stock_count;
                      const isSelected = selectedSector === name;
                      const perf = avgPerformance ?? 0;

                      // Always show a label — scale font down and abbreviate/truncate
                      // as the box shrinks, instead of hiding small sectors entirely.
                      const nameFontSize = width < 55 ? 8 : width < 90 ? 9.5 : 11;
                      const label = fitSectorLabel(name, width - 6, nameFontSize);
                      const canShowPerf = height > 32 && width > 34;
                      const nameY = canShowPerf ? y + height / 2 - 5 : y + height / 2 + nameFontSize / 3;

                      return (
                        <g
                          onClick={() => setSelectedSector(isSelected ? null : name)}
                          style={{ cursor: 'pointer' }}
                        >
                          <title>
                            {name}: {perf >= 0 ? '+' : ''}{perf.toFixed(2)}% ({stockCount} stocks)
                          </title>
                          <rect
                            x={x}
                            y={y}
                            width={width}
                            height={height}
                            rx={6}
                            style={{
                              fill: sectorColor(perf, theme),
                              stroke: isSelected ? palette.label : palette.gap,
                              strokeWidth: isSelected ? 3 : 2,
                            }}
                          />
                          <text
                            x={x + width / 2}
                            y={nameY}
                            textAnchor="middle"
                            fontSize={nameFontSize}
                            fontWeight={600}
                            fill={palette.label}
                            stroke="none"
                          >
                            {label}
                          </text>
                          {canShowPerf && (
                            <text x={x + width / 2} y={y + height / 2 + 11} textAnchor="middle" fontSize={10} fill={palette.sub} stroke="none">
                              {perf >= 0 ? '+' : ''}
                              {perf.toFixed(2)}%
                            </text>
                          )}
                        </g>
                      );
                    }}
                  />
                </ResponsiveContainer>
                </div>
                )}
              </CardBody>
            </Card>
          )}
          </div>
          <div className="min-w-0">
            <CommoditiesCard indices={marketIndices} loading={marketIndicesLoading} />
          </div>
        </div>

        {/* Whole-market movers: every liquid stock ranked by today's change and volume. */}
        <MarketMovers
          gainers={data.top_gainers ?? []}
          losers={data.top_losers ?? []}
          unusualVolume={data.unusual_volume ?? []}
        />

        <p className="pt-8 pb-2 text-center text-xs text-slate-400">This page refreshes automatically every 5 minutes.</p>
      </div>
    </div>
  );
}