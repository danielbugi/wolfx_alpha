// File: frontend/src/lib/strength.ts
// Plain-language strength views of the Screener's own metrics (nothing is recalculated): where a stock sits on the
// Strength Map, how a 0-100 percentile is coloured, and its trend against the 20/50-day averages. Descriptive only --
// a corner describes the past sessions, it is not a forecast or a recommendation.
import type { ScreenerRow } from '@/services/screenerV2Api';

export type Quadrant = 'leading' | 'improving' | 'fading' | 'lagging';

/** The midpoint of a percentile: above it a stock beat more than half of all stocks over the window. */
export const MIDPOINT = 50;

export const QUADRANTS: Record<Quadrant, { label: string; description: string }> = {
  leading: { label: 'Leading', description: 'Stronger than most stocks over both the last 20 and the last 60 sessions.' },
  improving: { label: 'Improving', description: 'Stronger than most over the last 20 sessions, weaker over the last 60.' },
  fading: { label: 'Fading', description: 'Stronger than most over the last 60 sessions, weaker over the last 20.' },
  lagging: { label: 'Lagging', description: 'Weaker than most stocks over both the last 20 and the last 60 sessions.' },
};
export const QUADRANT_ORDER: Quadrant[] = ['leading', 'improving', 'fading', 'lagging'];

/** The Strength Map corner from the two observed percentiles; null when either is unavailable (never guessed). */
export function quadrant(rs20: number | null | undefined, rs60: number | null | undefined): Quadrant | null {
  if (rs20 == null || rs60 == null || !Number.isFinite(rs20) || !Number.isFinite(rs60)) return null;
  const recent = rs20 >= MIDPOINT;
  const longer = rs60 >= MIDPOINT;
  return recent && longer ? 'leading' : recent ? 'improving' : longer ? 'fading' : 'lagging';
}

export function quadrantCounts(rows: Pick<ScreenerRow, 'rs_20' | 'rs_60'>[]): Record<Quadrant | 'unavailable', number> {
  const out = { leading: 0, improving: 0, fading: 0, lagging: 0, unavailable: 0 };
  for (const r of rows) out[quadrant(r.rs_20, r.rs_60) ?? 'unavailable']++;
  return out;
}

/** Five bands for a 0-100 percentile, weak to strong (used by meters and badges). */
export type Band = 0 | 1 | 2 | 3 | 4;
export function band(pctl: number | null | undefined): Band | null {
  if (pctl == null || !Number.isFinite(pctl)) return null;
  return Math.min(4, Math.max(0, Math.floor(pctl / 20))) as Band;
}
export const BAND_LABEL = ['Very weak', 'Weak', 'Middle', 'Strong', 'Very strong'] as const;
/** Tailwind background classes per band (red -> amber -> slate -> green). */
export const BAND_BG = ['bg-red-500', 'bg-orange-400', 'bg-slate-400', 'bg-emerald-400', 'bg-emerald-600'] as const;

export const QUADRANT_TONE: Record<Quadrant, string> = {
  leading: 'bg-emerald-100 text-emerald-800',
  improving: 'bg-sky-100 text-sky-800',
  fading: 'bg-amber-100 text-amber-800',
  lagging: 'bg-red-100 text-red-800',
};

/** The Strength Score's parts in display order, with their weights (backend STRENGTH_WEIGHTS). */
export const SCORE_PARTS: { key: 'strength_rs' | 'strength_trend' | 'strength_momentum' | 'strength_volume' | 'strength_liquidity'; label: string; weight: number; hint: string }[] = [
  { key: 'strength_rs', label: 'Relative strength', weight: 35, hint: 'mean of the 20- and 60-session RS percentiles' },
  { key: 'strength_trend', label: 'Trend', weight: 25, hint: 'above its 20- and 50-day averages, 20-day above 50-day' },
  { key: 'strength_momentum', label: 'Momentum', weight: 20, hint: 'position in the 20-day channel, plus a breakout or new high' },
  { key: 'strength_volume', label: 'Volume', weight: 10, hint: 'volume vs its 10-day average (0.5x to 2x)' },
  { key: 'strength_liquidity', label: 'Liquidity', weight: 10, hint: 'average daily dollar volume ($1M to $1B)' },
];

export type Trend = 'above_both' | 'below_both' | 'mixed';
/** Close vs its 20- and 50-day averages (the stored session indicators); null when either is unavailable. */
export function trend(dist20: number | null | undefined, dist50: number | null | undefined): Trend | null {
  if (dist20 == null || dist50 == null) return null;
  if (dist20 >= 0 && dist50 >= 0) return 'above_both';
  if (dist20 < 0 && dist50 < 0) return 'below_both';
  return 'mixed';
}
export const TREND_LABEL: Record<Trend, string> = {
  above_both: 'Above its 20- and 50-day averages',
  below_both: 'Below its 20- and 50-day averages',
  mixed: 'Between its 20- and 50-day averages',
};

/** Liquidity in words from the 20-session average dollar volume (thresholds stated in the label). */
export function liquidityLabel(dollarVol: number | null | undefined): string | null {
  if (dollarVol == null || !Number.isFinite(dollarVol)) return null;
  if (dollarVol >= 1e9) return 'Very liquid (≥ $1B a day)';
  if (dollarVol >= 1e8) return 'Liquid ($100M–1B a day)';
  if (dollarVol >= 1e7) return 'Moderate ($10–100M a day)';
  return 'Thin (< $10M a day)';
}
