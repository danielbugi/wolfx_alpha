'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { clsx } from 'clsx';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import SortHeader from '@/components/ui/SortHeader';
import { useTableSort } from '@/hooks/useTableSort';
import { formatDay, reportResult, type EarningsRow, type HistoryState } from '@/lib/earningsCalendar';

const UNKNOWN = '—';

const ACCESSORS = {
  symbol: (r: EarningsRow) => r.symbol,
  date: (r: EarningsRow) => r.date,
  sector: (r: EarningsRow) => r.sector,
  close: (r: EarningsRow) => r.close,
  est: (r: EarningsRow) => r.epsEstimate,
  surprise: (r: EarningsRow) => r.surprisePct,
  record: (r: EarningsRow) => (r.judged ? (r.beats ?? 0) / r.judged : null),
};
type Col = keyof typeof ACCESSORS;

const money = (v: number) => `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`;

function ResultCell({ row }: { row: EarningsRow }) {
  const result = reportResult(row);
  if (!result) return <span className="text-slate-400">{UNKNOWN}</span>;
  return (
    <span className="inline-flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-slate-800">{money(row.epsActual as number)}</span>
      <span
        className={clsx(
          'text-[12px] font-semibold',
          result === 'beat' ? 'text-emerald-600' : result === 'miss' ? 'text-red-600' : 'text-slate-500',
        )}
      >
        {result === 'beat' ? 'Beat' : result === 'miss' ? 'Miss' : 'In line'}
        {row.surprisePct != null && ` ${row.surprisePct >= 0 ? '+' : ''}${row.surprisePct.toFixed(1)}%`}
      </span>
    </span>
  );
}

/**
 * Earnings rows for one day or a range. EPS figures and the beat record come from each symbol's history, which loads
 * after the row itself: "…" while it is loading, "—" when the source has no value. Nothing is defaulted.
 * Product view: no screen/board signals are shown. On a phone the sector moves under the symbol so the table fits.
 */
export default function EarningsTable({
  rows,
  histories,
  showDate = false,
  showResult = false,
  ariaLabel,
}: {
  rows: EarningsRow[];
  histories: Record<string, HistoryState>;
  showDate?: boolean;
  showResult?: boolean;
  ariaLabel: string;
}) {
  const router = useRouter();
  const { sorted, sortKey, sortDir, toggle } = useTableSort<EarningsRow, Col>(rows, ACCESSORS);
  const head = (key: Col, label: string, align: 'start' | 'end' = 'start') => (
    <SortHeader label={label} active={sortKey === key} dir={sortKey === key ? sortDir : null} onClick={() => toggle(key)} align={align} />
  );
  const pending = (symbol: string) => histories[symbol] === undefined;

  const columns = [
    <TableColumn key="symbol">{head('symbol', 'Symbol')}</TableColumn>,
    ...(showDate ? [<TableColumn key="date">{head('date', 'Date')}</TableColumn>] : []),
    <TableColumn key="sector" className="hidden sm:table-cell">{head('sector', 'Sector')}</TableColumn>,
    <TableColumn key="close" align="end">{head('close', 'Last close', 'end')}</TableColumn>,
    <TableColumn key="est" align="end">{head('est', 'EPS est.', 'end')}</TableColumn>,
    ...(showResult ? [<TableColumn key="result" align="end">{head('surprise', 'Reported', 'end')}</TableColumn>] : []),
    <TableColumn key="record" align="end">{head('record', 'Beat record', 'end')}</TableColumn>,
  ];

  return (
    <div className="relative overflow-x-auto">
      <Table
        removeWrapper
        aria-label={ariaLabel}
        onRowAction={(key) => router.push(`/stock/${String(key).split('|')[0]}`)}
        classNames={{
          th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8 first:pl-5 last:pr-5 whitespace-nowrap',
          td: 'text-[13px] py-2 tabular-nums first:pl-5 last:pr-5',
          tr: 'cursor-pointer',
        }}
      >
        <TableHeader>{columns}</TableHeader>
        <TableBody>
          {sorted.map((r) => {
            const wait = pending(r.symbol);
            const cells = [
              <TableCell key="symbol">
                <div className="flex items-center gap-1.5">
                  <SymbolHoverLink symbol={r.symbol} />
                  {r.onWatchlist && (
                    <span className="rounded-full bg-accent-soft px-1.5 text-[10px] font-semibold text-accent" title="On your watchlist">
                      Watchlist
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-slate-400 sm:hidden">{r.sector ?? UNKNOWN}</div>
              </TableCell>,
              ...(showDate ? [<TableCell key="date" className="whitespace-nowrap text-slate-600">{formatDay(r.date)}</TableCell>] : []),
              <TableCell key="sector" className="hidden whitespace-nowrap text-slate-500 sm:table-cell">{r.sector ?? UNKNOWN}</TableCell>,
              <TableCell key="close" className="text-right text-slate-800">{r.close != null ? money(r.close) : UNKNOWN}</TableCell>,
              <TableCell key="est" className="text-right text-slate-800">
                {r.epsEstimate != null ? money(r.epsEstimate) : wait ? '…' : UNKNOWN}
              </TableCell>,
              ...(showResult
                ? [
                    <TableCell key="result" className="text-right">
                      {wait && r.epsActual == null ? <span className="text-slate-400">…</span> : <ResultCell row={r} />}
                    </TableCell>,
                  ]
                : []),
              <TableCell key="record" className="text-right text-slate-600">
                {r.judged ? `${r.beats} of ${r.judged}` : wait ? '…' : UNKNOWN}
              </TableCell>,
            ];
            return <TableRow key={`${r.symbol}|${r.date}`}>{cells}</TableRow>;
          })}
        </TableBody>
      </Table>
    </div>
  );
}
