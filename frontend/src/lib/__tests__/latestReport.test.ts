import { describe, expect, it } from 'vitest';
import { latestReport, statementsPending } from '../latestReport';

const today = new Date('2026-10-28T12:00:00');
const entries = [
  { date: '2027-02-16', eps_estimate: 2.45, eps_actual: null, surprise_pct: null },
  { date: '2026-10-27', eps_estimate: 2.92, eps_actual: 3.91, surprise_pct: 33.8 },
  { date: '2026-07-28', eps_estimate: 0.73, eps_actual: 1.43, surprise_pct: 95 },
  { date: '2026-04-28', eps_estimate: -0.21, eps_actual: 0.21, surprise_pct: 201.2 },
  { date: '2025-10-28', eps_estimate: 2.31, eps_actual: 2.22, surprise_pct: -4.1 },
];
const bars = [
  { date: '2026-10-23', close: 100 },
  { date: '2026-10-26', close: 98 },
  { date: '2026-10-27', close: 112 },
  { date: '2026-10-28', close: 115 },
];

describe('latestReport', () => {
  it('takes the newest report that has results, not the scheduled one', () => {
    const r = latestReport(entries, bars, today)!;
    expect(r).toMatchObject({ date: '2026-10-27', daysAgo: 1, epsActual: 3.91, epsEstimate: 2.92, beat: true });
    expect(r.yearAgo).toEqual({ date: '2025-10-28', eps: 2.22 });
    expect(r.sinceReportPct).toBeCloseTo((115 / 98 - 1) * 100, 6);
    expect(r.previous.map((p) => p.date)).toEqual(['2026-07-28', '2026-04-28', '2025-10-28']);
  });
  it('has no year-ago figure when none is on record, and no price figure when the bars do not bracket the report', () => {
    const r = latestReport(entries.slice(0, 3), [{ date: '2026-10-27', close: 112 }], today)!;
    expect(r.yearAgo).toBeNull();
    expect(r.sinceReportPct).toBeNull();
  });
  it('is null when nothing has been reported', () => {
    expect(latestReport([entries[0]], bars, today)).toBeNull();
  });
});

describe('statementsPending', () => {
  it('flags statements that have not arrived after the report', () => {
    expect(statementsPending('2026-10-27', '2026-06-30')).toBe(true);
    expect(statementsPending('2026-10-27', '2026-09-30')).toBe(false);
    expect(statementsPending('2026-10-27', null)).toBe(true);
  });
});
