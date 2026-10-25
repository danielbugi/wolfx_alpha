// File: frontend/src/lib/performance.ts
// Watchlist performance: every selected stock and the S&P 500 rebased to 0% on ONE common trading-session baseline.
//
// Rules (pure, tested in lib/__tests__/performance.test.ts):
// - The session calendar is the S&P 500's own bars (exchange trading sessions). The period's baseline is the last session
//   on or before the period anchor (YTD: the last session of the previous calendar year); the end is the newest session.
// - A stock with a close on the baseline starts there. Without one (IPO, listing later, or a missing bar) it starts at its
//   own first close in the period and is flagged: its return covers a shorter window and is never silently compared.
// - A stock whose newest close is older than the newest session is flagged stale; its return ends at its own last close,
//   and the S&P 500 is measured over exactly the same dates.
// - A recorded price discontinuity (e.g. an unadjusted split) inside the window means no return is reported for it.
// - Missing sessions stay gaps (null), never filled.
// Closes are the stored adjusted closes (splits adjusted; dividends are NOT consistently included), so these are price
// returns on adjusted closes, not total returns. The S&P 500 is the price index (no dividends).

export type Period = '1m' | '3m' | '6m' | 'ytd' | '1y';
export const PERIODS: { value: Period; label: string }[] = [
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
  { value: '6m', label: '6M' },
  { value: 'ytd', label: 'YTD' },
  { value: '1y', label: '1Y' },
];

export const MAX_CHART_SYMBOLS = 6;
export const DEFAULT_CHART_SYMBOLS = 3;

export interface Bar {
  date: string;
  close: number | null;
}
export interface Discontinuity {
  date: string;
  kind?: string | null;
}

export type Flag = 'late_start' | 'missing_baseline' | 'stale' | 'discontinuity' | 'no_data';

export interface SeriesResult {
  symbol: string;
  /** null when no return can be reported (no data in the window, or a discontinuity) */
  ret: number | null;
  /** stock return minus the S&P 500 return over the SAME start and end sessions, percentage points */
  vsBench: number | null;
  benchRet: number | null;
  start: string | null;
  end: string | null;
  flags: Flag[];
  /** session -> % from the stock's own start; null for a missing session */
  points: Map<string, number | null>;
  /** true when start = the period baseline and end = the newest session and no flag applies */
  comparable: boolean;
  discontinuityDate?: string;
}

export interface PerformanceResult {
  period: Period;
  baseline: string | null;
  end: string | null;
  sessions: string[];
  bench: Map<string, number> | null;
  series: SeriesResult[];
}

const iso = (y: number, m: number, d: number) => `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
const daysIn = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate();

/** Calendar anchor of a period ending on `end` (YYYY-MM-DD): same day N months earlier (clamped), or Dec 31 for YTD. */
export function periodAnchor(end: string, period: Period): string {
  const [y, m, d] = end.slice(0, 10).split('-').map(Number);
  if (period === 'ytd') return iso(y - 1, 12, 31);
  const back = period === '1m' ? 1 : period === '3m' ? 3 : period === '6m' ? 6 : 12;
  let ty = y;
  let tm = m - back;
  while (tm < 1) {
    tm += 12;
    ty -= 1;
  }
  return iso(ty, tm, Math.min(d, daysIn(ty, tm)));
}

/** The last session on or before `anchor`, or null when the calendar does not reach back that far. */
export function baselineSession(sessions: string[], anchor: string): string | null {
  let found: string | null = null;
  for (const s of sessions) {
    if (s <= anchor) found = s;
    else break;
  }
  return found;
}

const valid = (b: Bar) => b.close != null && Number.isFinite(b.close) && b.close > 0;

export function computePerformance(
  period: Period,
  benchBars: Bar[] | null,
  stocks: { symbol: string; bars: Bar[]; discontinuities?: Discontinuity[] }[],
): PerformanceResult {
  const benchValid = (benchBars ?? []).filter(valid).sort((a, b) => a.date.localeCompare(b.date));
  // Calendar: the S&P 500's sessions; without the benchmark, the union of the stocks' sessions (no relative figures then).
  const calendar = benchValid.length
    ? benchValid.map((b) => b.date)
    : [...new Set(stocks.flatMap((s) => s.bars.filter(valid).map((b) => b.date)))].sort();
  const end = calendar.length ? calendar[calendar.length - 1] : null;
  const baseline = end ? baselineSession(calendar, periodAnchor(end, period)) : null;
  const sessions = baseline && end ? calendar.filter((d) => d >= baseline && d <= end) : [];
  const benchClose = new Map(benchValid.map((b) => [b.date, b.close as number]));
  const bench = benchValid.length && baseline && benchClose.has(baseline)
    ? new Map(sessions.map((d) => [d, ((benchClose.get(d) as number) / (benchClose.get(baseline) as number) - 1) * 100]))
    : null;

  const series = stocks.map(({ symbol, bars, discontinuities = [] }): SeriesResult => {
    const empty: SeriesResult = { symbol, ret: null, vsBench: null, benchRet: null, start: null, end: null, flags: ['no_data'], points: new Map(), comparable: false };
    if (!baseline || !end) return empty;
    const close = new Map(bars.filter(valid).map((b) => [b.date, b.close as number]));
    const inWindow = sessions.filter((d) => close.has(d));
    if (inWindow.length === 0) return empty;
    const flags: Flag[] = [];
    const start = inWindow[0];
    if (start !== baseline) flags.push([...close.keys()].some((d) => d < baseline) ? 'missing_baseline' : 'late_start');
    const last = inWindow[inWindow.length - 1];
    if (last !== end) flags.push('stale');
    const disc = discontinuities.find((x) => x.date > start && x.date <= last);
    const base = close.get(start) as number;
    const points = new Map<string, number | null>(sessions.filter((d) => d >= start).map((d) => [d, close.has(d) ? ((close.get(d) as number) / base - 1) * 100 : null]));
    if (disc) {
      return { ...empty, flags: [...flags, 'discontinuity'], start, end: last, points, discontinuityDate: disc.date };
    }
    const ret = ((close.get(last) as number) / base - 1) * 100;
    const benchRet = benchClose.has(start) && benchClose.has(last) ? ((benchClose.get(last) as number) / (benchClose.get(start) as number) - 1) * 100 : null;
    return { symbol, ret, benchRet, vsBench: benchRet == null ? null : ret - benchRet, start, end: last, flags, points, comparable: flags.length === 0 };
  });

  return { period, baseline, end, sessions, bench, series };
}

/** One row per session for the chart: `{ date, SPX?, [symbol]: % }` (a series is absent before its own start). */
export function chartRows(r: PerformanceResult, symbols: string[]): Record<string, string | number | null>[] {
  const bySymbol = new Map(r.series.map((s) => [s.symbol, s]));
  return r.sessions.map((d) => {
    const row: Record<string, string | number | null> = { date: d };
    if (r.bench) row.SPX = r.bench.get(d) ?? null;
    for (const sym of symbols) {
      const s = bySymbol.get(sym);
      if (s && !s.flags.includes('discontinuity') && s.points.has(d)) row[sym] = s.points.get(d) ?? null;
    }
    return row;
  });
}

/** Ranked for the bars: reported returns high to low, then the ones without a return. */
export function ranked(series: SeriesResult[]): SeriesResult[] {
  return [...series].sort((a, b) => (a.ret == null ? 1 : 0) - (b.ret == null ? 1 : 0) || (b.ret ?? 0) - (a.ret ?? 0) || a.symbol.localeCompare(b.symbol));
}

/** Plain-language coverage note for a flagged series (null when it covers the full period). */
export function coverageNote(s: SeriesResult, baseline: string | null, end: string | null): string | null {
  if (s.flags.includes('no_data')) return 'no prices in this period';
  if (s.flags.includes('discontinuity')) return `a price discontinuity is recorded on ${s.discontinuityDate}; no return shown`;
  const parts: string[] = [];
  if (s.flags.includes('late_start')) parts.push(`history starts ${s.start}, after the period start ${baseline}`);
  if (s.flags.includes('missing_baseline')) parts.push(`no close on ${baseline}; measured from ${s.start}`);
  if (s.flags.includes('stale')) parts.push(`last close ${s.end}, before ${end}`);
  return parts.length ? parts.join('; ') : null;
}

/**
 * Equal-weight watchlist basket over the period: equal amounts bought at the baseline close of every COMPARABLE selected
 * stock (full period, no flag), held without rebalancing. Its return is therefore the plain average of the members'
 * returns, and its value on a session is the average of their rebased values (null when any member has no close then).
 * It is a reference figure for the list, not a portfolio: the watchlist holds no positions. Stocks without a comparable
 * full-period return are listed in `excluded`, never averaged in.
 */
export interface Basket {
  members: string[];
  excluded: string[];
  ret: number | null;
  benchRet: number | null;
  vsBench: number | null;
  points: Map<string, number | null>;
  /** members whose return beat the S&P 500 over the same window, out of the members with a relative figure */
  beat: number;
  beatOf: number;
}

export function equalWeightBasket(r: PerformanceResult, symbols: string[]): Basket {
  const all = symbols.map((s) => r.series.find((x) => x.symbol === s)).filter((s): s is SeriesResult => !!s);
  const members = all.filter((s) => s.comparable && s.ret != null);
  const excluded = symbols.filter((s) => !members.some((m) => m.symbol === s));
  const points = new Map<string, number | null>();
  if (members.length) {
    for (const d of r.sessions) {
      const vals = members.map((m) => m.points.get(d));
      points.set(d, vals.every((v) => v != null) ? (vals as number[]).reduce((a, b) => a + b, 0) / vals.length : null);
    }
  }
  const ret = members.length ? members.reduce((a, m) => a + (m.ret as number), 0) / members.length : null;
  const benchRet = r.bench && r.end ? r.bench.get(r.end) ?? null : null;
  const rel = members.filter((m) => m.vsBench != null);
  return {
    members: members.map((m) => m.symbol),
    excluded,
    ret,
    benchRet: ret == null ? null : benchRet,
    vsBench: ret != null && benchRet != null ? ret - benchRet : null,
    points,
    beat: rel.filter((m) => (m.vsBench as number) > 0).length,
    beatOf: rel.length,
  };
}

/** % move of a series on `date` versus the previous session of the window (null when either close is missing). */
export function sessionMove(r: PerformanceResult, symbol: string, date: string): number | null {
  const i = r.sessions.indexOf(date);
  if (i < 1) return null;
  const s = r.series.find((x) => x.symbol === symbol);
  if (!s || s.flags.includes('discontinuity')) return null;
  const cur = s.points.get(date);
  const prev = s.points.get(r.sessions[i - 1]);
  if (cur == null || prev == null) return null;
  return ((1 + cur / 100) / (1 + prev / 100) - 1) * 100;
}

/** The selected stock with the largest absolute move on `date` (null when no move can be computed). */
export function biggestMover(r: PerformanceResult, symbols: string[], date: string): { symbol: string; move: number } | null {
  let best: { symbol: string; move: number } | null = null;
  for (const s of symbols) {
    const m = sessionMove(r, s, date);
    if (m != null && (best == null || Math.abs(m) > Math.abs(best.move))) best = { symbol: s, move: m };
  }
  return best;
}

/** Count of saved symbols per CURRENT sector classification (not point-in-time), largest first; unknown last. */
export function sectorMix(symbols: string[], sectorOf: (s: string) => string | null | undefined): { sector: string; count: number; share: number }[] {
  const counts = new Map<string, number>();
  for (const s of symbols) {
    const k = sectorOf(s) || 'Unclassified';
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  const total = symbols.length || 1;
  return [...counts.entries()]
    .map(([sector, count]) => ({ sector, count, share: count / total }))
    .sort((a, b) => (a.sector === 'Unclassified' ? 1 : 0) - (b.sector === 'Unclassified' ? 1 : 0) || b.count - a.count || a.sector.localeCompare(b.sector));
}
