'use client';

import React, { Suspense, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
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
import { MultiSelect, SelectField, TextField } from '@/components/ui/Field';
import SortHeader from '@/components/ui/SortHeader';
import { XMarkIcon } from '@heroicons/react/20/solid';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';

const DEFAULT_FILTERS: ScreenerFilterRequest = { limit: 50, sort_by: 'price_change_pct', sort_order: 'desc' };

/** Columns the backend can sort by (its own `sort_by` values): the screener returns only the top `limit`
 * matches, so re-sorting them in the browser would rank a partial list — sorting has to go to the server. */
const SERVER_SORT: Record<string, string> = { change: 'price_change_pct', volume: 'volume', rsi: 'rsi_14', grade: 'quality_score' };

/** One removable chip per active filter, in plain words. */
function activeFilterChips(f: ScreenerFilterRequest): { id: string; label: string; clear: (f: ScreenerFilterRequest) => ScreenerFilterRequest }[] {
  const chips: { id: string; label: string; clear: (f: ScreenerFilterRequest) => ScreenerFilterRequest }[] = [];
  const num = (key: keyof ScreenerFilterRequest, label: (v: number) => string) => {
    const v = f[key];
    if (typeof v === 'number') chips.push({ id: key, label: label(v), clear: (x) => ({ ...x, [key]: undefined }) });
  };
  num('min_price', (v) => `Price ≥ $${v}`);
  num('max_price', (v) => `Price ≤ $${v}`);
  num('min_price_change', (v) => `Change ≥ ${v}%`);
  num('max_price_change', (v) => `Change ≤ ${v}%`);
  num('min_rsi', (v) => `RSI ≥ ${v}`);
  num('max_rsi', (v) => `RSI ≤ ${v}`);
  num('min_volume_ratio', (v) => `Vol ratio ≥ ${v}×`);
  num('min_quality_score', (v) => `Quality ≥ ${v}`);
  for (const sector of f.sectors ?? []) {
    chips.push({ id: `sector:${sector}`, label: sector, clear: (x) => ({ ...x, sectors: (x.sectors ?? []).filter((v) => v !== sector) }) });
  }
  if (f.quality_grades?.length) {
    chips.push({ id: 'grades', label: `Grade ${f.quality_grades.join(', ')}`, clear: (x) => ({ ...x, quality_grades: [] }) });
  }
  return chips;
}

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
        <div className="min-h-screen flex items-center justify-center bg-slate-50">
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
  const router = useRouter();
  // Each search gets an id; only the newest one may write results, so a slow older response can't overwrite a
  // newer one while the user is still typing.
  const requestIdRef = useRef(0);
  // JSON of the filters the results on screen were (or are being) fetched for — the live-filter effect below
  // only searches when the filters actually differ from it.
  const lastSearchedRef = useRef<string | null>(null);

  const [filters, setFilters] = useState<ScreenerFilterRequest>({
    limit: 50,
    sort_by: 'price_change_pct',
    sort_order: 'desc',
    ...quickFilterFromParam(initialFilter),
  });
  // The first search runs with the filters the page was opened with; later
  // changes re-run automatically (see the live-filter effect below).
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
    const requestId = ++requestIdRef.current;
    lastSearchedRef.current = JSON.stringify(f);
    try {
      setSearching(true);
      if (isFirstLoad) setLoading(true);
      else startProgress();
      setError(null);
      const resp = await screenerApi.search(f);
      if (requestId !== requestIdRef.current) return;
      setResults(resp.results);
      setTotalMatches(resp.total_matches);
      hasLoadedOnceRef.current = true;
    } catch {
      if (requestId === requestIdRef.current) setError('Failed to load screener results');
    } finally {
      if (requestId === requestIdRef.current) {
        setSearching(false);
        if (isFirstLoad) setLoading(false);
      }
      if (!isFirstLoad) doneProgress();
    }
  }, [startProgress, doneProgress]);

  useEffect(() => {
    runSearch(initialFiltersRef.current);
  }, [runSearch]);

  // Live filters: re-run shortly after the last edit (typing a number shouldn't fire a search per keystroke).
  useEffect(() => {
    if (JSON.stringify(filters) === lastSearchedRef.current) return;
    const timer = setTimeout(() => runSearch(filters), 450);
    return () => clearTimeout(timer);
  }, [filters, runSearch]);

  const applyPreset = async (preset: FilterPreset) => {
    setActivePreset(preset.name);
    const merged = { ...filters, ...preset.filters };
    // The preset endpoint does this search; mark it so the live-filter effect doesn't repeat it.
    lastSearchedRef.current = JSON.stringify(merged);
    setFilters(merged);
    const isFirstLoad = !hasLoadedOnceRef.current;
    const requestId = ++requestIdRef.current;
    try {
      setSearching(true);
      if (isFirstLoad) setLoading(true);
      else startProgress();
      setError(null);
      const resp = await screenerApi.searchWithPreset(preset.name.toLowerCase().replace(/ /g, '-'));
      if (requestId !== requestIdRef.current) return;
      setResults(resp.results);
      setTotalMatches(resp.total_matches);
      hasLoadedOnceRef.current = true;
    } catch {
      if (requestId === requestIdRef.current) setError('Failed to apply preset');
    } finally {
      if (requestId === requestIdRef.current) {
        setSearching(false);
        if (isFirstLoad) setLoading(false);
      }
      if (!isFirstLoad) doneProgress();
    }
  };

  const updateFilter = <K extends keyof ScreenerFilterRequest>(key: K, value: ScreenerFilterRequest[K]) => {
    setActivePreset(null);
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  const sortBy = (column: keyof typeof SERVER_SORT) => {
    const key = SERVER_SORT[column];
    setActivePreset(null);
    setFilters((prev) => ({
      ...prev,
      sort_by: key,
      sort_order: prev.sort_by === key && prev.sort_order !== 'asc' ? 'asc' : 'desc',
    }));
  };
  const sortHeader = (column: keyof typeof SERVER_SORT, label: string, align: 'start' | 'end' = 'end') => (
    <SortHeader
      label={label}
      align={align}
      active={filters.sort_by === SERVER_SORT[column]}
      dir={filters.sort_order ?? 'desc'}
      onClick={() => sortBy(column)}
    />
  );
  const chips = activeFilterChips(filters);

  const sectorItems = useMemo(() => filterOptions?.sectors || [], [filterOptions]);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-7xl">
        <div className="mb-6 px-1 pt-2 pb-1">
          <h1 className="large-title">Stock Screener</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">Filter the full universe by technicals, fundamentals, and quality</p>
        </div>

        {/* Presets */}
        {presets.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-4">
            {presets.map((p) => (
              <Button
                key={p.name}
                size="sm"
                variant={activePreset === p.name ? 'solid' : 'bordered'}
                className={activePreset === p.name ? 'rounded-full bg-slate-900 text-white text-xs' : 'rounded-full text-xs border-slate-300 text-slate-700'}
                onClick={() => applyPreset(p)}
              >
                {p.name}
              </Button>
            ))}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          {/* Filter sidebar */}
          <Card className="border border-black/[0.05] shadow-sm lg:col-span-1 h-fit overflow-visible">
            <CardHeader className="px-5 pt-5 pb-1">
              <h3 className="text-[15px] font-semibold text-slate-900">Filters</h3>
            </CardHeader>
            <CardBody className="px-5 pb-5 pt-3 space-y-4">
              <div className="grid grid-cols-2 gap-2">
                <TextField
                  type="number"
                  label="Min Price"
                  placeholder="Any"
                  value={filters.min_price?.toString() || ''}
                  onChange={(e) => updateFilter('min_price', e.target.value ? Number(e.target.value) : undefined)}
                />
                <TextField
                  type="number"
                  label="Max Price"
                  placeholder="Any"
                  value={filters.max_price?.toString() || ''}
                  onChange={(e) => updateFilter('max_price', e.target.value ? Number(e.target.value) : undefined)}
                />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <TextField
                  type="number"
                  label="Min RSI"
                  placeholder="Any"
                  value={filters.min_rsi?.toString() || ''}
                  onChange={(e) => updateFilter('min_rsi', e.target.value ? Number(e.target.value) : undefined)}
                />
                <TextField
                  type="number"
                  label="Max RSI"
                  placeholder="Any"
                  value={filters.max_rsi?.toString() || ''}
                  onChange={(e) => updateFilter('max_rsi', e.target.value ? Number(e.target.value) : undefined)}
                />
              </div>

              <TextField
                  type="number"
                  label="Min Volume Ratio"
                  placeholder="Any"
                  value={filters.min_volume_ratio?.toString() || ''}
                  onChange={(e) => updateFilter('min_volume_ratio', e.target.value ? Number(e.target.value) : undefined)}
                />

              <TextField
                  type="number"
                  label="Min Quality Score"
                  placeholder="Any"
                  value={filters.min_quality_score?.toString() || ''}
                  onChange={(e) => updateFilter('min_quality_score', e.target.value ? Number(e.target.value) : undefined)}
                />

              <MultiSelect
                label="Sectors"
                placeholder="All sectors"
                options={sectorItems.map((s) => ({ value: s.name, label: s.name, hint: String(s.stock_count) }))}
                selected={filters.sectors || []}
                onChange={(v) => updateFilter('sectors', v)}
              />

              <MultiSelect
                label="Quality Grades"
                placeholder="All grades"
                options={QUALITY_GRADES.map((g) => ({ value: g, label: g }))}
                selected={filters.quality_grades || []}
                onChange={(v) => updateFilter('quality_grades', v)}
              />

              <SelectField
                label="Sort By"
                value={filters.sort_by || 'price_change_pct'}
                options={SORT_OPTIONS}
                onChange={(v) => updateFilter('sort_by', v)}
              />

              <p className="text-xs text-slate-400">Results update as you change filters.</p>
            </CardBody>
          </Card>

          {/* Results */}
          <Card className="border border-black/[0.05] shadow-sm lg:col-span-3">
            <CardHeader className="px-5 pt-5 pb-3 flex flex-col items-start gap-2.5">
              <h3 className="text-[15px] font-semibold text-slate-900 flex items-center gap-2">
                Results {!loading && <span className="font-normal tabular-nums text-slate-400">{totalMatches.toLocaleString()}</span>}
                {searching && !loading && (
                  <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
                )}
              </h3>
              {chips.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {chips.map((chip) => (
                    <span key={chip.id} className="inline-flex items-center gap-1 rounded-full bg-accent-soft py-0.5 pl-2.5 pr-1 text-xs font-medium text-accent">
                      {chip.label}
                      <button
                        type="button"
                        aria-label={`Remove filter ${chip.label}`}
                        onClick={() => {
                          setActivePreset(null);
                          setFilters((prev) => chip.clear(prev));
                        }}
                        className="flex h-4 w-4 items-center justify-center rounded-full hover:bg-accent/15"
                      >
                        <XMarkIcon className="h-3 w-3" />
                      </button>
                    </span>
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      setActivePreset(null);
                      setFilters({ ...DEFAULT_FILTERS, sort_by: filters.sort_by, sort_order: filters.sort_order });
                    }}
                    className="ml-1 text-xs font-medium text-slate-500 hover:text-slate-800"
                  >
                    Clear all
                  </button>
                </div>
              )}
            </CardHeader>
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
                      aria-label="Screener results"
                      onRowAction={(key) => router.push(`/stock/${key}`)}
                      className="text-xs"
                      classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8 first:pl-5 last:pr-5', td: 'text-[13px] py-2 first:pl-5 last:pr-5', tr: 'cursor-pointer' }}
                    >
                      <TableHeader>
                        <TableColumn>SYMBOL</TableColumn>
                        <TableColumn align="end">PRICE</TableColumn>
                        <TableColumn align="end">{sortHeader('change', 'Change')}</TableColumn>
                        <TableColumn align="end">{sortHeader('volume', 'Volume')}</TableColumn>
                        <TableColumn align="end">{sortHeader('rsi', 'RSI')}</TableColumn>
                        <TableColumn>SECTOR</TableColumn>
                        <TableColumn>{sortHeader('grade', 'Grade', 'start')}</TableColumn>
                        <TableColumn>STRENGTH</TableColumn>
                      </TableHeader>
                      <TableBody>
                        {results.map((stock) => (
                          <TableRow key={stock.symbol}>
                            <TableCell>
                              <SymbolHoverLink symbol={stock.symbol} />
                            </TableCell>
                            <TableCell className="text-right tabular-nums text-slate-800">${stock.current_price.toFixed(2)}</TableCell>
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
