// File: frontend/src/components/screener/StrengthScore.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';
import type { ScreenerRow } from '@/services/screenerV2Api';
import { BAND_BG, BAND_HEX, BAND_LABEL, SCORE_PARTS, band } from '@/lib/strength';

/** Score badge tones on the one green scale: pale (weak) to deep (strong). */
const BADGE_TONE = [
  'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-100',
  'bg-emerald-100 text-emerald-800',
  'bg-emerald-300 text-emerald-950',
  'bg-emerald-600 text-white',
  'bg-emerald-800 text-white',
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
            <span className="relative block h-1.5 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
              {b != null && <span className={clsx('absolute inset-y-0 left-0 rounded-full', BAND_BG[b])} style={{ width: `${Math.max(3, Math.min(100, v as number))}%` }} />}
            </span>
            <span className={clsx('text-right tabular-nums', b == null ? 'text-slate-400' : 'text-slate-700')}>{b == null ? '—' : Math.round(v as number)}</span>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The strength as a five-bar icon (like a signal meter): one bar per band reached, pale to deep green; all bars grey when
 * the score is withheld. Decorative next to a number, labelled when alone.
 */
export function StrengthIcon({ score, size = 18, label = false }: { score: number | null | undefined; size?: number; label?: boolean }) {
  const b = band(score);
  const filled = b == null ? 0 : b + 1;
  const text = b == null ? 'No Strength Score' : `Strength ${filled} of 5 (${BAND_LABEL[b].toLowerCase()})`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      role={label ? 'img' : undefined}
      aria-label={label ? text : undefined}
      aria-hidden={label ? undefined : true}
      data-testid="strength-icon"
      data-filled={filled}
    >
      {label && <title>{text}</title>}
      {[0, 1, 2, 3, 4].map((i) => (
        <rect key={i} x={1 + i * 3.8} y={17 - (i + 1) * 3.2} width={2.8} height={(i + 1) * 3.2} rx={0.8} fill={i < filled && b != null ? BAND_HEX[b] : '#e2e8f0'} />
      ))}
    </svg>
  );
}
