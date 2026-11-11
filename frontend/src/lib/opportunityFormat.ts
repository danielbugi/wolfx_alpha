// Pure formatting for Opportunity Intelligence: a missing value is never shown as zero; every figure line carries its source and date.
import type { FigureUnit, OIFigure, Verdict } from '@/services/opportunityApi';

export const DASH = '—';
const MINUS = '−';

function signed(n: number, digits: number, suffix: string): string {
  const s = Math.abs(n).toFixed(digits);
  return (n > 0 ? '+' : n < 0 ? MINUS : '') + s + suffix;
}

export function fmtUsd(n: number): string {
  const a = Math.abs(n);
  const sign = n < 0 ? MINUS : '';
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  return `${sign}$${a.toFixed(0)}`;
}

export function fmtFigure(value: number | null | undefined, unit: FigureUnit, label = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  switch (unit) {
    case 'usd':
      return fmtUsd(value);
    case 'usd_per_share':
      return (value < 0 ? MINUS : '') + '$' + Math.abs(value).toFixed(2);
    case 'pct':
      // levels (a margin) are unsigned; changes (growth, move, surprise) are signed
      return /margin/i.test(label) && !/change/i.test(label) ? `${value.toFixed(1)}%` : signed(value, 1, '%');
    case 'pp':
      return signed(value, 1, ' pp');
    case 'bps':
      return signed(value, 0, ' bps');
    default:
      return value.toFixed(2);
  }
}

export function fmtWhen(iso: string): string {
  // '2026-11-04T21:05:00+00:00' -> '2026-11-04 21:05 UTC'; a bare date stays a date
  const m = /^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?/.exec(iso || '');
  if (!m) return DASH;
  return m[2] ? `${m[1]} ${m[2]} UTC` : m[1];
}

export function provenance(f: OIFigure): string {
  return `${f.source} · ${fmtWhen(f.available_at)}`;
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  supports: 'Supports',
  contradicts: 'Contradicts',
  neutral: 'No clear change',
  mixed: 'Mixed',
  unavailable: 'Not available',
  held: 'Held (non-operating)',
};

export const VERDICT_CLASS: Record<Verdict, string> = {
  supports: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  contradicts: 'bg-rose-50 text-rose-800 border-rose-200',
  neutral: 'bg-slate-50 text-slate-700 border-slate-200',
  mixed: 'bg-amber-50 text-amber-800 border-amber-200',
  unavailable: 'bg-white text-slate-400 border-slate-200',
  held: 'bg-amber-50 text-amber-800 border-amber-200',
};

export const CLASS_LABEL: Record<'fundamental' | 'surprise' | 'reaction', string> = {
  fundamental: 'Reported figures',
  surprise: 'EPS vs estimate',
  reaction: 'Price vs S&P 500',
};
