// File: frontend/src/components/marketIntel/ReturnsCard.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';
import type { MarketSnapshotAvailable } from '@/services/marketIntelligenceApi';
import { HORIZONS, fmtInt, fmtSigned } from '@/lib/marketIntel';

const tone = (v: number | null) => (v == null ? 'text-slate-400' : v >= 0 ? 'text-emerald-600' : 'text-red-600');

/** S&P 500 (cap-weighted) next to the equal-weight median stock, over 5 / 20 / 60 sessions. The gap between the two is
 * shown as a plain difference; it describes breadth of participation, not a prediction. */
export default function ReturnsCard({ snap }: { snap: MarketSnapshotAvailable }) {
  const r = snap.returns;
  return (
    <section aria-labelledby="returns-title" className="rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm">
      <h3 id="returns-title" className="text-[17px] font-semibold text-slate-900">Index vs typical stock</h3>
      <table className="mt-3 w-full text-[13px]">
        <thead>
          <tr className="text-left text-[11px] text-slate-400">
            <th className="pb-1 font-medium">Sessions</th>
            <th className="pb-1 text-right font-medium">S&amp;P 500</th>
            <th className="pb-1 text-right font-medium">Median stock</th>
            <th className="pb-1 text-right font-medium">Difference</th>
          </tr>
        </thead>
        <tbody>
          {HORIZONS.map((h) => {
            const spx = r.spx?.[h] ?? null;
            const med = r.universe_median?.[h]?.ret ?? null;
            const diff = spx != null && med != null ? med - spx : null;
            return (
              <tr key={h} className="border-t border-black/[0.05]">
                <td className="py-1.5 text-slate-700">{h}</td>
                <td className={clsx('py-1.5 text-right tabular-nums', tone(spx))}>{fmtSigned(spx, 2)}</td>
                <td className={clsx('py-1.5 text-right tabular-nums', tone(med))}>
                  {fmtSigned(med, 2)}
                  <span className="ml-1 text-[10px] text-slate-400">n={fmtInt(r.universe_median?.[h]?.n_valid)}</span>
                </td>
                <td className={clsx('py-1.5 text-right tabular-nums', tone(diff))}>{fmtSigned(diff, 2, ' pp')}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
        Price returns on stored closes. {r.universe_definition ? r.universe_definition.charAt(0).toUpperCase() + r.universe_definition.slice(1) : ''}. A stock with a price discontinuity in the window is left out.
      </p>
    </section>
  );
}
