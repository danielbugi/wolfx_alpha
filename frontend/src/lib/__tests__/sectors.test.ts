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

describe('observed vs current membership', () => {
  it('finds the observed snapshot entry by sector name and reports nulls it does not have', async () => {
    const { observedSector } = await import('@/lib/sectors');
    const { snapshot } = await import('@/components/marketIntel/__tests__/fixture');
    const alpha = observedSector(snapshot(), 'Alpha')!;
    expect(alpha.row.rank_20).toBe(1);
    expect(alpha.rankOf).toBe(3);
    expect(alpha.strengthState).toBe('leading');
    expect(alpha.aboveSma50?.numerator).toBe(70);
    expect(alpha.aboveSma200).toBeNull();
    expect(observedSector(snapshot(), 'Nope')).toBeNull();
  });

  it('defaults to a not-point-in-time membership statement', async () => {
    const { DEFAULT_MEMBERSHIP } = await import('@/lib/sectors');
    expect(DEFAULT_MEMBERSHIP.point_in_time).toBe(false);
    expect(DEFAULT_MEMBERSHIP.note).toMatch(/not a record of the sector as it was/);
  });
});
