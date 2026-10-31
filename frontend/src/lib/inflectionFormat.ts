// File: frontend/src/lib/inflectionFormat.ts
// Pure formatting for the inflection screens. A missing value is an em dash, never zero.
import type { InflectionKeyMetric } from '@/services/inflectionApi';

export const DASH = '—';

export function fmtMoney(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  const a = Math.abs(v);
  const s = a >= 1e12 ? `${(a / 1e12).toFixed(2)}T` : a >= 1e9 ? `${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(0)}K` : a.toFixed(0);
  return `${v < 0 ? '-' : ''}$${s}`;
}

export function fmtPct(v: number | null | undefined, digits = 1, signed = true): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  const x = (v * 100).toFixed(digits);
  return signed && v > 0 ? `+${x}%` : `${x}%`;
}

export function fmtMetric(m: InflectionKeyMetric, which: 'current' | 'previous'): string {
  const v = m[which];
  if (v == null || !Number.isFinite(v)) return DASH;
  switch (m.unit) {
    case 'pct': return fmtPct(v, 1, false);
    case 'money': return fmtMoney(v);
    case 'bps': return `${v > 0 ? '+' : ''}${v.toFixed(0)} bps`;
    case 'ratio': return `${v.toFixed(1)}x`;
    case 'score': return v.toFixed(0);
    default: return String(v);
  }
}

const BASIS: Record<string, { text: string; assumed: boolean }> = {
  earnings_calendar: { text: 'earnings report date', assumed: false },
  first_seen: { text: 'first seen by First Light', assumed: false },
};

/** How the "public on" date was determined; an assumed lag or a window end is labelled so it is never mistaken for a filing date. */
export function basisLabel(basis: string): { text: string; assumed: boolean } {
  if (BASIS[basis]) return BASIS[basis];
  if (basis.startsWith('assumed_lag')) return { text: `assumed ${basis.replace('assumed_lag_', '').replace('d', '')}-day lag after quarter end`, assumed: true };
  if (basis.startsWith('repricing_window_end')) return { text: 'end of the 10-session repricing window', assumed: false };
  if (basis.startsWith('sec_')) return { text: 'SEC filing date', assumed: false };
  return { text: basis.replace(/_/g, ' '), assumed: true };
}

export function freshnessText(days: number): string {
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

export function fiscalLabel(fy: number | null, fq: number | null, quarterEnd: string): string {
  return fy != null && fq != null ? `FY${fy} Q${fq}` : `quarter ending ${quarterEnd}`;
}

export const SEVERITY_STYLE: Record<string, string> = {
  high: 'bg-emerald-100 text-emerald-800',
  medium: 'bg-amber-100 text-amber-800',
  low: 'bg-slate-100 text-slate-600',
};

export function strengthColor(s: number): string {
  return s >= 75 ? 'bg-emerald-500' : s >= 55 ? 'bg-amber-400' : 'bg-slate-300';
}
