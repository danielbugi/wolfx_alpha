'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Card, CardBody, CardHeader, Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { XMarkIcon } from '@heroicons/react/20/solid';
import { clsx } from 'clsx';
import { stockApi, PriceBar } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { useWatchlist } from '@/lib/watchlist';

const UNKNOWN = '—';

interface Quote {
  close: number | null;
  change: number | null;
  date: string | null;
}

function quoteFrom(bars: PriceBar[]): Quote {
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];
  if (!last) return { close: null, change: null, date: null };
  return { close: last.close, change: prev ? (last.close / prev.close - 1) * 100 : null, date: last.date };
}

/** The stocks starred on their stock page (this browser), with last close and day change. Hidden when empty. */
export default function WatchlistCard() {
  const router = useRouter();
  const { symbols, remove } = useWatchlist();
  const [quotes, setQuotes] = useState<Record<string, Quote | 'error'>>({});

  useEffect(() => {
    let cancelled = false;
    const missing = symbols.filter((s) => !(s in quotes));
    missing.forEach((s) => {
      stockApi
        .getPriceHistory(s, 10)
        .then((r) => !cancelled && setQuotes((q) => ({ ...q, [s]: quoteFrom(r.bars) })))
        .catch(() => !cancelled && setQuotes((q) => ({ ...q, [s]: 'error' })));
    });
    return () => {
      cancelled = true;
    };
    // quotes is read only to skip symbols already loaded
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols]);

  if (symbols.length === 0) return null;

  return (
    <Card className="mb-6 border border-black/[0.05] shadow-sm" data-stock-list="Watchlist">
      <CardHeader className="px-5 pt-5 pb-2">
        <div>
          <h3 className="text-[17px] font-semibold text-slate-900">Watchlist</h3>
          <p className="mt-0.5 text-xs text-slate-500">Stocks you starred · saved in this browser</p>
        </div>
      </CardHeader>
      <CardBody className="p-0">
        <Table
          removeWrapper
          aria-label="Watchlist"
          onRowAction={(key) => router.push(`/stock/${key}`)}
          classNames={{
            th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8 first:pl-5 last:pr-5',
            td: 'text-[13px] py-2 tabular-nums first:pl-5 last:pr-5',
            tr: 'cursor-pointer',
          }}
        >
          <TableHeader>
            <TableColumn>SYMBOL</TableColumn>
            <TableColumn align="end">LAST CLOSE</TableColumn>
            <TableColumn align="end">CHANGE</TableColumn>
            <TableColumn align="end">AS OF</TableColumn>
            <TableColumn align="end"> </TableColumn>
          </TableHeader>
          <TableBody>
            {symbols.map((s) => {
              const q = quotes[s];
              const quote = q && q !== 'error' ? q : null;
              return (
                <TableRow key={s}>
                  <TableCell>
                    <SymbolHoverLink symbol={s} />
                  </TableCell>
                  <TableCell className="text-right text-slate-800">{quote?.close != null ? `$${quote.close.toFixed(2)}` : q === undefined ? '…' : UNKNOWN}</TableCell>
                  <TableCell
                    className={clsx(
                      'text-right font-semibold',
                      quote?.change == null ? 'text-slate-400' : quote.change >= 0 ? 'text-emerald-600' : 'text-red-600',
                    )}
                  >
                    {quote?.change != null ? `${quote.change >= 0 ? '+' : ''}${quote.change.toFixed(2)}%` : q === undefined ? '' : UNKNOWN}
                  </TableCell>
                  <TableCell className="text-right text-slate-400">{quote?.date ?? ''}</TableCell>
                  <TableCell className="text-right">
                    <button
                      type="button"
                      aria-label={`Remove ${s} from watchlist`}
                      onClick={(e) => {
                        e.stopPropagation();
                        remove(s);
                      }}
                      onPointerDown={(e) => e.stopPropagation()}
                      className="press inline-flex h-6 w-6 items-center justify-center rounded-full text-slate-400 hover:bg-black/[0.06] hover:text-slate-700"
                    >
                      <XMarkIcon className="h-4 w-4" />
                    </button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardBody>
    </Card>
  );
}
