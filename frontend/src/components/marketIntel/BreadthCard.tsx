// File: frontend/src/components/marketIntel/BreadthCard.tsx
'use client';

import React from 'react';
import type { CountRecord, MarketSnapshotAvailable } from '@/services/marketIntelligenceApi';
import { BREADTH_ROWS, describeCount, fmtInt } from '@/lib/marketIntel';

/** One recorded value per observed session, oldest first (null when that session did not measure it). */
export interface BreadthPoint {
  session: string;
  pct: number | null;
}
export type BreadthTrend = Partial<Record<'above_sma50' | 'above_sma200' | 'positive_ret_20', BreadthPoint[]>>;

const BAND = ['#ef4444', '#fb923c', '#94a3b8', '#34d399', '#059669'];
const bandColor = (pct: number) => BAND[Math.min(4, Math.max(0, Math.floor(pct / 20)))];

/** A half-circle dial for a 0-100 share: the filled arc is the share, the tick at the top is 50%. */
function Gauge({ pct }: { pct: number | null }) {
  const r = 40;
  const arc = (from: number, to: number) => {
    const a0 = Math.PI * (1 - from / 100);
    const a1 = Math.PI * (1 - to / 100);
    const p = (a: number) => `${50 + r * Math.cos(a)},${50 - r * Math.sin(a)}`;
    return `M${p(a0)} A${r},${r} 0 0 1 ${p(a1)}`;
  };
  return (
    <svg viewBox="0 0 100 56" className="mx-auto h-[64px] w-[116px]" aria-hidden="true" data-testid="breadth-gauge">
      <path d={arc(0, 100)} fill="none" stroke="currentColor" strokeOpacity={0.1} strokeWidth={9} strokeLinecap="round" />
      {pct != null && pct > 0 && <path d={arc(0, Math.min(100, pct))} fill="none" stroke={bandColor(pct)} strokeWidth={9} strokeLinecap="round" />}
      <line x1="50" y1="6" x2="50" y2="14" stroke="currentColor" strokeOpacity={0.35} strokeWidth={1.2} />
    </svg>
  );
}

/** The recorded sessions as a small line on a fixed 0-100 scale with the 50% line; nothing with fewer than 2 points. */
function Trend({ points }: { points: BreadthPoint[] | undefined }) {
  const valid = (points ?? []).filter((p): p is { session: string; pct: number } => p.pct != null);
  if (valid.length < 2) return null;
  const step = 100 / (valid.length - 1);
  const d = valid.map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(2)},${(28 - (p.pct / 100) * 26 - 1).toFixed(2)}`).join(' ');
  return (
    <div className="mt-1" title={valid.map((p) => `${p.session}: ${p.pct.toFixed(1)}%`).join(', ')}>
      <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="h-6 w-full text-slate-400" aria-hidden="true" data-testid="breadth-trend">
        <line x1="0" x2="100" y1="15" y2="15" stroke="currentColor" strokeOpacity={0.25} strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
        <path d={d} fill="none" stroke="#0a84ff" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      </svg>
      <p className="text-[10px] text-slate-400">
        last {valid.length} recorded sessions · {valid[0].session} to {valid[valid.length - 1].session}
      </p>
    </div>
  );
}

function Row({ label, window: win, rec, trend }: { label: string; window: string; rec: CountRecord | undefined; trend?: BreadthPoint[] }) {
  const d = describeCount(rec);
  const pct = rec?.state === 'ok' && rec.pct != null ? rec.pct : null;
  return (
    <div className="rounded-xl bg-slate-50 px-3 pb-2.5 pt-3 text-center" data-testid="breadth-row">
      <p className="text-[12px] font-medium text-slate-700">{label}</p>
      <Gauge pct={pct} />
      <p className="-mt-1 text-[11px] tabular-nums text-slate-500">
        {d.ratio}
        {d.pct && <span className="block text-[20px] font-semibold leading-tight text-slate-900">{d.pct}</span>}
      </p>
      <Trend points={trend} />
      <p className="mt-1 text-[10px] leading-snug text-slate-400">
        {win}
        {d.note ? ` · ${d.note}` : ''}
      </p>
    </div>
  );
}

/** Market breadth with explicit numerators and denominators (breadth_v1). Stocks that cannot be evaluated leave the
 * denominator and are reported as "not evaluable", never counted as negative. */
export default function BreadthCard({ snap, trend = {} }: { snap: MarketSnapshotAvailable; trend?: BreadthTrend }) {
  const m = snap.coverage.measurements?.breadth?.market ?? {};
  const hi = m.new_high_52w;
  const lo = m.new_low_52w;
  const adv = m.advancing_1d;
  const dec = m.declining_1d;
  return (
    <section aria-labelledby="breadth-title" className="rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="breadth-title" className="text-[17px] font-semibold text-slate-900">Market breadth</h3>
        <span className="text-xs text-slate-400">session {snap.session_date} · universe {fmtInt(snap.coverage.n_universe)} stocks</span>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
        {BREADTH_ROWS.map((r) => (
          <Row key={r.key} label={r.label} window={r.window} rec={m[r.key]} trend={trend[r.key]} />
        ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="rounded-xl bg-slate-50 px-3 py-2.5">
          <p className="text-[11px] font-medium text-slate-500">52-week highs / lows</p>
          <p className="text-[15px] font-semibold tabular-nums text-slate-900">
            {fmtInt(hi?.numerator)} / {fmtInt(lo?.numerator)}
          </p>
          <p className="text-[11px] tabular-nums text-slate-400">new 52-week high / low on the session, of {fmtInt(hi?.denominator)} stocks with 200+ sessions</p>
        </div>
        <div className="rounded-xl bg-slate-50 px-3 py-2.5">
          <p className="text-[11px] font-medium text-slate-500">Advancing / declining (1 session)</p>
          <p className="text-[15px] font-semibold tabular-nums text-slate-900">
            {fmtInt(adv?.numerator)} / {fmtInt(dec?.numerator)}
          </p>
          <p className="text-[11px] tabular-nums text-slate-400">close vs the previous close, of {fmtInt(adv?.denominator)} stocks</p>
        </div>
      </div>
    </section>
  );
}
