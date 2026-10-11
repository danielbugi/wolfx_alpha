'use client';

import React from 'react';
import Link from 'next/link';
import { Card, CardBody, CardHeader } from '@nextui-org/react';
import { clsx } from 'clsx';
import type { MarketIndexEntry } from '@/services/api';

/** Commodity futures shown in this card (and therefore left out of the top indices strip), in display order. */
export const COMMODITY_SYMBOLS = ['GC=F', 'SI=F', 'HG=F', 'PL=F', 'CL=F', 'NG=F'] as const;
const GROUP: Record<string, 'Metals' | 'Energy'> = {
  'GC=F': 'Metals',
  'SI=F': 'Metals',
  'HG=F': 'Metals',
  'PL=F': 'Metals',
  'CL=F': 'Energy',
  'NG=F': 'Energy',
};
const NAMES: Record<string, string> = {
  'GC=F': 'Gold',
  'SI=F': 'Silver',
  'HG=F': 'Copper',
  'PL=F': 'Platinum',
  'CL=F': 'Crude oil',
  'NG=F': 'Natural gas',
};
const UNITS: Record<string, string> = {
  'GC=F': '/oz',
  'SI=F': '/oz',
  'PL=F': '/oz',
  'HG=F': '/lb',
  'CL=F': '/bbl',
  'NG=F': '/MMBtu',
};

function Sparkline({ values, up }: { values: number[]; up: boolean }) {
  if (values.length < 2) return <div className="h-7 w-20" />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 80},${26 - ((v - min) / span) * 24}`).join(' ');
  return (
    <svg viewBox="0 0 80 28" className="h-7 w-20" aria-hidden>
      <polyline points={pts} fill="none" stroke={up ? '#10b981' : '#ef4444'} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function pct(v: number | null) {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

/**
 * Metals and energy futures: last price, 1-day change, change over the sparkline window (last ~30 sessions) and a
 * mini chart. Symbols the pipeline hasn't loaded yet are listed as pending rather than shown with made-up values.
 */
export default function CommoditiesCard({ indices, loading }: { indices: MarketIndexEntry[]; loading: boolean }) {
  const bySymbol = new Map(indices.map((i) => [i.symbol, i]));
  const rows = COMMODITY_SYMBOLS.map((s) => bySymbol.get(s)).filter((r): r is MarketIndexEntry => !!r);
  const pending = COMMODITY_SYMBOLS.filter((s) => !bySymbol.has(s));

  return (
    <Card className="h-full border border-black/[0.05] shadow-sm">
      <CardHeader className="items-start justify-between gap-3 px-5 pt-5 pb-2">
        <div>
          <h3 className="text-[17px] font-semibold text-slate-900">Commodities</h3>
          <p className="mt-0.5 text-xs text-slate-500">Futures · last close, day and 30-session change</p>
        </div>
        <Link href="/markets" className="shrink-0 whitespace-nowrap text-[13px] font-medium text-accent hover:underline">
          All markets →
        </Link>
      </CardHeader>
      <CardBody className="px-5 pb-5 pt-1">
        {loading && rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">No commodity data yet.</p>
        ) : (
          <ul className="divide-y divide-black/[0.06]">
            {rows.map((r, i) => {
              const spark = r.sparkline ?? [];
              const windowChange = spark.length > 1 ? (spark[spark.length - 1] / spark[0] - 1) * 100 : null;
              const newGroup = i === 0 || GROUP[rows[i - 1].symbol] !== GROUP[r.symbol];
              return (
                <li key={r.symbol}>
                  {newGroup && <p className="pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.06em] text-slate-400">{GROUP[r.symbol]}</p>}
                  <Link href={`/markets/${encodeURIComponent(r.symbol)}`} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-black/[0.03]">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-semibold text-slate-800">{r.display_name}</p>
                      <p className="text-[11px] tabular-nums text-slate-500">
                        {r.close != null ? `$${r.close.toLocaleString('en-US', { maximumFractionDigits: r.close < 10 ? 3 : 2 })}` : '—'}
                        <span className="text-slate-400">{UNITS[r.symbol] ?? ''}</span>
                      </p>
                    </div>
                    <Sparkline values={spark} up={(windowChange ?? 0) >= 0} />
                    <div className="w-24 shrink-0 whitespace-nowrap text-right">
                      <p className={clsx('text-[13px] font-semibold tabular-nums', r.change_pct == null ? 'text-slate-400' : r.change_pct >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                        {pct(r.change_pct)}
                      </p>
                      <p className={clsx('text-[11px] tabular-nums', windowChange == null ? 'text-slate-400' : windowChange >= 0 ? 'text-emerald-600/80' : 'text-red-600/80')}>
                        {pct(windowChange)} <span className="text-slate-400">30d</span>
                      </p>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
        {pending.length > 0 && rows.length > 0 && (
          <p className="mt-2 text-[11px] text-slate-400">
            {pending.map((s) => NAMES[s]).join(', ')} will appear after the next data update.
          </p>
        )}
      </CardBody>
    </Card>
  );
}
