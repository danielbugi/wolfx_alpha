// File: frontend/src/app/momentum-today/page.tsx
'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { StarIcon as StarOutline } from '@heroicons/react/24/outline';
import { StarIcon as StarSolid } from '@heroicons/react/20/solid';
import { screenerV2Api, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';
import { BOARDS, BoardKey, MomentumRow as Row, unionRows } from '@/lib/momentumToday';
import DataStamp from '@/components/common/DataStamp';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SortHeader from '@/components/ui/SortHeader';
import { StrengthIcon } from '@/components/screener/StrengthScore';
import { useTableSort } from '@/hooks/useTableSort';
import { formatValue } from '@/lib/screenerV2';
import { sectorHref } from '@/lib/sectors';
import { BAND_LABEL, SCORE_PARTS, strengthCellStyle } from '@/lib/strength';
import { useWatchlist } from '@/lib/watchlist';

type Tab = BoardKey | 'all';

const GROUP_LABEL: Record<BoardKey, string> = { breakouts: 'Breakout', new_highs: 'New high', strongest: 'Strong' };
const GROUP_TONE: Record<BoardKey, string> = {
  breakouts: 'bg-emerald-100 text-emerald-800',
  new_highs: 'bg-sky-100 text-sky-800',
  strongest: 'bg-slate-100 text-slate-700',
};
const PAGE_ROWS = 50;

/** Sort keys: click a header to sort; blanks always sink to the bottom. */
const ACCESSORS = {
  strength: (r: Row) => r.strength_score,
  symbol: (r: Row) => r.symbol,
  ret1: (r: Row) => r.change_1d,
  rvol: (r: Row) => r.volume_ratio,
  strength_rs: (r: Row) => r.strength_rs,
  strength_trend: (r: Row) => r.strength_trend,
  strength_momentum: (r: Row) => r.strength_momentum,
  strength_volume: (r: Row) => r.strength_volume,
  strength_liquidity: (r: Row) => r.strength_liquidity,
  sector: (r: Row) => r.sector,
  group: (r: Row) => GROUP_LABEL[r.group],
};
type Col = keyof typeof ACCESSORS;
const PART_COLS = SCORE_PARTS.map((p) => ({ key: p.key as Col, label: p.label.replace('Relative strength', 'RS'), weight: p.weight, hint: p.hint }));

/**
 * Momentum Today: the session's momentum from the Screener's shared engine, laid out as one table
 * with tabs (broke out / new 20-day highs / strongest / all) and sortable column headers, strongest first. Strength is
 * shaded on one green scale (pale = weak, deep = strong) with a five-bar icon. Counts and descriptions of the session only;
 * not a forecast or a recommendation.
 */
export default function MomentumTodayPage() {
  const watchlist = useWatchlist();
  const [results, setResults] = useState<Partial<Record<BoardKey, ScreenerResult | 'error'>>>({});
  const [tab, setTab] = useState<Tab>('breakouts');
  const [shown, setShown] = useState(PAGE_ROWS);

  useEffect(() => {
    let cancelled = false;
    for (const b of BOARDS) {
      screenerV2Api
        .search(b.pairs)
        .then((r) => !cancelled && setResults((m) => ({ ...m, [b.key]: r })))
        .catch(() => !cancelled && setResults((m) => ({ ...m, [b.key]: 'error' })));
    }
    return () => {
      cancelled = true;
    };
  }, []);

  const ok = (k: BoardKey) => (results[k] && results[k] !== 'error' ? (results[k] as ScreenerResult) : null);
  const any = ok('breakouts') ?? ok('strongest') ?? ok('new_highs');
  const allFailed = BOARDS.every((b) => results[b.key] === 'error');
  const allLoaded = BOARDS.every((b) => results[b.key] !== undefined);

  const union = useMemo(
    () => unionRows({ breakouts: ok('breakouts')?.rows, new_highs: ok('new_highs')?.rows, strongest: ok('strongest')?.rows }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [results],
  );
  const rows: Row[] = useMemo(() => (tab === 'all' ? union : (ok(tab)?.rows ?? []).map((r) => ({ ...r, group: tab }))), [tab, union, results]); // eslint-disable-line react-hooks/exhaustive-deps
  const { sorted, sortKey, sortDir, toggle } = useTableSort<Row, Col>(rows, ACCESSORS, { key: 'strength', dir: 'desc' });
  const head = (key: Col, label: string, align: 'start' | 'end' = 'start') => (
    <SortHeader label={label} align={align} active={sortKey === key} dir={sortKey === key ? sortDir : null} onClick={() => toggle(key)} />
  );

  const breakoutSectors = useMemo(() => {
    const r = ok('breakouts');
    if (!r) return [];
    const counts = new Map<string, number>();
    for (const row of r.rows) counts.set(row.sector ?? 'Unclassified', (counts.get(row.sector ?? 'Unclassified') ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results]);

  const tabLabel = (k: Tab, text: string) => {
    const n = k === 'all' ? (allLoaded && !allFailed ? union.length : null) : ok(k)?.total ?? null;
    return n != null ? `${text} ${n.toLocaleString('en-US')}` : text;
  };
  const active = tab === 'all' ? null : BOARDS.find((b) => b.key === tab)!;
  const tabState = tab === 'all' ? (allLoaded ? 'ready' : 'loading') : results[tab] === undefined ? 'loading' : results[tab] === 'error' ? 'error' : 'ready';
  const choose = (t: Tab) => {
    setTab(t);
    setShown(PAGE_ROWS);
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-[1500px] space-y-4 px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="px-1 pt-2">
          <h1 className="large-title">Momentum Today</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] text-slate-500">
            Where the session&apos;s momentum is: breakouts, new highs and the strongest stocks, each shaded by its Strength Score.
          </p>
          {any && <DataStamp className="mt-1" label="Session" asOf={any.session} source="same data as the Screener" />}
        </div>

        {allFailed && (
          <p className="rounded-xl bg-slate-100 px-3 py-2 text-[13px] text-slate-600" role="alert" data-testid="momentum-error">
            Momentum data is unavailable right now.
          </p>
        )}

        {/* headline counts (tap to open that tab) */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="momentum-headline">
          {BOARDS.map((b) => {
            const r = ok(b.key);
            return (
              <button
                key={b.key}
                type="button"
                onClick={() => choose(b.key)}
                className={clsx('rounded-2xl border bg-white p-4 text-left shadow-sm hover:shadow-md', tab === b.key ? 'border-emerald-300 ring-2 ring-emerald-100' : 'border-black/[0.05]')}
              >
                <p className="text-[12px] font-medium text-slate-500">{b.title}</p>
                <p className="text-[28px] font-bold tabular-nums text-slate-900">{r ? r.total.toLocaleString('en-US') : results[b.key] === 'error' ? '—' : '…'}</p>
                <p className="text-[11px] text-slate-400">{r ? `of ${r.universe.toLocaleString('en-US')} stocks on ${r.session}` : b.blurb}</p>
              </button>
            );
          })}
        </div>

        {/* where the breakouts come from */}
        {breakoutSectors.length > 0 && (
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

        {/* one table with tabs and sortable headers */}
        <section className="rounded-2xl border border-black/[0.05] bg-white shadow-sm" aria-labelledby="mt-board" data-testid="momentum-board">
          <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 sm:px-5">
            <div className="min-w-0">
              <h2 id="mt-board" className="text-[17px] font-semibold text-slate-900">
                Today&apos;s momentum
              </h2>
              <p className="text-[12px] text-slate-500" data-testid="momentum-board-blurb">
                {active ? active.blurb : 'All three lists together; a stock appears once, under its first list.'} Click a column to sort; strongest first by default.
              </p>
            </div>
            {active && (
              <Link href={active.screener} className="text-[13px] font-medium text-accent hover:underline">
                Open in Screener →
              </Link>
            )}
          </div>
          <div className="mt-3 max-w-full overflow-x-auto px-4 sm:px-5">
            <SegmentedControl
              aria-label="Momentum list"
              value={tab}
              onChange={choose}
              options={[
                { value: 'breakouts', label: tabLabel('breakouts', 'Broke out') },
                { value: 'new_highs', label: tabLabel('new_highs', 'New highs') },
                { value: 'strongest', label: tabLabel('strongest', 'Strongest') },
                { value: 'all', label: tabLabel('all', 'All') },
              ]}
            />
          </div>

          {tabState === 'loading' ? (
            <div className="m-4 h-32 animate-pulse rounded-xl bg-black/[0.03]" aria-busy="true" />
          ) : tabState === 'error' ? (
            <p className="m-4 text-sm text-slate-500">This list is unavailable right now.</p>
          ) : rows.length === 0 ? (
            <p className="m-4 text-sm text-slate-500">{any ? `None on ${any.session}.` : 'No data.'}</p>
          ) : (
            <div className="px-2 pb-3 sm:px-3">
              <div className="relative mt-2 overflow-x-auto">
                <table className="w-full min-w-[980px] border-separate border-spacing-y-1 text-[13px]" data-testid="momentum-table">
                  <thead className="text-[11px] text-slate-500">
                    <tr>
                      <th className="sticky left-0 z-10 bg-white px-2 py-1 text-left font-medium">{head('strength', 'Strength')}</th>
                      <th className="px-2 py-1 text-left font-medium">{head('symbol', 'Symbol')}</th>
                      <th className="px-2 py-1 text-right font-medium">{head('ret1', '1D %', 'end')}</th>
                      <th className="px-2 py-1 text-right font-medium" title="Volume vs its 10-day average">
                        {head('rvol', 'Vol ×', 'end')}
                      </th>
                      {PART_COLS.map((c) => (
                        <th key={c.key} className="px-1 py-1 text-center font-medium" title={`${c.label} (${c.weight}% of the score): ${c.hint}`}>
                          {head(c.key, `${c.label} ${c.weight}%`, 'end')}
                        </th>
                      ))}
                      <th className="px-2 py-1 text-left font-medium">{head('sector', 'Sector')}</th>
                      <th className="px-2 py-1 text-left font-medium">{head('group', 'Group')}</th>
                      <th className="px-1 py-1">
                        <span className="sr-only">Watchlist</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.slice(0, shown).map((r) => (
                      <tr key={r.symbol} data-testid="momentum-row">
                        <td className="sticky left-0 z-10 rounded-l-lg px-2 py-1.5" style={strengthCellStyle(r.strength_score)}>
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
                        <td className="max-w-[200px] bg-slate-50/60 px-2 py-1.5">
                          <Link href={`/stock/${r.symbol}`} className="font-semibold text-slate-900 hover:text-accent hover:underline" data-testid="momentum-symbol">
                            {r.symbol}
                          </Link>
                          <span className="block truncate text-[11px] text-slate-500">{r.industry ?? '—'}</span>
                        </td>
                        <td className={clsx('bg-slate-50/60 px-2 py-1.5 text-right font-semibold tabular-nums', r.change_1d == null ? 'text-slate-400' : r.change_1d >= 0 ? 'text-emerald-700' : 'text-red-600')}>
                          {formatValue(r.change_1d, '%')}
                        </td>
                        <td className="bg-slate-50/60 px-2 py-1.5 text-right tabular-nums text-slate-700">{formatValue(r.volume_ratio, 'x')}</td>
                        {PART_COLS.map((c) => {
                          const v = r[c.key as keyof ScreenerRow] as number | null | undefined;
                          return (
                            <td key={c.key} className="px-0.5 py-0.5">
                              <span className="block rounded-md px-1 py-1 text-center tabular-nums" style={strengthCellStyle(v)} data-testid={`part-${c.key}`}>
                                {v == null ? '—' : Math.round(v)}
                              </span>
                            </td>
                          );
                        })}
                        <td className="max-w-[150px] truncate bg-slate-50/60 px-2 py-1.5 text-slate-600">{r.sector ?? '—'}</td>
                        <td className="bg-slate-50/60 px-2 py-1.5">
                          <span className={clsx('whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold', GROUP_TONE[r.group])} data-testid="momentum-group">
                            {GROUP_LABEL[r.group]}
                          </span>
                        </td>
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
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-3 px-1 text-[12px] text-slate-500">
                <span data-testid="momentum-showing">
                  Showing {Math.min(shown, sorted.length)} of {sorted.length}
                  {active && (ok(active.key)?.total ?? 0) > sorted.length ? ` (the ${sorted.length} strongest of ${ok(active.key)!.total.toLocaleString('en-US')})` : ''}
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

        {watchlist.notice && (
          <p role="status" className="text-[13px] text-amber-700" data-testid="watchlist-notice">
            {watchlist.notice}
          </p>
        )}

        <p className="px-1 text-[11px] text-slate-400" data-testid="momentum-legend">
          Shading = strength on one green scale, pale (weak) to deep (strong); the bar icon fills one bar per step:{' '}
          {BAND_LABEL.map((l, i) => `${l.toLowerCase()} ${i * 20}–${i === 4 ? 100 : i * 20 + 19}`).join(' · ')}; grey = no score (data-health gate).
          Strength Score = relative strength 35%, trend 25%, momentum 20%, volume 10%, liquidity 10%, from the session&apos;s data. It describes the
          session; it is not a forecast or a recommendation.
        </p>
      </div>
    </div>
  );
}
