import { describe, expect, it } from 'vitest';
import { DASH, fmtFigure, fmtUsd, fmtWhen, provenance, VERDICT_CLASS, VERDICT_LABEL, VIEW_LABEL } from '@/lib/opportunityFormat';

describe('opportunityFormat', () => {
  it('never turns a missing value into zero', () => {
    expect(fmtFigure(null, 'usd')).toBe(DASH);
    expect(fmtFigure(undefined, 'pct')).toBe(DASH);
    expect(fmtFigure(Number.NaN, 'pp')).toBe(DASH);
  });
  it('money, changes and levels', () => {
    expect(fmtUsd(1026248000)).toBe('$1.03B');
    expect(fmtUsd(-11e6)).toBe('−$11.0M');
    expect(fmtFigure(29.77, 'pct', 'Revenue growth, year over year')).toBe('+29.8%');
    expect(fmtFigure(27.37, 'pct', 'Operating margin')).toBe('27.4%');
    expect(fmtFigure(-2.18, 'pct', 'EPS surprise')).toBe('−2.2%');
    expect(fmtFigure(31.13, 'pp')).toBe('+31.1 pp');
    expect(fmtFigure(5.32, 'usd_per_share')).toBe('$5.32');
  });
  it('every figure line names its source and when it became available', () => {
    expect(fmtWhen('2026-11-04T21:05:00+00:00')).toBe('2026-11-04 21:05 UTC');
    expect(fmtWhen('2026-11-10')).toBe('2026-11-10');
    expect(provenance({ label: 'Revenue', value: 1, unit: 'usd', source: 'SEC 10-Q 0001511737-26-000074', available_at: '2026-11-06T12:01:36+00:00' }))
      .toBe('SEC 10-Q 0001511737-26-000074 · 2026-11-06 12:01 UTC');
  });
  it('labels are descriptive, never advice or a forecast', () => {
    for (const v of [...Object.values(VERDICT_LABEL), ...Object.values(VIEW_LABEL)]) expect(v).not.toMatch(/buy|sell|target|should|outperform|opportunit|signal|winner/i);
  });
  it('a market response is never coloured as business evidence', () => {
    expect(VERDICT_CLASS.positive).not.toBe(VERDICT_CLASS.supports);
    expect(VERDICT_CLASS.negative).not.toBe(VERDICT_CLASS.contradicts);
    expect(VERDICT_CLASS.unverified).toBe(VERDICT_CLASS.unavailable);
  });
});
