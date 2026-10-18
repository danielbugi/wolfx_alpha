// Watchlist view helpers: one request for all saved symbols, ordering without recalculation (missing values last in both
// directions), and staleness.
import { describe, expect, it } from 'vitest';
import type { ScreenerRow } from '@/services/screenerV2Api';
import { daysBetween, sortSymbols, staleness, watchlistPairs } from '@/lib/watchlistView';

const r = (symbol: string, over: Partial<ScreenerRow>) => ({ symbol, sector: null, ...over }) as ScreenerRow;

describe('watchlistPairs', () => {
  it('asks the Screener once for every saved symbol (exact symbols), sized to the list', () => {
    const p = watchlistPairs(['NVDA', 'AAPL', 'MSFT'], null);
    expect(p.filter(([k]) => k === 'symbol').map(([, v]) => v)).toEqual(['NVDA', 'AAPL', 'MSFT']);
    expect(p).toContainEqual(['page_size', '3']);
    expect(p.some(([k]) => k === 'session')).toBe(false);
    expect(watchlistPairs(['NVDA'], '2026-10-14')[0]).toEqual(['session', '2026-10-14']);
  });
});

describe('sortSymbols', () => {
  const rows = { A: r('A', { rs_20: 50, sector: 'Energy' }), B: r('B', { rs_20: null, sector: 'Technology' }), C: r('C', { rs_20: 90, sector: null }) };
  const syms = ['C', 'D', 'B', 'A']; // D has no row at all

  it('keeps the saved order (newest first) for "added", reversed on request', () => {
    expect(sortSymbols(syms, rows, { key: 'added', order: 'desc' })).toEqual(syms);
    expect(sortSymbols(syms, rows, { key: 'added', order: 'asc' })).toEqual(['A', 'B', 'D', 'C']);
  });

  it('sorts by the server value with missing values and missing rows last in both directions', () => {
    expect(sortSymbols(syms, rows, { key: 'rs_20', order: 'desc' })).toEqual(['C', 'A', 'B', 'D']);
    expect(sortSymbols(syms, rows, { key: 'rs_20', order: 'asc' })).toEqual(['A', 'C', 'B', 'D']);
    expect(sortSymbols(syms, rows, { key: 'sector', order: 'asc' })).toEqual(['A', 'B', 'C', 'D']);
    expect(sortSymbols(syms, rows, { key: 'symbol', order: 'asc' })).toEqual(['A', 'B', 'C', 'D']);
  });
});

describe('staleness', () => {
  it('flags an older selected session and data not updated for more than 4 days', () => {
    expect(daysBetween('2026-10-16', '2026-10-18')).toBe(2);
    expect(staleness('2026-10-16', '2026-10-16', '2026-10-18')).toEqual({ olderSession: false, notUpdatedDays: null });
    expect(staleness('2026-10-14', '2026-10-16', '2026-10-18')).toEqual({ olderSession: true, notUpdatedDays: null });
    expect(staleness('2026-10-09', '2026-10-09', '2026-10-18')).toEqual({ olderSession: false, notUpdatedDays: 9 });
  });
});
