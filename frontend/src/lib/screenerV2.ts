// File: frontend/src/lib/screenerV2.ts
// Pure helpers for the Smart Screener v2 page: the screen's state <-> URL query, value formatting by unit, and the
// table's column set. Nothing here fills a gap: a null value formats as "—".
import type { FundamentalsCutoff, MetricFamily, QueryPairs, ScreenerRow } from '@/services/screenerV2Api';

export const PAGE_SIZES = [25, 50, 100, 200] as const;

/** Range filters offered in the UI, with the unit the user types in and the multiplier to the API's unit. */
export const RANGE_FILTERS: { key: string; label: string; suffix: string; scale: number }[] = [
  { key: 'rs_20', label: 'RS pctl 20', suffix: '', scale: 1 },
  { key: 'rs_60', label: 'RS pctl 60', suffix: '', scale: 1 },
  { key: 'ret_20', label: '20-session return', suffix: '%', scale: 1 },
  { key: 'vs_sector_20', label: 'vs sector 20', suffix: 'pp', scale: 1 },
  { key: 'channel_pos', label: 'Channel position', suffix: '%', scale: 1 },
  { key: 'atr_pct', label: 'ATR %', suffix: '%', scale: 1 },
  { key: 'dollar_vol_20', label: 'Dollar volume', suffix: '$M', scale: 1e6 },
  { key: 'market_cap', label: 'Market cap', suffix: '$B', scale: 1e9 },
];

export interface ScreenState {
  session: string | null;
  preset: string | null;
  sort: string;
  order: 'asc' | 'desc';
  page: number;
  pageSize: number;
  sectors: string[];
  q: string;
  ranges: Record<string, { min: string; max: string }>;
  breakout20: boolean;
  newHigh20: boolean;
  fundamentalsCutoff: FundamentalsCutoff;
}

export const DEFAULT_STATE: ScreenState = {
  session: null, preset: null, sort: 'rs_20', order: 'desc', page: 1, pageSize: 50, sectors: [], q: '', ranges: {}, breakout20: false, newHigh20: false, fundamentalsCutoff: 'post_session',
};

const num = (s: string): number | null => {
  if (s.trim() === '') return null;
  const v = Number(s);
  return Number.isFinite(v) ? v : null;
};

/** State -> API query pairs (UI units converted to API units; empty or invalid inputs are simply not sent). */
export function toQueryPairs(s: ScreenState): QueryPairs {
  const out: QueryPairs = [];
  if (s.session) out.push(['session', s.session]);
  if (s.preset) out.push(['preset', s.preset]);
  out.push(['sort', s.sort], ['order', s.order], ['page', String(s.page)], ['page_size', String(s.pageSize)]);
  for (const sec of s.sectors) out.push(['sector', sec]);
  if (s.q.trim()) out.push(['q', s.q.trim().toUpperCase()]);
  for (const f of RANGE_FILTERS) {
    const r = s.ranges[f.key];
    if (!r) continue;
    const lo = num(r.min);
    const hi = num(r.max);
    if (lo != null) out.push([`min_${f.key}`, String(lo * f.scale)]);
    if (hi != null) out.push([`max_${f.key}`, String(hi * f.scale)]);
  }
  if (s.breakout20) out.push(['breakout_20', 'true']);
  if (s.newHigh20) out.push(['new_high_20', 'true']);
  if (s.fundamentalsCutoff !== 'post_session') out.push(['fundamentals_cutoff', s.fundamentalsCutoff]);
  return out;
}

/** URL search params -> state (unknown keys ignored; the backend validates everything again). */
export function fromSearchParams(p: URLSearchParams): ScreenState {
  const s: ScreenState = { ...DEFAULT_STATE, ranges: {} };
  s.session = p.get('session');
  s.preset = p.get('preset');
  s.sort = p.get('sort') || DEFAULT_STATE.sort;
  s.order = p.get('order') === 'asc' ? 'asc' : 'desc';
  s.page = Math.max(1, Number(p.get('page')) || 1);
  const ps = Number(p.get('page_size'));
  s.pageSize = (PAGE_SIZES as readonly number[]).includes(ps) ? ps : DEFAULT_STATE.pageSize;
  s.sectors = p.getAll('sector');
  s.q = p.get('q') || '';
  for (const f of RANGE_FILTERS) {
    const lo = p.get(`min_${f.key}`);
    const hi = p.get(`max_${f.key}`);
    if (lo != null || hi != null) {
      s.ranges[f.key] = { min: lo != null && num(lo) != null ? String(num(lo)! / f.scale) : '', max: hi != null && num(hi) != null ? String(num(hi)! / f.scale) : '' };
    }
  }
  s.breakout20 = p.get('breakout_20') === 'true';
  s.newHigh20 = p.get('new_high_20') === 'true';
  s.fundamentalsCutoff = p.get('fundamentals_cutoff') === 'session_close' ? 'session_close' : 'post_session';
  return s;
}

export function formatValue(v: unknown, unit: string): string {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  switch (unit) {
    case '%':
      return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
    case 'pp':
      return `${v > 0 ? '+' : ''}${v.toFixed(1)} pp`;
    case 'pctl':
      return v.toFixed(0);
    case 'x':
      return `${v.toFixed(2)}x`;
    case '$':
      if (Math.abs(v) >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
      if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
      if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
      return `$${v.toFixed(2)}`;
    default:
      return String(v);
  }
}

/** Positional metrics shown without a sign (a level, not a change). */
export const LEVEL_PERCENT = new Set(['channel_pos', 'channel_width', 'atr_pct']);

export function formatCell(row: ScreenerRow, key: keyof ScreenerRow, unit: string): string {
  const v = row[key];
  if (LEVEL_PERCENT.has(key as string) && typeof v === 'number') return `${v.toFixed(1)}%`;
  return formatValue(v, unit);
}

export const FAMILY_LABEL: Record<MetricFamily, string> = { observed: 'Observed', derived: 'Derived', reference: 'Reference (ingestion-time)' };

/** The table's metric columns, in order. */
export const COLUMNS: (keyof ScreenerRow)[] = [
  'close', 'change_1d', 'ret_5', 'ret_20', 'ret_60', 'vs_spx_20', 'vs_sector_20', 'rs_20', 'rs_60',
  'breakout_20', 'channel_pos', 'channel_width_pctile', 'atr_pct', 'volume_ratio', 'dollar_vol_20', 'market_cap',
];

/** "3,061 of 3,063" */
export const ofTotal = (n: number | null | undefined, total: number | null | undefined) =>
  n == null || total == null ? '—' : `${n.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}`;
