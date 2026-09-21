'use client';

import { useEffect, useState } from 'react';
import { stockApi, PriceBar, SignalSummary, EarningsDateEntry } from '@/services/api';

/**
 * `null`/`undefined` mean "not loaded yet" (vs. an empty/failed result), so the
 * card can render each piece the moment it arrives instead of waiting for the
 * slowest one:
 *   bars     null      = loading;  []   = loaded, no history
 *   signal   undefined = loading;  null = loaded, none / lookup failed
 *   earnings []        = nothing yet (markers are optional, they just pop in)
 */
export interface HoverStockSummary {
  bars: PriceBar[] | null;
  signal: SignalSummary | null | undefined;
  earnings: EarningsDateEntry[];
}

/**
 * off      -> no fetching
 * prefetch -> cursor has lingered on the symbol: fetch bars + signal (cheap,
 *             DB / in-memory) so they're usually ready by the time the popover
 *             opens
 * open     -> popover is open: also fetch earnings, the slow yfinance-backed
 *             call, only now so a mouse sweeping over rows never triggers it
 */
export type HoverStage = 'off' | 'prefetch' | 'open';

const HOVER_CACHE_TTL_MS = 60_000;

type Kind = 'bars' | 'signal' | 'earnings';
const cache = new Map<string, { value: unknown; ts: number }>();
const inflight = new Map<string, Promise<unknown>>();

const key = (kind: Kind, symbol: string) => `${kind}:${symbol}`;

function peek<T>(kind: Kind, symbol: string): T | undefined {
  const hit = cache.get(key(kind, symbol));
  return hit && Date.now() - hit.ts < HOVER_CACHE_TTL_MS ? (hit.value as T) : undefined;
}

/**
 * One request per (kind, symbol) at a time -- the prefetch stage and the open
 * stage can both ask for the same piece without a second network call. Failed
 * lookups resolve to `fallback` and are NOT cached, so a transient error
 * doesn't stick for a minute.
 */
function load<T>(kind: Kind, symbol: string, fetcher: () => Promise<T>, fallback: T): Promise<T> {
  const k = key(kind, symbol);
  const existing = inflight.get(k);
  if (existing) return existing as Promise<T>;

  const p = fetcher()
    .then((value) => {
      cache.set(k, { value, ts: Date.now() });
      return value;
    })
    .catch(() => fallback)
    .finally(() => inflight.delete(k));
  inflight.set(k, p);
  return p;
}

function initialState(symbol: string): HoverStockSummary {
  return {
    bars: peek<PriceBar[]>('bars', symbol) ?? null,
    signal: peek<SignalSummary | null>('signal', symbol),
    earnings: peek<EarningsDateEntry[]>('earnings', symbol) ?? [],
  };
}

/**
 * Fetches the small bundle of data a dashboard hover popover needs (mini-chart
 * bars + AI signal + earnings markers) in stages, publishing each piece as it
 * lands. A short module-level cache means re-hovering the same symbol shortly
 * after (common while scanning a table) costs no network calls -- the frontend
 * half of the anti-hammering strategy, alongside the backend's own per-symbol
 * caches for /signal and /earnings.
 */
export function useHoverStockSummary(symbol: string, stage: HoverStage): HoverStockSummary {
  const [state, setState] = useState<HoverStockSummary>(() => initialState(symbol));

  useEffect(() => {
    if (stage === 'off') return;

    let cancelled = false;
    const merge = (patch: Partial<HoverStockSummary>) => {
      if (!cancelled) setState((s) => ({ ...s, ...patch }));
    };

    // Seed from cache synchronously so a re-hover paints immediately.
    setState(initialState(symbol));

    load('bars', symbol, async () => (await stockApi.getPriceHistory(symbol, 90)).bars, [] as PriceBar[]).then(
      (bars) => merge({ bars })
    );
    load('signal', symbol, () => stockApi.getSignal(symbol), null as SignalSummary | null).then(
      (signal) => merge({ signal })
    );
    if (stage === 'open') {
      load(
        'earnings',
        symbol,
        async () => (await stockApi.getEarnings(symbol)).earnings_dates,
        [] as EarningsDateEntry[]
      ).then((earnings) => merge({ earnings }));
    }

    return () => {
      cancelled = true;
    };
  }, [symbol, stage]);

  return state;
}
