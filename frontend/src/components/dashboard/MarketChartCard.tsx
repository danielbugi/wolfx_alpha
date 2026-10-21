// File: frontend/src/components/dashboard/MarketChartCard.tsx
'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import StockChart from '@/components/charts/StockChart';
import SegmentedControl from '@/components/ui/SegmentedControl';
import { apiClient } from '@/services/api';
import { InstrumentHistory, formatInstrumentPrice, pricePrecision, unitSuffix } from '@/lib/instruments';

/** The Overview chart's tabs: the main US indices and Bitcoin (all registered instruments with stored daily OHLC). */
export const MARKET_TABS = [
  { value: '^GSPC', label: 'S&P 500' },
  { value: '^IXIC', label: 'Nasdaq' },
  { value: '^DJI', label: 'Dow' },
  { value: '^RUT', label: 'Russell 2000' },
  { value: 'BTC-USD', label: 'Bitcoin' },
] as const;
type TabSymbol = (typeof MARKET_TABS)[number]['value'];

/** Same history the Markets instrument page loads, so the two charts are identical. */
export const HISTORY_DAYS = 1825;
const CACHE_MS = 10 * 60 * 1000;
const TAB_KEY = 'overview_chart_tab';
const cache = new Map<string, { at: number; p: Promise<InstrumentHistory> }>();

/** One request per instrument, cached and shared while in flight: switching back to a tab refetches nothing. */
export function loadInstrument(symbol: string): Promise<InstrumentHistory> {
  const hit = cache.get(symbol);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.p;
  const p = apiClient
    .get<InstrumentHistory>(`/api/market/instruments/${encodeURIComponent(symbol)}/history`, { params: { days: HISTORY_DAYS } })
    .then((r) => r.data)
    .catch((e) => {
      cache.delete(symbol);
      throw e;
    });
  cache.set(symbol, { at: Date.now(), p });
  return p;
}
export const _clearMarketChartCache = () => cache.clear();

function readTab(): TabSymbol {
  try {
    const v = window.localStorage.getItem(TAB_KEY);
    return (MARKET_TABS.find((t) => t.value === v)?.value ?? '^GSPC') as TabSymbol;
  } catch {
    return '^GSPC';
  }
}

/**
 * Overview market chart: candles for the main US indices and Bitcoin on the same TradingView chart as stock pages
 * (daily / weekly / monthly, ranges, the saved Indicators choice). Each tab loads when first opened; the last tab is
 * remembered in this browser. Index closes are exchange sessions; Bitcoin trades every day, so its last close can be newer.
 */
export default function MarketChartCard() {
  const [tab, setTab] = useState<TabSymbol>('^GSPC');
  const [data, setData] = useState<InstrumentHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setTab(readTab()), []);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    loadInstrument(tab)
      .then((h) => !cancelled && setData(h))
      .catch((e) => !cancelled && setError(e?.response?.status === 404 ? 'This market is not available on this server yet.' : 'This chart is unavailable right now.'));
    return () => {
      cancelled = true;
    };
  }, [tab]);

  const choose = (t: TabSymbol) => {
    setTab(t);
    try {
      window.localStorage.setItem(TAB_KEY, t);
    } catch {
      /* per-viewer convenience only */
    }
  };

  const bars = data?.bars ?? [];
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];
  const change = last && prev && prev.close ? (last.close / prev.close - 1) * 100 : null;
  const label = MARKET_TABS.find((t) => t.value === tab)?.label ?? tab;

  return (
    <section className="mb-6 rounded-2xl border border-black/[0.05] bg-white shadow-sm" aria-labelledby="market-chart-title" data-testid="market-chart">
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 sm:px-5">
        <div className="min-w-0">
          <h2 id="market-chart-title" className="text-[17px] font-semibold text-slate-900">
            {data?.name ?? label}
          </h2>
          {data && last ? (
            <p className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-slate-500">
              <span className="text-[22px] font-semibold tabular-nums text-slate-900">
                {formatInstrumentPrice(data, last.close)}
                <span className="text-[12px] font-normal text-slate-400">{unitSuffix(data)}</span>
              </span>
              {change != null && (
                <span className={clsx('text-[14px] font-semibold tabular-nums', change >= 0 ? 'text-emerald-600' : 'text-red-600')} data-testid="market-chart-change">
                  {change >= 0 ? '+' : ''}
                  {change.toFixed(2)}%
                </span>
              )}
              <span className="text-[12px]" data-testid="market-chart-asof">
                close {last.date}
                {tab === 'BTC-USD' ? ' · trades every day' : ''}
              </span>
            </p>
          ) : (
            <p className="mt-0.5 text-[12px] text-slate-400">{error ? '' : 'Loading…'}</p>
          )}
        </div>
        <div className="flex max-w-full items-center gap-3">
          <div className="-mx-1 max-w-full overflow-x-auto px-1">
            <SegmentedControl aria-label="Market" value={tab} onChange={choose} options={MARKET_TABS.map((t) => ({ value: t.value, label: t.label }))} />
          </div>
        </div>
      </div>
      <div className="mt-2 px-1 pb-2 sm:px-2">
        {error ? (
          <p className="py-24 text-center text-sm text-slate-500" data-testid="market-chart-error">
            {error}
          </p>
        ) : !data ? (
          <div className="h-[400px] animate-pulse rounded-xl bg-black/[0.03]" aria-busy="true" />
        ) : bars.length === 0 ? (
          <p className="py-24 text-center text-sm text-slate-500">No price history stored for {label}.</p>
        ) : (
          <StockChart key={tab} bars={bars} height={400} precision={pricePrecision(data, last?.close ?? null)} plan={null} />
        )}
      </div>
      <p className="flex flex-wrap items-center justify-between gap-2 border-t border-black/[0.05] px-4 py-2 text-[11px] text-slate-400 sm:px-5">
        <span>Daily closes from stored history{bars.length ? ` · ${bars[0].date} to ${last?.date}` : ''}. Indices are price levels (no dividends).</span>
        <Link href={`/markets/${encodeURIComponent(tab)}`} className="font-medium text-accent hover:underline">
          Full {label} page →
        </Link>
      </p>
    </section>
  );
}
