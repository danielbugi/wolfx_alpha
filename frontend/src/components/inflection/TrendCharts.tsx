'use client';

import React from 'react';
import { clsx } from 'clsx';
import type { InflectionTrendPoint } from '@/services/inflectionApi';
import { DASH, fmtMoney, fmtPct } from '@/lib/inflectionFormat';

type Series = { key: 'revenue' | 'op_margin' | 'net_income' | 'free_cash_flow'; label: string; fmt: (v: number | null) => string };
const SERIES: Series[] = [
  { key: 'revenue', label: 'Revenue', fmt: fmtMoney },
  { key: 'op_margin', label: 'Operating margin', fmt: (v) => fmtPct(v, 1, false) },
  { key: 'net_income', label: 'Net income', fmt: fmtMoney },
  { key: 'free_cash_flow', label: 'Free cash flow', fmt: fmtMoney },
];

function Bars({ series, points }: { series: Series; points: InflectionTrendPoint[] }) {
  const vals = points.map((p) => p[series.key]);
  const finite = vals.filter((v): v is number => v != null && Number.isFinite(v));
  const max = Math.max(1e-9, ...finite.map((v) => Math.abs(v)));
  const hasNeg = finite.some((v) => v < 0);
  return (
    <figure className="rounded-lg border border-slate-200 p-2.5" aria-label={`${series.label} by quarter`}>
      <figcaption className="mb-1 flex items-baseline justify-between">
        <span className="text-xs font-semibold text-slate-700">{series.label}</span>
        <span className="text-xs tabular-nums text-slate-500">{series.fmt(vals[vals.length - 1] ?? null)}</span>
      </figcaption>
      <div className="flex h-16 items-stretch gap-1">
        {points.map((p, i) => {
          const v = vals[i];
          const h = v == null ? 0 : (Math.abs(v) / max) * (hasNeg ? 50 : 100);
          return (
            <div key={p.quarter_end} className="flex min-w-0 flex-1 flex-col justify-center" title={`${p.quarter_end}: ${series.fmt(v)}${p.data_quality === 'partial' ? ' (partial data)' : ''}`}>
              {v == null ? (
                <div className="mx-auto h-px w-full bg-slate-300" aria-label="no data" />
              ) : (
                <div className={clsx('flex h-full flex-col', hasNeg ? 'justify-center' : 'justify-end')}>
                  {hasNeg && <div style={{ height: v >= 0 ? `${h}%` : 0 }} className={v >= 0 ? 'mt-auto w-full rounded-t bg-emerald-500' : ''} />}
                  {hasNeg && <div style={{ height: v < 0 ? `${h}%` : 0 }} className={v < 0 ? 'w-full rounded-b bg-red-400' : ''} />}
                  {!hasNeg && <div style={{ height: `${Math.max(2, h)}%` }} className="w-full rounded-t bg-emerald-500" />}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1">
        {points.map((p) => (
          <span key={p.quarter_end} className="min-w-0 flex-1 text-center text-[10px] text-slate-400">
            {p.fiscal_year != null && p.fiscal_quarter != null ? `Q${p.fiscal_quarter}` : p.quarter_end.slice(2, 7)}
          </span>
        ))}
      </div>
    </figure>
  );
}

/** Quarterly revenue, operating margin, net income and free cash flow, oldest to newest. A missing quarter is a flat line, never a zero bar. */
export default function TrendCharts({ trend }: { trend: InflectionTrendPoint[] }) {
  const points = trend.slice(-8);
  if (points.length === 0) return <p className="text-sm text-slate-400">{DASH} no quarterly history stored for this stock.</p>;
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid="inflection-trend">
      {SERIES.map((s) => (
        <Bars key={s.key} series={s} points={points} />
      ))}
    </div>
  );
}
