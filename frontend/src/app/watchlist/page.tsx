// File: frontend/src/app/watchlist/page.tsx
'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { screenerV2Api, ScreenerRow } from '@/services/screenerV2Api';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SymbolChart from '@/components/watchlist/SymbolChart';
import WatchlistPanel from '@/components/watchlist/WatchlistPanel';
import WatchlistPerformance from '@/components/watchlist/WatchlistPerformance';
import WatchlistDetails from '@/components/watchlist/WatchlistDetails';
import { useWatchlist } from '@/lib/watchlist';
import { watchlistPairs } from '@/lib/watchlistView';

type View = 'performance' | 'chart' | 'details';
const VIEWS: View[] = ['performance', 'chart', 'details'];
const SELECTED_KEY = 'watchlist_selected';
const VIEW_KEY = 'watchlist_view';

function readView(): View {
  try {
    const v = window.localStorage.getItem(VIEW_KEY) as View | null;
    return v && VIEWS.includes(v) ? v : 'performance';
  } catch {
    return 'performance';
  }
}

function readSelected(): Record<string, string> {
  try {
    const v = JSON.parse(window.localStorage.getItem(SELECTED_KEY) ?? '{}');
    return v && typeof v === 'object' ? v : {};
  } catch {
    return {};
  }
}

/**
 * Watchlist, laid out like a trading terminal: the main view on the left (Performance, the default: the selected stocks
 * rebased against the S&P 500 with summary, ranking and cards; Chart: the selected symbol on the stock page's TradingView
 * chart; Details: the Screener metrics table, secondary detail) and
 * the watchlist panel on the right (several lists, last close and day change from ONE Screener request). On a phone the
 * chart comes first and the list follows; picking a symbol scrolls back to the chart.
 */
export default function WatchlistPage() {
  const wl = useWatchlist();
  const { symbols, activeId } = wl;
  const [view, setViewState] = useState<View>('performance');
  const [selected, setSelected] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, ScreenerRow>>({});
  const [session, setSession] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const top = useRef<HTMLDivElement>(null);

  // the last view is a per-viewer convenience; the page renders the default without storage
  useEffect(() => setViewState(readView()), []);
  const setView = (v: View) => {
    setViewState(v);
    try {
      window.localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* per-viewer convenience only */
    }
  };

  // the selected symbol, remembered per list; falls back to the list's first symbol
  const symKey = symbols.join(',');
  useEffect(() => {
    const remembered = readSelected()[activeId];
    setSelected((cur) => (cur && symbols.includes(cur) ? cur : remembered && symbols.includes(remembered) ? remembered : symbols[0] ?? null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, symKey]);

  const select = (s: string) => {
    setSelected(s);
    if (view !== 'chart') setView('chart'); // picking a stock in the list charts it
    try {
      window.localStorage.setItem(SELECTED_KEY, JSON.stringify({ ...readSelected(), [activeId]: s }));
    } catch {
      /* per-viewer convenience only */
    }
    if (typeof window !== 'undefined' && window.innerWidth < 1024) top.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  };

  // ONE Screener request for the active list: last close, day change, sector/industry for the panel and the header
  useEffect(() => {
    if (!symKey) {
      setRows({});
      setSession(null);
      setUnavailable(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    screenerV2Api
      .search(watchlistPairs(symKey.split(','), null))
      .then((r) => {
        if (cancelled) return;
        setRows(Object.fromEntries(r.rows.map((x) => [x.symbol, x])));
        setSession(r.session);
        setUnavailable(null);
      })
      .catch((e) => {
        if (cancelled) return;
        const status = e?.response?.status;
        setRows({});
        setSession(null);
        setUnavailable(status === 404 || status === 422 ? 'Prices are not available on this server yet.' : 'Prices are unavailable right now; your lists are kept.');
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symKey]);

  const row = useMemo(() => (selected ? rows[selected] : undefined), [rows, selected]);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-[1600px] px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3 px-1 pt-2" ref={top}>
          <div className="min-w-0">
            <h1 className="large-title">Watchlist</h1>
            <p className="mt-1.5 text-[15px] text-slate-500">Your lists, saved in this browser. Compare several against the S&amp;P 500, or pick one to chart it.</p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_340px] lg:items-start">
          <div className="min-w-0 space-y-3">
            {symbols.length > 0 && (
              <SegmentedControl
                aria-label="Watchlist view"
                value={view}
                onChange={setView}
                options={[
                  { value: 'performance', label: 'Performance' },
                  { value: 'chart', label: 'Chart' },
                  { value: 'details', label: 'Details' },
                ]}
              />
            )}
            {symbols.length === 0 ? (
              <div className="rounded-2xl border border-black/[0.05] bg-white p-8 text-center shadow-sm" data-testid="watchlist-empty">
                <p className="text-[15px] font-medium text-slate-800">&quot;{wl.activeName}&quot; is empty</p>
                <p className="mt-1 text-sm text-slate-500">
                  Add a symbol in the list panel, or use the star on the <Link href="/screener" className="text-accent hover:underline">Screener</Link> or on a stock page. Up to {wl.max} stocks per list, saved in this browser only.
                </p>
              </div>
            ) : view === 'performance' ? (
              <WatchlistPerformance saved={symbols} rows={rows} session={session} />
            ) : view === 'chart' ? (
              selected && <SymbolChart symbol={selected} row={row} />
            ) : (
              <WatchlistDetails />
            )}
          </div>
          <WatchlistPanel rows={rows} session={session} loading={loading} unavailable={unavailable} selected={view === 'chart' ? selected : null} onSelect={select} />
        </div>
      </div>
    </div>
  );
}
