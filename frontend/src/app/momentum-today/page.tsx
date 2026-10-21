// File: frontend/src/app/momentum-today/page.tsx
'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { StarIcon as StarOutline } from '@heroicons/react/24/outline';
import { StarIcon as StarSolid } from '@heroicons/react/20/solid';
import { screenerV2Api, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';
import { BOARDS } from '@/lib/momentumToday';
import DataStamp from '@/components/common/DataStamp';
import { StrengthIcon } from '@/components/screener/StrengthScore';
import { formatValue } from '@/lib/screenerV2';
import { sectorHref } from '@/lib/sectors';
import { BAND_LABEL, SCORE_PARTS, strengthCellStyle } from '@/lib/strength';
import { useWatchlist } from '@/lib/watchlist';

const PAGE_ROWS = 50;
const PART_COLS = SCORE_PARTS.map((p) => ({ key: p.key, label: p.label.replace('Relative strength', 'RS'), weight: p.weight, hint: p.hint }));

/** One board as a table: strength icon + score, the stock, its day change and the five parts shaded on the green scale. */
function BoardTable({ rows, isStarred, onStar }: { rows: ScreenerRow[]; isStarred: (s: string) => boolean; onStar: (s: string) => void }) {
  const [shown, setShown] = useState(PAGE_ROWS);
  return (
    <>
      <div className="relative mt-3 overflow-x-auto">
        <table className="w-full min-w-[760px] border-separate border-spacing-y-1 text-[13px]" data-testid="momentum-table">
          <thead className="text-[11px] text-slate-500">
            <tr>
              <th className="sticky left-0 z-10 bg-white px-2 py-1 text-left font-medium">Strength</th>
              <th className="px-2 py-1 text-left font-medium">Stock</th>
              <th className="px-2 py-1 text-right font-medium">1D</th>
              {PART_COLS.map((c) => (
                <th key={c.key} className="px-1 py-1 text-center font-medium" title={`${c.label} (${c.weight}% of the score): ${c.hint}`}>
                  {c.label} <span className="text-slate-400">{c.weight}%</span>
                </th>
              ))}
              <th className="px-1 py-1">
                <span className="sr-only">Watchlist</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, shown).map((r) => {
              const sc = strengthCellStyle(r.strength_score);
              return (
                <tr key={r.symbol} data-testid="momentum-row">
                  <td className="sticky left-0 z-10 rounded-l-lg px-2 py-1.5" style={sc}>
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
                  <td className="max-w-[220px] bg-slate-50/60 px-2 py-1.5">
                    <Link href={`/stock/${r.symbol}`} className="font-semibold text-slate-900 hover:text-accent hover:underline">
                      {r.symbol}
                    </Link>
                    <span className="block truncate text-[11px] text-slate-500">{r.industry ?? r.sector ?? '—'}</span>
                  </td>
                  <td className={clsx('bg-slate-50/60 px-2 py-1.5 text-right font-semibold tabular-nums', r.change_1d == null ? 'text-slate-400' : r.change_1d >= 0 ? 'text-emerald-700' : 'text-red-600')}>
                    {formatValue(r.change_1d, '%')}
                  </td>
                  {PART_COLS.map((c) => {
                    const v = r[c.key] as number | null | undefined;
                    return (
                      <td key={c.key} className="px-0.5 py-0.5">
                        <span className="block rounded-md px-1 py-1 text-center tabular-nums" style={strengthCellStyle(v)} data-testid={`part-${c.key}`}>
                          {v == null ? '—' : Math.round(v)}
                        </span>
                      </td>
                    );
                  })}
                  <td className="rounded-r-lg bg-slate-50/60 px-1 py-1 text-center">
                    <button
                      type="button"
                      onClick={() => onStar(r.symbol)}
                      aria-pressed={isStarred(r.symbol)}
                      aria-label={isStarred(r.symbol) ? `Remove ${r.symbol} from watchlist` : `Add ${r.symbol} to watchlist`}
                      className="press inline-flex h-7 w-7 items-center justify-center rounded-full hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      {isStarred(r.symbol) ? <StarSolid className="h-4 w-4 text-amber-400" /> : <StarOutline className="h-4 w-4 text-slate-400" />}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > shown && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px] text-slate-500">
          <span>
            Showing {shown} of {rows.length}
          </span>
          <button type="button" onClick={() => setShown((n) => n + PAGE_ROWS)} className="press rounded-full bg-slate-900 px-3 py-1 font-medium text-white">
            Show {Math.min(PAGE_ROWS, rows.length - shown)} more
          </button>
        </div>
      )}
    </>
  );
}

/**
 * Momentum Today: the session's momentum at a glance, from the Screener's shared engine -- stocks that broke out, new 20-day
 * highs, and the strongest by Strength Score, as tables shaded on one green scale (pale = weak, deep = strong), plus where the
 * breakouts come from.
 * Counts and descriptions of the session only; not a forecast or a recommendation.
 */
export default function MomentumTodayPage() {
  const watchlist = useWatchlist();
  const [results, setResults] = useState<Partial<Record<(typeof BOARDS)[number]['key'], ScreenerResult | 'error'>>>({});

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

  const ok = (k: (typeof BOARDS)[number]['key']) => (results[k] && results[k] !== 'error' ? (results[k] as ScreenerResult) : null);
  const any = ok('breakouts') ?? ok('strongest') ?? ok('new_highs');
  const breakoutSectors = useMemo(() => {
    const r = ok('breakouts');
    if (!r) return [];
    const counts = new Map<string, number>();
    for (const row of r.rows) counts.set(row.sector ?? 'Unclassified', (counts.get(row.sector ?? 'Unclassified') ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results]);
  const allFailed = BOARDS.every((b) => results[b.key] === 'error');

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-[1500px] space-y-4 px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="px-1 pt-2">
          <h1 className="large-title">Momentum Today</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] text-slate-500">
            Where the session&apos;s momentum is: breakouts, new highs and the strongest stocks, each coloured by its Strength Score.
          </p>
          {any && <DataStamp className="mt-1" label="Session" asOf={any.session} source="same data as the Screener" />}
        </div>

        {allFailed && (
          <p className="rounded-xl bg-slate-100 px-3 py-2 text-[13px] text-slate-600" role="alert" data-testid="momentum-error">
            Momentum data is unavailable right now.
          </p>
        )}

        {/* headline counts */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="momentum-headline">
          {BOARDS.map((b) => {
            const r = ok(b.key);
            return (
              <a key={b.key} href={`#${b.key}`} className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm hover:shadow-md">
                <p className="text-[12px] font-medium text-slate-500">{b.title}</p>
                <p className="text-[28px] font-bold tabular-nums text-slate-900">{r ? r.total.toLocaleString('en-US') : results[b.key] === 'error' ? '—' : '…'}</p>
                <p className="text-[11px] text-slate-400">{r ? `of ${r.universe.toLocaleString('en-US')} stocks on ${r.session}` : b.blurb}</p>
              </a>
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

        {/* boards */}
        {BOARDS.map((b) => {
          const r = results[b.key];
          const res = ok(b.key);
          return (
            <section key={b.key} id={b.key} className="scroll-mt-20 rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm" aria-labelledby={`mt-${b.key}`} data-testid={`board-${b.key}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <h2 id={`mt-${b.key}`} className="text-[17px] font-semibold text-slate-900">
                    {b.title} {res && <span className="font-normal text-slate-400">{res.total.toLocaleString('en-US')}</span>}
                  </h2>
                  <p className="text-[12px] text-slate-500">{b.blurb} Strongest first.</p>
                </div>
                <Link href={b.screener} className="text-[13px] font-medium text-accent hover:underline">
                  Open in Screener →
                </Link>
              </div>
              {r === undefined ? (
                <div className="mt-3 h-32 animate-pulse rounded-xl bg-black/[0.03]" aria-busy="true" />
              ) : r === 'error' ? (
                <p className="mt-3 text-sm text-slate-500">This list is unavailable right now.</p>
              ) : r.rows.length === 0 ? (
                <p className="mt-3 text-sm text-slate-500">None on {r.session}.</p>
              ) : (
                <>
                  <BoardTable rows={r.rows} isStarred={watchlist.has} onStar={(s) => void watchlist.toggle(s)} />
                  {r.total > r.rows.length && (
                    <p className="mt-2 text-[12px] text-slate-500">
                      The {r.rows.length} strongest of {r.total.toLocaleString('en-US')} are listed.{' '}
                      <Link href={b.screener} className="font-medium text-accent hover:underline">
                        See all in the Screener
                      </Link>
                    </p>
                  )}
                </>
              )}
            </section>
          );
        })}

        {watchlist.notice && (
          <p role="status" className="text-[13px] text-amber-700" data-testid="watchlist-notice">
            {watchlist.notice}
          </p>
        )}

        <p className="px-1 text-[11px] text-slate-400" data-testid="momentum-legend">
          Shading = strength on one green scale, pale (weak) to deep (strong); the bar icon fills one bar per step:{' '}
          {BAND_LABEL.map((l, i) => `${l.toLowerCase()} ${i * 20}–${i === 4 ? 100 : i * 20 + 19}`).join(' · ')}; grey = no score
          (data-health gate). Strength Score = relative strength 35%, trend 25%, momentum 20%, volume 10%, liquidity 10%, from the session&apos;s data. It
          describes the session; it is not a forecast or a recommendation.
        </p>
      </div>
    </div>
  );
}
