// File: frontend/src/components/watchlist/SymbolChart.tsx
'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import StockChart from '@/components/charts/StockChart';
import { stockApi, EarningsDateEntry, PriceBar } from '@/services/api';
import type { ScreenerRow } from '@/services/screenerV2Api';
import { formatValue } from '@/lib/screenerV2';
import { sectorHref } from '@/lib/sectors';

/** Same history the stock page loads, so the chart (weekly/monthly candles, 200-day average) behaves identically. */
export const CHART_DAYS = 730;
const EARNINGS_LIMIT = 16;
const CACHE_MS = 10 * 60 * 1000;

type Loaded = { bars: PriceBar[]; earnings: EarningsDateEntry[] };
const cache = new Map<string, { at: number; p: Promise<Loaded> }>();

/** Bars + earnings for one symbol, cached and shared while in flight: re-selecting a symbol refetches nothing. */
export function loadChart(symbol: string): Promise<Loaded> {
  const hit = cache.get(symbol);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.p;
  const p = Promise.all([
    stockApi.getPriceHistory(symbol, CHART_DAYS).then((r) => r.bars),
    // earnings markers are optional: a failure leaves the chart without them
    stockApi.getEarnings(symbol, EARNINGS_LIMIT).then((r) => r.earnings_dates).catch(() => [] as EarningsDateEntry[]),
  ])
    .then(([bars, earnings]) => ({ bars, earnings }))
    .catch((e) => {
      cache.delete(symbol);
      throw e;
    });
  cache.set(symbol, { at: Date.now(), p });
  return p;
}
export const _clearChartCache = () => cache.clear();

/**
 * The left pane's Chart tab: the selected watchlist symbol on the same TradingView (lightweight-charts) chart the stock
 * page uses -- candles, daily/weekly/monthly, moving averages, channel, volume, RSI/MACD, earnings markers. No trade plan
 * and no signal markers (product view).
 */
export default function SymbolChart({ symbol, row }: { symbol: string; row?: ScreenerRow }) {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(false);
    loadChart(symbol)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const last = data?.bars[data.bars.length - 1];
  const chg = row?.change_1d ?? null;
  return (
    <section className="rounded-2xl border border-black/[0.05] bg-white shadow-sm" aria-label={`${symbol} chart`} data-testid="symbol-chart">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 pt-4 sm:px-5">
        <div className="min-w-0">
          <h2 className="text-[22px] font-bold leading-tight text-slate-900">{symbol}</h2>
          <p className="truncate text-[12px] text-slate-500">
            {row?.sector && sectorHref(row.sector) ? (
              <Link href={sectorHref(row.sector)!} className="hover:text-accent hover:underline">
                {row.sector}
              </Link>
            ) : (
              row?.sector ?? ''
            )}
            {row?.industry ? ` · ${row.industry}` : ''}
          </p>
        </div>
        <div className="flex items-baseline gap-3">
          {last && <span className="text-[20px] font-semibold tabular-nums text-slate-900">{formatValue(last.close, '$')}</span>}
          {chg != null && <span className={clsx('text-[14px] font-semibold tabular-nums', chg >= 0 ? 'text-emerald-600' : 'text-red-600')}>{formatValue(chg, '%')}</span>}
          <Link href={`/stock/${symbol}`} className="whitespace-nowrap text-[13px] font-medium text-accent hover:underline">
            Stock page →
          </Link>
        </div>
      </div>
      {last && <p className="px-4 text-[11px] text-slate-400 sm:px-5">Last close {last.date} · adjusted daily closes</p>}
      <div className="mt-2 px-1 pb-2 sm:px-2">
        {error ? (
          <p className="py-24 text-center text-sm text-slate-500" data-testid="chart-error">
            The price history for {symbol} is unavailable right now.
          </p>
        ) : !data ? (
          <div className="h-[420px] animate-pulse rounded-xl bg-black/[0.03]" aria-busy="true" />
        ) : data.bars.length === 0 ? (
          <p className="py-24 text-center text-sm text-slate-500">No price history stored for {symbol}.</p>
        ) : (
          <StockChart bars={data.bars} earnings={data.earnings} plan={null} height={420} />
        )}
      </div>
    </section>
  );
}
