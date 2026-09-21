// File: frontend/src/components/strategy/PlanBar.tsx
'use client';

import React, { useMemo } from 'react';

export interface PlanBarData {
  stop_loss_price: number;
  entry_price: number;
  tp1: number;
  tp2: number;
  tp3: number;
}

/** Lay out Stop/Entry/TP1/TP2/TP3 proportionally along a 0-100% track,
 * regardless of direction, plus the risk (entry->stop) and reward
 * (entry->tp3) zones as highlighted ranges. */
function buildPlanBar(plan: PlanBarData) {
  const points = [
    { key: 'stop', label: 'Stop', value: plan.stop_loss_price },
    { key: 'entry', label: 'Entry', value: plan.entry_price },
    { key: 'tp1', label: 'TP1', value: plan.tp1 },
    { key: 'tp2', label: 'TP2', value: plan.tp2 },
    { key: 'tp3', label: 'TP3', value: plan.tp3 },
  ];
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;

  const withPct = points.map((p) => ({ ...p, pct: ((p.value - min) / span) * 100 }));
  const byKey = Object.fromEntries(withPct.map((p) => [p.key, p]));

  const riskLeft = Math.min(byKey.stop.pct, byKey.entry.pct);
  const riskWidth = Math.abs(byKey.entry.pct - byKey.stop.pct);
  const rewardLeft = Math.min(byKey.entry.pct, byKey.tp3.pct);
  const rewardWidth = Math.abs(byKey.tp3.pct - byKey.entry.pct);

  return { points: withPct, riskZone: { left: riskLeft, width: riskWidth }, rewardZone: { left: rewardLeft, width: rewardWidth } };
}

export default function PlanBar({ plan }: { plan: PlanBarData }) {
  const { points, riskZone, rewardZone } = useMemo(() => buildPlanBar(plan), [plan]);

  return (
    <div className="relative w-full h-9 min-w-[180px]">
      <div className="absolute top-3 left-0 right-0 h-1.5 rounded-full bg-slate-100" />
      <div
        className="absolute top-3 h-1.5 rounded-full bg-red-300"
        style={{ left: `${riskZone.left}%`, width: `${riskZone.width}%` }}
      />
      <div
        className="absolute top-3 h-1.5 rounded-full bg-emerald-300"
        style={{ left: `${rewardZone.left}%`, width: `${rewardZone.width}%` }}
      />
      {points.map((p) => (
        <div
          key={p.key}
          className="absolute top-2 flex flex-col items-center"
          style={{ left: `${p.pct}%`, transform: 'translateX(-50%)' }}
          title={`${p.label}: $${p.value.toFixed(2)}`}
        >
          <div
            className={`w-2 h-2 rounded-full ${
              p.key === 'stop' ? 'bg-red-600' : p.key === 'entry' ? 'bg-slate-700' : 'bg-emerald-600'
            }`}
          />
          {(p.key === 'stop' || p.key === 'entry' || p.key === 'tp3') && (
            <span className="text-[9px] text-slate-500 mt-3 whitespace-nowrap">{p.label}</span>
          )}
        </div>
      ))}
    </div>
  );
}
