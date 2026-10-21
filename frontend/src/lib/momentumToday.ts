// File: frontend/src/lib/momentumToday.ts
// Momentum Today's boards: each is ONE Screener v2 request (the shared engine), plus the matching Screener link.
import type { QueryPairs, ScreenerRow } from '@/services/screenerV2Api';

/** The three boards, each ONE request to the Screener's shared engine (same session, same values as everywhere else). */
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
export type MomentumRow = ScreenerRow & { group: BoardKey };

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

