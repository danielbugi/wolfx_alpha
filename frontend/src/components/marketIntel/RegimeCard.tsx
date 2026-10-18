// File: frontend/src/components/marketIntel/RegimeCard.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';
import type { MarketSnapshotAvailable } from '@/services/marketIntelligenceApi';
import { REGIME_LABEL, componentValue, scorePosition } from '@/lib/marketIntel';

const TONE: Record<string, string> = {
  RISK_ON: 'bg-emerald-100 text-emerald-800',
  NEUTRAL: 'bg-slate-100 text-slate-700',
  RISK_OFF: 'bg-red-100 text-red-700',
  UNAVAILABLE: 'bg-slate-100 text-slate-400',
};

/** The rules-based market regime (risk_regime_v1) in neutral words, with every input it used and the ones it lacked. */
export default function RegimeCard({ snap }: { snap: MarketSnapshotAvailable }) {
  const r = snap.regime;
  const pos = scorePosition(r.score);
  const onAt = scorePosition(r.thresholds.risk_on);
  const offAt = scorePosition(r.thresholds.risk_off);
  return (
    <section aria-labelledby="regime-title" className="rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="regime-title" className="text-[17px] font-semibold text-slate-900">Market regime</h3>
        <span className="text-xs text-slate-400">rules-based description, not a forecast</span>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <span title={`Model state: ${r.state} (${r.model_version})`} className={clsx('rounded-full px-3 py-1 text-[15px] font-semibold', TONE[r.state] ?? TONE.UNAVAILABLE)}>
          {REGIME_LABEL[r.state] ?? 'Unavailable'}
        </span>
        <span className="text-sm tabular-nums text-slate-600">
          score {r.score == null ? '—' : r.score.toFixed(2)}
          {r.strength_label ? ` · strength: ${r.strength_label}` : ''}
        </span>
      </div>

      <div className="mt-4" aria-label="Score position between -1 and +1">
        <div className="relative h-2 rounded-full bg-gradient-to-r from-red-200 via-slate-200 to-emerald-200">
          {offAt != null && <span className="absolute top-[-3px] h-[14px] w-px bg-slate-400" style={{ left: `${offAt}%` }} aria-hidden />}
          {onAt != null && <span className="absolute top-[-3px] h-[14px] w-px bg-slate-400" style={{ left: `${onAt}%` }} aria-hidden />}
          {pos != null && (
            <span className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-slate-900 shadow" style={{ left: `${pos}%` }} />
          )}
        </div>
        <div className="mt-1 flex justify-between text-[10px] tabular-nums text-slate-400">
          <span>−1</span>
          <span>
            broadly negative ≤ {r.thresholds.risk_off} · broadly positive ≥ +{r.thresholds.risk_on}
          </span>
          <span>+1</span>
        </div>
      </div>

      <table className="mt-4 w-full text-[13px]">
        <thead>
          <tr className="text-left text-[11px] font-medium text-slate-400">
            <th className="pb-1 font-medium">Input</th>
            <th className="pb-1 text-right font-medium">Value</th>
            <th className="pb-1 text-right font-medium">Weight</th>
          </tr>
        </thead>
        <tbody>
          {r.components.map((c) => (
            <tr key={c.key} className="border-t border-black/[0.05]">
              <td className="py-1.5 pr-2 text-slate-700">{c.label}</td>
              <td className={clsx('py-1.5 text-right tabular-nums', c.present ? 'text-slate-900' : 'text-slate-400')}>
                {c.present ? componentValue(c.key, c.value) : <span title={c.missing_reason ?? undefined}>missing</span>}
              </td>
              <td className="py-1.5 pl-2 text-right tabular-nums text-slate-500">{Math.round(c.weight * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-slate-500">
        Inputs present: {r.present_weight == null ? '—' : `${Math.round(r.present_weight * 100)}% of the weight`} (below{' '}
        {Math.round(r.min_present_weight * 100)}% the state is unavailable).
      </p>
      {r.reasons.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-slate-600">
          {r.reasons.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-slate-400">{r.disclaimer}</p>
    </section>
  );
}
