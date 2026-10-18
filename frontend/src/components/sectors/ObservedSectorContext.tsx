// File: frontend/src/components/sectors/ObservedSectorContext.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';
import type { MarketSnapshot } from '@/services/marketIntelligenceApi';
import DataStamp from '@/components/common/DataStamp';
import { HORIZONS, capturedLabel, describeCount, fmtSigned, isOlderThan, strengthWord } from '@/lib/marketIntel';
import { observedSector } from '@/lib/sectors';

const tone = (v: number | null | undefined) => (v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-600' : v < 0 ? 'text-red-600' : 'text-slate-600');

/**
 * The OBSERVED, point-in-time view of one sector: its rank, strength state, relative returns and breadth as captured
 * for that session by the daily market-intelligence run. Separate from the current-membership figures elsewhere on the
 * page. Missing snapshot, missing sector or a stale session are stated, never filled in.
 */
export default function ObservedSectorContext({ snap, sector, priceDate }: { snap: MarketSnapshot | null; sector: string; priceDate?: string | null }) {
  if (!snap) return null;
  const box = 'rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm';
  if (!snap.available) {
    return (
      <section className={box} data-testid="observed-unavailable">
        <h3 className="text-[15px] font-semibold text-slate-900">Observed sector context</h3>
        <p className="mt-1 text-sm text-slate-500">Not available{snap.message ? `: ${snap.message.replace(/\.\s*$/, '')}` : ` (${snap.reason})`}.</p>
      </section>
    );
  }
  const o = observedSector(snap, sector);
  const stale = isOlderThan(snap.session_date, priceDate);
  return (
    <section className={box} aria-labelledby="observed-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="observed-title" className="text-[15px] font-semibold text-slate-900">Observed sector context</h3>
        <DataStamp label="Session" asOf={snap.session_date} provenance={snap.provenance} source={capturedLabel(snap.meta.captured_at) ?? undefined} stale={stale === true} />
      </div>
      <p className="mt-1 text-xs text-slate-500">
        Point-in-time: captured for that session with the sector assignment known then
        {snap.coverage.sector_map?.pit_safe === false ? ' (this snapshot flags its sector map as not point-in-time)' : ''}.
      </p>
      {stale === true && (
        <p className="mt-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="observed-stale">
          This snapshot is for {snap.session_date}; prices on this page go to {priceDate}.
        </p>
      )}
      {!o ? (
        <p className="mt-3 text-sm text-slate-500" data-testid="observed-missing">
          No observed entry for {sector} in this snapshot (sectors need at least {snap.coverage.min_sector_members ?? '—'} members).
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <span className="text-[22px] font-semibold tabular-nums text-slate-900">
              {o.row.rank_20 != null ? `#${o.row.rank_20}` : '—'}
              <span className="text-sm font-normal text-slate-500"> of {o.rankOf ?? '—'} by 20-session return vs the median stock</span>
            </span>
            <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700">{strengthWord(o.strengthState)}</span>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[420px] text-[13px]">
              <thead>
                <tr className="text-left text-[11px] text-slate-400">
                  <th className="pb-1 font-medium">Sessions</th>
                  <th className="pb-1 text-right font-medium">Median return</th>
                  <th className="pb-1 text-right font-medium">vs median stock</th>
                  <th className="pb-1 text-right font-medium">vs S&amp;P 500</th>
                  <th className="pb-1 text-right font-medium">Members used</th>
                </tr>
              </thead>
              <tbody>
                {HORIZONS.map((h) => {
                  const x = o.row.horizons?.[h];
                  return (
                    <tr key={h} className="border-t border-black/[0.05]">
                      <td className="py-1.5 text-slate-700">{h}</td>
                      <td className={clsx('py-1.5 text-right tabular-nums', tone(x?.ret))}>{fmtSigned(x?.ret, 1)}</td>
                      <td className={clsx('py-1.5 text-right tabular-nums', tone(x?.vs_univ))}>{fmtSigned(x?.vs_univ, 1, ' pp')}</td>
                      <td className={clsx('py-1.5 text-right tabular-nums', tone(x?.vs_spx))}>{fmtSigned(x?.vs_spx, 1, ' pp')}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-500">
                        {x?.n_valid ?? '—'}
                        {x?.n_excluded ? ` (${x.n_excluded} excluded)` : ''}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {([['Above 50-day average', o.aboveSma50], ['Above 200-day average', o.aboveSma200]] as const).map(([label, rec]) => {
              const d = describeCount(rec);
              return (
                <div key={label} className="rounded-xl bg-slate-50 px-3 py-2.5">
                  <p className="text-[11px] font-medium text-slate-500">{label}</p>
                  <p className="text-[15px] font-semibold tabular-nums text-slate-900">
                    {d.ratio}
                    {d.pct && <span className="ml-1.5">{d.pct}</span>}
                  </p>
                  {d.note && <p className="text-[11px] text-slate-400">{d.note}</p>}
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
