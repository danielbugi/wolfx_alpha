// Strength views: the Strength Map corners from the two observed percentiles (50 is the line; unavailable is never
// guessed), counts, the five meter bands, trend vs the 20/50-day averages, and liquidity in words.
import { describe, expect, it } from 'vitest';
import { band, liquidityLabel, quadrant, quadrantCounts, trend } from '@/lib/strength';

describe('quadrant', () => {
  it('names the corner from RS 20 (recent) and RS 60 (longer), with 50 counted as the stronger side', () => {
    expect(quadrant(80, 90)).toBe('leading');
    expect(quadrant(50, 50)).toBe('leading');
    expect(quadrant(70, 30)).toBe('improving');
    expect(quadrant(20, 75)).toBe('fading');
    expect(quadrant(49.9, 49.9)).toBe('lagging');
  });

  it('is unavailable when either percentile is missing', () => {
    expect(quadrant(null, 80)).toBeNull();
    expect(quadrant(80, undefined)).toBeNull();
    expect(quadrant(Number.NaN, 80)).toBeNull();
  });

  it('counts corners and the unavailable ones', () => {
    expect(quadrantCounts([{ rs_20: 90, rs_60: 90 }, { rs_20: 90, rs_60: 10 }, { rs_20: null, rs_60: 10 }, { rs_20: 10, rs_60: 10 }])).toEqual({
      leading: 1, improving: 1, fading: 0, lagging: 1, unavailable: 1,
    });
  });
});

describe('meters, trend and liquidity', () => {
  it('bands a percentile into five steps, with 100 in the top band and missing as null', () => {
    expect([0, 19.9, 20, 59, 80, 100].map(band)).toEqual([0, 0, 1, 2, 4, 4]);
    expect(band(null)).toBeNull();
  });

  it('reads the trend from the distances to the 20- and 50-day averages', () => {
    expect(trend(2, 5)).toBe('above_both');
    expect(trend(-1, -3)).toBe('below_both');
    expect(trend(-1, 4)).toBe('mixed');
    expect(trend(0, 0)).toBe('above_both');
    expect(trend(null, 4)).toBeNull();
  });

  it('describes liquidity with its thresholds', () => {
    expect(liquidityLabel(2.4e10)).toBe('Very liquid (≥ $1B a day)');
    expect(liquidityLabel(5e8)).toBe('Liquid ($100M–1B a day)');
    expect(liquidityLabel(2e7)).toBe('Moderate ($10–100M a day)');
    expect(liquidityLabel(4e6)).toBe('Thin (< $10M a day)');
    expect(liquidityLabel(null)).toBeNull();
  });
});
