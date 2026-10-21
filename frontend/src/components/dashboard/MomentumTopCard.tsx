// File: frontend/src/components/dashboard/MomentumTopCard.tsx
'use client';

import React from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { useMomentumToday } from '@/hooks/useMomentumToday';
import { catalyst, MomentumRow, View, VIEWS } from '@/lib/momentumToday';
import { formatValue } from '@/lib/screenerV2';
import { StrengthIcon } from '@/components/screener/StrengthScore';
import { strengthCellStyle } from '@/lib/strength';

const TOP_N = 5;

/**
 * Overview: the top five of each Momentum Today view (same data, same ranking as that page): Today's Movers and
 * Strength Leaders, each linking to its full table.
 */
export default function MomentumTopCard() {
  const data = useMomentumToday();
  const lists: { view: View; rows: MomentumRow[]; state: 'loading' | 'ready' | 'error' }[] = [
    { view: 'movers', rows: data.movers, state: data.moversState },
    { view: 'strength', rows: data.strength, state: data.strengthState },
  ];
  if (data.allFailed) return null;
  return (
    <section className="mb-6 rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm sm:p-5" aria-labelledby="ov-momentum" data-testid="momentum-top">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="ov-momentum" className="text-[17px] font-semibold text-slate-900">
          Momentum Today
        </h2>
        {data.session && <span className="text-[12px] text-slate-400">Session {data.session}</span>}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {lists.map(({ view, rows, state }) => {
          const meta = VIEWS.find((v) => v.key === view)!;
          return (
            <div key={view} className="min-w-0 rounded-xl bg-slate-50 p-3" data-testid={`top-${view}`}>
              <div className="flex items-baseline justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="text-[15px] font-semibold text-slate-900">{meta.label}</h3>
                  <p className="text-[11px] text-slate-500">{meta.title}</p>
                </div>
                <Link href={`/momentum-today?view=${view}`} className="shrink-0 text-[12px] font-medium text-accent hover:underline">
                  See all →
                </Link>
              </div>
              {state === 'loading' ? (
                <div className="mt-2 h-40 animate-pulse rounded-lg bg-black/[0.04]" aria-busy="true" />
              ) : state === 'error' ? (
                <p className="mt-2 text-[13px] text-slate-500">Unavailable right now.</p>
              ) : rows.length === 0 ? (
                <p className="mt-2 text-[13px] text-slate-500">None on {data.session ?? 'this session'}.</p>
              ) : (
                <ol className="mt-2 space-y-1">
                  {rows.slice(0, TOP_N).map((r) => {
                    const c = catalyst(r);
                    return (
                      <li key={r.symbol} className="flex items-center gap-2 rounded-lg bg-white px-2 py-1.5" data-testid="top-row">
                        <span
                          className={clsx(
                            'inline-flex min-w-[22px] justify-center rounded-full px-1 text-[11px] font-bold tabular-nums',
                            view === 'movers' && r.top_two ? 'bg-emerald-700 text-white' : 'text-slate-500',
                          )}
                          title={view === 'movers' && r.top_two ? "Top 5 in two or more of the session's lists" : undefined}
                        >
                          {r.rank}
                        </span>
                        <Link href={`/stock/${r.symbol}`} className="min-w-0 flex-1">
                          <span className="font-semibold text-slate-900 hover:text-accent" data-testid="top-symbol">
                            {r.symbol}
                          </span>
                          <span className="block truncate text-[11px] text-slate-500">
                            {c.tone === 'reported' ? c.label : (r.company_name ?? r.industry ?? r.sector ?? '')}
                          </span>
                        </Link>
                        <span className={clsx('text-[13px] font-semibold tabular-nums', r.change_1d == null ? 'text-slate-400' : r.change_1d >= 0 ? 'text-emerald-700' : 'text-red-600')}>
                          {formatValue(r.change_1d, '%')}
                        </span>
                        <span
                          className="inline-flex w-[58px] items-center justify-end gap-1 rounded-md px-1 py-0.5 text-[12px] font-bold tabular-nums"
                          style={strengthCellStyle(r.strength_score)}
                          title={r.strength_score == null ? 'No Strength Score' : `Strength Score ${Math.round(r.strength_score)} of 100`}
                        >
                          <StrengthIcon score={r.strength_score} size={14} />
                          {r.strength_score == null ? '—' : Math.round(r.strength_score)}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] text-slate-400">Describes the session only; not a forecast or a recommendation.</p>
    </section>
  );
}
