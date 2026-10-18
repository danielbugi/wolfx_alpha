// File: frontend/src/lib/watchlistView.ts
// Pure helpers for the Watchlist page: the ONE Screener v2 request for the saved symbols, client-side ordering of the
// rows the server returned (values are never recalculated here), the comparison selection, and staleness.
import type { QueryPairs, ScreenerRow } from '@/services/screenerV2Api';

/** Metric columns of the watchlist table, in order (all come straight from the Screener v2 row). */
export const WATCH_COLUMNS: (keyof ScreenerRow)[] = [
  'close', 'change_1d', 'ret_5', 'ret_20', 'ret_60', 'rs_20', 'rs_60', 'vs_spx_20', 'dist_sma20', 'dist_sma50', 'channel_pos', 'atr_pct', 'dollar_vol_20',
];

export const WATCH_SHORT: Partial<Record<keyof ScreenerRow, string>> = {
  close: 'Close', change_1d: '1D', ret_5: '5S', ret_20: '20S', ret_60: '60S', rs_20: 'RS 20', rs_60: 'RS 60', vs_spx_20: 'vs SPX 20',
  dist_sma20: 'vs 20D avg', dist_sma50: 'vs 50D avg', channel_pos: 'Chan pos', atr_pct: 'ATR %', dollar_vol_20: '$ vol 20',
};

/** Metrics shown green/red by sign (changes, not levels). */
export const WATCH_SIGNED = new Set(['change_1d', 'ret_5', 'ret_20', 'ret_60', 'vs_spx_20', 'vs_spx_60', 'vs_sector_20', 'dist_sma20', 'dist_sma50']);

/** Rows in the comparison panel, grouped like the stock page's context panel. */
export const COMPARE_ROWS: { group: string; keys: (keyof ScreenerRow)[] }[] = [
  { group: 'Relative strength', keys: ['rs_20', 'rs_60', 'ret_5', 'ret_20', 'ret_60', 'vs_spx_20', 'vs_spx_60', 'vs_sector_20'] },
  { group: 'Trend and channel', keys: ['dist_sma20', 'dist_sma50', 'channel_pos', 'breakout_20', 'new_high_20'] },
  { group: 'Volatility and liquidity', keys: ['atr_pct', 'atr_pct_pctile', 'channel_width_pctile', 'volume_ratio', 'dollar_vol_20'] },
];

export const COMPARE_MAX = 4;

/** The single request: every saved symbol as an exact-symbol filter on the Screener's own query. */
export function watchlistPairs(symbols: string[], session: string | null): QueryPairs {
  const out: QueryPairs = [];
  if (session) out.push(['session', session]);
  for (const s of symbols) out.push(['symbol', s]);
  out.push(['sort', 'symbol'], ['order', 'asc'], ['page', '1'], ['page_size', String(Math.max(1, Math.min(200, symbols.length)))]);
  return out;
}

export type WatchSort = { key: 'added' | 'symbol' | 'sector' | keyof ScreenerRow; order: 'asc' | 'desc' };

/**
 * Order the saved symbols for display. "added" keeps the saved order (newest first). Other keys sort by the server's
 * value; symbols with no value (or no row at all) always go last, whatever the direction.
 */
export function sortSymbols(symbols: string[], rows: Record<string, ScreenerRow>, sort: WatchSort): string[] {
  if (sort.key === 'added') return sort.order === 'desc' ? [...symbols] : [...symbols].reverse();
  const dir = sort.order === 'asc' ? 1 : -1;
  const val = (s: string): string | number | null => {
    if (sort.key === 'symbol') return s;
    const r = rows[s];
    if (!r) return null;
    const v = r[sort.key as keyof ScreenerRow];
    if (typeof v === 'boolean') return v ? 1 : 0;
    return typeof v === 'number' || typeof v === 'string' ? v : null;
  };
  return [...symbols].sort((a, b) => {
    const va = val(a);
    const vb = val(b);
    if (va == null && vb == null) return a.localeCompare(b);
    if (va == null) return 1;
    if (vb == null) return -1;
    const c = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb));
    return c === 0 ? a.localeCompare(b) : c * dir;
  });
}

/** Calendar days between a session date and today (both YYYY-MM-DD, UTC). */
export function daysBetween(fromIso: string, toIso: string): number {
  return Math.round((Date.parse(`${toIso.slice(0, 10)}T00:00:00Z`) - Date.parse(`${fromIso.slice(0, 10)}T00:00:00Z`)) / 86_400_000);
}

/** More than this many calendar days between the newest session and today means the data has not been updated. */
export const STALE_AFTER_DAYS = 4;

export function staleness(session: string, newestSession: string | null, today: string): { olderSession: boolean; notUpdatedDays: number | null } {
  const newest = newestSession ?? session;
  const age = daysBetween(newest, today);
  return { olderSession: !!newestSession && session < newestSession, notUpdatedDays: age > STALE_AFTER_DAYS ? age : null };
}
