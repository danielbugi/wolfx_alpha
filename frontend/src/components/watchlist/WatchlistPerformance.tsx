// File: frontend/src/components/watchlist/WatchlistPerformance.tsx
'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import SegmentedControl from '@/components/ui/SegmentedControl';
import { useResolvedTheme } from '@/lib/theme';
import {
  Bar, DEFAULT_CHART_SYMBOLS, MAX_CHART_SYMBOLS, PERIODS, Period, SeriesResult, chartRows, computePerformance, coverageNote, ranked,
} from '@/lib/performance';
import { performanceApi, StockHistory } from '@/services/performanceApi';

const SELECTION_KEY = 'watchlist_chart_selection';
const LIGHT = ['#0a84ff', '#ff9f0a', '#30b158', '#bf5af2', '#ff375f', '#00a0b4'];
const DARK = ['#64d2ff', '#ffd60a', '#32d74b', '#da8fff', '#ff6482', '#5ce1e6'];

const pct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`);
const pp = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)} pp`);
const tone = (v: number | null | undefined) => (v == null ? 'text-slate-400' : v >= 0 ? 'text-emerald-600' : 'text-red-600');
const shortDate = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });

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

/**
 * Watchlist performance: the selected stocks (up to 6 of the saved 30) and the S&P 500 rebased to 0% on one common
 * session baseline (lib/performance.ts), with ranked period returns and compact cards. Only the selected symbols' price
 * histories are fetched (plus the S&P 500 once); switching the period reuses them.
 */
export default function WatchlistPerformance({ saved }: { saved: string[] }) {
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
  const rows = useMemo(() => chartRows(result, loaded), [result, loaded.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps
  const colorOf = (s: string) => palette[selected.indexOf(s) % palette.length];
  const bySymbol = new Map(result.series.map((s) => [s.symbol, s]));
  const benchRet = result.bench && result.end ? result.bench.get(result.end) ?? null : null;
  const unchecked = loaded.filter((s) => !(histories[s] as StockHistory).discontinuitiesChecked);
  const waiting = selected.some((s) => histories[s] === undefined) || bench === null;

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

  return (
    <div className="space-y-4" data-testid="watchlist-performance">
      {/* chart card */}
      <section className="rounded-2xl border border-black/[0.05] bg-white shadow-sm" aria-labelledby="perf-title">
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
          ) : rows.length < 2 ? (
            <p className="py-20 text-center text-sm text-slate-400">{waiting ? 'Loading…' : 'Not enough price history in this period.'}</p>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={rows} margin={{ top: 10, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={c.grid} vertical={false} />
                <XAxis dataKey="date" tick={{ fill: c.tick, fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={36} tickFormatter={shortDate} />
                <YAxis tick={{ fill: c.tick, fontSize: 11 }} tickLine={false} axisLine={false} width={44} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
                <ReferenceLine y={0} stroke={c.tick} strokeOpacity={0.4} />
                <Tooltip
                  formatter={(v) => (typeof v === 'number' ? pct(v) : '—')}
                  labelFormatter={(d) => String(d)}
                  contentStyle={{ background: c.tipBg, border: 'none', borderRadius: 10, fontSize: 12, color: c.tipText, boxShadow: '0 4px 16px rgb(0 0 0 / 0.12)' }}
                  labelStyle={{ color: c.tipText }}
                />
                {result.bench && <Line isAnimationActive={false} type="linear" dataKey="SPX" name="S&P 500" stroke={c.spx} strokeWidth={1.5} strokeDasharray="5 4" dot={false} />}
                {loaded
                  .filter((s) => !bySymbol.get(s)?.flags.includes('discontinuity'))
                  .map((s) => (
                    <Line key={s} isAnimationActive={false} type="linear" dataKey={s} name={s} stroke={colorOf(s)} strokeWidth={2} dot={false} connectNulls={false} />
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
          <p>Returns use stored adjusted closes (splits adjusted); dividends are not consistently included, so this is not a total return. The S&amp;P 500 line is the price index without dividends. Past performance only.</p>
        </div>
      </section>

      {/* ranked bars */}
      {selected.length > 0 && (
        <section className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm sm:p-5" aria-labelledby="rank-title" data-testid="ranked">
          <h3 id="rank-title" className="text-[15px] font-semibold text-slate-900">
            Ranked over {PERIODS.find((p) => p.value === period)?.label}
          </h3>
          <ul className="mt-3 space-y-2">
            {rankedSeries.map((s) => (
              <li key={s.symbol} className="grid grid-cols-[3.5rem_minmax(0,1fr)_4.5rem] items-center gap-2 text-[13px]" data-testid="rank-row">
                <Link href={`/stock/${s.symbol}`} className="font-semibold text-slate-900 hover:text-accent hover:underline">
                  {s.symbol}
                </Link>
                <div className="relative h-3 rounded-full bg-slate-100">
                  <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300" aria-hidden="true" />
                  {s.ret != null && (
                    <span
                      className={clsx('absolute inset-y-0 rounded-full', s.ret >= 0 ? 'bg-emerald-500' : 'bg-red-500', !s.comparable && 'opacity-50')}
                      style={s.ret >= 0 ? { left: '50%', width: `${(s.ret / maxAbs) * 50}%` } : { right: '50%', width: `${(-s.ret / maxAbs) * 50}%` }}
                    />
                  )}
                </div>
                <span className={clsx('text-right font-semibold tabular-nums', tone(s.ret))}>
                  {pct(s.ret)}
                  {!s.comparable && s.ret != null && <span title="Covers a shorter window: see the note above">*</span>}
                </span>
              </li>
            ))}
            {benchRet != null && (
              <li className="grid grid-cols-[3.5rem_minmax(0,1fr)_4.5rem] items-center gap-2 text-[13px] text-slate-500" data-testid="rank-bench">
                <span>S&amp;P 500</span>
                <div className="relative h-3 rounded-full bg-slate-100">
                  <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300" aria-hidden="true" />
                  <span
                    className="absolute inset-y-0 rounded-full border border-dashed border-slate-400"
                    style={benchRet >= 0 ? { left: '50%', width: `${(benchRet / maxAbs) * 50}%` } : { right: '50%', width: `${(-benchRet / maxAbs) * 50}%` }}
                  />
                </div>
                <span className={clsx('text-right tabular-nums', tone(benchRet))}>{pct(benchRet)}</span>
              </li>
            )}
          </ul>
          {rankedSeries.some((s) => !s.comparable && s.ret != null) && (
            <p className="mt-2 text-[11px] text-slate-400">* covers a shorter window than the period (see the notes above).</p>
          )}
        </section>
      )}

      {/* compact cards */}
      {selected.length > 0 && (
        <section aria-label="Selected stocks" className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {selected.map((s) => {
            const r = bySymbol.get(s);
            const pts = r ? result.sessions.filter((d) => r.points.has(d)).map((d) => r.points.get(d) ?? null) : [];
            return (
              <article key={s} className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm" data-testid="perf-card">
                <div className="flex items-center justify-between gap-2">
                  <Link href={`/stock/${s}`} className="inline-flex items-center gap-1.5 text-[15px] font-semibold text-slate-900 hover:text-accent hover:underline">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: colorOf(s) }} aria-hidden="true" />
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
      )}
    </div>
  );
}
