import { describe, expect, it } from 'vitest';
import { excludesZero, fmtInt, fmtInterval, fmtPct, fmtR } from '@/lib/labFormat';

describe('labFormat never turns a missing value into zero', () => {
  it('null / undefined / NaN are an em dash', () => {
    expect(fmtR(null)).toBe('—');
    expect(fmtR(undefined)).toBe('—');
    expect(fmtR(Number.NaN)).toBe('—');
    expect(fmtPct(null)).toBe('—');
    expect(fmtInt(null)).toBe('—');
    expect(fmtInterval(null)).toBe('—');
  });
  it('signed values use a real minus and an explicit plus', () => {
    expect(fmtR(0.0421)).toBe('+0.042');
    expect(fmtR(-0.0948)).toBe('−0.095');
    expect(fmtR(0)).toBe('0.000');
    expect(fmtPct(0.0245)).toBe('+2.45%');
    expect(fmtPct(-0.0372)).toBe('−3.72%');
  });
  it('intervals and integers', () => {
    expect(fmtInterval([-0.063, 0.03])).toBe('−0.063 to +0.030');
    expect(fmtInt(592687)).toBe('592,687');
  });
  it('excludesZero is true only when the whole interval is on one side', () => {
    expect(excludesZero([-0.18, -0.007])).toBe(true);
    expect(excludesZero([0.02, 0.1])).toBe(true);
    expect(excludesZero([-0.02, 0.1])).toBe(false);
    expect(excludesZero(null)).toBe(false);
  });
});
