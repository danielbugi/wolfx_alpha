import { describe, expect, it } from 'vitest';
import { periodsAhead } from '@/components/charts/StockChart';

describe('periodsAhead (next earnings placed ahead of the last candle)', () => {
  it('counts weekdays only on daily candles', () => {
    expect(periodsAhead('2026-10-09', '2026-10-12', 'daily')).toBe(1); // Fri -> Mon
    expect(periodsAhead('2026-10-09', '2026-10-19', 'daily')).toBe(6);
  });
  it('counts weeks on weekly and calendar months on monthly', () => {
    expect(periodsAhead('2026-10-09', '2026-10-30', 'weekly')).toBe(3);
    expect(periodsAhead('2026-10-09', '2026-12-15', 'monthly')).toBe(2);
  });
  it('is zero for a date that is not ahead', () => {
    expect(periodsAhead('2026-10-09', '2026-10-09', 'daily')).toBe(0);
  });
});
