// File: frontend/src/app/momentum-today/page.tsx
'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { StarIcon as StarOutline } from '@heroicons/react/24/outline';
import { StarIcon as StarSolid } from '@heroicons/react/20/solid';
import type { ScreenerRow } from '@/services/screenerV2Api';
import {
  applyFilters,
  BOARDS,
  catalyst,
  CatalystTone,
  DAY_LISTS,
  DEFAULT_FILTERS,
  EARNINGS_SOON_DAYS,
  Filters,
  filtersActive,
  Group,
  LIQUIDITY_OPTIONS,
  MomentumRow as Row,
  QUALITY_OPTIONS,
  rankByStrength,
  rankRows,
  SIZE_OPTIONS,
  View,
  VIEWS,
} from '@/lib/momentumToday';
import { useMomentumToday } from '@/hooks/useMomentumToday';
import DataStamp from '@/components/common/DataStamp';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SortHeader from '@/components/ui/SortHeader';
import { StrengthIcon } from '@/components/screener/StrengthScore';
import { useTableSort } from '@/hooks/useTableSort';
import { formatValue } from '@/lib/screenerV2';
import { sectorHref } from '@/lib/sectors';
import { BAND_LABEL, SCORE_PARTS, strengthCellStyle } from '@/lib/strength';
import { useWatchlist } from '@/lib/watchlist';

const GROUP_LABEL: Record<Group, string> = { breakouts: 'Breakout', new_highs: 'New high', lists: 'On the lists', strongest: 'Strong' };
const GROUP_TONE: Record<Group, string> = {
  breakouts: 'bg-emerald-100 text-emerald-800',
  new_highs: 'bg-sky-100 text-sky-800',
  lists: 'bg-violet-100 text-violet-800',
  strongest: 'bg-slate-100 text-slate-700',
};
const CATALYST_TONE: Record<CatalystTone, string> = {
  reported: 'bg-emerald-100 text-emerald-800',
  soon: 'bg-red-100 text-red-700',
  upcoming: 'bg-amber-100 text-amber-800',
  none: 'text-slate-400',
};
const GRADE_TONE: Record<string, string> = {
  A: 'bg-emerald-100 text-emerald-800',
  B: 'bg-sky-100 text-sky-800',
  C: 'bg-slate-100 text-slate-700',
  D: 'bg-amber-100 text-amber-800',
  F: 'bg-red-100 text-red-700',
};
const PAGE_ROWS = 50;
type MoverGroup = 'all' | 'breakouts' | 'new_highs' | 'lists';

/** Sort keys: click a header to sort; blanks always sink to the bottom. */
const ACCESSORS = {
  rank: (r: Row) => r.rank,
  strength: (r: Row) => r.strength_score,
  symbol: (r: Row) => r.symbol,
  ret1: (r: Row) => r.change_1d,
  rvol: (r: Row) => r.volume_ratio,
  lists: (r: Row) => (r.list_ranks ? Object.keys(r.list_ranks).length : null),
  // most recent report first, then the nearest upcoming one; no report = blank
  catalyst: (r: Row) =>
    r.days_since_earnings != null && r.days_since_earnings <= 3 ? -r.days_since_earnings : r.days_to_earnings != null ? -100 - r.days_to_earnings : null,
  size: (r: Row) => r.market_cap,
  quality: (r: Row) => r.quality_score,
  strength_rs: (r: Row) => r.strength_rs,
  strength_trend: (r: Row) => r.strength_trend,
  strength_momentum: (r: Row) => r.strength_momentum,
  strength_volume: (r: Row) => r.strength_volume,
  strength_liquidity: (r: Row) => r.strength_liquidity,
  sector: (r: Row) => r.sector,
  group: (r: Row) => GROUP_LABEL[r.group],
};
type Col = keyof typeof ACCESSORS;
const PART_SHORT: Record<string, string> = { strength_rs: 'RS', strength_trend: 'Trend', strength_momentum: 'Mom', strength_volume: 'Vol', strength_liquidity: 'Liq' };
const PART_COLS = SCORE_PARTS.map((p) => ({ key: p.key as Col, label: p.label, short: PART_SHORT[p.key] ?? p.label, weight: p.weight, hint: p.hint }));

function readView(): View {
  if (typeof window === 'undefined') return 'movers';
  return new URLSearchParams(window.location.search).get('view') === 'strength' ? 'strength' : 'movers';
}

/**
 * Momentum Today: two rankings of the session from the Screener's shared engine.
 *   Today's Movers   — what moved hardest: top 5 in two or more of the session's lists (gainers, range vs ATR, volume surge)
 *                      first, then the biggest 1-day gain (the Telegram digest's rule).
 *   Strength Leaders — strength and trend health: Strength Score 80+, highest first.
 * Both show why a stock may be moving (earnings), its size and a quality check, with filters. Describes the session only.
 */
export default function MomentumTodayPage() {
  const watchlist = useWatchlist();
  const data = useMomentumToday();
  const [view, setView] = useState<View>('movers');
  useEffect(() => setView(readView()), []);
  const choose = (v: View) => {
    setView(v);
    try {
      const u = new URL(window.location.href);
      u.searchParams.set('view', v);
      window.history.replaceState(null, '', u.toString());
    } catch {
      /* the URL is a convenience only */
    }
  };

  const { ok, any, session } = data;
  const topTwo = data.movers.filter((r) => r.top_two).length;
  const breakoutSectors = useMemo(() => {
    const r = ok('breakouts');
    if (!r) return [];
    const counts = new Map<string, number>();
    for (const row of r.rows) counts.set(row.sector ?? 'Unclassified', (counts.get(row.sector ?? 'Unclassified') ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [data.results]); // eslint-disable-line react-hooks/exhaustive-deps

  const count = (k: 'breakouts' | 'new_highs' | 'strongest') => {
    const r = ok(k);
    return r ? r.total.toLocaleString('en-US') : data.results[k] === 'error' ? '—' : '…';
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-[1500px] space-y-4 px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="px-1 pt-2">
          <h1 className="large-title">Momentum Today</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] text-slate-500">
            Two ways to read the session: what moved hardest today, and which stocks have the strongest trends.
          </p>
          {any && <DataStamp className="mt-1" label="Session" asOf={any.session} source="same data as the Screener" />}
        </div>

        {data.allFailed && (
          <p className="rounded-xl bg-slate-100 px-3 py-2 text-[13px] text-slate-600" role="alert" data-testid="momentum-error">
            Momentum data is unavailable right now.
          </p>
        )}

        {/* the two views, with their headline counts (tap to open) */}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2" data-testid="momentum-headline" role="tablist" aria-label="Momentum view">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              type="button"
              role="tab"
              aria-selected={view === v.key}
              onClick={() => choose(v.key)}
              data-testid={`view-${v.key}`}
              className={clsx(
                'rounded-2xl border bg-white p-4 text-left shadow-sm hover:shadow-md',
                view === v.key ? 'border-emerald-300 ring-2 ring-emerald-100' : 'border-black/[0.05]',
              )}
            >
              <p className="text-[17px] font-semibold text-slate-900">{v.label}</p>
              <p className="text-[12px] text-slate-500">{v.title}</p>
              {v.key === 'movers' ? (
                <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-slate-600">
                  <span>
                    <b className="text-[22px] tabular-nums text-slate-900">{data.moversState === 'ready' ? topTwo : '…'}</b> top on 2+ lists
                  </span>
                  <span>
                    <b className="text-[22px] tabular-nums text-slate-900">{count('breakouts')}</b> broke out
                  </span>
                  <span>
                    <b className="text-[22px] tabular-nums text-slate-900">{count('new_highs')}</b> new highs
                  </span>
                </p>
              ) : (
                <p className="mt-2 text-[13px] text-slate-600">
                  <b className="text-[22px] tabular-nums text-slate-900">{count('strongest')}</b> stocks with Strength 80+
                  {any && <span className="text-slate-400"> · of {any.universe.toLocaleString('en-US')} on {any.session}</span>}
                </p>
              )}
            </button>
          ))}
        </div>

        {view === 'movers' && breakoutSectors.length > 0 && (
          <section className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm" aria-labelledby="mt-sectors" data-testid="momentum-sectors">
            <h2 id="mt-sectors" className="text-[15px] font-semibold text-slate-900">
              Where today&apos;s breakouts come from
            </h2>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {breakoutSectors.map(([s, n]) => {
                const href = sectorHref(s);
                const chip = (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-[12px] font-medium text-slate-700">
                    {s} <span className="tabular-nums font-bold text-slate-900">{n}</span>
                  </span>
                );
                return href ? (
                  <Link key={s} href={href} className="hover:opacity-80">
                    {chip}
                  </Link>
                ) : (
                  <span key={s}>{chip}</span>
                );
              })}
            </div>
          </section>
        )}

        <Board
          key={view}
          view={view}
          rows={view === 'movers' ? data.movers : data.strength}
          state={view === 'movers' ? data.moversState : data.strengthState}
          session={session}
          listsNote={view === 'movers' ? data.listsNote : null}
          watchlist={watchlist}
        />

        {watchlist.notice && (
          <p role="status" className="text-[13px] text-amber-700" data-testid="watchlist-notice">
            {watchlist.notice}
          </p>
        )}

        <p className="px-1 text-[11px] text-slate-400" data-testid="momentum-legend">
          Today&apos;s Movers: # = top 5 in two or more of the session&apos;s lists (biggest gainers, biggest range vs ATR, biggest volume
          surge; a filled number), then the biggest 1-day gain. Strength Leaders: # = Strength Score, highest first. Shading = strength on one
          green scale, pale (weak) to deep (strong); the bar icon fills one bar per step:{' '}
          {BAND_LABEL.map((l, i) => `${l.toLowerCase()} ${i * 20}–${i === 4 ? 100 : i * 20 + 19}`).join(' · ')}; grey = no score (data-health
          gate). Strength Score = relative strength 35%, trend 25%, momentum 20%, volume 10%, liquidity 10%, from the session&apos;s data.
          &ldquo;Why it&apos;s moving&rdquo; shows earnings from the earnings calendar only (report time of day not stored). Size, P/E, P/S and
          quality are reference data from the latest fundamentals row (not verified point-in-time); the quality grade is a heuristic from
          reported fundamentals. It describes the session; it is not a forecast or a recommendation.
        </p>
      </div>
    </div>
  );
}

function FilterGroup<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <label className="flex items-center gap-1.5 text-[12px] text-slate-500">
      {label}
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="rounded-full border border-black/[0.08] bg-white px-2.5 py-1 text-[12px] font-medium text-slate-800"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Board({
  view,
  rows: allRows,
  state,
  session,
  listsNote,
  watchlist,
}: {
  view: View;
  rows: Row[];
  state: 'loading' | 'ready' | 'error';
  session: string | null;
  listsNote: string | null;
  watchlist: ReturnType<typeof useWatchlist>;
}) {
  const meta = VIEWS.find((v) => v.key === view)!;
  const [group, setGroup] = useState<MoverGroup>('all');
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [shown, setShown] = useState(PAGE_ROWS);
  const set = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    setShown(PAGE_ROWS);
  };

  const rows = useMemo(() => {
    const grouped = view === 'movers' && group !== 'all' ? allRows.filter((r) => r.group === group) : allRows;
    const filtered = applyFilters(grouped, filters);
    return view === 'movers' ? rankRows(filtered) : rankByStrength(filtered);
  }, [allRows, view, group, filters]);
  const { sorted, sortKey, sortDir, toggle } = useTableSort<Row, Col>(rows, ACCESSORS, { key: 'rank', dir: 'asc' });
  const head = (key: Col, label: string, align: 'start' | 'end' = 'start') => (
    <SortHeader label={label} align={align} active={sortKey === key} dir={sortKey === key ? sortDir : null} onClick={() => toggle(key)} />
  );
  const groupCount = (g: MoverGroup) => (g === 'all' ? allRows.length : allRows.filter((r) => r.group === g).length);
  const screener = view === 'movers' ? (group === 'new_highs' ? BOARDS[1].screener : BOARDS[0].screener) : BOARDS[2].screener;

  const strengthCell = (r: Row) => (
    <td className="px-2 py-1.5" style={strengthCellStyle(r.strength_score)}>
      <span
        className="inline-flex items-center gap-1.5 font-bold tabular-nums"
        title={r.strength_score == null ? `No Strength Score: ${r.strength_reason ?? 'not available'}` : `Strength Score ${Math.round(r.strength_score)} of 100`}
        data-testid="momentum-score"
      >
        <span className="rounded bg-white/80 p-0.5">
          <StrengthIcon score={r.strength_score} size={16} />
        </span>
        {r.strength_score == null ? '—' : Math.round(r.strength_score)}
      </span>
    </td>
  );
  const cell = 'bg-slate-50/60 px-2 py-1.5';

  return (
    <section className="rounded-2xl border border-black/[0.05] bg-white shadow-sm" aria-labelledby="mt-board" data-testid="momentum-board">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 sm:px-5">
        <div className="min-w-0">
          <h2 id="mt-board" className="text-[17px] font-semibold text-slate-900">
            {meta.label}
          </h2>
          <p className="max-w-3xl text-[12px] text-slate-500" data-testid="momentum-board-blurb">
            {meta.blurb} Click a column to sort.
          </p>
        </div>
        <Link href={screener} className="text-[13px] font-medium text-accent hover:underline">
          Open in Screener →
        </Link>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 px-4 sm:px-5" data-testid="momentum-filters">
        {view === 'movers' && (
          <div className="max-w-full overflow-x-auto">
            <SegmentedControl
              aria-label="Movers list"
              value={group}
              onChange={(g: MoverGroup) => {
                setGroup(g);
                setShown(PAGE_ROWS);
              }}
              options={[
                { value: 'all', label: `All ${groupCount('all')}` },
                { value: 'breakouts', label: `Broke out ${groupCount('breakouts')}` },
                { value: 'new_highs', label: `New highs ${groupCount('new_highs')}` },
                ...(groupCount('lists') > 0 ? [{ value: 'lists' as MoverGroup, label: `On the lists ${groupCount('lists')}` }] : []),
              ]}
            />
          </div>
        )}
        <FilterGroup label="Liquidity" value={filters.liquidity} options={LIQUIDITY_OPTIONS} onChange={(liquidity) => set({ liquidity })} />
        <FilterGroup label="Size" value={filters.size} options={SIZE_OPTIONS} onChange={(size) => set({ size })} />
        <FilterGroup label="Quality" value={filters.quality} options={QUALITY_OPTIONS} onChange={(quality) => set({ quality })} />
        <label className="flex items-center gap-1.5 text-[12px] text-slate-600">
          <input type="checkbox" checked={filters.hideEarningsSoon} onChange={(e) => set({ hideEarningsSoon: e.target.checked })} />
          Hide earnings in the next {EARNINGS_SOON_DAYS} days
        </label>
        {filtersActive(filters) && (
          <button type="button" onClick={() => set(DEFAULT_FILTERS)} className="text-[12px] font-medium text-accent hover:underline">
            Reset filters
          </button>
        )}
      </div>

      {state === 'loading' ? (
        <div className="m-4 h-32 animate-pulse rounded-xl bg-black/[0.03]" aria-busy="true" />
      ) : state === 'error' ? (
        <p className="m-4 text-sm text-slate-500">This list is unavailable right now.</p>
      ) : rows.length === 0 ? (
        <p className="m-4 text-sm text-slate-500" data-testid="momentum-empty">
          {allRows.length > 0 ? 'No stocks match these filters.' : session ? `None on ${session}.` : 'No data.'}
        </p>
      ) : (
        <div className="px-2 pb-3 sm:px-3">
          {listsNote && (
            <p className="mx-1 mt-2 rounded-lg bg-amber-50 px-3 py-1.5 text-[12px] text-amber-800" role="status" data-testid="momentum-lists-note">
              {listsNote}
            </p>
          )}
          <div className="relative mt-2 overflow-x-auto">
            <table className="w-full min-w-[1040px] border-separate border-spacing-y-1 text-[13px]" data-testid="momentum-table">
              <thead className="text-[11px] text-slate-500">
                <tr>
                  <th className="sticky left-0 z-10 bg-white px-2 py-1 text-left font-medium">{head('rank', '#')}</th>
                  {view === 'strength' && <th className="px-2 py-1 text-left font-medium">{head('strength', 'Strength')}</th>}
                  <th className="px-2 py-1 text-left font-medium">{head('symbol', 'Symbol')}</th>
                  {view === 'strength' &&
                    PART_COLS.map((c) => (
                      <th key={c.key} className="w-14 px-0.5 py-1 text-center font-medium" title={`${c.label} (${c.weight}% of the score): ${c.hint}`}>
                        {head(c.key, `${c.short} ${c.weight}%`, 'end')}
                      </th>
                    ))}
                  <th className="px-2 py-1 text-right font-medium">{head('ret1', '1D %', 'end')}</th>
                  {view === 'movers' && (
                    <th className="px-2 py-1 text-right font-medium" title="Volume vs its 10-day average">
                      {head('rvol', 'Vol ×', 'end')}
                    </th>
                  )}
                  {view === 'movers' && (
                    <th
                      className="px-2 py-1 text-left font-medium"
                      title="Rank in the session's lists: Gain = biggest 1-day gainers, Range = biggest range vs ATR, Vol = biggest volume surge"
                    >
                      {head('lists', 'Lists')}
                    </th>
                  )}
                  <th className="px-2 py-1 text-left font-medium" title="Earnings from the earnings calendar: reported in the last 3 days, or due in the next 14">
                    {head('catalyst', "Why it's moving")}
                  </th>
                  <th className="px-2 py-1 text-right font-medium" title="Market cap, and the float (shares available to trade)">
                    {head('size', 'Size', 'end')}
                  </th>
                  <th className="px-2 py-1 text-left font-medium" title="Quality grade (heuristic from reported fundamentals) with P/E, or P/S when there is no P/E">
                    {head('quality', 'Quality')}
                  </th>
                  {view === 'movers' && <th className="px-2 py-1 text-left font-medium">{head('strength', 'Strength')}</th>}
                  <th className="px-1 py-1">
                    <span className="sr-only">Watchlist</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.slice(0, shown).map((r) => {
                  const c = catalyst(r);
                  const topTwo = view === 'movers' && !!r.top_two;
                  return (
                    <tr key={r.symbol} data-testid="momentum-row">
                      <td className="sticky left-0 z-10 rounded-l-lg bg-slate-50 px-2 py-1.5 text-center tabular-nums" data-testid="momentum-rank">
                        <span
                          className={clsx('inline-flex min-w-[26px] justify-center rounded-full px-1.5 py-0.5 text-[12px] font-bold', topTwo ? 'bg-emerald-700 text-white' : 'text-slate-500')}
                          title={topTwo ? "Top 5 in two or more of the session's lists" : undefined}
                          data-top-two={topTwo ? 'true' : undefined}
                        >
                          {r.rank}
                        </span>
                      </td>
                      {view === 'strength' && strengthCell(r)}
                      <td className={clsx(cell, 'max-w-[230px]')} title={r.sector ?? undefined}>
                        <span className="flex items-center gap-1.5">
                          <Link href={`/stock/${r.symbol}`} className="font-semibold text-slate-900 hover:text-accent hover:underline" data-testid="momentum-symbol">
                            {r.symbol}
                          </Link>
                          {view === 'movers' && (
                            <span className={clsx('whitespace-nowrap rounded-full px-1.5 text-[10px] font-semibold', GROUP_TONE[r.group])} data-testid="momentum-group">
                              {GROUP_LABEL[r.group]}
                            </span>
                          )}
                        </span>
                        <span className="block truncate text-[11px] text-slate-500" data-testid="momentum-name">
                          {r.company_name ?? r.industry ?? '—'}
                        </span>
                      </td>
                      {view === 'strength' &&
                        PART_COLS.map((col) => {
                          const v = r[col.key as keyof ScreenerRow] as number | null | undefined;
                          return (
                            <td key={col.key} className="px-0.5 py-0.5">
                              <span className="block rounded-md px-1 py-1 text-center tabular-nums" style={strengthCellStyle(v)} data-testid={`part-${col.key}`}>
                                {v == null ? '—' : Math.round(v)}
                              </span>
                            </td>
                          );
                        })}
                      <td className={clsx(cell, 'text-right font-semibold tabular-nums', r.change_1d == null ? 'text-slate-400' : r.change_1d >= 0 ? 'text-emerald-700' : 'text-red-600')}>
                        {formatValue(r.change_1d, '%')}
                      </td>
                      {view === 'movers' && <td className={clsx(cell, 'text-right tabular-nums text-slate-700')}>{formatValue(r.volume_ratio, 'x')}</td>}
                      {view === 'movers' && (
                        <td className={cell} data-testid="momentum-lists">
                          {r.list_ranks && Object.keys(r.list_ranks).length > 0 ? (
                            <span className="flex w-[150px] flex-wrap gap-1">
                              {DAY_LISTS.filter((l) => r.list_ranks![l.key] != null).map((l) => (
                                <span
                                  key={l.key}
                                  className={clsx(
                                    'whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-semibold tabular-nums',
                                    r.list_ranks![l.key] <= 5 ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600',
                                  )}
                                >
                                  {l.label} #{r.list_ranks![l.key]}
                                </span>
                              ))}
                            </span>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                      )}
                      <td className={cell} data-testid="momentum-catalyst">
                        <span title={c.title} className={clsx('whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-semibold', CATALYST_TONE[c.tone])}>
                          {c.label}
                        </span>
                      </td>
                      <td className={clsx(cell, 'text-right tabular-nums')} data-testid="momentum-size">
                        <span className="text-slate-800">{formatValue(r.market_cap, '$')}</span>
                        <span className="block whitespace-nowrap text-[11px] text-slate-400">{r.float_shares != null ? `float ${formatValue(r.float_shares, 'shares')}` : ''}</span>
                      </td>
                      <td className={cell} data-testid="momentum-quality">
                        <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                          {r.quality_grade ? (
                            <span className={clsx('rounded px-1.5 py-0.5 text-[11px] font-bold', GRADE_TONE[r.quality_grade] ?? 'bg-slate-100 text-slate-700')}>{r.quality_grade}</span>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                          <span className="text-[11px] tabular-nums text-slate-500">
                            {r.pe_ratio != null ? `P/E ${r.pe_ratio.toFixed(1)}` : r.ps_ratio != null ? `P/S ${r.ps_ratio.toFixed(1)}` : ''}
                          </span>
                        </span>
                      </td>
                      {view === 'movers' && strengthCell(r)}
                      <td className="rounded-r-lg bg-slate-50/60 px-1 py-1 text-center">
                        <button
                          type="button"
                          onClick={() => void watchlist.toggle(r.symbol)}
                          aria-pressed={watchlist.has(r.symbol)}
                          aria-label={watchlist.has(r.symbol) ? `Remove ${r.symbol} from watchlist` : `Add ${r.symbol} to watchlist`}
                          className="press inline-flex h-7 w-7 items-center justify-center rounded-full hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                        >
                          {watchlist.has(r.symbol) ? <StarSolid className="h-4 w-4 text-amber-400" /> : <StarOutline className="h-4 w-4 text-slate-400" />}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3 px-1 text-[12px] text-slate-500">
            <span data-testid="momentum-showing">
              Showing {Math.min(shown, sorted.length)} of {sorted.length}
            </span>
            {sorted.length > shown && (
              <button type="button" onClick={() => setShown((n) => n + PAGE_ROWS)} className="press rounded-full bg-slate-900 px-3 py-1 font-medium text-white">
                Show {Math.min(PAGE_ROWS, sorted.length - shown)} more
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
