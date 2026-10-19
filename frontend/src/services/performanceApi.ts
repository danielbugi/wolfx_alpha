// File: frontend/src/services/performanceApi.ts
// Price histories for the watchlist performance chart: only the SELECTED symbols plus the S&P 500, each fetched once and
// cached for CACHE_MS (in-flight requests are shared), long enough for every period (1Y and YTD) so switching the period
// never refetches.
import { apiClient } from '@/services/api';
import type { Bar, Discontinuity } from '@/lib/performance';

/** Most recent bars per stock: about 14 months of sessions, enough for 1Y and YTD. */
export const STOCK_BARS = 300;
/** Calendar days of S&P 500 history (the instrument endpoint counts calendar days). */
export const BENCH_DAYS = 430;
export const BENCHMARK_SYMBOL = '^GSPC';
const CACHE_MS = 10 * 60 * 1000;

export interface StockHistory {
  symbol: string;
  bars: Bar[];
  discontinuities: Discontinuity[];
  /** false when the server did not send the discontinuity list (an older backend): the page says it was not checked */
  discontinuitiesChecked: boolean;
}

const cache = new Map<string, { at: number; p: Promise<unknown> }>();

function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.p as Promise<T>;
  const p = load().catch((e) => {
    cache.delete(key); // a failure is not cached: the next view retries
    throw e;
  });
  cache.set(key, { at: Date.now(), p });
  return p;
}

export const performanceApi = {
  stock(symbol: string): Promise<StockHistory> {
    return cached(`s:${symbol}`, async () => {
      const r = await apiClient.get<{ bars: { date: string; close: number | null }[]; discontinuities?: Discontinuity[] }>(
        `/api/stock/${encodeURIComponent(symbol)}/price-history`,
        { params: { days: STOCK_BARS } },
      );
      return {
        symbol,
        bars: r.data.bars.map((b) => ({ date: b.date, close: b.close })),
        discontinuities: r.data.discontinuities ?? [],
        discontinuitiesChecked: Array.isArray(r.data.discontinuities),
      };
    });
  },
  benchmark(): Promise<Bar[]> {
    return cached('b', async () => {
      const r = await apiClient.get<{ bars: { date: string; close: number | null }[] }>(
        `/api/market/instruments/${encodeURIComponent(BENCHMARK_SYMBOL)}/history`,
        { params: { days: BENCH_DAYS } },
      );
      return r.data.bars.map((b) => ({ date: b.date, close: b.close }));
    });
  },
  /** test helper */
  _clear() {
    cache.clear();
  },
};
