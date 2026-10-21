// File: frontend/src/lib/momentumToday.ts
// Momentum Today: two rankings of the session from the Screener's shared engine (paged until complete).
//   Movers   — what moved hardest today: top 5 in two or more of the session's lists first, then the biggest 1-day gain
//              (the same rule as the Telegram digest).
//   Strength — strength and trend health: the Strength Score, highest first.
// Pure helpers only (no I/O): the boards, the two rankings, the filters and the "why it's moving" label.
import type { QueryPairs, ScreenerRow } from '@/services/screenerV2Api';

/** The three boards, each from the Screener's shared engine (same session, same values as everywhere else). */
export const BOARDS: { key: 'breakouts' | 'new_highs' | 'strongest'; title: string; blurb: string; pairs: QueryPairs; screener: string }[] = [
  {
    key: 'breakouts',
    title: 'Broke out today',
    blurb: 'Closed above its previous 20-day high on the session.',
    pairs: [['breakout_20', 'true'], ['sort', 'change_1d'], ['order', 'desc'], ['page', '1'], ['page_size', '200']],
    screener: '/screener?breakout_20=true&sort=change_1d&order=desc',
  },
  {
    key: 'new_highs',
    title: 'New 20-day highs',
    blurb: 'Traded at a new 20-day high on the session without closing above the previous one.',
    pairs: [['new_high_20', 'true'], ['breakout_20', 'false'], ['sort', 'change_1d'], ['order', 'desc'], ['page', '1'], ['page_size', '200']],
    screener: '/screener?new_high_20=true&sort=change_1d&order=desc',
  },
  {
    key: 'strongest',
    title: 'Strength 80+',
    blurb: 'Strength Score of 80 or more (relative strength, trend, momentum, volume, liquidity).',
    pairs: [['min_strength_score', '80'], ['sort', 'strength_score'], ['order', 'desc'], ['page', '1'], ['page_size', '200']],
    screener: '/screener?preset=strongest&view=cards',
  },
];

export type BoardKey = (typeof BOARDS)[number]['key'];
/** `lists` = only on the session's lists (not a breakout or new high). */
export type Group = BoardKey | 'lists';
export type MomentumRow = ScreenerRow & {
  group: Group;
  /** Rank in each of the session's lists the stock made (gainers / atr / volume); empty when it made none. */
  list_ranks?: Record<string, number>;
  /** In the top 5 of two or more of those lists (the shared rule, as sent by the backend). */
  top_two?: boolean;
  /** 1-based position in the view's ranking. */
  rank?: number;
};

export type View = 'movers' | 'strength';
export const VIEWS: { key: View; label: string; title: string; blurb: string }[] = [
  {
    key: 'movers',
    label: "Today's Movers",
    title: 'What moved hardest today',
    blurb: 'Breakouts, new highs and the session’s biggest movers. Top 5 in two or more lists (gainers, range vs ATR, volume surge) first, then the biggest 1-day gain.',
  },
  {
    key: 'strength',
    label: 'Strength Leaders',
    title: 'Strongest trends',
    blurb: 'Stocks with a Strength Score of 80 or more, highest first: relative strength, trend health, momentum, volume and liquidity.',
  },
];

/** The session's lists, in display order, with the names used in list_ranks. */
export const DAY_LISTS: { key: string; label: string }[] = [
  { key: 'gainers', label: 'Gain' },
  { key: 'atr', label: 'Range' },
  { key: 'volume', label: 'Vol' },
];

/** One row per symbol across the lists, tagged with the first list it appears in. */
export function unionRows(lists: Partial<Record<Group, ScreenerRow[]>>, order: Group[] = ['breakouts', 'new_highs', 'lists', 'strongest']): MomentumRow[] {
  const seen = new Set<string>();
  const out: MomentumRow[] = [];
  for (const k of order) {
    for (const r of lists[k] ?? []) {
      if (seen.has(r.symbol)) continue;
      seen.add(r.symbol);
      out.push({ ...r, group: k });
    }
  }
  return out;
}

const withRank = <T,>(rows: T[]) => rows.map((r, i) => ({ ...r, rank: i + 1 }));
const num = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? Number.NEGATIVE_INFINITY : v);

/** Movers ranking: top 5 in two or more of the session's lists first, then the biggest 1-day gain (no change last). Stable. */
export function rankRows(rows: MomentumRow[]): MomentumRow[] {
  const keyed = rows.map((r, i) => ({ r, i }));
  keyed.sort((a, b) => Number(!!b.r.top_two) - Number(!!a.r.top_two) || num(b.r.change_1d) - num(a.r.change_1d) || a.i - b.i);
  return withRank(keyed.map(({ r }) => r));
}

/** Strength ranking: Strength Score, highest first (withheld scores last), then the 1-day gain. Stable. */
export function rankByStrength(rows: MomentumRow[]): MomentumRow[] {
  const keyed = rows.map((r, i) => ({ r, i }));
  keyed.sort((a, b) => num(b.r.strength_score) - num(a.r.strength_score) || num(b.r.change_1d) - num(a.r.change_1d) || a.i - b.i);
  return withRank(keyed.map(({ r }) => r));
}

// ------------------------------------------------------------------------------------------------------------- filters
export type Liquidity = 'any' | '5m' | '20m';
export type Size = 'any' | 'small' | 'mid' | 'large';
export type Quality = 'any' | 'C' | 'B' | 'A';
export interface Filters {
  liquidity: Liquidity;
  size: Size;
  quality: Quality;
  /** Hide stocks with an earnings report in the next EARNINGS_SOON_DAYS days. */
  hideEarningsSoon: boolean;
}
export const DEFAULT_FILTERS: Filters = { liquidity: 'any', size: 'any', quality: 'any', hideEarningsSoon: false };
export const EARNINGS_SOON_DAYS = 2;

export const LIQUIDITY_OPTIONS: { value: Liquidity; label: string; min: number }[] = [
  { value: 'any', label: 'Any', min: 0 },
  { value: '5m', label: '$5M+/day', min: 5e6 },
  { value: '20m', label: '$20M+/day', min: 2e7 },
];
export const SIZE_OPTIONS: { value: Size; label: string; lo: number; hi: number }[] = [
  { value: 'any', label: 'Any', lo: 0, hi: Infinity },
  { value: 'small', label: 'Small <$2B', lo: 0, hi: 2e9 },
  { value: 'mid', label: 'Mid $2–10B', lo: 2e9, hi: 1e10 },
  { value: 'large', label: 'Large $10B+', lo: 1e10, hi: Infinity },
];
export const QUALITY_OPTIONS: { value: Quality; label: string }[] = [
  { value: 'any', label: 'Any' },
  { value: 'C', label: 'C+' },
  { value: 'B', label: 'B+' },
  { value: 'A', label: 'A' },
];
const GRADE_RANK: Record<string, number> = { A: 4, B: 3, C: 2, D: 1, F: 0 };

/** Client-side filters over rows already fetched. A filter that is set excludes rows whose value is missing. */
export function applyFilters<T extends ScreenerRow>(rows: T[], f: Filters): T[] {
  const liq = LIQUIDITY_OPTIONS.find((o) => o.value === f.liquidity)!;
  const size = SIZE_OPTIONS.find((o) => o.value === f.size)!;
  return rows.filter((r) => {
    if (f.liquidity !== 'any' && (r.dollar_vol_20 == null || r.dollar_vol_20 < liq.min)) return false;
    if (f.size !== 'any' && (r.market_cap == null || r.market_cap < size.lo || r.market_cap >= size.hi)) return false;
    if (f.quality !== 'any' && (r.quality_grade == null || (GRADE_RANK[r.quality_grade] ?? -1) < GRADE_RANK[f.quality])) return false;
    if (f.hideEarningsSoon && r.days_to_earnings != null && r.days_to_earnings <= EARNINGS_SOON_DAYS) return false;
    return true;
  });
}

export const filtersActive = (f: Filters) =>
  f.liquidity !== DEFAULT_FILTERS.liquidity || f.size !== DEFAULT_FILTERS.size || f.quality !== DEFAULT_FILTERS.quality || f.hideEarningsSoon;

// ---------------------------------------------------------------------------------------------------- why it's moving
export const RECENT_REPORT_DAYS = 3;
export const UPCOMING_REPORT_DAYS = 14;
export type CatalystTone = 'reported' | 'soon' | 'upcoming' | 'none';

/**
 * What the stored data says about a catalyst. Only earnings are known for every stock (news is fetched per stock page),
 * so "no report" means no earnings report in the window, not that nothing happened.
 */
export function catalyst(r: Pick<ScreenerRow, 'days_since_earnings' | 'days_to_earnings' | 'earnings_surprise_pct' | 'last_earnings_date' | 'next_earnings_date'>): {
  tone: CatalystTone;
  label: string;
  title: string;
} {
  const since = r.days_since_earnings;
  if (since != null && since <= RECENT_REPORT_DAYS) {
    const s = r.earnings_surprise_pct;
    const surprise = s != null && Number.isFinite(s) && Math.abs(s) < 1000 ? ` · EPS ${s >= 0 ? '+' : ''}${s.toFixed(0)}% vs est.` : '';
    return {
      tone: 'reported',
      label: `${since === 0 ? 'Earnings on the session' : `Earnings ${since}d ago`}${surprise}`,
      title: `Reported ${r.last_earnings_date ?? ''} (time of day not stored)`,
    };
  }
  const to = r.days_to_earnings;
  if (to != null && to <= UPCOMING_REPORT_DAYS) {
    return {
      tone: to <= EARNINGS_SOON_DAYS ? 'soon' : 'upcoming',
      label: `Earnings in ${to}d`,
      title: `Next report ${r.next_earnings_date ?? ''} (dates can move)`,
    };
  }
  return { tone: 'none', label: 'No earnings report', title: `No earnings report in the last ${RECENT_REPORT_DAYS} or next ${UPCOMING_REPORT_DAYS} days` };
}
