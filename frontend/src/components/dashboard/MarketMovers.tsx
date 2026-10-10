'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Card, CardBody, CardHeader, Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { ChevronRightIcon } from '@heroicons/react/20/solid';
import { StockItem } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SortHeader from '@/components/ui/SortHeader';
import { useTableSort } from '@/hooks/useTableSort';

type MoversList = 'gainers' | 'losers' | 'volume';

const LISTS: { value: MoversList; label: string; screenerFilter: string; blurb: string }[] = [
  { value: 'gainers', label: 'Top Gainers', screenerFilter: 'gainers', blurb: 'Biggest one-day rises' },
  { value: 'losers', label: 'Top Losers', screenerFilter: 'losers', blurb: 'Biggest one-day falls' },
  { value: 'volume', label: 'Unusual Volume', screenerFilter: 'volume', blurb: 'Trading well above their usual volume' },
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

/**
 * Whole-market movers: the three "any stock" lists (gainers, losers, unusual volume) in one card behind a segmented
 * control, instead of three stacked tables. Unlike the Momentum Board these are not breakout-confirmed.
 */
export default function MarketMovers({ gainers, losers, unusualVolume }: { gainers: StockItem[]; losers: StockItem[]; unusualVolume: StockItem[] }) {
  const router = useRouter();
  const [list, setList] = useState<MoversList>('gainers');
  const rows = list === 'gainers' ? gainers : list === 'losers' ? losers : unusualVolume;
  const { sorted, sortKey, sortDir, toggle } = useTableSort(rows, ACCESSORS);
  const meta = LISTS.find((l) => l.value === list)!;

  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardHeader className="flex flex-wrap items-end justify-between gap-3 px-5 pt-5 pb-3">
        <div>
          <h2 className="text-[17px] font-semibold text-slate-900">Whole-Market Movers</h2>
          <p className="mt-0.5 text-xs text-slate-500">Any stock, not just breakout-confirmed names · {meta.blurb.toLowerCase()}</p>
        </div>
        <div className="flex items-center gap-3">
          <SegmentedControl
            aria-label="Movers list"
            options={LISTS.map((l) => ({ value: l.value, label: l.label }))}
            value={list}
            onChange={setList}
          />
          <Link
            href={`/screener?filter=${meta.screenerFilter}`}
            className="inline-flex items-center text-xs font-medium text-accent hover:underline"
          >
            View all
            <ChevronRightIcon className="h-4 w-4" />
          </Link>
        </div>
      </CardHeader>
      <CardBody className="p-0">
        {sorted.length === 0 ? (
          <p className="py-10 text-center text-sm text-slate-400">Nothing on this list today.</p>
        ) : (
          <Table
            removeWrapper
            aria-label={meta.label}
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
                  <TableCell className="text-right tabular-nums text-slate-800">${stock.current_price.toFixed(2)}</TableCell>
                  <TableCell className="text-right">
                    <span className={`font-semibold tabular-nums ${stock.price_change_pct >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                      {stock.price_change_pct >= 0 ? '+' : ''}
                      {stock.price_change_pct.toFixed(2)}%
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-slate-600">{(stock.volume / 1_000_000).toFixed(1)}M</TableCell>
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
