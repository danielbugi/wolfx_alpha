// File: frontend/src/services/dayListsApi.ts
// The session's three fact lists (biggest 1-day gainers, biggest range expansion vs ATR, biggest volume surge) and each
// stock's rank in them, as stored for the session by the daily digest. Same endpoint, same rows and same "top 5 of 2+
// lists" rule (mechanism/alerts/star.py) as the internal board page and the channel; this wrapper only narrows the shape.
import { momentumBoardApi } from '@/services/api';

export interface DayListEntry {
  list_ranks: Record<string, number>;
  /** In the top 5 of two or more lists (computed by the backend with the shared rule, never here). */
  top_two: boolean;
}

export interface DayLists {
  session: string | null;
  bySymbol: Map<string, DayListEntry>;
}

export async function getDayLists(): Promise<DayLists> {
  const r = await momentumBoardApi.getBoard({ category: 'all', limit: 500 });
  const bySymbol = new Map<string, DayListEntry>();
  for (const row of r.results) bySymbol.set(row.symbol, { list_ranks: row.list_ranks ?? {}, top_two: row.starred });
  return { session: r.session_date, bySymbol };
}
