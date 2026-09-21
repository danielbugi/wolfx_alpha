'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import {
  Card,
  CardBody,
  CardHeader,
  Table,
  TableHeader,
  TableColumn,
  TableBody,
  TableRow,
  TableCell,
  Button,
  Chip,
  Divider,
} from '@nextui-org/react';
import {
  ResponsiveContainer,
  Treemap,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts';
import { API_BASE_URL, screenerApi, alphaApi, marketApi, MainPageData, MarketOverview, AlphaFinderResult, MarketIndexEntry, SectorHistoryEntry } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { confidenceColor } from '@/lib/uiColors';
import { usePagePerf } from '@/hooks/usePagePerf';
import { useTopProgress } from '@/lib/topProgress';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import MarketIndicesStrip from '@/components/dashboard/MarketIndicesStrip';
import SectorTrendChart from '@/components/dashboard/SectorTrendChart';

/** What recharts 3 passes to a <Scatter onClick>: the plotted datum spread with pixel
 * geometry, plus the untouched original datum under `payload`. */
interface ScatterClickPoint {
  payload: AlphaFinderResult;
}

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

const SIGNAL_TYPE_FILTERS = [
  { label: 'All Signals', value: undefined },
  { label: 'Bullish Breakout', value: 'bullish_breakout' },
  { label: 'Near Bullish', value: 'near_bullish' },
  { label: 'Bearish Breakout', value: 'bearish_breakout' },
  { label: 'Near Bearish', value: 'near_bearish' },
];

function sectorColor(perf: number): string {
  if (perf >= 1) return '#059669';
  if (perf >= 0) return '#6ee7b7';
  if (perf >= -1) return '#fca5a5';
  return '#dc2626';
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
  const router = useRouter();
  const { markLoaded } = usePagePerf('/');
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
  const [signalType, setSignalType] = useState<string | undefined>(undefined);
  const [highConfidenceOnly, setHighConfidenceOnly] = useState(false);
  const [alphaView, setAlphaView] = useState<'list' | 'chart'>('list');
  const [alphaResults, setAlphaResults] = useState<AlphaFinderResult[]>([]);
  const [alphaLoading, setAlphaLoading] = useState(true);
  const [alphaError, setAlphaError] = useState(false);

  const fetchData = useCallback(async () => {
    const isFirstLoad = !hasLoadedOnceRef.current;
    try {
      if (isFirstLoad) setInitialLoading(true);
      else {
        setRefreshing(true);
        startProgress();
      }
      setError(null);
      const response = await fetch(`${API_BASE_URL}/api/dashboard/main-page-data`);
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const result: MainPageData = await response.json();
      setData(result);
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

  const fetchAlpha = useCallback(async () => {
    try {
      setAlphaLoading(true);
      const resp = await alphaApi.getFinder({
        signal_type: signalType,
        min_confidence: highConfidenceOnly ? 'high' : undefined,
        sector: selectedSector || undefined,
        limit: 25,
      });
      setAlphaResults(resp.results);
      setAlphaError(false);
    } catch {
      setAlphaResults([]);
      setAlphaError(true);
    } finally {
      setAlphaLoading(false);
    }
  }, [signalType, highConfidenceOnly, selectedSector]);

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

  useEffect(() => {
    fetchAlpha();
  }, [fetchAlpha]);

  if (initialLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 to-cyan-50">
        <div className="text-center">
          <LoadingSpinner size="large" className="mb-4" />
          <p className="text-slate-600 text-base font-medium">Loading Trading Data</p>
          <p className="text-slate-500 text-sm mt-1">Connecting to market feeds</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 to-cyan-50 p-4">
        <Card className="max-w-md w-full shadow-lg">
          <CardBody className="p-6">
            <ErrorAlert
              title="Connection Error"
              message={error}
              onRetry={fetchData}
            />
            <p className="text-xs text-slate-500 mt-3 text-center">
              Ensure backend is running on http://localhost:8000
            </p>
          </CardBody>
        </Card>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-7xl">

        {/* Market Indices Strip -- real S&P 500/Nasdaq/Russell/VIX/etc. data,
            placed above everything else: traders orient top-down (macro
            regime -> sector -> single name), not the other way around. */}
        <MarketIndicesStrip indices={marketIndices} loading={marketIndicesLoading} error={marketIndicesError} />

        {/* Header */}
        <div className="flex justify-between items-center mb-6 p-4 bg-white rounded-lg shadow-sm border border-slate-200">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Trading Dashboard</h1>
            <p className="text-slate-600 text-sm mt-1">Professional market analysis and AI signals</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-xs text-slate-500 flex items-center justify-end gap-1.5">
                {refreshing && (
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-500 animate-pulse" />
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
              className="bg-slate-700 text-white"
            >
              {refreshing ? 'Refreshing' : 'Refresh'}
            </Button>
          </div>
        </div>

        {/* Market Summary Cards */}
        {data?.market_summary && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
            <Card className="shadow-sm border border-slate-200">
              <CardBody className="text-center p-4">
                <p className="text-xs text-slate-500 mb-2 uppercase tracking-wide">Total Symbols</p>
                <p className="text-xl font-bold text-slate-800">
                  {data.market_summary.total_symbols.toLocaleString()}
                </p>
              </CardBody>
            </Card>

            <Card className="shadow-sm border border-slate-200">
              <CardBody className="text-center p-4">
                <p className="text-xs text-slate-500 mb-2 uppercase tracking-wide">Volume Ratio</p>
                <p className="text-xl font-bold text-slate-800">
                  {data.market_summary.avg_volume_ratio.toFixed(2)}x
                </p>
              </CardBody>
            </Card>

            <Card className="shadow-sm border border-slate-200">
              <CardBody className="text-center p-4">
                <p className="text-xs text-slate-500 mb-2 uppercase tracking-wide">Active Signals</p>
                <p className="text-xl font-bold text-slate-800">
                   {data.top_gainers?.length + data.top_losers?.length + data.unusual_volume?.length + data.top_ai_picks?.length || 0}
                </p>
              </CardBody>
            </Card>

            <Card className="shadow-sm border border-slate-200">
              <CardBody className="text-center p-4">
                <p className="text-xs text-slate-500 mb-2 uppercase tracking-wide">Data Updated</p>
                <p className="text-sm font-semibold text-slate-700">
                  {data.market_summary.last_updated}
                </p>
              </CardBody>
            </Card>
          </div>
        )}

        {/* Market Overview */}
        {!marketOverview && marketOverviewError && (
          <ErrorAlert
            title="Market overview unavailable"
            message="Couldn't load sector performance and market breadth."
            onRetry={fetchMarketOverview}
            className="mb-6"
          />
        )}
        {marketOverview && (
          <Card className="mb-6 shadow-sm border border-slate-200">
            <CardHeader className="p-3 pb-2 flex items-center justify-between flex-wrap gap-2">
              <h3 className="text-sm font-semibold text-slate-700">Market Overview — Today</h3>
              <div className="flex items-center gap-2">
                {marketOverviewError && (
                  <Chip size="sm" className="bg-amber-100 text-amber-800 text-[10px]">
                    showing stale data — last refresh failed
                  </Chip>
                )}
                <Button
                  size="sm"
                  variant={sectorView === 'heatmap' ? 'solid' : 'bordered'}
                  className={sectorView === 'heatmap' ? 'bg-slate-700 text-white text-xs h-7' : 'text-xs h-7 border-slate-300'}
                  onClick={() => setSectorView('heatmap')}
                >
                  Heatmap
                </Button>
                <Button
                  size="sm"
                  variant={sectorView === 'trend' ? 'solid' : 'bordered'}
                  className={sectorView === 'trend' ? 'bg-slate-700 text-white text-xs h-7' : 'text-xs h-7 border-slate-300'}
                  onClick={() => setSectorView('trend')}
                >
                  Trend
                </Button>
              </div>
            </CardHeader>
            <Divider className="bg-slate-200" />
            <CardBody className="p-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div className="bg-slate-50 rounded p-3 text-center">
                  <p className="text-xs text-slate-500 mb-1">Advance/Decline</p>
                  <p className="text-lg font-bold text-slate-800">
                    {marketOverview.market_breadth.gainers} / {marketOverview.market_breadth.losers}
                  </p>
                  <p className="text-xs text-slate-500">ratio {marketOverview.market_breadth.advance_decline_ratio.toFixed(2)}</p>
                </div>
                <div className="bg-slate-50 rounded p-3 text-center">
                  <p className="text-xs text-slate-500 mb-1">Unusual Volume</p>
                  <p className="text-lg font-bold text-slate-800">{marketOverview.market_breadth.unusual_volume_count}</p>
                  <p className="text-xs text-slate-500">stocks</p>
                </div>
                <div className="bg-slate-50 rounded p-3 text-center">
                  <p className="text-xs text-slate-500 mb-1">Overbought / Oversold</p>
                  <p className="text-lg font-bold text-slate-800">
                    <span className="text-red-600">{marketOverview.market_breadth.overbought_count}</span>
                    {' / '}
                    <span className="text-emerald-600">{marketOverview.market_breadth.oversold_count}</span>
                  </p>
                  <p className="text-xs text-slate-500">RSI &gt;70 / &lt;30</p>
                </div>
                <div className="bg-slate-50 rounded p-3 text-center">
                  <p className="text-xs text-slate-500 mb-1">Avg RSI / Vol Ratio</p>
                  <p className="text-lg font-bold text-slate-800">
                    {marketOverview.market_indicators.avg_rsi.toFixed(0)} / {marketOverview.market_indicators.avg_volume_ratio.toFixed(2)}x
                  </p>
                  <p className="text-xs text-slate-500">across {marketOverview.market_breadth.total_stocks} stocks</p>
                </div>
              </div>

              <div className="flex justify-between items-center mb-2">
                <p className="text-xs text-slate-500 uppercase tracking-wide">
                  {sectorView === 'heatmap'
                    ? 'Sector Performance — box size = # stocks, color = avg change. Click a sector to filter Alpha Finder below.'
                    : 'Sector Performance — 90-day cumulative return. Click a sector below to isolate it and filter Alpha Finder.'}
                </p>
                {selectedSector && (
                  <Chip
                    size="sm"
                    className="bg-slate-700 text-white text-xs cursor-pointer"
                    onClose={() => setSelectedSector(null)}
                  >
                    {selectedSector}
                  </Chip>
                )}
              </div>
              {sectorView === 'trend' ? (
                <SectorTrendChart
                  sectors={sectorHistory}
                  selectedSector={selectedSector}
                  onSelectSector={setSelectedSector}
                />
              ) : (
              <ResponsiveContainer width="100%" height={280}>
                <Treemap
                  data={marketOverview.sector_performance.map((s) => ({
                    name: s.sector,
                    size: Math.max(s.stock_count, 1),
                    stock_count: s.stock_count,
                    avg_performance: s.avg_performance,
                  }))}
                  dataKey="size"
                  stroke="#fff"
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
                          style={{
                            fill: sectorColor(perf),
                            stroke: isSelected ? '#0f172a' : '#fff',
                            strokeWidth: isSelected ? 3 : 2,
                          }}
                        />
                        <text
                          x={x + width / 2}
                          y={nameY}
                          textAnchor="middle"
                          fontSize={nameFontSize}
                          fontWeight={600}
                          fill="#0f172a"
                        >
                          {label}
                        </text>
                        {canShowPerf && (
                          <text x={x + width / 2} y={y + height / 2 + 11} textAnchor="middle" fontSize={10} fill="#1e293b">
                            {perf >= 0 ? '+' : ''}
                            {perf.toFixed(2)}%
                          </text>
                        )}
                      </g>
                    );
                  }}
                />
              </ResponsiveContainer>
              )}
            </CardBody>
          </Card>
        )}

        {/* Alpha Finder */}
        <Card className="mb-6 shadow-sm border border-slate-200">
          <CardHeader className="bg-slate-700 text-white p-4">
            <div className="flex flex-wrap justify-between items-center gap-3 w-full">
              <div>
                <h3 className="text-base font-semibold">Alpha Finder</h3>
                <p className="text-slate-300 text-xs mt-1">
                  Ranked by combined confidence + alignment + quality score
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Chip size="sm" className="bg-cyan-100 text-cyan-800 text-xs">
                  {alphaResults.length} results
                </Chip>
                <Button
                  size="sm"
                  variant={alphaView === 'list' ? 'solid' : 'ghost'}
                  className={alphaView === 'list' ? 'bg-white text-slate-800 text-xs' : 'text-white border-white text-xs'}
                  onClick={() => setAlphaView('list')}
                >
                  List
                </Button>
                <Button
                  size="sm"
                  variant={alphaView === 'chart' ? 'solid' : 'ghost'}
                  className={alphaView === 'chart' ? 'bg-white text-slate-800 text-xs' : 'text-white border-white text-xs'}
                  onClick={() => setAlphaView('chart')}
                >
                  Chart
                </Button>
              </div>
            </div>
          </CardHeader>

          {/* Filter chips */}
          <div className="flex flex-wrap gap-2 p-3 bg-slate-50 border-b border-slate-200">
            {SIGNAL_TYPE_FILTERS.map((f) => (
              <Button
                key={f.label}
                size="sm"
                variant={signalType === f.value ? 'solid' : 'bordered'}
                className={signalType === f.value ? 'bg-slate-700 text-white text-xs' : 'text-xs border-slate-300'}
                onClick={() => setSignalType(f.value)}
              >
                {f.label}
              </Button>
            ))}
            <Button
              size="sm"
              variant={highConfidenceOnly ? 'solid' : 'bordered'}
              className={highConfidenceOnly ? 'bg-emerald-600 text-white text-xs' : 'text-xs border-slate-300'}
              onClick={() => setHighConfidenceOnly(!highConfidenceOnly)}
            >
              High Confidence Only
            </Button>
          </div>

          <CardBody className="p-0">
            {alphaLoading ? (
              <div className="flex justify-center py-10">
                <LoadingSpinner size="medium" />
              </div>
            ) : alphaError ? (
              <ErrorAlert
                title="Alpha Finder unavailable"
                message="Couldn't load ranked signals for these filters."
                onRetry={fetchAlpha}
                className="m-4"
              />
            ) : alphaResults.length === 0 ? (
              <p className="text-center text-slate-400 text-sm py-10">No signals match these filters.</p>
            ) : alphaView === 'chart' ? (
              <div className="p-3">
                <p className="text-xs text-slate-500 mb-2">
                  Confidence vs. alignment — bubble size = quality score. Click a point to open the stock.
                </p>
                <ResponsiveContainer width="100%" height={320}>
                  <ScatterChart margin={{ top: 10, right: 20, bottom: 10, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis
                      type="number"
                      dataKey="alignment_score"
                      name="Alignment Score"
                      domain={[0, 100]}
                      tick={{ fontSize: 10 }}
                      label={{ value: 'Alignment Score', position: 'insideBottom', offset: -5, fontSize: 10 }}
                    />
                    <YAxis
                      type="number"
                      dataKey="ml_momentum_probability"
                      name="ML Confidence %"
                      domain={[0, 100]}
                      tick={{ fontSize: 10 }}
                      label={{ value: 'ML Score %', angle: -90, position: 'insideLeft', fontSize: 10 }}
                    />
                    <ZAxis type="number" dataKey="overall_quality_score" range={[40, 400]} name="Quality" />
                    <Tooltip
                      cursor={{ strokeDasharray: '3 3' }}
                      contentStyle={{ fontSize: 12, borderRadius: 8 }}
                      formatter={(value, name) => [typeof value === 'number' ? value.toFixed(1) : value, name]}
                      labelFormatter={() => ''}
                    />
                    <Scatter
                      data={alphaResults.filter((r) => r.signal_type.includes('bullish'))}
                      fill="#10b981"
                      fillOpacity={0.7}
                      name="Bullish"
                      onClick={(point: ScatterClickPoint) => router.push(`/stock/${point.payload.symbol}`)}
                      cursor="pointer"
                    />
                    <Scatter
                      data={alphaResults.filter((r) => r.signal_type.includes('bearish'))}
                      fill="#ef4444"
                      fillOpacity={0.7}
                      name="Bearish"
                      onClick={(point: ScatterClickPoint) => router.push(`/stock/${point.payload.symbol}`)}
                      cursor="pointer"
                    />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <Table
                removeWrapper
                className="text-xs"
                classNames={{
                  th: "bg-slate-50 text-slate-600 font-medium text-xs h-8",
                  td: "text-xs py-2"
                }}
              >
                <TableHeader>
                  <TableColumn>SYMBOL</TableColumn>
                  <TableColumn align="end">PRICE</TableColumn>
                  <TableColumn align="end">CHANGE</TableColumn>
                  <TableColumn align="end">SCORE</TableColumn>
                  <TableColumn>CONFIDENCE</TableColumn>
                  <TableColumn>TYPE</TableColumn>
                  <TableColumn>SECTOR</TableColumn>
                  <TableColumn align="end">TARGET</TableColumn>
                </TableHeader>
                <TableBody>
                  {alphaResults.map((r) => (
                    <TableRow key={r.symbol}>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <SymbolHoverLink symbol={r.symbol} />
                          {r.is_new && (
                            <Chip size="sm" className="bg-amber-100 text-amber-800 text-[10px] h-4 px-1">
                              NEW
                            </Chip>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-right text-slate-700">${r.current_price?.toFixed(2)}</TableCell>
                      <TableCell className="text-right">
                        <span className={`font-semibold ${r.price_change_pct >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                          {r.price_change_pct >= 0 ? '+' : ''}
                          {r.price_change_pct?.toFixed(2)}%
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-bold text-slate-700">
                        {r.combined_score != null ? r.combined_score.toFixed(0) : 'N/A'}
                      </TableCell>
                      <TableCell>
                        <Chip size="sm" className={`text-xs ${confidenceColor(r.ml_confidence)}`}>
                          {r.ml_confidence?.replace('_', ' ') || 'N/A'}
                        </Chip>
                      </TableCell>
                      <TableCell className="text-slate-600">{r.signal_type.replace(/_/g, ' ')}</TableCell>
                      <TableCell className="text-slate-500">{r.sector}</TableCell>
                      <TableCell className="text-right text-slate-600">
                        {r.target_price != null ? `$${r.target_price.toFixed(2)}` : 'N/A'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardBody>
        </Card>

        {/* Market Data Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">

          {/* Top Gainers */}
          {data?.top_gainers && (
            <Card className="shadow-sm border border-slate-200">
            <CardHeader className="p-3 pb-2">
  <div className="flex justify-between items-center">
    <h3 className="text-sm font-semibold text-slate-700">Top Gainers</h3>
    <Button
      size="sm"
      variant="light"
      className="text-xs"
      onClick={() => window.location.href = '/screener?filter=gainers'}
    >
      View All
    </Button>
  </div>
</CardHeader>
              <Divider className="bg-slate-200"/>
              <CardBody className="p-0">
                <Table
                  removeWrapper
                  className="text-xs"
                  classNames={{
                    th: "bg-slate-50 text-slate-600 font-medium text-xs h-7",
                    td: "text-xs py-1.5"
                  }}
                >
                  <TableHeader>
                    <TableColumn>SYMBOL</TableColumn>
                    <TableColumn align="end">PRICE</TableColumn>
                    <TableColumn align="end">CHANGE</TableColumn>
                    <TableColumn>SECTOR</TableColumn>
                  </TableHeader>
                  <TableBody>
                    {data.top_gainers.map((stock) => (
                      <TableRow key={stock.symbol}>
                        <TableCell>
                          <SymbolHoverLink symbol={stock.symbol} />
                        </TableCell>
                        <TableCell className="text-right text-slate-700">
                          ${stock.current_price.toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className="font-semibold text-emerald-600">
                            +{stock.price_change_pct.toFixed(2)}%
                          </span>
                        </TableCell>
                        <TableCell className="text-slate-500 text-xs">
                          {stock.sector}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardBody>
            </Card>
          )}

          {/* Top Losers */}
          {data?.top_losers && (
            <Card className="shadow-sm border border-slate-200">
             <CardHeader className="p-3 pb-2">
  <div className="flex justify-between items-center">
    <h3 className="text-sm font-semibold text-slate-700">Top Losers</h3>
    <Button
      size="sm"
      variant="light"
      className="text-xs"
      onClick={() => window.location.href = '/screener?filter=losers'}
    >
      View All
    </Button>
  </div>
</CardHeader>
              <Divider className="bg-slate-200"/>
              <CardBody className="p-0">
                <Table
                  removeWrapper
                  className="text-xs"
                  classNames={{
                    th: "bg-slate-50 text-slate-600 font-medium text-xs h-7",
                    td: "text-xs py-1.5"
                  }}
                >
                  <TableHeader>
                    <TableColumn>SYMBOL</TableColumn>
                    <TableColumn align="end">PRICE</TableColumn>
                    <TableColumn align="end">CHANGE</TableColumn>
                    <TableColumn>SECTOR</TableColumn>
                  </TableHeader>
                  <TableBody>
                    {data.top_losers.map((stock) => (
                      <TableRow key={stock.symbol}>
                        <TableCell>
                          <SymbolHoverLink symbol={stock.symbol} />
                        </TableCell>
                        <TableCell className="text-right text-slate-700">
                          ${stock.current_price.toFixed(2)}
                        </TableCell>
                        <TableCell className="text-right">
                          <span className="font-semibold text-red-600">
                            {stock.price_change_pct.toFixed(2)}%
                          </span>
                        </TableCell>
                        <TableCell className="text-slate-500 text-xs">
                          {stock.sector}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardBody>
            </Card>
          )}
        </div>

        {/* Unusual Volume - Full Width */}
        {data?.unusual_volume && (
          <Card className="shadow-sm border border-slate-200">
        <CardHeader className="p-3 pb-2">
  <div className="flex justify-between items-center">
    <div>
      <h3 className="text-sm font-semibold text-slate-700">Unusual Volume Activity</h3>
      <p className="text-xs text-slate-500 mt-1">Stocks with significantly higher trading volume</p>
    </div>
    <Button
      size="sm"
      variant="light"
      className="text-xs"
      onClick={() => window.location.href = '/screener?filter=volume'}
    >
      View All
    </Button>
  </div>
</CardHeader>
            <Divider className="bg-slate-200"/>
            <CardBody className="p-0">
              <Table
                removeWrapper
                className="text-xs"
                classNames={{
                  th: "bg-slate-50 text-slate-600 font-medium text-xs h-7",
                  td: "text-xs py-1.5"
                }}
              >
                <TableHeader>
                  <TableColumn>SYMBOL</TableColumn>
                  <TableColumn align="end">PRICE</TableColumn>
                  <TableColumn align="end">VOLUME</TableColumn>
                  <TableColumn align="end">RATIO</TableColumn>
                  <TableColumn align="end">CHANGE</TableColumn>
                  <TableColumn>SECTOR</TableColumn>
                </TableHeader>
                <TableBody>
                  {data.unusual_volume.map((stock) => (
                    <TableRow key={stock.symbol}>
                      <TableCell>
                        <SymbolHoverLink symbol={stock.symbol} />
                      </TableCell>
                      <TableCell className="text-right text-slate-700">
                        ${stock.current_price.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right text-slate-600">
                        {(stock.volume / 1000000).toFixed(1)}M
                      </TableCell>
                      <TableCell className="text-right">
                        <span className={`font-semibold ${
                          stock.volume_ratio >= 3 ? 'text-red-600' :
                          stock.volume_ratio >= 2 ? 'text-amber-600' : 'text-slate-600'
                        }`}>
                          {stock.volume_ratio.toFixed(1)}x
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <span className={`font-semibold ${
                          stock.price_change_pct >= 0 ? 'text-emerald-600' : 'text-red-600'
                        }`}>
                          {stock.price_change_pct >= 0 ? '+' : ''}{stock.price_change_pct.toFixed(2)}%
                        </span>
                      </TableCell>
                      <TableCell className="text-slate-500 text-xs">
                        {stock.sector}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardBody>
          </Card>
        )}

        {/* Footer */}
        <div className="text-center py-4">
          <p className="text-xs text-slate-500">
            Data refreshes automatically every 5 minutes | Trading System v1.0
          </p>
        </div>
      </div>
    </div>
  );
}