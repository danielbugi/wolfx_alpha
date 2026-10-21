// File: frontend/src/hooks/useStockContext.ts
// The Screener v2 row for one symbol (GET /api/stock/{symbol}/context), loaded once per stock page and shared by the
// header, the Strength card and the Stock context panel. `symbol = null` loads nothing.
import { useEffect, useState } from 'react';
import { stockContextApi, StockContext } from '@/services/stockContextApi';

export interface StockContextState {
  ctx: StockContext | null;
  error: string | null;
}

export function useStockContext(symbol: string | null): StockContextState {
  const [state, setState] = useState<StockContextState>({ ctx: null, error: null });
  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    setState({ ctx: null, error: null });
    stockContextApi
      .get(symbol)
      .then((c) => !cancelled && setState({ ctx: c, error: null }))
      .catch((e) => {
        if (cancelled) return;
        setState({
          ctx: null,
          error: e?.response?.status === 404 ? 'Stock context is not available on this server yet.' : 'Stock context is unavailable right now.',
        });
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);
  return state;
}
