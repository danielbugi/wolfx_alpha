// File: frontend/src/components/watchlist/WatchlistPerformance.tsx
'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import SegmentedControl from '@/components/ui/SegmentedControl';
import { useResolvedTheme } from '@/lib/theme';
import {
  Bar, DEFAULT_CHART_SYMBOLS, MAX_CHART_SYMBOLS, PERIODS, Period, PerformanceResult, SeriesResult, biggestMover, chartRows, computePerformance,
  coverageNote, equalWeightBasket, ranked, sectorMix,
} from '@/lib/performance';
import { formatValue } from '@/lib/screenerV2';
import { performanceApi, StockHistory } from '@/services/performanceApi';
import type { ScreenerRow } from '@/services/screenerV2Api';

const SELECTION_KEY = 'watchlist_chart_selection';
const LIGHT = ['#0a84ff', '#ff9f0a', '#30b158', '#bf5af2', '#ff375f', '#00a0b4'];
const DARK = ['#64d2ff', '#ffd60a', '#32d74b', '#da8fff', '#ff6482', '#5ce1e6'];
/** Sector-mix segments: the 5 largest sectors get a hue, everything else is grouped as "Other" in neutral gray */
const SECTOR_HUES = ['#0a84ff', '#bf5af2', '#ff375f', '#ff9f0a', '#30b158'];
const SECTOR_REST = '#8e8e93';

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`);
const pp = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)} pp`);
const tone = (v: number | null | undefined) => (v == null ? 'text-slate-400' : v >= 0 ? 'text-emerald-600' : 'text-red-600');
const pill = (v: number | null | undefined) => (v == null ? 'bg-slate-100 text-slate-500' : v >= 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700');
const shortDate = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const longDate = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const card = 'rounded-2xl border border-black/[0.05] bg-white shadow-sm';

function readSelection(saved: string[]): string[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(SELECTION_KEY) ?? 'null');
    if (Array.isArray(raw)) {
      const kept = raw.filter((s): s is string => typeof s === 'string' && saved.includes(s)).slice(0, MAX_CHART_SYMBOLS);
      if (kept.length) return kept;
    }
  } catch {
    /* storage blocked or unreadable: fall back to the default */
  }
  return saved.slice(0, DEFAULT_CHART_SYMBOLS);
}

function Sparkline({ points, color }: { points: (number | null)[]; color: string }) {
  const vals = points.filter((v): v is number => v != null);
  if (vals.length < 2) return <div className="h-8" />;
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const step = 100 / Math.max(1, points.length - 1);
  let d = '';
  points.forEach((v, i) => {
    if (v == null) return;
    d += `${d === '' || points[i - 1] == null ? 'M' : 'L'}${(i * step).toFixed(2)},${(30 - ((v - lo) / span) * 28 - 1).toFixed(2)} `;
  });
  const zero = 30 - ((0 - lo) / span) * 28 - 1;
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="h-8 w-full" aria-hidden="true" data-testid="sparkline">
      <line x1="0" x2="100" y1={zero} y2={zero} stroke="currentColor" strokeOpacity="0.15" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
      <path d={d.trim()} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Chart tooltip: the session, every line's % from its own start (highest first), and the selected stock that moved most that session. */
export function ChartTip({ active, label, result, symbols, colorOf, theme }: {
  active?: boolean; label?: string; result: PerformanceResult; symbols: string[]; colorOf: (s: string) => string; theme: { tipBg: string; tipText: string; spx: string };
}) {
  if (!active || !label) return null;
  const d = String(label);
  const lines = symbols
    .map((s) => {
      const r = result.series.find((x) => x.symbol === s);
      return { s, v: r && !r.flags.includes('discontinuity') ? r.points.get(d) ?? null : null };
    })
    .filter((x): x is { s: string; v: number } => x.v != null)
    .sort((a, b) => b.v - a.v);
  const spx = result.bench?.get(d) ?? null;
  const mover = biggestMover(result, symbols, d);
  return (
    <div className="min-w-[180px] rounded-xl px-3 py-2 text-[12px] shadow-lg" style={{ background: theme.tipBg, color: theme.tipText }} data-testid="chart-tip">
      <p className="mb-1 text-[11px] opacity-60">{longDate(d)}</p>
      {lines.map(({ s, v }) => (
        <p key={s} className="flex items-center justify-between gap-4 tabular-nums">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: colorOf(s) }} aria-hidden="true" />
            {s}
          </span>
          <span>{pct(v)}</span>
        </p>
      ))}
      {spx != null && (
        <p className="flex items-center justify-between gap-4 tabular-nums opacity-70">
          <span>S&amp;P 500</span>
          <span>{pct(spx)}</span>
        </p>
      )}
      {mover && (
        <p className="mt-1 border-t pt-1" style={{ borderColor: `${theme.spx}55` }} data-testid="chart-tip-mover">
          Biggest move that session: {mover.symbol} <span className={mover.move >= 0 ? 'text-emerald-500' : 'text-red-500'}>{pct(mover.move, 2)}</span>
        </p>
      )}
    </div>
  );
}

/**
 * Watchlist performance dashboard: the selected stocks (up to 6 of the saved 30) and the S&P 500 rebased to 0% on one
 * common session baseline (lib/performance.ts).
 * - Top: a strip of the list's last closes and day changes (the page's ONE Screener request, passed in as `rows`).
 * - Summary: the equal-weight basket of the comparable selected stocks vs the S&P 500, and the list's sector mix.
 * - Chart, ranking table and compact cards.
 * Only the selected symbols' price histories are fetched (plus the S&P 500 once); switching the period reuses them.
 */
export default function WatchlistPerformance({ saved, rows = {}, session = null }: { saved: string[]; rows?: Record<string, ScreenerRow>; session?: string | null }) {
  const theme = useResolvedTheme();
  const palette = theme === 'dark' ? DARK : LIGHT;
  const c = theme === 'dark'
    ? { grid: '#2f2f33', tick: '#98989f', spx: '#98989f', tipBg: '#2c2c2e', tipText: '#f5f5f7' }
    : { grid: '#e3e3e8', tick: '#737378', spx: '#8e8e93', tipBg: '#ffffff', tipText: '#1d1d1f' };

  const [period, setPeriod] = useState<Period>('6m');
  const [selected, setSelected] = useState<string[]>([]);
  const [histories, setHistories] = useState<Record<string, StockHistory | 'error'>>({});
  const [bench, setBench] = useState<Bar[] | 'error' | null>(null);
  const [limitNote, setLimitNote] = useState(false);

  const savedKey = saved.join(',');
  // (re)initialise the selection when the saved list changes; drop symbols that were removed
  useEffect(() => {
    setSelected(readSelection(savedKey ? savedKey.split(',') : []));
  }, [savedKey]);

  useEffect(() => {
    try {
      if (selected.length) window.localStorage.setItem(SELECTION_KEY, JSON.stringify(selected));
    } catch {
      /* per-viewer convenience only */
    }
  }, [selected]);

  useEffect(() => {
    let cancelled = false;
    performanceApi.benchmark().then((b) => !cancelled && setBench(b)).catch(() => !cancelled && setBench('error'));
    return () => {
      cancelled = true;
    };
  }, []);

  const selKey = selected.join(',');
  useEffect(() => {
    let cancelled = false;
    for (const s of selKey ? selKey.split(',') : []) {
      performanceApi
        .stock(s)
        .then((h) => !cancelled && setHistories((m) => ({ ...m, [s]: h })))
        .catch(() => !cancelled && setHistories((m) => ({ ...m, [s]: 'error' })));
    }
    return () => {
      cancelled = true;
    };
  }, [selKey]);

  const loaded = selected.filter((s) => histories[s] && histories[s] !== 'error') as string[];
  const failed = selected.filter((s) => histories[s] === 'error');
  const result = useMemo(
    () =>
      computePerformance(
        period,
        Array.isArray(bench) ? bench : null,
        loaded.map((s) => {
          const h = histories[s] as StockHistory;
          return { symbol: s, bars: h.bars, discontinuities: h.discontinuities };
        }),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [period, bench, loaded.join(','), histories],
  );
  const chartData = useMemo(() => chartRows(result, loaded), [result, loaded.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps
  const basket = useMemo(() => equalWeightBasket(result, loaded), [result, loaded.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps
  const colorOf = (s: string) => palette[selected.indexOf(s) % palette.length];
  const bySymbol = new Map(result.series.map((s) => [s.symbol, s]));
  const benchRet = result.bench && result.end ? result.bench.get(result.end) ?? null : null;
  const unchecked = loaded.filter((s) => !(histories[s] as StockHistory).discontinuitiesChecked);
  const waiting = selected.some((s) => histories[s] === undefined) || bench === null;
  const periodLabel = PERIODS.find((p) => p.value === period)?.label;
  const hasRows = Object.keys(rows).length > 0;

  const mix = useMemo(() => {
    const all = sectorMix(saved, (s) => rows[s]?.sector);
    const top = all.filter((m) => m.sector !== 'Unclassified').slice(0, SECTOR_HUES.length);
    const rest = all.filter((m) => !top.includes(m));
    const restCount = rest.reduce((a, m) => a + m.count, 0);
    return [
      ...top.map((m, i) => ({ ...m, color: SECTOR_HUES[i] })),
      ...(restCount ? [{ sector: rest.length === 1 ? rest[0].sector : 'Other', count: restCount, share: restCount / (saved.length || 1), color: SECTOR_REST }] : []),
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedKey, rows]);

  const toggle = (s: string) => {
    if (selected.includes(s)) {
      setSelected(selected.filter((x) => x !== s));
      setLimitNote(false);
    } else if (selected.length >= MAX_CHART_SYMBOLS) {
      setLimitNote(true); // refused: the chart stays readable; nothing is dropped
    } else {
      setSelected([...selected, s]);
      setLimitNote(false);
    }
  };

  const rankedSeries = ranked(selected.map((s) => bySymbol.get(s)).filter((s): s is SeriesResult => !!s));
  const maxAbs = Math.max(1, ...rankedSeries.map((s) => Math.abs(s.ret ?? 0)), Math.abs(benchRet ?? 0));
  const withRet = rankedSeries.filter((s) => s.ret != null && s.comparable);
  const best = withRet[0];
  const worst = withRet.length > 1 ? withRet[withRet.length - 1] : undefined;

  const bar = (v: number | null, dashed = false, dim = false) => (
    <div className="relative h-2.5 rounded-full bg-slate-100">
      <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300" aria-hidden="true" />
      {v != null && (
        <span
          className={clsx('absolute inset-y-0 rounded-full', dashed ? 'border border-dashed border-slate-400' : v >= 0 ? 'bg-emerald-500' : 'bg-red-500', dim && 'opacity-50')}
          style={v >= 0 ? { left: '50%', width: `${(v / maxAbs) * 50}%` } : { right: '50%', width: `${(-v / maxAbs) * 50}%` }}
        />
      )}
    </div>
  );

  return (
    <div className="space-y-4" data-testid="watchlist-performance">
      {/* last close strip: the list's ONE Screener request, one market session */}
      {hasRows && (
        <section className={clsx(card, 'px-3 py-2.5')} aria-label="Last closes">
          <ul className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-0.5">
            {saved.map((s) => {
              const r = rows[s];
              return (
                <li key={s} className="shrink-0" data-testid="ticker-item">
                  <Link
                    href={`/stock/${s}`}
                    className="inline-flex h-8 items-center gap-2 rounded-full bg-slate-100 px-3 text-[12px] tabular-nums hover:bg-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  >
                    <span className="font-semibold text-slate-900">{s}</span>
                    <span className="text-slate-500">{r?.close != null ? formatValue(r.close, '$') : '—'}</span>
                    <span className={clsx('font-semibold', tone(r?.change_1d))}>{r?.change_1d != null ? formatValue(r.change_1d, '%') : ''}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
          {session && <p className="mt-1.5 px-1 text-[11px] text-slate-400">Closes and day change for the {session} session.</p>}
        </section>
      )}

      <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        {/* summary: two halves side by side above the chart; a column beside it on very wide screens */}
        <section className={clsx(card, 'grid grid-cols-1 gap-x-6 p-4 sm:grid-cols-2 sm:p-5 2xl:grid-cols-1 2xl:content-start')} aria-labelledby="sum-title" data-testid="perf-summary">
          <div className="min-w-0">
            <h2 id="sum-title" className="text-[13px] font-medium text-slate-500">
              Equal-weight watchlist · {periodLabel}
            </h2>
            <p className={clsx('mt-1 text-[34px] font-semibold leading-tight tabular-nums', basket.ret == null ? 'text-slate-400' : 'text-slate-900')} data-testid="basket-ret">
              {waiting && basket.ret == null ? '…' : pct(basket.ret, 2)}
            </p>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-[12px] text-slate-500">
              <span className={clsx('rounded-md px-1.5 py-0.5 font-semibold tabular-nums', pill(basket.vsBench))} data-testid="basket-vs">
                {pp(basket.vsBench)}
              </span>
              vs S&amp;P 500 {basket.benchRet != null ? `(${pct(basket.benchRet)})` : ''}
            </p>
            <dl className="mt-4 grid grid-cols-2 gap-3 text-[12px]">
              <div>
                <dt className="text-slate-400">Beat the S&amp;P 500</dt>
                <dd className="text-[15px] font-semibold tabular-nums text-slate-900" data-testid="beat">
                  {basket.beatOf ? `${basket.beat} of ${basket.beatOf}` : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-slate-400">Best / worst</dt>
                <dd className="text-[13px] font-semibold tabular-nums" data-testid="best-worst">
                  {best ? <span className={tone(best.ret)}>{best.symbol} {pct(best.ret)}</span> : '—'}
                  {worst && <span className={clsx('block', tone(worst.ret))}>{worst.symbol} {pct(worst.ret)}</span>}
                </dd>
              </div>
            </dl>
            <p className="mt-3 text-[11px] leading-snug text-slate-400" data-testid="basket-note">
              The plain average of the {basket.members.length || 'selected'} comparable stock{basket.members.length === 1 ? '' : 's'}&apos; returns from {result.baseline ?? '—'} (equal amounts at that close, held, no rebalancing). A reference for the list, not a portfolio.
              {basket.excluded.length > 0 && ` Not included (no full-period return): ${basket.excluded.join(', ')}.`}
            </p>
          </div>

          {mix.length > 0 && hasRows && (
            <div className="mt-5 min-w-0 border-t border-black/[0.05] pt-4 sm:mt-0 sm:border-l sm:border-t-0 sm:pl-6 sm:pt-0 2xl:mt-5 2xl:border-l-0 2xl:border-t 2xl:pl-0 2xl:pt-4" data-testid="sector-mix">
              <h3 className="text-[13px] font-medium text-slate-500">Sector mix · all {saved.length} saved</h3>
              <div className="mt-2 flex h-3 gap-0.5 overflow-hidden rounded-full" aria-hidden="true">
                {mix.map((m) => (
                  <span key={m.sector} style={{ width: `${m.share * 100}%`, background: m.color }} />
                ))}
              </div>
              <ul className="mt-3 space-y-1.5 text-[12px]">
                {mix.map((m) => (
                  <li key={m.sector} className="flex items-center justify-between gap-2" data-testid="sector-row">
                    <span className="inline-flex min-w-0 items-center gap-2 text-slate-700">
                      <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: m.color }} aria-hidden="true" />
                      <span className="truncate">{m.sector}</span>
                    </span>
                    <span className="tabular-nums text-slate-500">
                      {m.count} · {Math.round(m.share * 100)}%
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] text-slate-400">By number of stocks, using today&apos;s sector classification (not as of past dates).</p>
            </div>
          )}
        </section>

        {/* chart card */}
        <section className={clsx(card, 'min-w-0')} aria-labelledby="perf-title">
          <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 sm:px-5">
            <div className="min-w-0">
              <h2 id="perf-title" className="text-[17px] font-semibold text-slate-900">
                Performance
              </h2>
              <p className="mt-0.5 text-xs text-slate-500" data-testid="perf-window">
                {result.baseline && result.end
                  ? `% change from the close of ${result.baseline} to ${result.end} · S&P 500 dashed`
                  : bench === 'error'
                    ? 'S&P 500 unavailable'
                    : 'Loading…'}
              </p>
            </div>
            <SegmentedControl aria-label="Performance period" value={period} onChange={setPeriod} options={PERIODS} />
          </div>

          {/* selection chips */}
          <div className="mt-3 flex flex-wrap gap-1.5 px-4 sm:px-5" role="group" aria-label="Stocks on the chart">
            {saved.map((s) => {
              const on = selected.includes(s);
              return (
                <button
                  key={s}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(s)}
                  className={clsx(
                    'press inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[12px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
                    on ? 'border-transparent bg-slate-900 text-white' : 'border-slate-300 text-slate-600 hover:border-slate-400',
                  )}
                >
                  {on && <span className="h-2 w-2 rounded-full" style={{ background: colorOf(s) }} aria-hidden="true" />}
                  {s}
                </button>
              );
            })}
          </div>
          <p className="mt-1.5 px-4 text-[11px] text-slate-400 sm:px-5" aria-live="polite">
            {limitNote ? (
              <span className="font-medium text-amber-700" data-testid="limit-note">
                Up to {MAX_CHART_SYMBOLS} stocks at once. Remove one to add another.
              </span>
            ) : (
              `${selected.length} of up to ${MAX_CHART_SYMBOLS} shown · tap a symbol to add or remove it`
            )}
          </p>

          <div className="h-[260px] px-1 pb-2 pt-2 sm:h-[340px] sm:px-2">
            {selected.length === 0 ? (
              <p className="py-20 text-center text-sm text-slate-400">Pick at least one stock above to chart it.</p>
            ) : chartData.length < 2 ? (
              <p className="py-20 text-center text-sm text-slate-400">{waiting ? 'Loading…' : 'Not enough price history in this period.'}</p>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 10, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={c.grid} vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: c.tick, fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={36} tickFormatter={shortDate} />
                  <YAxis tick={{ fill: c.tick, fontSize: 11 }} tickLine={false} axisLine={false} width={44} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
                  <ReferenceLine y={0} stroke={c.tick} strokeOpacity={0.4} />
                  <Tooltip
                    cursor={{ stroke: c.tick, strokeDasharray: '3 3' }}
                    content={<ChartTip result={result} symbols={loaded} colorOf={colorOf} theme={c} />}
                  />
                  {result.bench && <Line isAnimationActive={false} type="linear" dataKey="SPX" name="S&P 500" stroke={c.spx} strokeWidth={1.5} strokeDasharray="5 4" dot={false} />}
                  {loaded
                    .filter((s) => !bySymbol.get(s)?.flags.includes('discontinuity'))
                    .map((s) => (
                      <Line key={s} isAnimationActive={false} type="linear" dataKey={s} name={s} stroke={colorOf(s)} strokeWidth={2} dot={false} activeDot={{ r: 3 }} connectNulls={false} />
                    ))}
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* legend + honest notes */}
          <div className="space-y-1 border-t border-black/[0.05] px-4 py-3 text-[11px] text-slate-500 sm:px-5">
            <p className="flex flex-wrap gap-x-3 gap-y-1">
              {result.bench && (
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: c.spx }} aria-hidden="true" /> S&amp;P 500 (price index)
                </span>
              )}
              {selected.map((s) => (
                <span key={s} className="inline-flex items-center gap-1.5">
                  <span className="inline-block h-0.5 w-4" style={{ background: colorOf(s) }} aria-hidden="true" /> {s}
                  {bySymbol.get(s)?.flags.includes('discontinuity') || histories[s] === 'error' ? <span className="text-slate-400">(not drawn)</span> : null}
                </span>
              ))}
            </p>
            {selected.map((s) => {
              const r = bySymbol.get(s);
              const note = r ? coverageNote(r, result.baseline, result.end) : histories[s] === 'error' ? 'price history unavailable right now' : null;
              return note ? (
                <p key={s} className="text-amber-700" data-testid="coverage-note">
                  {s}: {note}
                  {r && !r.comparable && r.ret != null ? ' (not directly comparable over the full period)' : ''}
                </p>
              ) : null;
            })}
            {bench === 'error' && <p className="text-amber-700">S&amp;P 500 history is unavailable right now, so no relative figures are shown.</p>}
            {unchecked.length > 0 && <p>Price discontinuities were not checked for {unchecked.join(', ')} (this server does not report them).</p>}
            <p data-testid="baseline-rule">
              Each line starts at 0% on the period&apos;s first session: the last close on or before the same date {period === 'ytd' ? 'at the end of last year' : `${periodLabel} earlier`}, on the S&amp;P 500&apos;s trading sessions. A missing close is a gap, never filled.
            </p>
            <p>Returns use stored adjusted closes (splits adjusted); dividends are not consistently included, so this is not a total return. The S&amp;P 500 line is the price index without dividends. Past performance only.</p>
          </div>
        </section>
      </div>

      {selected.length > 0 && (
        <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          {/* ranking table */}
          <section className={clsx(card, 'p-4 sm:p-5')} aria-labelledby="rank-title" data-testid="ranked">
            <h3 id="rank-title" className="text-[15px] font-semibold text-slate-900">
              Ranked over {periodLabel}
            </h3>
            <table className="mt-3 w-full table-fixed text-[13px]">
              <thead>
                <tr className="text-left text-[11px] text-slate-400">
                  <th scope="col" className="w-[4.5rem] pb-2 font-medium">Stock</th>
                  <th scope="col" className="pb-2 font-medium"><span className="sr-only">Return bar</span></th>
                  <th scope="col" className="w-[4.5rem] pb-2 text-right font-medium">Return</th>
                  <th scope="col" className="hidden w-[5rem] pb-2 text-right font-medium sm:table-cell">vs S&amp;P</th>
                  <th scope="col" className="hidden w-[4.5rem] pb-2 text-right font-medium sm:table-cell">Day</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/[0.05]">
                {rankedSeries.map((s) => (
                  <tr key={s.symbol} data-testid="rank-row">
                    <td className="py-2">
                      <Link href={`/stock/${s.symbol}`} className="inline-flex items-center gap-1.5 font-semibold text-slate-900 hover:text-accent hover:underline">
                        {s.symbol}
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: colorOf(s.symbol) }} aria-hidden="true" />
                      </Link>
                    </td>
                    <td className="px-2 py-2">{bar(s.ret, false, !s.comparable)}</td>
                    <td className={clsx('py-2 text-right font-semibold tabular-nums', tone(s.ret))}>
                      {pct(s.ret)}
                      {!s.comparable && s.ret != null && <span title="Covers a shorter window: see the notes on the chart">*</span>}
                    </td>
                    <td className={clsx('hidden py-2 text-right tabular-nums sm:table-cell', tone(s.vsBench))}>{pp(s.vsBench)}</td>
                    <td className={clsx('hidden py-2 text-right tabular-nums sm:table-cell', tone(rows[s.symbol]?.change_1d))}>
                      {rows[s.symbol]?.change_1d != null ? formatValue(rows[s.symbol].change_1d, '%') : '—'}
                    </td>
                  </tr>
                ))}
                {benchRet != null && (
                  <tr className="text-slate-500" data-testid="rank-bench">
                    <td className="py-2">S&amp;P 500</td>
                    <td className="px-2 py-2">{bar(benchRet, true)}</td>
                    <td className={clsx('py-2 text-right tabular-nums', tone(benchRet))}>{pct(benchRet)}</td>
                    <td className="hidden py-2 sm:table-cell" />
                    <td className="hidden py-2 sm:table-cell" />
                  </tr>
                )}
              </tbody>
            </table>
            {rankedSeries.some((s) => !s.comparable && s.ret != null) && (
              <p className="mt-2 text-[11px] text-slate-400">* covers a shorter window than the period (see the notes on the chart).</p>
            )}
            {hasRows && <p className="mt-2 text-[11px] text-slate-400">Day = change on the {session ?? 'latest'} session.</p>}
          </section>

          {/* compact cards */}
          <section aria-label="Selected stocks" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-2">
            {selected.map((s) => {
              const r = bySymbol.get(s);
              const pts = r ? result.sessions.filter((d) => r.points.has(d)).map((d) => r.points.get(d) ?? null) : [];
              const close = rows[s]?.close;
              return (
                <article key={s} className={clsx(card, 'p-4')} data-testid="perf-card">
                  <div className="flex items-center justify-between gap-2">
                    <Link href={`/stock/${s}`} className="inline-flex min-w-0 items-center gap-1.5 text-[15px] font-semibold text-slate-900 hover:text-accent hover:underline">
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: colorOf(s) }} aria-hidden="true" />
                      {s}
                    </Link>
                    <button
                      type="button"
                      onClick={() => toggle(s)}
                      className="press rounded-full px-2 py-1 text-[11px] text-slate-400 hover:bg-black/[0.05] hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                      aria-label={`Remove ${s} from the chart`}
                    >
                      Hide
                    </button>
                  </div>
                  {close != null && <p className="mt-1 text-[18px] font-semibold tabular-nums text-slate-900" data-testid="card-close">{formatValue(close, '$')}</p>}
                  <div className="mt-2 text-slate-400">
                    {r && !r.flags.includes('discontinuity') ? <Sparkline points={pts} color={colorOf(s)} /> : <div className="h-8" />}
                  </div>
                  <dl className="mt-2 grid grid-cols-2 gap-2 text-[12px]">
                    <div>
                      <dt className="text-slate-400">Return</dt>
                      <dd className={clsx('text-[15px] font-semibold tabular-nums', tone(r?.ret))}>{histories[s] === undefined ? '…' : pct(r?.ret)}</dd>
                    </div>
                    <div>
                      <dt className="text-slate-400">vs S&amp;P 500</dt>
                      <dd className={clsx('text-[15px] font-semibold tabular-nums', tone(r?.vsBench))}>{histories[s] === undefined ? '…' : pp(r?.vsBench)}</dd>
                    </div>
                  </dl>
                  {r && r.start && r.end && (
                    <p className="mt-1 text-[11px] text-slate-400">
                      {r.start} → {r.end}
                      {!r.comparable && r.ret != null ? ' · shorter window' : ''}
                    </p>
                  )}
                  {failed.includes(s) && <p className="mt-1 text-[11px] text-amber-700">Price history unavailable right now.</p>}
                </article>
              );
            })}
          </section>
        </div>
      )}
    </div>
  );
}
