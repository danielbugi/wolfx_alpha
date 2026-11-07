// File: frontend/src/lib/signals.ts
// Pure helpers for the public Fundamental Updates pages (/signals): route checks, URL keys, filter lists and date formatting.
// The patterns mirror the backend's validation (backend/services/public_signals_service.py), so a malformed URL is rejected before any request.

export const UPDATE_TYPES: { slug: string; label: string }[] = [
  { slug: 'revenue-acceleration', label: 'Revenue growth accelerated' },
  { slug: 'margin-expansion', label: 'Operating margin expanded' },
  { slug: 'operating-leverage', label: 'Operating income outgrew revenue' },
  { slug: 'net-income-turnaround', label: 'Net income turned positive' },
  { slug: 'fcf-turnaround', label: 'Free cash flow turned positive' },
];

export const SECTORS = ['Basic Materials', 'Communication Services', 'Consumer Cyclical', 'Consumer Defensive', 'Energy', 'Financial Services', 'Healthcare',
  'Industrials', 'Real Estate', 'Technology', 'Utilities'] as const;

const SYMBOL_RE = /^[A-Z][A-Z0-9.-]{0,9}$/;
const QUARTER_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Pages anyone may open, signed in or not, with no redirect either way. */
export const OPEN_PATH_PREFIXES: readonly string[] = ['/signals'];

export function isOpenPath(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  return OPEN_PATH_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/** The page URL of one update from its natural key ("POWI/2026-09-30/margin-expansion"). */
export function signalHref(key: string): string {
  return `/signals/${key.split('/').map(encodeURIComponent).join('/')}`;
}

export interface SignalParams {
  symbol: string;
  quarter: string;
  slug: string;
}

/** Validated route params, or null when any part is malformed (the page then shows "not found" without calling the API). */
export function parseSignalParams(symbol: string, quarter: string, slug: string): SignalParams | null {
  const s = decodeURIComponent(symbol ?? '');
  const q = decodeURIComponent(quarter ?? '');
  const t = decodeURIComponent(slug ?? '');
  if (!SYMBOL_RE.test(s) || !QUARTER_RE.test(q) || Number.isNaN(Date.parse(`${q}T00:00:00Z`))) return null;
  if (new Date(`${q}T00:00:00Z`).toISOString().slice(0, 10) !== q) return null;          // 2026-02-30 parses, but is not a real date
  if (!UPDATE_TYPES.some((u) => u.slug === t)) return null;
  return { symbol: s, quarter: q, slug: t };
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** '2026-11-05' -> 'Nov 5, 2026' (calendar dates are shown as dates, never shifted by the viewer's time zone). */
export function fmtDay(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

/** '2026-11-04T23:03:00Z' -> 'Nov 4, 2026, 23:03 UTC' (news and record times are UTC on purpose: one time for every reader). */
export function fmtUtc(iso: string): string {
  return `${fmtDay(iso)}, ${iso.slice(11, 16)} UTC`;
}

export const CORRECTION_TEXT: Record<string, string> = {
  restated: 'Figures revised',
  redated: 'Public date revised',
  withdrawn: 'Withdrawn',
};
