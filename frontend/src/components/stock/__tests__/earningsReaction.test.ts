import { describe, expect, it } from 'vitest';
import { reactionPct } from '@/components/stock/EarningsPanel';
import type { PriceBar } from '@/services/api';

const b = (date: string, close: number): PriceBar => ({
  date, open: close, high: close, low: close, close, volume: 1, donchian_high_20: null, donchian_low_20: null,
});
const bars = [b('2026-07-28', 100), b('2026-07-29', 102), b('2026-07-30', 110), b('2026-07-31', 108), b('2026-08-03', 109)];

describe('reactionPct', () => {
  it('measures close before the report day to close the day after', () => {
    // report 07-30: before = 07-29 (102), after = 07-31 (108)
    expect(reactionPct(bars, '2026-07-30')).toBeCloseTo((108 / 102 - 1) * 100);
  });
  it('a weekend report date lands on the next session', () => {
    // report 08-01 (Sat): session = 08-03, before = 07-31 (108), after = none -> unknown
    expect(reactionPct(bars, '2026-08-01')).toBeNull();
  });
  it('is unknown when the bars do not cover the day before', () => {
    expect(reactionPct(bars, '2026-07-28')).toBeNull();
  });
});
