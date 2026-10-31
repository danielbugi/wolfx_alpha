'use client';

import React from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import type { InflectionEvent } from '@/services/inflectionApi';
import { DASH, SEVERITY_STYLE, basisLabel, fiscalLabel, fmtMetric, fmtMoney, fmtPct, freshnessText, strengthColor } from '@/lib/inflectionFormat';

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-2.5 py-2">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className={clsx('text-sm font-semibold tabular-nums', tone ?? 'text-slate-800')}>{value}</p>
      {sub && <p className="text-[11px] tabular-nums text-slate-400">{sub}</p>}
    </div>
  );
}

const tone = (v: number | null | undefined) => (v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-600' : v < 0 ? 'text-red-600' : 'text-slate-800');

/**
 * One inflection event: what changed (numbers), how strong, when it became public and how that date was determined, what the stock has done since and what it costs,
 * and why it was flagged. A research signal: it says so on the card.
 */
export default function EventCard({ e, showStockLink = true }: { e: InflectionEvent; showStockLink?: boolean }) {
  const basis = basisLabel(e.availability_basis);
  const v = e.valuation;
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-3.5" data-testid="inflection-event">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2">
            {showStockLink ? (
              <Link href={`/stock/${encodeURIComponent(e.symbol)}`} className="text-base font-semibold text-slate-900 hover:text-accent hover:underline">
                {e.symbol}
              </Link>
            ) : (
              <span className="text-base font-semibold text-slate-900">{e.symbol}</span>
            )}
            {e.company_name && <span className="truncate text-sm text-slate-500">{e.company_name}</span>}
          </div>
          <p className="text-[11px] text-slate-400">
            {e.sector ?? 'sector unknown'} · {fiscalLabel(e.fiscal_year, e.fiscal_quarter, e.quarter_end)}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={clsx('rounded px-2 py-0.5 text-[11px] font-semibold', SEVERITY_STYLE[e.severity] ?? SEVERITY_STYLE.low)}>{e.severity}</span>
          <span className="rounded bg-slate-900 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">Research signal</span>
        </div>
      </div>

      <h3 className="mt-2 text-sm font-semibold text-slate-800">{e.label}</h3>
      <div className="mt-1 flex items-center gap-2" aria-label={`Strength ${e.strength.toFixed(0)} of 100`}>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
          <div className={clsx('h-full rounded-full', strengthColor(e.strength))} style={{ width: `${Math.max(2, Math.min(100, e.strength))}%` }} />
        </div>
        <span className="w-24 shrink-0 text-right text-xs tabular-nums text-slate-600">strength {e.strength.toFixed(0)} · score {e.score.toFixed(0)}</span>
      </div>

      {e.key_metrics.length > 0 && (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {e.key_metrics.map((m) => (
            <Tile key={m.label} label={m.label} value={fmtMetric(m, 'current')} sub={m.previous != null ? `was ${fmtMetric(m, 'previous')}` : undefined} />
          ))}
        </div>
      )}

      <div className="mt-2 grid grid-cols-3 gap-2">
        <Tile label="Price, 2 sessions" value={fmtPct(e.reaction_2d)} tone={tone(e.reaction_2d)} sub="around the report" />
        <Tile label="vs sector since" value={fmtPct(e.excess_vs_sector_since_publication)} tone={tone(e.excess_vs_sector_since_publication)} />
        <Tile label="Price / sales" value={v.ps_ratio != null ? v.ps_ratio.toFixed(1) : DASH}
              sub={v.ps_percentile_own_history != null ? `${(v.ps_percentile_own_history * 100).toFixed(0)}th pct of its history` : v.ps_vs_sector_median != null ? `${v.ps_vs_sector_median.toFixed(1)}x sector` : undefined} />
      </div>

      <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-slate-600">
        {e.explanation.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>

      <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
        Public {e.available_at} ({freshnessText(e.freshness_days)}) · date from {basis.text}
        {basis.assumed ? ' (an assumption, not a filing date)' : ''} · source {e.source ?? DASH}
        {e.market_cap != null ? ` · market cap ${fmtMoney(e.market_cap)}` : ''}
        {e.data_quality === 'partial' ? ' · partial data' : ''}
        {e.revision_count > 0 ? ` · restated ${e.revision_count}x` : ''}
      </p>
    </article>
  );
}
