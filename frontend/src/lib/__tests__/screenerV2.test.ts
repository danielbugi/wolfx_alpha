import { describe, expect, it } from 'vitest';
import { DEFAULT_STATE, formatCell, formatValue, fromSearchParams, ofTotal, toQueryPairs } from '@/lib/screenerV2';
import type { ScreenerRow } from '@/services/screenerV2Api';

describe('screen state <-> query', () => {
  it('sends only filled, valid inputs, converting UI units to API units', () => {
    const pairs = toQueryPairs({
      ...DEFAULT_STATE,
      session: '2026-10-16',
      preset: 'leaders',
      sectors: ['Technology', 'Energy'],
      q: ' aa ',
      ranges: { rs_20: { min: '80', max: '' }, dollar_vol_20: { min: '5', max: 'abc' }, market_cap: { min: '', max: '10' } },
      breakout20: true,
    });
    expect(pairs).toEqual([
      ['session', '2026-10-16'], ['preset', 'leaders'], ['sort', 'rs_20'], ['order', 'desc'], ['page', '1'], ['page_size', '50'],
      ['sector', 'Technology'], ['sector', 'Energy'], ['q', 'AA'],
      ['min_rs_20', '80'], ['min_dollar_vol_20', '5000000'], ['max_market_cap', '10000000000'],
      ['breakout_20', 'true'],
    ]);
  });

  it('round-trips through the URL and falls back to defaults for invalid values', () => {
    const s = { ...DEFAULT_STATE, sort: 'atr_pct', order: 'asc' as const, page: 3, pageSize: 100, ranges: { dollar_vol_20: { min: '20', max: '' } }, newHigh20: true };
    const back = fromSearchParams(new URLSearchParams(toQueryPairs(s)));
    expect(back).toMatchObject({ sort: 'atr_pct', order: 'asc', page: 3, pageSize: 100, newHigh20: true });
    expect(back.ranges.dollar_vol_20).toEqual({ min: '20', max: '' });
    const bad = fromSearchParams(new URLSearchParams('page=-4&page_size=999&order=sideways'));
    expect([bad.page, bad.pageSize, bad.order]).toEqual([1, 50, 'desc']);
  });
});

describe('formatting', () => {
  it('formats by unit and shows missing values as a dash, never zero', () => {
    expect(formatValue(null, '%')).toBe('—');
    expect(formatValue(undefined, 'pctl')).toBe('—');
    expect(formatValue(Number.NaN, '$')).toBe('—');
    expect(formatValue(1.234, '%')).toBe('+1.23%');
    expect(formatValue(-0.5, 'pp')).toBe('-0.5 pp');
    expect(formatValue(91.6, 'pctl')).toBe('92');
    expect(formatValue(2.5e10, '$')).toBe('$25.00B');
    expect(formatValue(7.25e6, '$')).toBe('$7.3M');
    expect(formatValue(true, 'flag')).toBe('Yes');
    expect(formatValue(false, 'flag')).toBe('No');
    const row = { channel_pos: 82.4, atr_pct: null } as unknown as ScreenerRow;
    expect(formatCell(row, 'channel_pos', '%')).toBe('82.4%');                // a level, no sign
    expect(formatCell(row, 'atr_pct', '%')).toBe('—');
    expect(ofTotal(3061, 3063)).toBe('3,061 of 3,063');
    expect(ofTotal(null, 3063)).toBe('—');
  });
});
