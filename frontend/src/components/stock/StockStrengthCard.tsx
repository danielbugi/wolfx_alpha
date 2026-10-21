// File: frontend/src/components/stock/StockStrengthCard.tsx
'use client';

import React from 'react';
import { ScoreBadge, ScoreBreakdown, StrengthIcon } from '@/components/screener/StrengthScore';
import DataStamp from '@/components/common/DataStamp';
import type { StockContextState } from '@/hooks/useStockContext';
import { band, BAND_LABEL } from '@/lib/strength';

/**
 * The stock's Strength Score for the session (the same value as the Screener and Momentum Today): the score, its band,
 * the five-bar icon and the five weighted parts, or the data-health reason when it is withheld.
 */
export default function StockStrengthCard({ state }: { state: StockContextState }) {
  const { ctx, error } = state;
  const box = 'rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm';
  if (error) return null; // the Stock context panel below says why
  if (!ctx) return <div className="h-36 animate-pulse rounded-2xl bg-black/[0.04]" aria-busy="true" />;
  if (!ctx.in_universe || !ctx.row) return null;
  const row = ctx.row;
  const b = band(row.strength_score);
  return (
    <section className={box} aria-labelledby="strength-title" data-testid="stock-strength">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="strength-title" className="text-[17px] font-semibold text-slate-900">
          Strength
        </h3>
        <DataStamp label="Session" asOf={ctx.session} source="same data as the Screener" />
      </div>
      <div className="mt-3 grid grid-cols-1 items-center gap-5 md:grid-cols-[auto_minmax(0,1fr)]">
        <div className="flex items-center gap-4">
          <ScoreBadge row={row} size="lg" />
          <div>
            <p className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
              <StrengthIcon score={row.strength_score} size={22} label />
              {b == null ? 'No score' : BAND_LABEL[b]}
            </p>
            <p className="text-[12px] text-slate-500">Strength Score 0–100 from this session&apos;s data</p>
          </div>
        </div>
        <ScoreBreakdown row={row} />
      </div>
      <p className="mt-3 text-[11px] text-slate-400">
        Relative strength 35%, trend 25%, momentum 20%, volume 10%, liquidity 10%. It describes the session; it is not a forecast or a recommendation.
      </p>
    </section>
  );
}
