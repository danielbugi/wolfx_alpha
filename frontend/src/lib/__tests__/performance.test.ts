// Watchlist performance rules: one common session baseline, YTD and month-end boundaries, late starts / missing baseline /
// stale series flagged, discontinuities withheld, gaps never filled, the S&P 500 measured over the same dates.
import { describe, expect, it } from 'vitest';
import {
  Bar, baselineSession, biggestMover, chartRows, computePerformance, coverageNote, equalWeightBasket, periodAnchor, ranked, sectorMix, sessionMove,
} from '@/lib/performance';

/** Weekday sessions between two dates, inclusive. */
function sessions(from: string, to: string): string[] {
  const out: string[] = [];
  for (let d = new Date(`${from}T00:00:00Z`); d <= new Date(`${to}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.push(d.toISOString().slice(0, 10));
  }
  return out;
}
const bars = (dates: string[], f: (i: number, d: string) => number | null): Bar[] => dates.map((d, i) => ({ date: d, close: f(i, d) }));

const CAL = sessions('2025-09-01', '2026-10-16');
const spx = bars(CAL, (i) => 100 + i * 0.1);

describe('period anchors and the baseline session', () => {
  it('YTD anchors on Dec 31 of the previous year; months clamp to the month end', () => {
    expect(periodAnchor('2026-10-16', 'ytd')).toBe('2025-12-31');
    expect(periodAnchor('2026-01-02', 'ytd')).toBe('2025-12-31');
    expect(periodAnchor('2026-10-16', '1m')).toBe('2026-09-16');
    expect(periodAnchor('2026-03-31', '1m')).toBe('2026-02-28');
    expect(periodAnchor('2026-05-31', '3m')).toBe('2026-02-28');
    expect(periodAnchor('2026-10-16', '1y')).toBe('2025-10-16');
    expect(periodAnchor('2026-01-15', '6m')).toBe('2025-07-15');
  });

  it('the baseline is the last session on or before the anchor (a weekend anchor falls back to Friday)', () => {
    expect(baselineSession(CAL, '2025-12-31')).toBe('2025-12-31');
    expect(baselineSession(CAL, '2026-02-28')).toBe('2026-02-27');
    expect(baselineSession(CAL, '2020-01-01')).toBeNull();
  });
});

describe('computePerformance', () => {
  it('rebases every full-coverage series to 0% on the same baseline and measures the S&P 500 over the same dates', () => {
    const a = bars(CAL, (i) => 50 + i);
    const r = computePerformance('ytd', spx, [{ symbol: 'AAA', bars: a }]);
    expect(r.baseline).toBe('2025-12-31');
    expect(r.end).toBe('2026-10-16');
    const s = r.series[0];
    expect(s.comparable).toBe(true);
    expect(s.points.get('2025-12-31')).toBe(0);
    const i0 = CAL.indexOf('2025-12-31');
    const iN = CAL.length - 1;
    expect(s.ret).toBeCloseTo(((50 + iN) / (50 + i0) - 1) * 100, 10);
    expect(s.benchRet).toBeCloseTo(((100 + iN * 0.1) / (100 + i0 * 0.1) - 1) * 100, 10);
    expect(s.vsBench).toBeCloseTo((s.ret as number) - (s.benchRet as number), 10);
    expect(r.bench?.get('2025-12-31')).toBe(0);
  });

  it('a stock listed after the period start starts at its own first close and is flagged, never silently compared', () => {
    const ipo = bars(CAL.filter((d) => d >= '2026-03-02'), (i) => 20 + i);
    const r = computePerformance('ytd', spx, [{ symbol: 'IPO', bars: ipo }]);
    const s = r.series[0];
    expect(s.flags).toEqual(['late_start']);
    expect(s.comparable).toBe(false);
    expect(s.start).toBe('2026-03-02');
    expect(s.benchRet).toBeCloseTo(((100 + (CAL.length - 1) * 0.1) / (100 + CAL.indexOf('2026-03-02') * 0.1) - 1) * 100, 10);
    expect(coverageNote(s, r.baseline, r.end)).toBe('history starts 2026-03-02, after the period start 2025-12-31');
    expect(chartRows(r, ['IPO']).find((row) => row.date === '2026-02-27')).not.toHaveProperty('IPO');
  });

  it('a missing close on the baseline session is flagged as such (the stock has earlier history)', () => {
    const gap = bars(CAL, (i, d) => (d === '2025-12-31' ? null : 10 + i));
    const s = computePerformance('ytd', spx, [{ symbol: 'GAP', bars: gap }]).series[0];
    expect(s.flags).toEqual(['missing_baseline']);
    expect(s.start).toBe(CAL[CAL.indexOf('2025-12-31') + 1]); // the next session after the baseline
  });

  it('a stale series ends at its own last close and says so', () => {
    const stale = bars(CAL.filter((d) => d <= '2026-10-09'), (i) => 10 + i);
    const r = computePerformance('1m', spx, [{ symbol: 'OLD', bars: stale }]);
    const s = r.series[0];
    expect(s.flags).toEqual(['stale']);
    expect(s.end).toBe('2026-10-09');
    expect(coverageNote(s, r.baseline, r.end)).toBe('last close 2026-10-09, before 2026-10-16');
  });

  it('a recorded discontinuity inside the window withholds the return; one before the window does not', () => {
    const b = bars(CAL, (i) => 10 + i);
    const inside = computePerformance('3m', spx, [{ symbol: 'SPLT', bars: b, discontinuities: [{ date: '2026-09-01', kind: 'split' }] }]).series[0];
    expect(inside.ret).toBeNull();
    expect(inside.flags).toContain('discontinuity');
    expect(coverageNote(inside, null, null)).toContain('2026-09-01');
    const before = computePerformance('1m', spx, [{ symbol: 'SPLT', bars: b, discontinuities: [{ date: '2026-02-02', kind: 'split' }] }]).series[0];
    expect(before.ret).not.toBeNull();
  });

  it('missing sessions stay gaps and no data in the period is reported as such', () => {
    const holes = bars(CAL, (i, d) => (d === '2026-10-14' ? null : 10 + i));
    const r = computePerformance('1m', spx, [{ symbol: 'HOLE', bars: holes }, { symbol: 'NONE', bars: bars(['2025-09-02'], () => 5) }]);
    expect(r.series[0].points.get('2026-10-14')).toBeNull();
    expect(r.series[0].comparable).toBe(true);
    expect(r.series[1].flags).toEqual(['no_data']);
    expect(chartRows(r, ['HOLE']).find((row) => row.date === '2026-10-14')?.HOLE).toBeNull();
  });

  it('without the benchmark the stocks still chart on their own sessions but no relative figure is invented', () => {
    const r = computePerformance('1m', null, [{ symbol: 'AAA', bars: bars(CAL, (i) => 10 + i) }]);
    expect(r.bench).toBeNull();
    expect(r.series[0].ret).not.toBeNull();
    expect(r.series[0].vsBench).toBeNull();
    expect(chartRows(r, ['AAA'])[0]).not.toHaveProperty('SPX');
  });

  it('ranks reported returns high to low and puts series without a return last', () => {
    const r = computePerformance('1m', spx, [
      { symbol: 'LOW', bars: bars(CAL, (i) => 100 - i * 0.01) },
      { symbol: 'NONE', bars: [] },
      { symbol: 'HIGH', bars: bars(CAL, (i) => 10 + i) },
    ]);
    expect(ranked(r.series).map((s) => s.symbol)).toEqual(['HIGH', 'LOW', 'NONE']);
  });
});

describe('equal-weight basket, session moves and sector mix', () => {
  it('averages only comparable full-period members; the basket return equals the mean of their returns and its last point', () => {
    const r = computePerformance('1m', spx, [
      { symbol: 'UP', bars: bars(CAL, (i) => 100 + i) },
      { symbol: 'DOWN', bars: bars(CAL, (i) => 400 - i) },
      { symbol: 'LATE', bars: bars(CAL.filter((d) => d >= '2026-10-01'), (i) => 10 + i) },
      { symbol: 'SPLIT', bars: bars(CAL, (i) => 50 + i), discontinuities: [{ date: '2026-10-05' }] },
    ]);
    const b = equalWeightBasket(r, ['UP', 'DOWN', 'LATE', 'SPLIT']);
    expect(b.members).toEqual(['UP', 'DOWN']);
    expect(b.excluded).toEqual(['LATE', 'SPLIT']);
    const up = r.series.find((s) => s.symbol === 'UP')!.ret as number;
    const down = r.series.find((s) => s.symbol === 'DOWN')!.ret as number;
    expect(b.ret).toBeCloseTo((up + down) / 2, 10);
    expect(b.points.get(r.end!)).toBeCloseTo(b.ret!, 10);
    expect(b.points.get(r.baseline!)).toBe(0);
    expect(b.vsBench).toBeCloseTo(b.ret! - r.bench!.get(r.end!)!, 10);
    expect(b.beat).toBe(1); // UP beats the slowly rising index, DOWN does not
    expect(b.beatOf).toBe(2);
  });

  it('a session where any member has no close is a gap in the basket, never an average of the rest', () => {
    const hole = CAL[CAL.length - 3];
    const r = computePerformance('1m', spx, [
      { symbol: 'A', bars: bars(CAL, (i) => 100 + i) },
      { symbol: 'B', bars: bars(CAL, (i, d) => (d === hole ? null : 200 + i)) },
    ]);
    const b = equalWeightBasket(r, ['A', 'B']);
    expect(b.points.get(hole)).toBeNull();
    expect(b.points.get(r.end!)).not.toBeNull();
  });

  it('has no basket return when nothing is comparable, and no relative figure without the S&P 500', () => {
    const r = computePerformance('1m', null, [{ symbol: 'A', bars: bars(CAL, (i) => 100 + i) }]);
    const b = equalWeightBasket(r, ['A']);
    expect(b.ret).not.toBeNull();
    expect(b.vsBench).toBeNull();
    expect(b.beatOf).toBe(0);
    const late = computePerformance('1m', spx, [{ symbol: 'L', bars: bars(CAL.filter((d) => d >= '2026-10-12'), (i) => 1 + i) }]);
    expect(equalWeightBasket(late, ['L']).ret).toBeNull();
  });

  it('a session move is close-to-close against the previous session; a missing close gives no move', () => {
    const hole = CAL[CAL.length - 3];
    const r = computePerformance('1m', spx, [
      { symbol: 'A', bars: bars(CAL, (i) => 100 + i) },
      { symbol: 'B', bars: bars(CAL, (i, d) => (d === hole ? null : 200 + 5 * i)) },
    ]);
    const n = CAL.length - 1;
    expect(sessionMove(r, 'A', CAL[n])).toBeCloseTo(((100 + n) / (100 + n - 1) - 1) * 100, 10);
    expect(sessionMove(r, 'B', CAL[n - 1])).toBeNull(); // the previous session is the hole
    expect(sessionMove(r, 'A', r.baseline!)).toBeNull(); // nothing before the window's first session
    expect(biggestMover(r, ['A', 'B'], CAL[n])?.symbol).toBe('B');
    expect(biggestMover(r, ['A', 'B'], r.baseline!)).toBeNull();
  });

  it('sector mix counts the current classification, largest first, unclassified last', () => {
    const sec: Record<string, string | null> = { A: 'Technology', B: 'Energy', C: 'Technology', D: null };
    expect(sectorMix(['A', 'B', 'C', 'D'], (s) => sec[s])).toEqual([
      { sector: 'Technology', count: 2, share: 0.5 },
      { sector: 'Energy', count: 1, share: 0.25 },
      { sector: 'Unclassified', count: 1, share: 0.25 },
    ]);
  });
});
