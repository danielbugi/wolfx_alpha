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

export interface RegimePoint {
  session: string;
  score: number | null;
}

/** A point on the dial's arc for a score in [-1, +1] (left = -1, top = 0, right = +1). */
function dialPoint(score: number, radius: number): [number, number] {
  const a = Math.PI * (1 - (Math.max(-1, Math.min(1, score)) + 1) / 2);
  return [60 + radius * Math.cos(a), 60 - radius * Math.sin(a)];
}
function arcPath(from: number, to: number, radius: number): string {
  const [x0, y0] = dialPoint(from, radius);
  const [x1, y1] = dialPoint(to, radius);
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A${radius},${radius} 0 0 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
}

/** The score as a semicircle dial: red zone up to the risk-off threshold, grey between, green from the risk-on threshold. */
function Dial({ score, off, on }: { score: number | null; off: number; on: number }) {
  const needle = score == null ? null : dialPoint(score, 38);
  return (
    <svg viewBox="0 0 120 68" className="mx-auto h-[104px] w-[184px]" aria-hidden="true" data-testid="regime-dial">
      <path d={arcPath(-1, off, 48)} fill="none" stroke="#fca5a5" strokeWidth={12} />
      <path d={arcPath(off, on, 48)} fill="none" stroke="#e2e8f0" strokeWidth={12} />
      <path d={arcPath(on, 1, 48)} fill="none" stroke="#86efac" strokeWidth={12} />
      {needle && <line x1="60" y1="60" x2={needle[0].toFixed(2)} y2={needle[1].toFixed(2)} stroke="#0f172a" strokeWidth={2.5} strokeLinecap="round" />}
      <circle cx="60" cy="60" r="4" fill="#0f172a" />
    </svg>
  );
}

/** The regime score over the recorded sessions on the fixed -1..+1 scale with the two thresholds; nothing under 2 points. */
function ScoreTrend({ points, off, on }: { points: RegimePoint[]; off: number; on: number }) {
  const valid = points.filter((p): p is { session: string; score: number } => p.score != null);
  if (valid.length < 2) return null;
  const y = (v: number) => (15 - v * 13).toFixed(2);
  const step = 100 / (valid.length - 1);
  const d = valid.map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(2)},${y(p.score)}`).join(' ');
  return (
    <div className="mt-2" title={valid.map((p) => `${p.session}: ${p.score.toFixed(2)}`).join(', ')}>
      <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="h-8 w-full text-slate-400" aria-hidden="true" data-testid="regime-trend">
        <line x1="0" x2="100" y1={y(on)} y2={y(on)} stroke="#22c55e" strokeOpacity={0.5} strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
        <line x1="0" x2="100" y1={y(off)} y2={y(off)} stroke="#ef4444" strokeOpacity={0.5} strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
        <path d={d} fill="none" stroke="#0f172a" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      </svg>
      <p className="text-[10px] tabular-nums text-slate-400" data-testid="regime-trend-range">
        score {valid[0].score.toFixed(2)} → {valid[valid.length - 1].score.toFixed(2)} over the last {valid.length} recorded sessions ({valid[0].session} to{' '}
        {valid[valid.length - 1].session}); dashed lines are the thresholds
      </p>
    </div>
  );
}

/** The rules-based market regime (risk_regime_v1) in neutral words, with every input it used and the ones it lacked. */
export default function RegimeCard({ snap, history = [] }: { snap: MarketSnapshotAvailable; history?: RegimePoint[] }) {
  const r = snap.regime;
  const pos = scorePosition(r.score);
  const onAt = scorePosition(r.thresholds.risk_on);
  const offAt = scorePosition(r.thresholds.risk_off);
  const maxPull = Math.max(0.0001, ...r.components.map((c) => Math.abs((c.score ?? 0) * c.weight)));
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

      <Dial score={r.score} off={r.thresholds.risk_off} on={r.thresholds.risk_on} />
      <div className="mt-1" aria-label="Score position between -1 and +1">
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
        <ScoreTrend points={history} off={r.thresholds.risk_off} on={r.thresholds.risk_on} />
      </div>

      <table className="mt-4 w-full text-[13px]">
        <thead>
          <tr className="text-left text-[11px] font-medium text-slate-400">
            <th className="pb-1 font-medium">Input</th>
            <th className="pb-1 text-right font-medium">Value</th>
            <th className="pb-1 text-right font-medium">Weight</th>
            <th className="w-20 pb-1 pl-2 text-right font-medium" title="How much this input pulls the score down (red) or up (green): its own score times its weight">
              Pull
            </th>
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
              <td className="py-1.5 pl-2" aria-hidden="true">
                <span className="relative block h-1.5 rounded-full bg-slate-100" data-testid="regime-pull">
                  <span className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
                  {c.present && c.score != null && c.score !== 0 && (
                    <span
                      className={clsx('absolute inset-y-0 rounded-full', c.score > 0 ? 'bg-emerald-500' : 'bg-red-500')}
                      style={{ [c.score > 0 ? 'left' : 'right']: '50%', width: `${(Math.abs(c.score * c.weight) / maxPull) * 50}%` }}
                    />
                  )}
                </span>
              </td>
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
