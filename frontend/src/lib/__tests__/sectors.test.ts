import { describe, expect, it } from 'vitest';
import { rebasedComparison, sectorHref, sectorSlug } from '@/lib/sectors';

describe('sector links', () => {
  it('slug matches the backend rule', () => {
    expect(sectorSlug('Financial Services')).toBe('financial-services');
  });
  it('the Unknown bucket has no page', () => {
    expect(sectorHref('Unknown')).toBeNull();
    expect(sectorHref(null)).toBeNull();
    expect(sectorHref('Technology')).toBe('/sectors/technology');
  });
});

describe('rebasedComparison', () => {
  const index = [
    { date: '2026-01-02', level: 100 },
    { date: '2026-01-05', level: 110 },
    { date: '2026-01-06', level: 121 },
  ];
  const spx = [
    { date: '2026-01-05', close: 200 },
    { date: '2026-01-06', close: 210 },
  ];
  it('rebases both lines to 0% on the first shared date and drops unshared dates', () => {
    const out = rebasedComparison(index, spx, '2026-01-01');
    expect(out.map((p) => p.date)).toEqual(['2026-01-05', '2026-01-06']);
    expect(out[0]).toMatchObject({ sector: 0, spx: 0 });
    expect(out[1].sector).toBeCloseTo(10);
    expect(out[1].spx).toBeCloseTo(5);
  });
  it('empty when nothing overlaps', () => {
    expect(rebasedComparison(index, [], '2026-01-01')).toEqual([]);
  });
});
