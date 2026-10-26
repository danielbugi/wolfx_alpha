// The Overview's market-activity payload is normalised once: null, missing and partially loaded values become null
// (shown as "—"), never a default and never a crash; sectors without a numeric change are dropped.
import { describe, expect, it } from 'vitest';
import { normalizeMarketOverview, showCount } from '@/lib/marketOverview';

const FULL = {
  market_breadth: { total_stocks: 3041, gainers: 1056, losers: 1932, unchanged: 53, advance_decline_ratio: 0.55, unusual_volume_count: 80, overbought_count: 202, oversold_count: 254 },
  market_indicators: { avg_volume_ratio: 0.99, avg_rsi: 49.22, total_market_cap: 8.8e13 },
  sector_performance: [{ sector: 'Energy', stock_count: 126, avg_performance: 3.06, up_count: 90 }],
  session: '2026-10-23',
  population: { min_close: 1, liquidity_floor: null, universe: 3060, description: 'x' },
  coverage: { change_known: 3041, volume_ratio_known: 3033, rsi_known: 3035, market_cap_known: 3020 },
};

describe('normalizeMarketOverview', () => {
  it('keeps a complete payload as it is', () => {
    const o = normalizeMarketOverview(FULL)!;
    expect(o.market_breadth).toEqual(FULL.market_breadth);
    expect(o.market_indicators).toEqual(FULL.market_indicators);
    expect(o.sector_performance).toEqual(FULL.sector_performance);
    expect(o.session).toBe('2026-10-23');
  });

  it('null figures stay null (a session whose indicators are not computed yet)', () => {
    const o = normalizeMarketOverview({
      ...FULL,
      market_breadth: { ...FULL.market_breadth, advance_decline_ratio: null },
      market_indicators: { avg_volume_ratio: null, avg_rsi: null, total_market_cap: null },
      coverage: { change_known: 3041, volume_ratio_known: 0, rsi_known: 0, market_cap_known: 3020 },
    })!;
    expect(o.market_indicators).toEqual({ avg_volume_ratio: null, avg_rsi: null, total_market_cap: null });
    expect(o.market_breadth.advance_decline_ratio).toBeNull();
    expect(o.coverage?.rsi_known).toBe(0);
  });

  it('missing blocks and fields become null, not undefined dereferences', () => {
    const o = normalizeMarketOverview({ session: '2026-10-23' })!;
    expect(o.market_breadth.total_stocks).toBeNull();
    expect(o.market_breadth.unusual_volume_count).toBeNull();
    expect(o.market_indicators.avg_rsi).toBeNull();
    expect(o.sector_performance).toEqual([]);
    expect(o.coverage).toBeNull();
    expect(o.population).toBeNull();
    expect(normalizeMarketOverview({})!.session).toBeNull();
  });

  it('partially loaded: non-numeric, NaN and string values are not trusted', () => {
    const o = normalizeMarketOverview({
      market_breadth: { total_stocks: '3041', gainers: NaN, losers: Infinity, unusual_volume_count: 7 },
      market_indicators: { avg_rsi: '50', avg_volume_ratio: 1.1 },
      sector_performance: [
        { sector: 'Energy', stock_count: 10, avg_performance: null },
        { sector: 'Tech', stock_count: null, avg_performance: 1.2 },
        { sector: 7, stock_count: 3, avg_performance: 0.4 },
        null,
        { sector: 'Utilities', stock_count: 74, avg_performance: -0.7 },
      ],
    })!;
    expect(o.market_breadth).toMatchObject({ total_stocks: null, gainers: null, losers: null, unusual_volume_count: 7 });
    expect(o.market_indicators).toEqual({ avg_rsi: null, avg_volume_ratio: 1.1, total_market_cap: null });
    expect(o.sector_performance.map((s) => s.sector)).toEqual(['Utilities']);   // only sectors that can be drawn honestly
  });

  it('an unusable payload is no payload', () => {
    expect(normalizeMarketOverview(null)).toBeNull();
    expect(normalizeMarketOverview('error')).toBeNull();
  });

  it('counts show a dash when unknown, including 0 as a real value', () => {
    expect(showCount(null)).toBe('—');
    expect(showCount(undefined)).toBe('—');
    expect(showCount(0)).toBe('0');
  });
});
