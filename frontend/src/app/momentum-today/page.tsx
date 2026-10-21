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
import { ScoreBadge } from '@/components/screener/StrengthScore';
import { formatValue } from '@/lib/screenerV2';
import { sectorHref } from '@/lib/sectors';
import { BAND_LABEL, band } from '@/lib/strength';
import { useWatchlist } from '@/lib/watchlist';

const TILES = 36;
const TILE_TONE = ['border-red-200 bg-red-50', 'border-orange-200 bg-orange-50', 'border-slate-200 bg-white', 'border-emerald-200 bg-emerald-50', 'border-emerald-300 bg-emerald-100'] as const;

function Tile({ r, starred, onStar }: { r: ScreenerRow; starred: boolean; onStar: () => void }) {
  const b = band(r.strength_score);
  return (
    <div className={clsx('relative rounded-xl border p-2.5', b == null ? 'border-slate-200 bg-slate-50' : TILE_TONE[b])} data-testid="momentum-tile">
      <div className="flex items-start justify-between gap-1">
        <Link href={`/stock/${r.symbol}`} className="min-w-0 truncate text-[14px] font-bold text-slate-900 hover:underline">
          {r.symbol}
        </Link>
        <ScoreBadge row={r} size="sm" />
      </div>
      <p className="truncate text-[11px] text-slate-500">{r.industry ?? r.sector ?? '—'}</p>
      <div className="mt-1 flex items-center justify-between">
        <span className={clsx('text-[12px] font-semibold tabular-nums', r.change_1d == null ? 'text-slate-400' : r.change_1d >= 0 ? 'text-emerald-700' : 'text-red-600')}>
          {formatValue(r.change_1d, '%')}
        </span>
        <button
          type="button"
          onClick={onStar}
          aria-pressed={starred}
          aria-label={starred ? `Remove ${r.symbol} from watchlist` : `Add ${r.symbol} to watchlist`}
          className="press inline-flex h-7 w-7 items-center justify-center rounded-full hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {starred ? <StarSolid className="h-4 w-4 text-amber-400" /> : <StarOutline className="h-4 w-4 text-slate-400" />}
        </button>
      </div>
    </div>
  );
}

/**
 * Momentum Today: the session's momentum at a glance, from the Screener's shared engine -- stocks that broke out, new 20-day
 * highs, and the strongest by Strength Score, each stock a tile coloured by its score, plus where the breakouts come from.
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
                  <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
                    {r.rows.slice(0, TILES).map((row) => (
                      <Tile key={row.symbol} r={row} starred={watchlist.has(row.symbol)} onStar={() => void watchlist.toggle(row.symbol)} />
                    ))}
                  </div>
                  {r.total > TILES && (
                    <p className="mt-2 text-[12px] text-slate-500">
                      Showing the {TILES} strongest of {r.total.toLocaleString('en-US')}.{' '}
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
          Tile colour = Strength Score: {BAND_LABEL.map((l, i) => `${l.toLowerCase()} ${i * 20}–${i === 4 ? 100 : i * 20 + 19}`).join(' · ')}; grey = no score
          (data-health gate). Strength Score = relative strength 35%, trend 25%, momentum 20%, volume 10%, liquidity 10%, from the session&apos;s data. It
          describes the session; it is not a forecast or a recommendation.
        </p>
      </div>
    </div>
  );
}
