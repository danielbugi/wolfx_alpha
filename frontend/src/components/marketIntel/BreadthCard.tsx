// File: frontend/src/components/marketIntel/BreadthCard.tsx
'use client';

import React from 'react';
import type { CountRecord, MarketSnapshotAvailable } from '@/services/marketIntelligenceApi';
import { BREADTH_ROWS, describeCount, fmtInt } from '@/lib/marketIntel';

function Row({ label, window: win, rec }: { label: string; window: string; rec: CountRecord | undefined }) {
  const d = describeCount(rec);
  const width = rec?.state === 'ok' && rec.pct != null ? rec.pct : null;
  return (
    <div className="py-2" data-testid="breadth-row">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] text-slate-700">{label}</span>
        <span className="text-[13px] tabular-nums text-slate-900">
          {d.ratio}
          {d.pct && <span className="ml-1.5 font-semibold">{d.pct}</span>}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-black/[0.06]" aria-hidden>
        {width != null && <div className="h-full rounded-full bg-sky-500/80" style={{ width: `${width}%` }} />}
      </div>
      <p className="mt-0.5 text-[11px] text-slate-400">
        {win}
        {d.note ? ` · ${d.note}` : ''}
      </p>
    </div>
  );
}

/** Market breadth with explicit numerators and denominators (breadth_v1). Stocks that cannot be evaluated leave the
 * denominator and are reported as "not evaluable", never counted as negative. */
export default function BreadthCard({ snap }: { snap: MarketSnapshotAvailable }) {
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
      <div className="mt-2 divide-y divide-black/[0.05]">
        {BREADTH_ROWS.map((r) => (
          <Row key={r.key} label={r.label} window={r.window} rec={m[r.key]} />
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
