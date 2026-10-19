'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { XMarkIcon } from '@heroicons/react/20/solid';
import { clsx } from 'clsx';
import { screenerV2Api, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';
import { useWatchlist } from '@/lib/watchlist';
import { watchlistPairs } from '@/lib/watchlistView';
import { formatValue } from '@/lib/screenerV2';

const UNKNOWN = '—';
const MAX_ROWS = 10;

/**
 * The stocks starred on the Screener or a stock page (this browser), with last close and day change for the newest
 * market session. ONE Screener request for the whole list (the same exact-symbol query the Watchlist page uses), so the
 * numbers match the Screener and the stock page. Saved symbols stay listed when the request fails. Hidden when empty.
 */
export default function WatchlistCard() {
  const { symbols, remove } = useWatchlist();
  const [result, setResult] = useState<ScreenerResult | null>(null);
  const [failed, setFailed] = useState(false);

  const key = symbols.join(',');
  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    screenerV2Api
      .search(watchlistPairs(key.split(','), null))
      .then((r) => {
        if (cancelled) return;
        setResult(r);
        setFailed(false);
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [key]);

  if (symbols.length === 0) return null;

  const rows: Record<string, ScreenerRow> = Object.fromEntries((result?.rows ?? []).map((r) => [r.symbol, r]));
  const shown = symbols.slice(0, MAX_ROWS);

  return (
    <section className="mb-6 rounded-2xl border border-black/[0.05] bg-white shadow-sm" data-stock-list="Watchlist">
      <div className="px-5 pb-2 pt-5">
        <h3 className="text-[17px] font-semibold text-slate-900">Watchlist</h3>
        <p className="mt-0.5 text-xs text-slate-500">
          Stocks you starred · saved in this browser
          {result ? ` · session ${result.session}` : ''} ·{' '}
          <Link href="/watchlist" className="font-medium text-accent hover:underline">
            Open watchlist
          </Link>
        </p>
        {failed && <p className="mt-1 text-xs text-slate-500">Prices are unavailable right now; your saved stocks are kept.</p>}
      </div>
      <div className="relative overflow-x-auto">
        <table className="w-full text-[13px] tabular-nums">
          <thead className="bg-slate-50/70 text-[11px] text-slate-500">
            <tr>
              <th className="py-2 pl-5 pr-3 text-left font-medium">Symbol</th>
              <th className="px-3 py-2 text-right font-medium">Last close</th>
              <th className="px-3 py-2 text-right font-medium">Change</th>
              <th className="py-2 pl-3 pr-5 text-right font-medium">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => {
              const r = rows[s];
              const waiting = !result && !failed;
              const missing = result && !r;
              return (
                <tr key={s} className="border-t border-black/[0.05]" data-testid="watchlist-card-row">
                  <td className="py-2 pl-5 pr-3 font-semibold">
                    <Link href={`/stock/${s}`} className="text-slate-900 hover:text-accent hover:underline">
                      {s}
                    </Link>
                    {missing && <span className="ml-2 text-[11px] font-normal text-slate-400">no bar on {result.session}</span>}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-800">{r?.close != null ? formatValue(r.close, '$') : waiting ? '…' : UNKNOWN}</td>
                  <td
                    className={clsx(
                      'px-3 py-2 text-right font-semibold',
                      r?.change_1d == null ? 'text-slate-400' : r.change_1d >= 0 ? 'text-emerald-600' : 'text-red-600',
                    )}
                  >
                    {r?.change_1d != null ? formatValue(r.change_1d, '%') : waiting ? '' : UNKNOWN}
                  </td>
                  <td className="py-2 pl-3 pr-5 text-right">
                    <button
                      type="button"
                      aria-label={`Remove ${s} from watchlist`}
                      onClick={() => remove(s)}
                      className="press inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-black/[0.06] hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    >
                      <XMarkIcon className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {symbols.length > MAX_ROWS && (
        <Link href="/watchlist" className="block border-t border-slate-100 px-5 py-2.5 text-center text-[13px] font-medium text-accent hover:underline">
          {symbols.length - MAX_ROWS} more in your watchlist →
        </Link>
      )}
    </section>
  );
}
