// File: frontend/src/components/marketIntel/SectorHeatmap.tsx
'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import type { Horizon, MarketSnapshotAvailable, SectorRow } from '@/services/marketIntelligenceApi';
import SegmentedControl from '@/components/ui/SegmentedControl';
import { fmtSigned } from '@/lib/marketIntel';
import { sectorHref } from '@/lib/sectors';

type Basis = 'ret' | 'vs_spx' | 'vs_univ';
const HORIZONS: Horizon[] = ['5', '20', '60'];
/** Colour saturates at this many percent (or percentage points) from zero, per horizon: longer windows move more. */
export const SCALE: Record<Horizon, number> = { '5': 4, '20': 8, '60': 15 };
const BASIS_LABEL: Record<Basis, string> = { ret: 'Return', vs_spx: 'vs S&P 500', vs_univ: 'vs median stock' };

/** Diverging tile colour: red below zero, green above, stronger with distance; neutral grey when unavailable. */
export function tileStyle(v: number | null | undefined, scale: number): React.CSSProperties {
  if (v == null || !Number.isFinite(v)) return { background: 'rgba(148,163,184,0.15)' };
  const a = Math.min(1, Math.abs(v) / scale);
  return { background: v >= 0 ? `rgba(16,185,129,${0.12 + a * 0.68})` : `rgba(239,68,68,${0.12 + a * 0.68})` };
}

/**
 * Sector heatmap from the observed market snapshot: the 11 sectors (ordered by their 20-session rank) across 5 / 20 / 60
 * sessions, coloured by the median member return, or its difference from the S&P 500 or from the median stock. Each tile
 * opens the sector. Values are the snapshot's own; current sector membership (not point-in-time).
 */
export default function SectorHeatmap({ snap }: { snap: MarketSnapshotAvailable }) {
  const [basis, setBasis] = useState<Basis>('vs_spx');
  const rows: SectorRow[] = snap.sectors ?? [];
  const unit = basis === 'ret' ? '%' : ' pp';

  return (
    <section aria-labelledby="heatmap-title" className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm sm:p-5" data-testid="sector-heatmap">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id="heatmap-title" className="text-[17px] font-semibold text-slate-900">
            Sector heatmap
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Median stock in each sector · session {snap.session_date} · sorted by 20-session rank · tap a sector to open it
          </p>
        </div>
        <SegmentedControl
          aria-label="Heatmap colour"
          value={basis}
          onChange={setBasis}
          options={(['ret', 'vs_spx', 'vs_univ'] as Basis[]).map((b) => ({ value: b, label: BASIS_LABEL[b] }))}
        />
      </div>
      {rows.length === 0 ? (
        <p className="py-8 text-center text-sm text-slate-400">No sector data in this snapshot.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[320px] border-separate border-spacing-1 text-[12px]">
            <thead>
              <tr className="text-[11px] text-slate-400">
                <th className="text-left font-medium">Sector</th>
                {HORIZONS.map((h) => (
                  <th key={h} className="w-[22%] text-center font-medium">
                    {h} sessions
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const href = sectorHref(r.sector);
                return (
                  <tr key={r.sector} data-testid="heatmap-row">
                    <td className="max-w-[140px] truncate pr-2 text-[12px] font-medium text-slate-700">
                      {r.rank_20 != null && <span className="mr-1.5 tabular-nums text-slate-400">{r.rank_20}</span>}
                      {href ? (
                        <Link href={href} className="hover:text-accent hover:underline">
                          {r.sector}
                        </Link>
                      ) : (
                        r.sector
                      )}
                    </td>
                    {HORIZONS.map((h) => {
                      const cell = r.horizons?.[h];
                      const v = cell?.[basis] ?? null;
                      const text = fmtSigned(v, 1, unit);
                      const tip = `${r.sector}, ${h} sessions: return ${fmtSigned(cell?.ret, 1)}, vs S&P 500 ${fmtSigned(cell?.vs_spx, 1, ' pp')}, vs median stock ${fmtSigned(cell?.vs_univ, 1, ' pp')} · ${cell?.n_valid ?? '—'} stocks`;
                      const tile = (
                        <span className={clsx('block rounded-lg px-1 py-2 text-center font-semibold tabular-nums', v == null ? 'text-slate-400' : 'text-slate-900')} style={tileStyle(v, SCALE[h])}>
                          {text}
                        </span>
                      );
                      return (
                        <td key={h} title={tip} data-testid={`tile-${h}`}>
                          {href ? (
                            <Link href={href} aria-label={tip} className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
                              {tile}
                            </Link>
                          ) : (
                            tile
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-[11px] text-slate-400">
        Colour is strongest at ±{SCALE['5']} / ±{SCALE['20']} / ±{SCALE['60']}{unit.trim() === '%' ? '%' : ' pp'} for 5 / 20 / 60 sessions. Sector = median of its
        current members (not point-in-time). Describes past sessions only.
      </p>
    </section>
  );
}
