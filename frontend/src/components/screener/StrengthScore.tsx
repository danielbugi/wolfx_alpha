// File: frontend/src/components/screener/StrengthScore.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';
import type { ScreenerRow } from '@/services/screenerV2Api';
import { BAND_BG, BAND_LABEL, SCORE_PARTS, band } from '@/lib/strength';

const BADGE_TONE = [
  'bg-red-100 text-red-800',
  'bg-orange-100 text-orange-800',
  'bg-slate-100 text-slate-700',
  'bg-emerald-100 text-emerald-800',
  'bg-emerald-600 text-white',
] as const;

type ScoreRow = Pick<ScreenerRow, 'strength_score' | 'strength_reason'> & Partial<ScreenerRow>;

/**
 * The Strength Score (0-100) as a coloured badge, weak -> strong. When the data-health gate withholds it, a grey "—" whose
 * title gives the reason (never a 0).
 */
export function ScoreBadge({ row, size = 'md' }: { row: ScoreRow; size?: 'sm' | 'md' | 'lg' }) {
  const s = row.strength_score;
  const b = band(s);
  const cls = size === 'lg' ? 'min-w-[3.25rem] px-2.5 py-1 text-[22px]' : size === 'sm' ? 'min-w-[2.1rem] px-1.5 py-0.5 text-[12px]' : 'min-w-[2.6rem] px-2 py-0.5 text-[14px]';
  if (b == null) {
    return (
      <span
        className={clsx('inline-flex items-center justify-center rounded-lg bg-slate-100 font-semibold tabular-nums text-slate-400', cls)}
        title={row.strength_reason ? `No Strength Score: ${row.strength_reason}` : 'No Strength Score'}
        aria-label={row.strength_reason ? `No Strength Score: ${row.strength_reason}` : 'No Strength Score'}
        data-testid="score-badge"
      >
        —
      </span>
    );
  }
  const text = `Strength Score ${Math.round(s as number)} of 100 (${BAND_LABEL[b].toLowerCase()})`;
  return (
    <span className={clsx('inline-flex items-center justify-center rounded-lg font-bold tabular-nums', BADGE_TONE[b], cls)} title={text} aria-label={text} data-testid="score-badge">
      {Math.round(s as number)}
    </span>
  );
}

/** The five parts of the score with their weights and values; the withheld reason instead when there is no score. */
export function ScoreBreakdown({ row, compact = false }: { row: ScoreRow; compact?: boolean }) {
  return (
    <div className={clsx('space-y-1', compact ? 'text-[11px]' : 'text-[12px]')} data-testid="score-breakdown">
      {row.strength_score == null && row.strength_reason && (
        <p className="rounded-lg bg-slate-50 px-2 py-1 text-slate-500" data-testid="score-reason">
          No score: {row.strength_reason}
        </p>
      )}
      {SCORE_PARTS.map((p) => {
        const v = row[p.key] as number | null | undefined;
        const b = band(v);
        return (
          <div key={p.key} className="grid grid-cols-[minmax(0,1fr)_4.5rem_2rem] items-center gap-2" title={`${p.label} (${p.weight}%): ${p.hint}`} data-testid="score-part">
            <span className="truncate text-slate-600">
              {p.label} <span className="text-slate-400">{p.weight}%</span>
            </span>
            <span className="relative block h-1.5 overflow-hidden rounded-full bg-slate-200" aria-hidden="true">
              {b != null && <span className={clsx('absolute inset-y-0 left-0 rounded-full', BAND_BG[b])} style={{ width: `${Math.max(3, Math.min(100, v as number))}%` }} />}
            </span>
            <span className={clsx('text-right tabular-nums', b == null ? 'text-slate-400' : 'text-slate-700')}>{b == null ? '—' : Math.round(v as number)}</span>
          </div>
        );
      })}
    </div>
  );
}
