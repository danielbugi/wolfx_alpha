// File: frontend/src/components/screener/StrengthCards.tsx
'use client';

import React from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { StarIcon as StarOutline } from '@heroicons/react/24/outline';
import { StarIcon as StarSolid } from '@heroicons/react/20/solid';
import type { ScreenerRow } from '@/services/screenerV2Api';
import { formatValue } from '@/lib/screenerV2';
import { sectorHref } from '@/lib/sectors';
import { liquidityLabel, quadrant, trend, TREND_LABEL } from '@/lib/strength';
import { QuadrantChip, StrengthMeter } from '@/components/screener/StrengthMeter';
import { ScoreBadge, ScoreBreakdown } from '@/components/screener/StrengthScore';

/** A return vs the S&P 500 as a centred bar (scaled to ±20 pp, clipped) with its value. */
function RelBar({ label, value }: { label: string; value: number | null }) {
  const w = value == null ? 0 : Math.min(50, (Math.abs(value) / 20) * 50);
  return (
    <div className="grid grid-cols-[2.6rem_minmax(0,1fr)_3.4rem] items-center gap-1.5 text-[11px]">
      <span className="text-slate-400">{label}</span>
      <span className="relative h-1.5 rounded-full bg-slate-100" aria-hidden="true">
        <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
        {value != null && (
          <span className={clsx('absolute inset-y-0 rounded-full', value >= 0 ? 'bg-emerald-500' : 'bg-red-500')} style={value >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }} />
        )}
      </span>
      <span className={clsx('text-right tabular-nums', value == null ? 'text-slate-400' : value >= 0 ? 'text-emerald-600' : 'text-red-600')}>{formatValue(value, 'pp')}</span>
    </div>
  );
}

/**
 * Card view of the screen: per stock, the Strength Map corner, strength meters (20 / 60 sessions), returns vs the S&P 500,
 * where the close sits in its 20-day channel, the trend against its averages and liquidity in words. Same rows and values
 * as the table; nothing recalculated.
 */
export default function StrengthCards({ rows, isStarred, onStar }: { rows: ScreenerRow[]; isStarred: (s: string) => boolean; onStar: (s: string) => void }) {
  return (
    <div className="grid grid-cols-1 gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="strength-cards">
      {rows.map((r) => {
        const t = trend(r.dist_sma20, r.dist_sma50);
        const liq = liquidityLabel(r.dollar_vol_20);
        return (
          <article key={r.symbol} className="rounded-2xl border border-black/[0.06] bg-white p-4" data-testid="strength-card">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <Link href={`/stock/${r.symbol}`} className="text-[16px] font-semibold text-slate-900 hover:text-accent hover:underline">
                  {r.symbol}
                </Link>
                <p className="truncate text-[11px] text-slate-500">
                  {r.sector && sectorHref(r.sector) ? (
                    <Link href={sectorHref(r.sector)!} className="hover:text-accent hover:underline">
                      {r.sector}
                    </Link>
                  ) : (
                    r.sector ?? '—'
                  )}
                  {r.industry ? ` · ${r.industry}` : ''}
                </p>
              </div>
              <div className="flex items-center gap-1">
                <QuadrantChip q={quadrant(r.rs_20, r.rs_60)} />
                <ScoreBadge row={r} />
                <button
                  type="button"
                  onClick={() => onStar(r.symbol)}
                  aria-pressed={isStarred(r.symbol)}
                  aria-label={isStarred(r.symbol) ? `Remove ${r.symbol} from watchlist` : `Add ${r.symbol} to watchlist`}
                  className="press inline-flex h-8 w-8 items-center justify-center rounded-full hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {isStarred(r.symbol) ? <StarSolid className="h-4 w-4 text-amber-400" /> : <StarOutline className="h-4 w-4 text-slate-400" />}
                </button>
              </div>
            </div>

            <div className="mt-3 space-y-1.5 text-[11px] text-slate-500">
              <div className="flex items-center justify-between gap-2">
                <span>Strength, 20 sessions</span>
                <StrengthMeter value={r.rs_20} label="RS 20" width="w-20" />
              </div>
              <div className="flex items-center justify-between gap-2">
                <span>Strength, 60 sessions</span>
                <StrengthMeter value={r.rs_60} label="RS 60" width="w-20" />
              </div>
            </div>

            <details className="mt-3 group">
              <summary className="cursor-pointer text-[11px] font-medium text-slate-500 hover:text-slate-800">What makes up the score</summary>
              <div className="mt-1.5">
                <ScoreBreakdown row={r} compact />
              </div>
            </details>

            <div className="mt-3 space-y-1">
              <p className="text-[11px] text-slate-400">Return vs the S&amp;P 500</p>
              <RelBar label="20 S" value={r.vs_spx_20} />
              <RelBar label="60 S" value={r.vs_spx_60} />
            </div>

            <div className="mt-3">
              <div className="flex items-center justify-between text-[11px] text-slate-400">
                <span>20-day channel</span>
                <span className="tabular-nums text-slate-600">{r.channel_pos == null ? '—' : `${Math.round(r.channel_pos)}% of the way up`}</span>
              </div>
              <div className="relative mt-1 h-1.5 rounded-full bg-gradient-to-r from-red-200 via-slate-200 to-emerald-200" aria-hidden="true">
                {r.channel_pos != null && (
                  <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-slate-900 shadow" style={{ left: `${Math.min(100, Math.max(0, r.channel_pos))}%` }} />
                )}
              </div>
            </div>

            <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 border-t border-black/[0.05] pt-2 text-[11px] text-slate-500">
              <span>{t ? TREND_LABEL[t] : 'Trend unavailable'}</span>
              {liq && <span>{liq}</span>}
              {r.breakout_20 && <span className="font-medium text-slate-700">Closed above its prior 20-day high</span>}
            </div>
          </article>
        );
      })}
    </div>
  );
}
