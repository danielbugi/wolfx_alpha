// Earnings calendar logic: New York session anchors (yesterday / today / tomorrow / this week), merging the two
// sources the dashboard can read today, ranking and filtering. Pure functions so every date edge (Monday, Friday,
// holidays, Israel after midnight) is testable with any "now".
//
// Sources (frontend-only for now -- there is no date-range earnings endpoint yet):
//   1. Momentum Board rows (/api/momentum-board): each row's NEXT report date from earnings_calendar.
//   2. Per-symbol history (/api/stock/{symbol}/earnings): past + upcoming dates with EPS, used for watchlist names
//      and, lazily, for the rows on screen.
// So coverage is "Momentum Board names + your watchlist", not the whole market -- the UI says so.

import type { EarningsDateEntry, MomentumBoardResult } from '@/services/api';
import { zoneParts, type MarketCalendar } from '@/lib/marketSession';

const NY = 'America/New_York';

// ---------------------------------------------------------------------------------------------------------------
// Dates (ISO yyyy-mm-dd, calendar arithmetic at UTC noon so DST never shifts a day)

export function nyDateOf(now: Date): string {
  return zoneParts(now, NY).isoDate;
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** 0 = Sunday ... 6 = Saturday */
export function weekdayIndex(iso: string): number {
  return new Date(`${iso}T12:00:00Z`).getUTCDay();
}

export function mondayOf(iso: string): string {
  const w = weekdayIndex(iso);
  return addDays(iso, w === 0 ? -6 : 1 - w);
}

/** The Monday of the trading week to show as "this week": on a weekend that is the coming week. */
export function currentWeekStart(nyToday: string): string {
  const w = weekdayIndex(nyToday);
  if (w === 6) return addDays(nyToday, 2);
  if (w === 0) return addDays(nyToday, 1);
  return mondayOf(nyToday);
}

export function weekDays(monday: string): string[] {
  return [0, 1, 2, 3, 4].map((i) => addDays(monday, i));
}

/** "Wed Oct 15" */
export function formatDay(iso: string): string {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
}

/** "Oct 12 – 16, 2026" / "Sep 28 – Oct 2, 2026" */
export function formatWeekRange(monday: string): string {
  const friday = addDays(monday, 4);
  const md = (iso: string, withMonth: boolean) =>
    new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US', withMonth ? { month: 'short', day: 'numeric', timeZone: 'UTC' } : { day: 'numeric', timeZone: 'UTC' });
  const sameMonth = monday.slice(0, 7) === friday.slice(0, 7);
  return `${md(monday, true)} – ${md(friday, !sameMonth)}, ${friday.slice(0, 4)}`;
}

// ---------------------------------------------------------------------------------------------------------------
// Sessions

export interface SessionInfo {
  session: boolean;
  /** true when the official calendar covers this date (holidays known); false = weekday assumption */
  verified: boolean;
}

/** Is `iso` a US trading session? Uses the official calendar inside its window, a Mon-Fri assumption outside it. */
export function sessionChecker(cal: MarketCalendar | null): (iso: string) => SessionInfo {
  const dates = cal && cal.holidays_known ? cal.sessions.map((s) => s.date).sort() : [];
  const set = new Set(dates);
  const lo = dates[0];
  const hi = dates[dates.length - 1];
  return (iso: string) => {
    if (dates.length > 0 && iso >= lo && iso <= hi) return { session: set.has(iso), verified: true };
    const w = weekdayIndex(iso);
    return { session: w >= 1 && w <= 5, verified: false };
  };
}

export interface SessionAnchors {
  nyToday: string;
  todayIsSession: boolean;
  /** previous session before today (Monday -> Friday) */
  yesterday: string | null;
  /** next session after today (Friday -> Monday, holidays skipped) */
  tomorrow: string | null;
  weekStart: string;
  weekDays: string[];
  /** false when any anchor rests on the weekday assumption instead of the official calendar */
  verified: boolean;
}

export function sessionAnchors(nyToday: string, cal: MarketCalendar | null): SessionAnchors {
  const check = sessionChecker(cal);
  const step = (dir: 1 | -1): { date: string | null; verified: boolean } => {
    let d = nyToday;
    let verified = true;
    for (let i = 0; i < 10; i++) {
      d = addDays(d, dir);
      const info = check(d);
      verified &&= info.verified;
      if (info.session) return { date: d, verified };
    }
    return { date: null, verified: false };
  };
  const today = check(nyToday);
  const prev = step(-1);
  const next = step(1);
  const weekStart = currentWeekStart(nyToday);
  return {
    nyToday,
    todayIsSession: today.session,
    yesterday: prev.date,
    tomorrow: next.date,
    weekStart,
    weekDays: weekDays(weekStart),
    verified: today.verified && prev.verified && next.verified,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Rows

export type SignalCategory = 'breakout' | 'near_breakout';

export interface EarningsRow {
  symbol: string;
  date: string;
  sector: string | null;
  close: number | null;
  marketCap: number | null;
  /** on today's Momentum Board (breakout / near breakout), null when not on the board */
  signal: SignalCategory | null;
  /** confirmed by 2+ Momentum Board lists */
  starred: boolean;
  onWatchlist: boolean;
  epsEstimate: number | null;
  epsActual: number | null;
  surprisePct: number | null;
  /** beats out of judged reports, over the last 8 reports before this one; null until history is loaded */
  beats: number | null;
  judged: number | null;
}

export type HistoryState = EarningsDateEntry[] | 'error' | undefined;

/**
 * One row per (symbol, report date) in [from, to]. A loaded per-symbol history is authoritative for its symbol (live
 * and complete); otherwise the board's next report date is used. Board metadata (sector, close, signal) is attached
 * whenever the symbol is on the board.
 */
export function buildRows({
  board,
  watchlist,
  histories,
  from,
  to,
}: {
  board: MomentumBoardResult[];
  watchlist: string[];
  histories: Record<string, HistoryState>;
  from: string;
  to: string;
}): EarningsRow[] {
  const meta = new Map(board.map((r) => [r.symbol, r]));
  const wl = new Set(watchlist);
  const out = new Map<string, EarningsRow>();
  const base = (symbol: string, date: string): EarningsRow => {
    const b = meta.get(symbol);
    return {
      symbol,
      date,
      sector: b?.sector ?? null,
      close: b?.close ?? null,
      marketCap: b?.market_cap ?? null,
      signal: b?.category ?? null,
      starred: b?.starred ?? false,
      onWatchlist: wl.has(symbol),
      epsEstimate: null,
      epsActual: null,
      surprisePct: null,
      beats: null,
      judged: null,
    };
  };

  const fromHistory = new Set<string>();
  for (const symbol of watchlist) {
    const h = histories[symbol];
    if (!Array.isArray(h)) continue;
    fromHistory.add(symbol);
    for (const e of h) if (e.date >= from && e.date <= to) out.set(`${symbol}|${e.date}`, base(symbol, e.date));
  }
  for (const r of board) {
    const d = r.next_earnings_date;
    if (!d || fromHistory.has(r.symbol) || d < from || d > to) continue;
    out.set(`${r.symbol}|${d}`, base(r.symbol, d));
  }
  return [...out.values()].map((row) => enrich(row, histories[row.symbol]));
}

/** Fill EPS fields and the beat record from a symbol's history (no-op until it has loaded). */
export function enrich(row: EarningsRow, history: HistoryState): EarningsRow {
  if (!Array.isArray(history)) return row;
  const own = history.find((e) => e.date === row.date);
  const judged = history
    .filter((e) => e.date < row.date && e.eps_actual != null && e.eps_estimate != null)
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, 8);
  return {
    ...row,
    epsEstimate: own?.eps_estimate ?? null,
    epsActual: own?.eps_actual ?? null,
    surprisePct: own?.surprise_pct ?? null,
    beats: judged.length ? judged.filter((e) => (e.eps_actual as number) > (e.eps_estimate as number)).length : null,
    judged: judged.length || null,
  };
}

export type ReportResult = 'beat' | 'miss' | 'inline' | null;

export function reportResult(row: Pick<EarningsRow, 'epsActual' | 'epsEstimate'>): ReportResult {
  if (row.epsActual == null || row.epsEstimate == null) return null;
  if (row.epsActual > row.epsEstimate) return 'beat';
  if (row.epsActual < row.epsEstimate) return 'miss';
  return 'inline';
}

/** Your names first, then confirmed board names, breakouts, near breakouts, then by size. */
export function rankRows(rows: EarningsRow[]): EarningsRow[] {
  const tier = (r: EarningsRow) =>
    (r.onWatchlist ? 0 : 10) + (r.starred ? 0 : 4) + (r.signal === 'breakout' ? 0 : r.signal === 'near_breakout' ? 1 : 2);
  return [...rows].sort(
    (a, b) =>
      a.date.localeCompare(b.date) ||
      tier(a) - tier(b) ||
      (b.marketCap ?? -1) - (a.marketCap ?? -1) ||
      a.symbol.localeCompare(b.symbol),
  );
}

export interface EarningsFilters {
  query: string;
  sector: string | null;
  watchlistOnly: boolean;
  signalOnly: boolean;
}

export function filterRows(rows: EarningsRow[], f: EarningsFilters): EarningsRow[] {
  const q = f.query.trim().toUpperCase();
  return rows.filter(
    (r) =>
      (!q || r.symbol.includes(q)) &&
      (!f.sector || r.sector === f.sector) &&
      (!f.watchlistOnly || r.onWatchlist) &&
      (!f.signalOnly || r.signal != null),
  );
}

export function countByDate(rows: EarningsRow[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const r of rows) counts[r.date] = (counts[r.date] ?? 0) + 1;
  return counts;
}
