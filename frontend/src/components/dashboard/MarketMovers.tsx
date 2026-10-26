'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Card, CardBody, CardHeader, Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { ChevronRightIcon } from '@heroicons/react/20/solid';
import { MoversMeta, StockItem } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SortHeader from '@/components/ui/SortHeader';
import { useTableSort } from '@/hooks/useTableSort';
import { moversScreenerHref } from '@/lib/screenerV2';

type MoversList = 'gainers' | 'losers' | 'volume';

const LISTS: { value: MoversList; label: string; blurb: string }[] = [
  { value: 'gainers', label: 'Top Gainers', blurb: 'Biggest one-day rises' },
  { value: 'losers', label: 'Top Losers', blurb: 'Biggest one-day falls' },
  { value: 'volume', label: 'Unusual Volume', blurb: 'Trading well above their usual volume' },
];

const ACCESSORS = {
  symbol: (s: StockItem) => s.symbol,
  price: (s: StockItem) => s.current_price,
  change: (s: StockItem) => s.price_change_pct,
  volume: (s: StockItem) => s.volume,
  ratio: (s: StockItem) => s.volume_ratio,
  sector: (s: StockItem) => s.sector,
};
type Col = keyof typeof ACCESSORS;

const COLUMNS: { key: Col; label: string; align: 'start' | 'end' }[] = [
  { key: 'symbol', label: 'Symbol', align: 'start' },
  { key: 'price', label: 'Price', align: 'end' },
  { key: 'change', label: 'Change', align: 'end' },
  { key: 'volume', label: 'Volume', align: 'end' },
  { key: 'ratio', label: 'Vol ×', align: 'end' },
  { key: 'sector', label: 'Sector', align: 'start' },
];

const pct = (v: number | null) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);

/** Why a list is empty, from what the session's data actually holds (never a generic "nothing today"). */
function emptyReason(list: MoversList, meta: MoversMeta | null | undefined): string {
  if (!meta) return 'Nothing on this list today.';
  const s = meta.session;
  if (list === 'volume')
    return meta.coverage.volume_ratio === 0
      ? `Volume ratios for the ${s} session are not available yet.`
      : `No stock traded at ${meta.min_volume_ratio}× its usual volume on ${s}.`;
  if (meta.coverage.change_1d === 0) return `No previous session to compare ${s} with.`;
  return `No stock ${list === 'gainers' ? 'rose' : 'fell'} on ${s}.`;
}

/**
 * Whole-market movers: the three "any stock" lists (gainers, losers, unusual volume) in one card behind a segmented
 * control. They are Screener queries for ONE explicit session (`meta.session`, served by the backend's Screener v2
 * engine), and "View all" opens exactly that query and session in the Screener.
 */
export default function MarketMovers({
  gainers,
  losers,
  unusualVolume,
  meta,
}: {
  gainers: StockItem[];
  losers: StockItem[];
  unusualVolume: StockItem[];
  meta?: MoversMeta | null;
}) {
  const router = useRouter();
  const [list, setList] = useState<MoversList>('gainers');
  const rows = list === 'gainers' ? gainers : list === 'losers' ? losers : unusualVolume;
  const { sorted, sortKey, sortDir, toggle } = useTableSort(rows, ACCESSORS);
  const listMeta = LISTS.find((l) => l.value === list)!;

  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardHeader className="flex flex-wrap items-end justify-between gap-3 px-5 pt-5 pb-3">
        <div>
          <h2 className="text-[17px] font-semibold text-slate-900">Whole-Market Movers</h2>
          <p className="mt-0.5 text-xs text-slate-500" data-testid="movers-session">
            Every liquid stock ($1+, $1M+ a day) · {listMeta.blurb.toLowerCase()}
            {meta?.session ? ` · session ${meta.session}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <SegmentedControl
            aria-label="Movers list"
            options={LISTS.map((l) => ({ value: l.value, label: l.label }))}
            value={list}
            onChange={setList}
          />
          <Link
            href={moversScreenerHref(list, meta?.session)}
            title="The whole list in the Screener, ranked the same way (same price and liquidity floor)"
            data-testid="movers-view-all"
            className="inline-flex items-center text-xs font-medium text-accent hover:underline"
          >
            View all
            <ChevronRightIcon className="h-4 w-4" />
          </Link>
        </div>
      </CardHeader>
      <CardBody className="p-0">
        {sorted.length === 0 ? (
          <p className="py-10 text-center text-sm text-slate-400" data-testid="movers-empty">
            {emptyReason(list, meta)}
          </p>
        ) : (
          <Table
            removeWrapper
            aria-label={listMeta.label}
            onRowAction={(key) => router.push(`/stock/${key}`)}
            classNames={{
              th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8 first:pl-5 last:pr-5',
              td: 'text-[13px] py-2 first:pl-5 last:pr-5',
              tr: 'cursor-pointer',
            }}
          >
            <TableHeader>
              {COLUMNS.map((c) => (
                <TableColumn key={c.key} align={c.align}>
                  <SortHeader label={c.label} align={c.align} active={sortKey === c.key} dir={sortDir} onClick={() => toggle(c.key)} />
                </TableColumn>
              ))}
            </TableHeader>
            <TableBody>
              {sorted.map((stock) => (
                <TableRow key={stock.symbol}>
                  <TableCell>
                    <SymbolHoverLink symbol={stock.symbol} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-slate-800">
                    {Number.isFinite(stock.current_price) ? `$${stock.current_price.toFixed(2)}` : '—'}
                  </TableCell>
                  <TableCell className="text-right">
                    <span
                      className={`font-semibold tabular-nums ${
                        stock.price_change_pct == null ? 'text-slate-400' : stock.price_change_pct >= 0 ? 'text-emerald-600' : 'text-red-600'
                      }`}
                    >
                      {pct(stock.price_change_pct)}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-slate-600">{stock.volume != null ? `${(stock.volume / 1_000_000).toFixed(1)}M` : '—'}</TableCell>
                  <TableCell className="text-right">
                    <span
                      className={`font-semibold tabular-nums ${
                        stock.volume_ratio == null
                          ? 'text-slate-400'
                          : stock.volume_ratio >= 3
                            ? 'text-red-600'
                            : stock.volume_ratio >= 2
                              ? 'text-amber-600'
                              : 'text-slate-600'
                      }`}
                    >
                      {stock.volume_ratio != null ? `${stock.volume_ratio.toFixed(1)}x` : '—'}
                    </span>
                  </TableCell>
                  <TableCell className="text-slate-500">{stock.sector}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}
