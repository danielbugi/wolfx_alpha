'use client';

import React, { Suspense, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
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
  Select,
  SelectItem,
  Input,
} from '@nextui-org/react';
import {
  screenerApi,
  ScreenerFilterRequest,
  ScreenerResult,
  FilterOptions,
  FilterPreset,
} from '@/services/api';
import { useTopProgress } from '@/lib/topProgress';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';

const QUALITY_GRADES = ['A', 'B', 'C', 'D', 'F'];

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: 'price_change_pct', label: 'Price Change' },
  { value: 'volume', label: 'Volume' },
  { value: 'volume_ratio', label: 'Volume Ratio' },
  { value: 'rsi_14', label: 'RSI' },
  { value: 'market_cap', label: 'Market Cap' },
  { value: 'quality_score', label: 'Quality Score' },
];

/** Map the legacy dashboard "View All" quick links to sensible default filters */
function quickFilterFromParam(filter: string | null): Partial<ScreenerFilterRequest> {
  switch (filter) {
    case 'gainers':
      return { sort_by: 'price_change_pct', sort_order: 'desc', min_price_change: 0 };
    case 'losers':
      return { sort_by: 'price_change_pct', sort_order: 'asc', max_price_change: 0 };
    case 'volume':
      return { sort_by: 'volume_ratio', sort_order: 'desc', min_volume_ratio: 2.0 };
    case 'ai-picks':
      return { sort_by: 'quality_score', sort_order: 'desc', min_quality_score: 70 };
    default:
      return {};
  }
}

function gradeColor(grade: string | null) {
  switch (grade) {
    case 'A':
      return 'bg-emerald-100 text-emerald-800';
    case 'B':
      return 'bg-cyan-100 text-cyan-800';
    case 'C':
      return 'bg-amber-100 text-amber-800';
    default:
      return 'bg-red-100 text-red-700';
  }
}

/** `useSearchParams()` opts the route out of static prerendering, so Next requires
 * it to sit under a Suspense boundary (the production build fails otherwise). */
export default function ScreenerPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 to-cyan-50">
          <LoadingSpinner size="large" />
        </div>
      }
    >
      <ScreenerPageContent />
    </Suspense>
  );
}

function ScreenerPageContent() {
  const searchParams = useSearchParams();
  const initialFilter = searchParams.get('filter');

  const { start: startProgress, done: doneProgress } = useTopProgress();

  const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
  const [presets, setPresets] = useState<FilterPreset[]>([]);
  const [results, setResults] = useState<ScreenerResult[]>([]);
  const [totalMatches, setTotalMatches] = useState(0);
  // `loading` gates the whole results area (only true before the very first
  // search ever resolves). `searching` covers every search including
  // filter/preset changes — those keep the existing table mounted and dimmed
  // instead of wiping it for a spinner (UX audit finding F5).
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const hasLoadedOnceRef = useRef(false);

  const [filters, setFilters] = useState<ScreenerFilterRequest>({
    limit: 50,
    sort_by: 'price_change_pct',
    sort_order: 'desc',
    ...quickFilterFromParam(initialFilter),
  });
  // The first search runs with the filters the page was opened with; later
  // changes only apply through the Apply button / presets.
  const initialFiltersRef = useRef(filters);

  // Load filter options + presets once
  useEffect(() => {
    (async () => {
      try {
        const [opts, presetResp] = await Promise.all([
          screenerApi.getFilterOptions(),
          screenerApi.getPresets(),
        ]);
        setFilterOptions(opts);
        setPresets(presetResp.presets);
      } catch {
        // filter chrome is optional; search still works without it
      }
    })();
  }, []);

  const runSearch = useCallback(async (f: ScreenerFilterRequest) => {
    const isFirstLoad = !hasLoadedOnceRef.current;
    try {
      setSearching(true);
      if (isFirstLoad) setLoading(true);
      else startProgress();
      setError(null);
      const resp = await screenerApi.search(f);
      setResults(resp.results);
      setTotalMatches(resp.total_matches);
      hasLoadedOnceRef.current = true;
    } catch {
      setError('Failed to load screener results');
    } finally {
      setSearching(false);
      if (isFirstLoad) setLoading(false);
      else doneProgress();
    }
  }, [startProgress, doneProgress]);

  useEffect(() => {
    runSearch(initialFiltersRef.current);
  }, [runSearch]);

  const applyFilters = () => {
    setActivePreset(null);
    runSearch(filters);
  };

  const applyPreset = async (preset: FilterPreset) => {
    setActivePreset(preset.name);
    setFilters({ ...filters, ...preset.filters });
    const isFirstLoad = !hasLoadedOnceRef.current;
    try {
      setSearching(true);
      if (isFirstLoad) setLoading(true);
      else startProgress();
      setError(null);
      const resp = await screenerApi.searchWithPreset(preset.name.toLowerCase().replace(/ /g, '-'));
      setResults(resp.results);
      setTotalMatches(resp.total_matches);
      hasLoadedOnceRef.current = true;
    } catch {
      setError('Failed to apply preset');
    } finally {
      setSearching(false);
      if (isFirstLoad) setLoading(false);
      else doneProgress();
    }
  };

  const updateFilter = <K extends keyof ScreenerFilterRequest>(key: K, value: ScreenerFilterRequest[K]) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  const sectorItems = useMemo(() => filterOptions?.sectors || [], [filterOptions]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-7xl">
        <div className="mb-6 p-4 bg-white rounded-lg shadow-sm border border-slate-200">
          <h1 className="text-2xl font-bold text-slate-800">Stock Screener</h1>
          <p className="text-slate-600 text-sm mt-1">Filter the full universe by technicals, fundamentals, and quality</p>
        </div>

        {/* Presets */}
        {presets.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-4">
            {presets.map((p) => (
              <Button
                key={p.name}
                size="sm"
                variant={activePreset === p.name ? 'solid' : 'bordered'}
                className={activePreset === p.name ? 'bg-slate-700 text-white text-xs' : 'text-xs border-slate-300'}
                onClick={() => applyPreset(p)}
              >
                {p.name}
              </Button>
            ))}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          {/* Filter sidebar */}
          <Card className="shadow-sm border border-slate-200 lg:col-span-1 h-fit">
            <CardHeader className="p-3 pb-2">
              <h3 className="text-sm font-semibold text-slate-700">Filters</h3>
            </CardHeader>
            <Divider className="bg-slate-200" />
            <CardBody className="p-4 space-y-4">
              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="number"
                  label="Min Price"
                  size="sm"
                  value={filters.min_price?.toString() || ''}
                  onChange={(e) => updateFilter('min_price', e.target.value ? Number(e.target.value) : undefined)}
                />
                <Input
                  type="number"
                  label="Max Price"
                  size="sm"
                  value={filters.max_price?.toString() || ''}
                  onChange={(e) => updateFilter('max_price', e.target.value ? Number(e.target.value) : undefined)}
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="number"
                  label="Min RSI"
                  size="sm"
                  value={filters.min_rsi?.toString() || ''}
                  onChange={(e) => updateFilter('min_rsi', e.target.value ? Number(e.target.value) : undefined)}
                />
                <Input
                  type="number"
                  label="Max RSI"
                  size="sm"
                  value={filters.max_rsi?.toString() || ''}
                  onChange={(e) => updateFilter('max_rsi', e.target.value ? Number(e.target.value) : undefined)}
                />
              </div>

              <Input
                type="number"
                label="Min Volume Ratio"
                size="sm"
                value={filters.min_volume_ratio?.toString() || ''}
                onChange={(e) => updateFilter('min_volume_ratio', e.target.value ? Number(e.target.value) : undefined)}
              />

              <Input
                type="number"
                label="Min Quality Score"
                size="sm"
                value={filters.min_quality_score?.toString() || ''}
                onChange={(e) => updateFilter('min_quality_score', e.target.value ? Number(e.target.value) : undefined)}
              />

              <Select
                label="Sectors"
                selectionMode="multiple"
                size="sm"
                selectedKeys={new Set(filters.sectors || [])}
                onSelectionChange={(keys) => updateFilter('sectors', Array.from(keys as Set<string>))}
              >
                {sectorItems.map((s) => (
                  <SelectItem key={s.name} value={s.name}>
                    {`${s.name} (${s.stock_count})`}
                  </SelectItem>
                ))}
              </Select>

              <Select
                label="Quality Grades"
                selectionMode="multiple"
                size="sm"
                selectedKeys={new Set(filters.quality_grades || [])}
                onSelectionChange={(keys) => updateFilter('quality_grades', Array.from(keys as Set<string>))}
              >
                {QUALITY_GRADES.map((g) => (
                  <SelectItem key={g} value={g}>
                    {g}
                  </SelectItem>
                ))}
              </Select>

              <Select
                label="Sort By"
                size="sm"
                selectedKeys={new Set([filters.sort_by || 'price_change_pct'])}
                onSelectionChange={(keys) => updateFilter('sort_by', Array.from(keys as Set<string>)[0])}
              >
                {SORT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </Select>

              <Button className="w-full bg-slate-700 text-white" size="sm" onClick={applyFilters} isLoading={searching}>
                Apply Filters
              </Button>
            </CardBody>
          </Card>

          {/* Results */}
          <Card className="shadow-sm border border-slate-200 lg:col-span-3">
            <CardHeader className="p-3 pb-2 flex justify-between items-center">
              <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
                Results {!loading && `(${totalMatches})`}
                {searching && !loading && (
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-500 animate-pulse" />
                )}
              </h3>
            </CardHeader>
            <Divider className="bg-slate-200" />
            <CardBody className="p-0">
              {loading ? (
                <div className="flex justify-center py-12">
                  <LoadingSpinner size="large" />
                </div>
              ) : error && results.length === 0 ? (
                <ErrorAlert message={error} onRetry={() => runSearch(filters)} className="m-4" />
              ) : results.length === 0 ? (
                <p className="text-center text-slate-400 text-sm py-12">No stocks match these filters.</p>
              ) : (
                <>
                  {error && (
                    <ErrorAlert
                      title="Refresh failed"
                      message={`${error} — showing previous results.`}
                      onRetry={() => runSearch(filters)}
                      className="m-3"
                    />
                  )}
                  <div className={searching ? 'opacity-50 pointer-events-none transition-opacity' : 'transition-opacity'}>
                    <Table
                      removeWrapper
                      className="text-xs"
                      classNames={{ th: 'bg-slate-50 text-slate-600 font-medium text-xs h-8', td: 'text-xs py-2' }}
                    >
                      <TableHeader>
                        <TableColumn>SYMBOL</TableColumn>
                        <TableColumn align="end">PRICE</TableColumn>
                        <TableColumn align="end">CHANGE</TableColumn>
                        <TableColumn align="end">VOLUME</TableColumn>
                        <TableColumn align="end">RSI</TableColumn>
                        <TableColumn>SECTOR</TableColumn>
                        <TableColumn>GRADE</TableColumn>
                        <TableColumn>STRENGTH</TableColumn>
                      </TableHeader>
                      <TableBody>
                        {results.map((stock) => (
                          <TableRow key={stock.symbol} className="hover:bg-slate-50">
                            <TableCell>
                              <Link href={`/stock/${stock.symbol}`} className="font-semibold text-slate-800 hover:text-cyan-700">
                                {stock.symbol}
                              </Link>
                            </TableCell>
                            <TableCell className="text-right text-slate-700">${stock.current_price.toFixed(2)}</TableCell>
                            <TableCell className="text-right">
                              <span className={`font-semibold ${stock.price_change_pct >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                                {stock.price_change_pct >= 0 ? '+' : ''}
                                {stock.price_change_pct.toFixed(2)}%
                              </span>
                            </TableCell>
                            <TableCell className="text-right text-slate-600">{(stock.volume / 1e6).toFixed(1)}M</TableCell>
                            <TableCell className="text-right text-slate-600">{stock.rsi_14?.toFixed(1) ?? 'N/A'}</TableCell>
                            <TableCell className="text-slate-500">{stock.sector}</TableCell>
                            <TableCell>
                              {stock.quality_grade && (
                                <Chip size="sm" className={`${gradeColor(stock.quality_grade)} text-xs`}>
                                  {stock.quality_grade}
                                </Chip>
                              )}
                            </TableCell>
                            <TableCell className="text-slate-500">{stock.technical_strength}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
