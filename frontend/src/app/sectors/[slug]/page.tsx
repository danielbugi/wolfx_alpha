'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Card, CardBody } from '@nextui-org/react';
import { ChevronLeftIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { apiClient, formatters } from '@/services/api';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SortHeader from '@/components/ui/SortHeader';
import { Skeleton } from '@/components/ui/Skeleton';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { useTableSort } from '@/hooks/useTableSort';
import { useResolvedTheme } from '@/lib/theme';
import { IndustryRow, rebasedComparison, SECTOR_WINDOWS, SectorDetail, SectorStock, SectorWindow } from '@/lib/sectors';

const fmt = (v: number | null | undefined, d = 2) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`);
const tone = (v: number | null | undefined) => (v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-600' : v < 0 ? 'text-red-600' : 'text-slate-600');
const PAGE = 50;
type StockSortKey = 'symbol' | 'industry' | 'price' | '1d' | '1w' | '1m' | 'ytd' | 'cap' | 'rsi' | 'grade';

type ChartRange = '3m' | '6m' | '1y';
const RANGE_DAYS: Record<ChartRange, number> = { '3m': 91, '6m': 182, '1y': 365 };

function isoDaysBefore(iso: string, days: number) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

function SectorVsMarket({ detail }: { detail: SectorDetail }) {
  const theme = useResolvedTheme();
  const c = theme === 'dark' ? { grid: '#2f2f33', tick: '#98989f', sector: '#64d2ff', spx: '#98989f', tipBg: '#2c2c2e', tipText: '#f5f5f7' } : { grid: '#e3e3e8', tick: '#737378', sector: '#0a84ff', spx: '#8e8e93', tipBg: '#ffffff', tipText: '#1d1d1f' };
  const [range, setRange] = useState<ChartRange>('6m');
  const last = detail.index[detail.index.length - 1]?.date;
  const points = useMemo(
    () => (last ? rebasedComparison(detail.index, detail.spx, isoDaysBefore(last, RANGE_DAYS[range])) : []),
    [detail, last, range],
  );
  const end = points[points.length - 1];

  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pb-1 pt-4">
        <div>
          <h3 className="text-[15px] font-semibold text-slate-900">{detail.sector} vs the S&amp;P 500</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Equal-weighted index of the sector&apos;s stocks, % change over the period
            {end && (
              <>
                {' · '}
                <span className={tone(end.sector)}>{fmt(end.sector, 1)}</span> vs <span className={tone(end.spx)}>{fmt(end.spx, 1)}</span>
              </>
            )}
          </p>
        </div>
        <SegmentedControl
          aria-label="Chart period"
          value={range}
          onChange={setRange}
          options={[
            { value: '3m', label: '3M' },
            { value: '6m', label: '6M' },
            { value: '1y', label: '1Y' },
          ]}
        />
      </div>
      <div className="h-[280px] px-2 pb-3">
        {points.length < 2 ? (
          <p className="py-16 text-center text-sm text-slate-400">Not enough shared history to compare.</p>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={c.grid} vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: c.tick, fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                minTickGap={40}
                tickFormatter={(d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })}
              />
              <YAxis tick={{ fill: c.tick, fontSize: 11 }} tickLine={false} axisLine={false} width={48} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
              <ReferenceLine y={0} stroke={c.tick} strokeOpacity={0.4} />
              <Tooltip
                formatter={(v) => (typeof v === 'number' ? fmt(v, 1) : '—')}
                contentStyle={{ background: c.tipBg, border: 'none', borderRadius: 10, fontSize: 12, color: c.tipText, boxShadow: '0 4px 16px rgb(0 0 0 / 0.12)' }}
                labelStyle={{ color: c.tipText }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line isAnimationActive={false} type="monotone" dataKey="sector" name={detail.sector} stroke={c.sector} strokeWidth={2} dot={false} />
              <Line isAnimationActive={false} type="monotone" dataKey="spx" name="S&P 500" stroke={c.spx} strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}

function Industries({ rows, selected, onSelect }: { rows: IndustryRow[]; selected: string | null; onSelect: (i: string | null) => void }) {
  const { sorted, sortKey, sortDir, toggle } = useTableSort(
    rows,
    {
      industry: (r) => r.industry,
      stocks: (r) => r.stock_count,
      '1d': (r) => r.change['1d'].median,
      '1w': (r) => r.change['1w'].median,
      '1m': (r) => r.change['1m'].median,
      ytd: (r) => r.change.ytd.median,
      above: (r) => r.above_sma50_pct,
    },
    null,
  );
  const head = (key: Parameters<typeof toggle>[0], label: string, end = true) => (
    <th className={clsx('px-3 py-2 text-[11px] font-medium text-slate-500', end ? 'text-right' : 'text-left')}>
      <SortHeader label={label} active={sortKey === key} dir={sortDir} onClick={() => toggle(key)} align={end ? 'end' : 'start'} />
    </th>
  );
  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <div className="px-5 pb-2 pt-4">
        <h3 className="text-[15px] font-semibold text-slate-900">Industries</h3>
        <p className="mt-0.5 text-xs text-slate-500">Median stock per industry · click one to filter the stocks below</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-[13px]">
          <thead className="border-y border-black/[0.06]">
            <tr>
              {head('industry', 'Industry', false)}
              {head('stocks', 'Stocks')}
              {head('1d', '1D')}
              {head('1w', '1W')}
              {head('1m', '1M')}
              {head('ytd', 'YTD')}
              {head('above', '> 50-day')}
            </tr>
          </thead>
          <tbody className="divide-y divide-black/[0.05]">
            {sorted.map((r) => (
              <tr
                key={r.industry}
                onClick={() => onSelect(selected === r.industry ? null : r.industry)}
                className={clsx('cursor-pointer transition-colors hover:bg-black/[0.025]', selected === r.industry && 'bg-accent-soft')}
              >
                <td className="px-3 py-2 pl-5 font-medium text-slate-800">{r.industry}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{r.stock_count}</td>
                {(['1d', '1w', '1m', 'ytd'] as SectorWindow[]).map((w) => (
                  <td key={w} className={clsx('px-3 py-2 text-right font-semibold tabular-nums', tone(r.change[w].median))}>
                    {fmt(r.change[w].median)}
                  </td>
                ))}
                <td className="px-3 py-2 pr-5 text-right tabular-nums text-slate-600">{r.above_sma50_pct != null ? `${r.above_sma50_pct.toFixed(0)}%` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Stocks({ stocks, industry, onClear }: { stocks: SectorStock[]; industry: string | null; onClear: () => void }) {
  const [showAll, setShowAll] = useState(false);
  const rows = useMemo(() => (industry ? stocks.filter((s) => (s.industry ?? 'Other') === industry) : stocks), [stocks, industry]);
  const { sorted, sortKey, sortDir, toggle } = useTableSort<SectorStock, StockSortKey>(
    rows,
    {
      symbol: (s) => s.symbol,
      industry: (s) => s.industry,
      price: (s) => s.close,
      '1d': (s) => s.change_pct['1d'],
      '1w': (s) => s.change_pct['1w'],
      '1m': (s) => s.change_pct['1m'],
      ytd: (s) => s.change_pct.ytd,
      cap: (s) => s.market_cap,
      rsi: (s) => s.rsi_14,
      grade: (s) => s.quality_grade,
    },
    { key: 'cap', dir: 'desc' },
  );
  const visible = showAll ? sorted : sorted.slice(0, PAGE);
  const head = (key: Parameters<typeof toggle>[0], label: string, end = true) => (
    <th className={clsx('px-3 py-2 text-[11px] font-medium text-slate-500', end ? 'text-right' : 'text-left')}>
      <SortHeader label={label} active={sortKey === key} dir={sortDir} onClick={() => toggle(key)} align={end ? 'end' : 'start'} />
    </th>
  );

  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-2 pt-4">
        <div>
          <h3 className="text-[15px] font-semibold text-slate-900">Stocks</h3>
          <p className="mt-0.5 text-xs text-slate-500">{rows.length} stocks · largest first · click a column to sort</p>
        </div>
        {industry && (
          <button type="button" onClick={onClear} className="press inline-flex items-center gap-1 rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
            {industry} <XMarkIcon className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className="overflow-x-auto" data-stock-list>
        <table className="w-full min-w-[860px] text-[13px]">
          <thead className="border-y border-black/[0.06]">
            <tr>
              {head('symbol', 'Symbol', false)}
              {head('industry', 'Industry', false)}
              {head('price', 'Price')}
              {head('1d', '1D')}
              {head('1w', '1W')}
              {head('1m', '1M')}
              {head('ytd', 'YTD')}
              {head('cap', 'Mkt cap')}
              {head('rsi', 'RSI')}
              {head('grade', 'Grade')}
            </tr>
          </thead>
          <tbody className="divide-y divide-black/[0.05]">
            {visible.map((s) => (
              <tr key={s.symbol} className="hover:bg-black/[0.025]">
                <td className="px-3 py-2 pl-5 font-semibold">
                  <SymbolHoverLink symbol={s.symbol} />
                </td>
                <td className="max-w-[220px] truncate px-3 py-2 text-slate-600" title={s.industry ?? undefined}>
                  {s.industry ?? '—'}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-800">{s.close != null ? `$${s.close.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}</td>
                {(['1d', '1w', '1m', 'ytd'] as SectorWindow[]).map((w) => (
                  <td key={w} className={clsx('px-3 py-2 text-right font-semibold tabular-nums', tone(s.change_pct[w]))}>
                    {fmt(s.change_pct[w])}
                  </td>
                ))}
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{s.market_cap != null ? formatters.marketCap(s.market_cap) : '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-600">{s.rsi_14 != null ? s.rsi_14.toFixed(0) : '—'}</td>
                <td className="px-3 py-2 pr-5 text-right text-slate-600">{s.quality_grade ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sorted.length > PAGE && (
        <button type="button" onClick={() => setShowAll((v) => !v)} className="w-full border-t border-black/[0.06] py-2.5 text-[13px] font-medium text-accent hover:bg-black/[0.02]">
          {showAll ? 'Show fewer' : `Show all ${sorted.length}`}
        </button>
      )}
    </Card>
  );
}

/** One sector: how its stocks moved (median, breadth), its trend vs the market, its industries and its stocks. */
export default function SectorPage() {
  const params = useParams();
  const slug = String(params?.slug ?? '');
  const [detail, setDetail] = useState<SectorDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [industry, setIndustry] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    setIndustry(null);
    apiClient
      .get<SectorDetail>(`/api/market/sectors/${encodeURIComponent(slug)}`)
      .then((r) => !cancelled && setDetail(r.data))
      .catch((e) => !cancelled && setError(e?.response?.status === 404 ? 'No such sector.' : 'Sector data is unavailable right now.'));
    return () => {
      cancelled = true;
    };
  }, [slug]);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-7xl space-y-4 px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <Link href="/sectors" className="inline-flex items-center gap-1 px-1 text-sm font-medium text-accent hover:underline">
          <ChevronLeftIcon className="h-4 w-4" /> Sectors
        </Link>

        {error ? (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="py-10 text-center text-sm text-slate-500">{error}</CardBody>
          </Card>
        ) : !detail ? (
          <div className="space-y-4">
            <Skeleton className="h-36 rounded-2xl" />
            <Skeleton className="h-[340px] rounded-2xl" />
            <Skeleton className="h-64 rounded-2xl" />
          </div>
        ) : (
          <>
            <Card className="border border-black/[0.05] shadow-sm">
              <CardBody className="p-0">
                <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 px-5 pb-3 pt-4">
                  <div className="min-w-0">
                    <h1 className="large-title">{detail.sector}</h1>
                    <p className="mt-0.5 text-sm text-slate-500">
                      {detail.stock_count} stocks · {detail.industries.length} industries
                      {detail.market_cap ? ` · ${formatters.marketCap(detail.market_cap)} market cap` : ''}
                      {detail.as_of ? ` · as of ${detail.as_of}` : ''}
                    </p>
                  </div>
                  {detail.above_sma50_pct != null && (
                    <div className="text-right">
                      <p className="text-[22px] font-semibold leading-tight tabular-nums text-slate-900">{detail.above_sma50_pct.toFixed(0)}%</p>
                      <p className="text-[11px] text-slate-500">of stocks above their 50-day average</p>
                    </div>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-y-3 border-t border-black/[0.06] px-4 py-3 sm:grid-cols-6" aria-label="Performance">
                  {SECTOR_WINDOWS.map((w) => {
                    const c = detail.change[w.key];
                    return (
                      <div key={w.key} className="text-center">
                        <p className="text-[11px] font-medium text-slate-500">{w.label}</p>
                        <p className={clsx('text-[15px] font-semibold tabular-nums', tone(c.median))}>{fmt(c.median)}</p>
                        <p className="text-[10.5px] tabular-nums text-slate-400">{c.up_share != null ? `${c.up_share.toFixed(0)}% up` : '—'}</p>
                      </div>
                    );
                  })}
                </div>
              </CardBody>
            </Card>

            <SectorVsMarket detail={detail} />
            <Industries rows={detail.industries} selected={industry} onSelect={setIndustry} />
            <Stocks stocks={detail.stocks} industry={industry} onClear={() => setIndustry(null)} />

            <p className="px-1 text-xs leading-relaxed text-slate-400">
              Changes are the median of the stocks (with the share that rose) over calendar windows. The index is
              equal-weighted, built from today&apos;s members&apos; daily closes (survivorship bias: stocks that left the
              sector aren&apos;t in it); a daily move beyond ±50% is treated as a data error and left out of that day.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
