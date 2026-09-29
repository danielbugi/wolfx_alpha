import { describe, expect, it } from 'vitest';
import { describeMetric, formatR, formatSession, formatUtcTimestamp, formatValue } from '@/lib/strategyFormat';

describe('describeMetric follows the backend state, never inventing a zero', () => {
  it('no_data → dash with the no-data note', () => {
    expect(describeMetric({ value: null, n: 0, state: 'no_data' }, 'rate')).toEqual({ text: '—', note: 'No resolved signals yet', tone: 'empty' });
  });
  it('preliminary → value plus "Preliminary · N"', () => {
    expect(describeMetric({ value: 0.3333, n: 3, state: 'preliminary' }, 'rate')).toEqual({ text: '33.3%', note: 'Preliminary · N=3', tone: 'preliminary' });
  });
  it('a measured zero in a real sample is shown as a value, not as no-data', () => {
    expect(describeMetric({ value: 0, n: 2, state: 'preliminary' }, 'rate').text).toBe('0.0%');
  });
  it('ok → value plus N', () => {
    expect(describeMetric({ value: 0.624, n: 87, state: 'ok' }, 'rate')).toEqual({ text: '62.4%', note: 'N=87', tone: 'ok' });
  });
  it('not_available → release note', () => {
    expect(describeMetric({ value: null, n: null, state: 'not_available', release: 'B' }, 'r').note).toBe('Not available — Release B');
  });
  it('custom empty note', () => {
    expect(describeMetric({ value: null, n: 0, state: 'no_data' }, 'r', 'No expired signals yet').note).toBe('No expired signals yet');
  });
});

describe('formatting', () => {
  it('signed R with a real minus', () => {
    expect(formatR(1)).toBe('+1.00R');
    expect(formatR(-0.35)).toBe('−0.35R');
    expect(formatR(0)).toBe('0.00R');
  });
  it('magnitude R is unsigned (MAE is never shown as profit)', () => {
    expect(formatValue(0.59, 'magnitude_r')).toBe('0.59R');
  });
  it('session dates never shift with the browser time zone', () => {
    expect(formatSession('2026-09-28')).toBe('Sep 28, 2026');
    expect(formatSession(null)).toBe('—');
  });
  it('instants are shown in explicit UTC', () => {
    expect(formatUtcTimestamp('2026-09-29T02:22:08.585727+03:00')).toBe('2026-09-28 23:22 UTC');
  });
});
