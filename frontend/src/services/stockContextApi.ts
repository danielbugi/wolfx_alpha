// File: frontend/src/services/stockContextApi.ts
// Client for GET /api/stock/{symbol}/context: the Smart Screener v2 row for one symbol (same query, same session, same
// provenance) plus the stock-only 200-session average.
import { apiClient } from '@/services/api';
import type { FundamentalsCutoff, ScreenerCoverage, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';

export interface Ma200Context {
  key: 'dist_sma200';
  label: string;
  unit: string;
  family: 'derived';
  definition: string;
  sessions_required: number;
  value: number | null;
  sma200: number | null;
  sessions_with_close: number | null;
  window_from: string | null;
  window_to: string;
  state: 'ok' | 'unavailable';
  reason: string | null;
}

export interface StockContext {
  symbol: string;
  in_universe: boolean;
  unavailable_reason: string | null;
  session: string;
  previous_session: string | null;
  newest_price_date: string | null;
  dollar_volume_window: ScreenerResult['dollar_volume_window'];
  fundamentals_basis: ScreenerResult['fundamentals_basis'];
  fundamentals_cutoff_utc: string;
  universe: number;
  coverage: ScreenerCoverage;
  recent_sessions: string[];
  row: ScreenerRow | null;
  stock_only: { dist_sma200: Ma200Context | null };
}

export const stockContextApi = {
  async get(symbol: string, opts: { session?: string | null; fundamentalsCutoff?: FundamentalsCutoff } = {}): Promise<StockContext> {
    const params = new URLSearchParams();
    if (opts.session) params.append('session', opts.session);
    if (opts.fundamentalsCutoff && opts.fundamentalsCutoff !== 'post_session') params.append('fundamentals_cutoff', opts.fundamentalsCutoff);
    return (await apiClient.get<StockContext>(`/api/stock/${encodeURIComponent(symbol)}/context`, { params })).data;
  },
};
