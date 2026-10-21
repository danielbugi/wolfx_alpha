// File: frontend/src/lib/momentumToday.ts
// Momentum Today's boards: each is the Screener v2 shared engine (paged until complete), plus the matching Screener link.
import type { QueryPairs, ScreenerRow } from '@/services/screenerV2Api';

/** The three boards, each from the Screener's shared engine (same session, same values as everywhere else). */
export const BOARDS: { key: 'breakouts' | 'new_highs' | 'strongest'; title: string; blurb: string; pairs: QueryPairs; screener: string }[] = [
  {
    key: 'breakouts',
    title: 'Broke out today',
    blurb: 'Closed above its previous 20-day high on the session.',
    pairs: [['breakout_20', 'true'], ['sort', 'strength_score'], ['order', 'desc'], ['page', '1'], ['page_size', '200']],
    screener: '/screener?breakout_20=true&sort=strength_score&order=desc&view=cards',
  },
  {
    key: 'new_highs',
    title: 'New 20-day highs',
    blurb: 'Traded at a new 20-day high on the session without closing above the previous one.',
    pairs: [['new_high_20', 'true'], ['breakout_20', 'false'], ['sort', 'strength_score'], ['order', 'desc'], ['page', '1'], ['page_size', '200']],
    screener: '/screener?new_high_20=true&sort=strength_score&order=desc&view=cards',
  },
  {
    key: 'strongest',
    title: 'Strongest today',
    blurb: 'Strength Score of 80 or more (relative strength, trend, momentum, volume, liquidity).',
    pairs: [['min_strength_score', '80'], ['sort', 'strength_score'], ['order', 'desc'], ['page', '1'], ['page_size', '200']],
    screener: '/screener?preset=strongest&view=cards',
  },
];

export type BoardKey = (typeof BOARDS)[number]['key'];
export type MomentumRow = ScreenerRow & {
  group: BoardKey;
  /** Rank in each of the session's lists the stock made (gainers / atr / volume); empty when it made none. */
  list_ranks?: Record<string, number>;
  /** In the top 5 of two or more of those lists (the shared rule, as sent by the backend). */
  top_two?: boolean;
  /** 1-based position in the default ranking below. */
  rank?: number;
};

/** The session's lists, in display order, with the names used in list_ranks. */
export const DAY_LISTS: { key: string; label: string }[] = [
  { key: 'gainers', label: 'Gainers' },
  { key: 'atr', label: 'Range' },
  { key: 'volume', label: 'Volume' },
];

/**
 * The default ranking, the same rule the Telegram digest and the internal board use: stocks in the top 5 of two or more of
 * the session's lists first, then the biggest 1-day gain (no change last); ties keep their incoming order. Returns new rows
 * with `rank` set.
 */
export function rankRows(rows: MomentumRow[]): MomentumRow[] {
  const keyed = rows.map((r, i) => ({ r, i }));
  keyed.sort((a, b) => {
    const t = Number(!!b.r.top_two) - Number(!!a.r.top_two);
    if (t) return t;
    const x = a.r.change_1d ?? Number.NEGATIVE_INFINITY;
    const y = b.r.change_1d ?? Number.NEGATIVE_INFINITY;
    return y !== x ? (y > x ? 1 : -1) : a.i - b.i;
  });
  return keyed.map(({ r }, i) => ({ ...r, rank: i + 1 }));
}

/** One row per symbol across the lists, tagged with the first list it appears in (breakouts, then new highs, then strongest). */
export function unionRows(lists: Partial<Record<BoardKey, ScreenerRow[]>>): MomentumRow[] {
  const seen = new Set<string>();
  const out: MomentumRow[] = [];
  for (const k of ['breakouts', 'new_highs', 'strongest'] as BoardKey[]) {
    for (const r of lists[k] ?? []) {
      if (seen.has(r.symbol)) continue;
      seen.add(r.symbol);
      out.push({ ...r, group: k });
    }
  }
  return out;
}

