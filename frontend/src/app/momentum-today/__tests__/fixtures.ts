// Shared fixtures for the Momentum Today page and the Overview card: one session (2026-10-20), three boards and the
// session's list ranks. FFF made the lists without being a breakout or a new high (fetched by symbol).
import type { QueryPairs, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';

export const row = (symbol: string, over: Partial<ScreenerRow> = {}) =>
  ({
    symbol, sector: 'Technology', industry: 'Software', change_1d: 2.1, strength_score: 91.3, strength_reason: null,
    strength_rs: 95, strength_trend: 100, strength_momentum: 88, strength_volume: 60, strength_liquidity: 80,
    market_cap: 5e9, dollar_vol_20: 3e7, quality_grade: 'B', quality_score: 78, pe_ratio: 20, ps_ratio: 3, float_shares: 1.2e8,
    days_to_earnings: 40, days_since_earnings: 50, earnings_surprise_pct: null, company_name: null, ...over,
  }) as ScreenerRow;
export const result = (rows: ScreenerRow[], total = rows.length, session = '2026-10-20') =>
  ({ session, universe: 3060, total, rows }) as unknown as ScreenerResult;
export const has = (pairs: QueryPairs, k: string, v: string) => pairs.some(([a, b]) => a === k && b === v);
export const dayLists = (session: string, entries: Record<string, [Record<string, number>, boolean]>) => ({
  session,
  bySymbol: new Map(Object.entries(entries).map(([s, [list_ranks, top_two]]) => [s, { list_ranks, top_two }])),
});

export const AAA = row('AAA', { company_name: 'Acme Analytics Inc.', days_since_earnings: 1, earnings_surprise_pct: 12.4, last_earnings_date: '2026-10-19' });
export const BBB = row('BBB', { sector: 'Energy', strength_score: 35.2, change_1d: 1.0, quality_grade: 'D', market_cap: 1e9, dollar_vol_20: 2e6, days_to_earnings: 1, pe_ratio: null });
export const CCC = row('CCC', { strength_score: null, strength_reason: 'illiquid (under $1M a day)', change_1d: 5.0, quality_grade: null, market_cap: null, dollar_vol_20: null });
export const DDD = row('DDD', { strength_score: 64, change_1d: 0.5 });
export const EEE = row('EEE', { strength_score: 96.5, change_1d: 1.2 });
export const FFF = row('FFF', { strength_score: 70, change_1d: 30, quality_grade: 'C' });

export function searchMock(pairs: QueryPairs): ScreenerResult {
  const symbols = pairs.filter(([k]) => k === 'symbol').map(([, v]) => v);
  if (symbols.length) return result([FFF].filter((r) => symbols.includes(r.symbol)));
  if (!has(pairs, 'page', '1')) return result([], 0); // later pages: nothing more in these fixtures
  if (has(pairs, 'breakout_20', 'true')) return result([AAA, BBB, CCC], 142);
  if (has(pairs, 'new_high_20', 'true')) return result([DDD], 57);
  return result([EEE, AAA], 155);
}

export const LISTS = () => dayLists('2026-10-20', { BBB: [{ gainers: 2, volume: 4 }, true], CCC: [{ atr: 9 }, false], FFF: [{ gainers: 1, atr: 1 }, true] });
