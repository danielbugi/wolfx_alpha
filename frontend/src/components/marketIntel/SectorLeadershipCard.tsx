// File: frontend/src/components/marketIntel/SectorLeadershipCard.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';
import type { MarketSnapshotAvailable } from '@/services/marketIntelligenceApi';
import Link from 'next/link';
import { describeCount, fmtInt, fmtSigned, sectorLeaders, strengthWord } from '@/lib/marketIntel';
import { sectorHref } from '@/lib/sectors';

const STATE_TONE: Record<string, string> = {
  leading: 'bg-emerald-50 text-emerald-700',
  in_line: 'bg-slate-100 text-slate-600',
  lagging: 'bg-red-50 text-red-700',
};

/** Sectors ranked by 20-session return against the equal-weight universe median (rs_v1 / sector_v1). Medians of member
 * returns, minimum 5 members; the sector assignment's provenance is stated under the table. */
export default function SectorLeadershipCard({ snap }: { snap: MarketSnapshotAvailable }) {
  const rows = sectorLeaders(snap);
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.vsUniverse20 ?? 0)));
  const map = snap.coverage.sector_map;
  return (
    <section aria-labelledby="sectors-title" className="rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="sectors-title" className="text-[17px] font-semibold text-slate-900">Sector leadership</h3>
        <span className="text-xs text-slate-400">20 sessions, vs the median stock</span>
      </div>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-400">No sector data for this session.</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[640px] text-[13px]">
            <thead>
              <tr className="text-left text-[11px] text-slate-400">
                <th className="pb-1 font-medium">#</th>
                <th className="pb-1 font-medium">Sector</th>
                <th className="pb-1 font-medium">vs median stock</th>
                <th className="pb-1 text-right font-medium">Return</th>
                <th className="pb-1 text-right font-medium">vs S&amp;P 500</th>
                <th className="pb-1 text-right font-medium">Above 50-day</th>
                <th className="pb-1 text-right font-medium">State</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const v = r.vsUniverse20;
                const w = v == null ? 0 : (Math.abs(v) / maxAbs) * 50;
                const b = describeCount(r.aboveSma50);
                return (
                  <tr key={r.sector} className="border-t border-black/[0.05]" data-testid="sector-row">
                    <td className="py-1.5 pr-2 tabular-nums text-slate-400">{r.rank ?? '—'}</td>
                    <td className="py-1.5 pr-3 text-slate-800">
                      {sectorHref(r.sector) ? (
                        <Link href={sectorHref(r.sector)!} className="font-medium text-slate-900 hover:text-accent hover:underline">
                          {r.sector}
                        </Link>
                      ) : (
                        r.sector
                      )}
                      <span className="ml-1.5 text-[11px] text-slate-400">{fmtInt(r.members)}</span>
                    </td>
                    <td className="w-56 py-1.5 pr-3">
                      <div className="relative h-2 rounded-full bg-black/[0.04]" aria-hidden>
                        <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
                        {v != null && (
                          <span
                            className={clsx('absolute inset-y-0 rounded-full', v >= 0 ? 'bg-emerald-500/80' : 'bg-red-500/70')}
                            style={v >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }}
                          />
                        )}
                      </div>
                      <span className="text-[11px] tabular-nums text-slate-500">{fmtSigned(v, 1, ' pp')}</span>
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-slate-700">{fmtSigned(r.ret20, 1)}</td>
                    <td className="py-1.5 text-right tabular-nums text-slate-700">{fmtSigned(r.vsSpx20, 1, ' pp')}</td>
                    <td className="py-1.5 text-right tabular-nums text-slate-700" title={b.ratio}>
                      {b.pct ?? '—'}
                    </td>
                    <td className="py-1.5 pl-2 text-right">
                      <span className={clsx('rounded-full px-2 py-0.5 text-[11px] font-medium', STATE_TONE[r.strengthState ?? ''] ?? 'text-slate-400')}>
                        {strengthWord(r.strengthState)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
        Sector assignment: {map?.source ?? 'unknown source'}
        {map?.provenance ? `, ${map.provenance}` : ''}
        {map?.pit_safe === true ? ' (as known on the session)' : map?.pit_safe === false ? ' (today’s assignment, not as known on the session)' : ''}.{' '}
        {fmtInt(snap.coverage.n_classified)} of {fmtInt(snap.coverage.n_universe)} stocks classified; sectors need at least{' '}
        {fmtInt(snap.coverage.min_sector_members)} members. Leading / lagging = top / bottom third of the ranking.
      </p>
    </section>
  );
}
