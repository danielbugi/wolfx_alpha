// File: frontend/src/components/screener/StrengthMeter.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';
import { BAND_BG, BAND_LABEL, band, QUADRANT_TONE, QUADRANTS, Quadrant } from '@/lib/strength';

/** A 0-100 percentile as a short bar coloured weak -> strong, with the number. "—" when unavailable (never 0). */
export function StrengthMeter({ value, label, width = 'w-12', showValue = true }: { value: number | null | undefined; label: string; width?: string; showValue?: boolean }) {
  const b = band(value);
  const text = b == null ? `${label}: unavailable` : `${label}: ${Math.round(value as number)} of 100 (${BAND_LABEL[b].toLowerCase()})`;
  return (
    <span className="inline-flex items-center gap-1.5" role="img" aria-label={text} title={text} data-testid="strength-meter">
      <span className={clsx('relative inline-block h-1.5 overflow-hidden rounded-full bg-slate-200', width)} aria-hidden="true">
        {b != null && <span className={clsx('absolute inset-y-0 left-0 rounded-full', BAND_BG[b])} style={{ width: `${Math.max(4, Math.min(100, value as number))}%` }} />}
      </span>
      {showValue && <span className={clsx('w-6 text-right tabular-nums', b == null ? 'text-slate-400' : 'text-slate-700')}>{b == null ? '—' : Math.round(value as number)}</span>}
    </span>
  );
}

/** The Strength Map corner as a small chip; nothing when unavailable. */
export function QuadrantChip({ q, className }: { q: Quadrant | null; className?: string }) {
  if (!q) return <span className={clsx('text-[11px] text-slate-400', className)}>—</span>;
  return (
    <span className={clsx('inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold', QUADRANT_TONE[q], className)} title={QUADRANTS[q].description} data-testid="quadrant-chip">
      {QUADRANTS[q].label}
    </span>
  );
}
