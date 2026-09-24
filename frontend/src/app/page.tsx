'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
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
import { ResponsiveContainer, Treemap } from 'recharts';
import { apiClient, screenerApi, momentumBoardApi, momentumLeadersApi, marketApi, MainPageData, MarketOverview, MomentumBoardResult, MomentumLeadersBoard, MarketIndexEntry, SectorHistoryEntry } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { usePagePerf } from '@/hooks/usePagePerf';
import { useTopProgress } from '@/lib/topProgress';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import MarketIndicesStrip from '@/components/dashboard/MarketIndicesStrip';
import SectorTrendChart from '@/components/dashboard/SectorTrendChart';
import MomentumBoard, { BoardCategory } from '@/components/dashboard/MomentumBoard';
import MomentumLeaders from '@/components/dashboard/MomentumLeaders';

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

  // Momentum Board -- see CLAUDE.md's 2026-09-22 dashboard reframe. Defaults to
  // Breakout only (long side, confirmed moves), per the same instinct that used to
  // set Alpha Finder's filter chips: a "find winners" panel should not open on noise.
  const [boardCategory, setBoardCategory] = useState<BoardCategory>('breakout');
  const [boardMinQualityGrade, setBoardMinQualityGrade] = useState<string | undefined>(undefined);
  const [boardResults, setBoardResults] = useState<MomentumBoardResult[]>([]);
  const [boardSessionDate, setBoardSessionDate] = useState<string | null>(null);
  const [boardUniverseN, setBoardUniverseN] = useState(0);
  const [boardCounts, setBoardCounts] = useState<Record<string, number>>({});
  const [boardStarredTotal, setBoardStarredTotal] = useState(0);
  const [boardLoading, setBoardLoading] = useState(true);
  const [boardError, setBoardError] = useState(false);

  // Momentum Leaders -- "who's still moving after making an earlier list" (see
  // CLAUDE.md's 2026-09-23 dashboard audit; distinct from Momentum Board above,
  // which ranks today's own lists).
  const [leadersBoard, setLeadersBoard] = useState<MomentumLeadersBoard | null>(null);
  const [leadersSessionDate, setLeadersSessionDate] = useState<string | null>(null);
  const [leadersLoading, setLeadersLoading] = useState(true);
  const [leadersError, setLeadersError] = useState(false);

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

  const fetchBoard = useCallback(async () => {
    try {
      setBoardLoading(true);
      const resp = await momentumBoardApi.getBoard({
        category: boardCategory,
        min_quality_grade: boardMinQualityGrade,
        sector: selectedSector || undefined,
        limit: 50,
      });
      setBoardResults(resp.results);
      setBoardSessionDate(resp.session_date);
      setBoardUniverseN(resp.universe_n);
      setBoardCounts(resp.counts);
      setBoardStarredTotal(resp.starred_total);
      setBoardError(false);
    } catch {
      setBoardResults([]);
      setBoardError(true);
    } finally {
      setBoardLoading(false);
    }
  }, [boardCategory, boardMinQualityGrade, selectedSector]);

  const fetchLeaders = useCallback(async () => {
    try {
      setLeadersLoading(true);
      const resp = await momentumLeadersApi.getLeaders();
      setLeadersBoard(resp.board);
      setLeadersSessionDate(resp.session_date);
      setLeadersError(false);
    } catch {
      setLeadersBoard(null);
      setLeadersError(true);
    } finally {
      setLeadersLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    fetchMarketOverview();
    fetchMarketIndices();
    fetchSectorHistory();
    fetchLeaders();
    const interval = setInterval(() => {
      fetchData();
      fetchMarketOverview();
      fetchMarketIndices();
      fetchSectorHistory();
      fetchLeaders();
    }, 5 * 60 * 1000);
    // Tick once a minute just to re-render the "updated Xm ago" label.
    const clock = setInterval(() => forceTick((t) => t + 1), 60 * 1000);
    return () => {
      clearInterval(interval);
      clearInterval(clock);
    };
  }, [fetchData, fetchMarketOverview, fetchMarketIndices, fetchSectorHistory, fetchLeaders]);

  useEffect(() => {
    fetchBoard();
  }, [fetchBoard]);

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
            <p className="text-slate-600 text-sm mt-1">Professional market analysis and confirmed momentum signals</p>
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
                <p className="text-[10px] text-slate-400 mt-1">tracked, incl. illiquid</p>
              </CardBody>
            </Card>

            <Card className="shadow-sm border border-slate-200">
              <CardBody className="text-center p-4">
                <p className="text-xs text-slate-500 mb-2 uppercase tracking-wide">Volume Ratio</p>
                <p className="text-xl font-bold text-slate-800">
                  {data.market_summary.avg_volume_ratio != null ? `${data.market_summary.avg_volume_ratio.toFixed(2)}x` : '—'}
                </p>
              </CardBody>
            </Card>

            <Card className="shadow-sm border border-slate-200">
              <CardBody className="text-center p-4">
                <p className="text-xs text-slate-500 mb-2 uppercase tracking-wide">Confirmed Today</p>
                <p className="text-xl font-bold text-slate-800">
                  {boardStarredTotal}
                </p>
                <p className="text-[10px] text-slate-400 mt-1">★ on 2+ Momentum Board lists</p>
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
                    ? 'Sector Performance — box size = # stocks, color = avg change. Click a sector to filter the Momentum Board below.'
                    : 'Sector Performance — 90-day cumulative return. Click a sector below to isolate it and filter the Momentum Board.'}
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

        {/* Momentum Board -- replaces the old ML/alignment-based Alpha Finder. See
            CLAUDE.md's 2026-09-22 dashboard reframe entry for why. */}
        <MomentumBoard
          results={boardResults}
          loading={boardLoading}
          error={boardError}
          sessionDate={boardSessionDate}
          universeN={boardUniverseN}
          counts={boardCounts}
          category={boardCategory}
          onCategoryChange={setBoardCategory}
          minQualityGrade={boardMinQualityGrade}
          onMinQualityGradeChange={setBoardMinQualityGrade}
          onRetry={fetchBoard}
        />

        <MomentumLeaders
          board={leadersBoard}
          sessionDate={leadersSessionDate}
          loading={leadersLoading}
          error={leadersError}
          onRetry={fetchLeaders}
        />

        {/* Whole-Market Movers -- unlike Momentum Board above (breakout/near-breakout only,
            confirmed by 2+ lists), these three rank every liquid stock regardless of Donchian
            category: the widest-angle "what's moving" view on the page. */}
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-sm font-semibold text-slate-600 uppercase tracking-wide">Whole-Market Movers</h2>
          <span className="text-xs text-slate-400">— any stock, not just breakout-confirmed names</span>
        </div>

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
                          stock.volume_ratio == null ? 'text-slate-400' :
                          stock.volume_ratio >= 3 ? 'text-red-600' :
                          stock.volume_ratio >= 2 ? 'text-amber-600' : 'text-slate-600'
                        }`}>
                          {stock.volume_ratio != null ? `${stock.volume_ratio.toFixed(1)}x` : '—'}
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
            Data refreshes automatically every 5 minutes | Donchian Breakout Screening Platform
          </p>
        </div>
      </div>
    </div>
  );
}