import { describe, expect, it } from 'vitest';
import type { MarketCalendar } from '@/lib/marketSession';
import type { EarningsDateEntry, MomentumBoardResult } from '@/services/api';
import {
  addDays,
  buildRows,
  countByDate,
  currentWeekStart,
  enrich,
  filterRows,
  formatWeekRange,
  mondayOf,
  nextUpcoming,
  nyDateOf,
  rankRows,
  reportResult,
  sessionAnchors,
  type EarningsRow,
} from '@/lib/earningsCalendar';

describe('earnings read model in the calendar', () => {
  const e = (date: string, over: Partial<EarningsDateEntry> = {}): EarningsDateEntry => ({ date, eps_estimate: 1, eps_actual: null, surprise_pct: null, ...over });

  it("next upcoming follows the server's status (New York date), not the browser clock", () => {
    const rows = [e('2026-07-29', { status: 'reported', eps_actual: 6.18 }), e('2026-10-24', { status: 'upcoming', confidence: 'confirmed' })];
    expect(nextUpcoming(rows, '2026-10-25')?.date).toBe('2026-10-24');   // the server says it is still upcoming in New York
    expect(nextUpcoming([e('2026-10-20', { status: 'awaiting_results' })], '2026-10-01')).toBeNull();
  });

  it('falls back to the date for an older backend without statuses', () => {
    expect(nextUpcoming([e('2026-10-20'), e('2026-11-02')], '2026-10-25')?.date).toBe('2026-11-02');
  });

  it('marks a previously reported date the latest check did not confirm (META-style), from history or from the board', () => {
    const board = [{ symbol: 'META', next_earnings_date: '2026-10-28', category: 'breakout', starred: false, sector: 'Communication Services', close: 700, market_cap: 1 }] as unknown as MomentumBoardResult[];
    const hist = [e('2026-07-29', { status: 'reported', eps_actual: 6.18 }), e('2026-10-28', { status: 'upcoming', confidence: 'unverified' })];
    const rows = buildRows({ board, watchlist: ['META'], histories: { META: hist }, from: '2026-10-26', to: '2026-10-30' });
    expect(rows.map((r) => [r.symbol, r.date, r.unverified])).toEqual([['META', '2026-10-28', true]]);
    const ok = buildRows({ board, watchlist: ['META'], histories: { META: [e('2026-10-28', { status: 'upcoming', confidence: 'confirmed' })] }, from: '2026-10-26', to: '2026-10-30' });
    expect(ok[0].unverified).toBe(false);
    // a board-only row carries the board's confidence (same rule as the read model)
    const boardOnly = [{ ...board[0], symbol: 'ARR', next_earnings_confidence: 'unverified' }] as unknown as MomentumBoardResult[];
    expect(buildRows({ board: boardOnly, watchlist: [], histories: {}, from: '2026-10-26', to: '2026-10-30' })[0].unverified).toBe(true);
  });
});

// Thanksgiving week 2026: Thu 11-26 is a holiday (absent from the calendar).
const cal: MarketCalendar = {
  timezone: 'America/New_York',
  regular_open: '09:30',
  source: 'alpaca',
  holidays_known: true,
  sessions: ['2026-11-20', '2026-11-23', '2026-11-24', '2026-11-25', '2026-11-27', '2026-11-30', '2026-12-01'].map((date) => ({
    date,
    close: date === '2026-11-27' ? '13:00' : '16:00',
  })),
};

function boardRow(over: Partial<MomentumBoardResult>): MomentumBoardResult {
  return {
    symbol: 'AAA',
    category: 'breakout',
    close: 10,
    ret1_pct: 1,
    rvol: 1,
    range_atr: 1,
    below_high_pct: 0,
    atr: 1,
    dv20: 1,
    list_ranks: {},
    starred: false,
    star_lists: [],
    sector: 'Technology',
    quality_grade: null,
    overall_quality_score: null,
    market_cap: 1e9,
    next_earnings_date: null,
    days_to_earnings: null,
    ...over,
  } as MomentumBoardResult;
}

const entry = (date: string, est: number | null, actual: number | null, surprise: number | null = null): EarningsDateEntry => ({
  date,
  eps_estimate: est,
  eps_actual: actual,
  surprise_pct: surprise,
});

describe('dates', () => {
  it('New York date, not the browser date: 00:30 in Israel is still the previous day in New York', () => {
    expect(nyDateOf(new Date('2026-10-16T00:30:00+03:00'))).toBe('2026-10-15');
    expect(nyDateOf(new Date('2026-10-16T08:00:00+03:00'))).toBe('2026-10-16');
  });
  it('week arithmetic across month ends', () => {
    expect(addDays('2026-10-30', 3)).toBe('2026-11-02');
    expect(mondayOf('2026-10-15')).toBe('2026-10-12');
    expect(mondayOf('2026-10-18')).toBe('2026-10-12'); // Sunday belongs to the week before
    expect(formatWeekRange('2026-09-28')).toBe('Sep 28 – Oct 2, 2026');
    expect(formatWeekRange('2026-10-12')).toBe('Oct 12 – 16, 2026');
  });
  it('on a weekend, "this week" is the coming week', () => {
    expect(currentWeekStart('2026-10-17')).toBe('2026-10-19'); // Sat
    expect(currentWeekStart('2026-10-18')).toBe('2026-10-19'); // Sun
    expect(currentWeekStart('2026-10-16')).toBe('2026-10-12'); // Fri
  });
});

describe('sessionAnchors', () => {
  it('Monday: yesterday is Friday', () => {
    const a = sessionAnchors('2026-11-23', cal);
    expect(a).toMatchObject({ yesterday: '2026-11-20', tomorrow: '2026-11-24', todayIsSession: true, verified: true });
  });
  it('the day before a holiday: tomorrow skips it', () => {
    expect(sessionAnchors('2026-11-25', cal).tomorrow).toBe('2026-11-27');
  });
  it('the day after a holiday: yesterday skips it', () => {
    expect(sessionAnchors('2026-11-27', cal).yesterday).toBe('2026-11-25');
  });
  it('a holiday is not a session today', () => {
    expect(sessionAnchors('2026-11-26', cal)).toMatchObject({ todayIsSession: false, yesterday: '2026-11-25', tomorrow: '2026-11-27' });
  });
  it('Friday: tomorrow is Monday', () => {
    expect(sessionAnchors('2026-11-27', cal).tomorrow).toBe('2026-11-30');
  });
  it('without a calendar: Mon-Fri assumption, flagged unverified', () => {
    const a = sessionAnchors('2026-11-26', null);
    expect(a).toMatchObject({ todayIsSession: true, yesterday: '2026-11-25', tomorrow: '2026-11-27', verified: false });
  });
  it('a weekday-fallback calendar is not trusted for holidays', () => {
    expect(sessionAnchors('2026-11-26', { ...cal, holidays_known: false }).verified).toBe(false);
  });
});

describe('buildRows', () => {
  const board = [
    boardRow({ symbol: 'AAA', next_earnings_date: '2026-10-15' }),
    boardRow({ symbol: 'BBB', next_earnings_date: '2026-12-31' }), // outside the window
    boardRow({ symbol: 'WWW', next_earnings_date: '2026-10-14', sector: 'Energy' }),
  ];

  it('board rows inside the window, with board metadata', () => {
    const rows = buildRows({ board, watchlist: [], histories: {}, from: '2026-10-12', to: '2026-10-16' });
    expect(rows.map((r) => r.symbol).sort()).toEqual(['AAA', 'WWW']);
    expect(rows.find((r) => r.symbol === 'AAA')).toMatchObject({ signal: 'breakout', sector: 'Technology', onWatchlist: false, epsEstimate: null });
  });

  it('a loaded watchlist history replaces the board date and adds past reports', () => {
    const histories = { WWW: [entry('2026-10-13', 1, 1.2, 20), entry('2026-07-14', 1, 0.9)] };
    const rows = buildRows({ board, watchlist: ['WWW'], histories, from: '2026-10-12', to: '2026-10-16' });
    const www = rows.filter((r) => r.symbol === 'WWW');
    expect(www).toHaveLength(1);
    expect(www[0]).toMatchObject({ date: '2026-10-13', onWatchlist: true, sector: 'Energy', epsActual: 1.2, surprisePct: 20, beats: 0, judged: 1 });
  });

  it('a watchlist name off the board has no invented metadata', () => {
    const rows = buildRows({ board: [], watchlist: ['ZZZ'], histories: { ZZZ: [entry('2026-10-15', 2, null)] }, from: '2026-10-12', to: '2026-10-16' });
    expect(rows[0]).toMatchObject({ symbol: 'ZZZ', sector: null, close: null, signal: null, epsEstimate: 2, epsActual: null });
  });

  it('a failed history falls back to the board date', () => {
    const rows = buildRows({ board, watchlist: ['WWW'], histories: { WWW: 'error' }, from: '2026-10-12', to: '2026-10-16' });
    expect(rows.find((r) => r.symbol === 'WWW')?.date).toBe('2026-10-14');
  });
});

describe('enrich / reportResult', () => {
  const base = buildRows({ board: [boardRow({ next_earnings_date: '2026-10-15' })], watchlist: [], histories: {}, from: '2026-10-01', to: '2026-10-31' })[0];

  it('beat record counts only the last 8 judged reports before this one', () => {
    const history = [
      entry('2026-10-15', 1.5, null),
      // 10 past reports, newest first: the 3 oldest are misses and fall outside the last 8 except one
      ...['2026-07-20', '2026-04-20', '2026-01-20', '2025-10-20', '2025-07-20', '2025-04-20', '2025-01-20', '2024-10-20', '2024-07-20', '2024-04-20'].map(
        (d, i) => entry(d, 1, i >= 7 ? 0.5 : 2),
      ),
      entry('2026-11-01', 1, 5), // after the row: ignored
    ];
    const r = enrich(base, history);
    expect(r.epsEstimate).toBe(1.5);
    expect(r.judged).toBe(8);
    expect(r.beats).toBe(7); // 2024-10-20 is the only miss inside the last 8
  });

  it('no judged reports -> null record, never 0 of 0', () => {
    expect(enrich(base, [entry('2026-10-15', 1, null)])).toMatchObject({ beats: null, judged: null });
  });

  it('reportResult', () => {
    expect(reportResult({ epsActual: 1.1, epsEstimate: 1 })).toBe('beat');
    expect(reportResult({ epsActual: 0.9, epsEstimate: 1 })).toBe('miss');
    expect(reportResult({ epsActual: 1, epsEstimate: 1 })).toBe('inline');
    expect(reportResult({ epsActual: null, epsEstimate: 1 })).toBeNull();
  });
});

describe('rankRows / filterRows / countByDate', () => {
  const row = (over: Partial<EarningsRow>): EarningsRow => ({
    symbol: 'X',
    date: '2026-10-15',
    sector: 'Technology',
    close: 1,
    marketCap: 1,
    signal: null,
    starred: false,
    onWatchlist: false,
    epsEstimate: null,
    epsActual: null,
    surprisePct: null,
    beats: null,
    judged: null,
    ...over,
  });
  const rows = [
    row({ symbol: 'BIG', marketCap: 1e12 }),
    row({ symbol: 'NEAR', signal: 'near_breakout' }),
    row({ symbol: 'BRK', signal: 'breakout' }),
    row({ symbol: 'STAR', signal: 'near_breakout', starred: true }),
    row({ symbol: 'MINE', onWatchlist: true, sector: 'Energy' }),
    row({ symbol: 'EARLY', date: '2026-10-14' }),
  ];

  it('date, then watchlist, confirmed, breakout, near breakout, then size', () => {
    expect(rankRows(rows).map((r) => r.symbol)).toEqual(['EARLY', 'MINE', 'STAR', 'BRK', 'NEAR', 'BIG']);
  });

  it('filters combine', () => {
    const f = { query: '', sector: null, watchlistOnly: false, signalOnly: false };
    expect(filterRows(rows, { ...f, signalOnly: true }).map((r) => r.symbol)).toEqual(['NEAR', 'BRK', 'STAR']);
    expect(filterRows(rows, { ...f, watchlistOnly: true }).map((r) => r.symbol)).toEqual(['MINE']);
    expect(filterRows(rows, { ...f, sector: 'Energy' }).map((r) => r.symbol)).toEqual(['MINE']);
    expect(filterRows(rows, { ...f, query: 'br' }).map((r) => r.symbol)).toEqual(['BRK']);
  });

  it('counts per day', () => {
    expect(countByDate(rows)).toEqual({ '2026-10-15': 5, '2026-10-14': 1 });
  });
});
