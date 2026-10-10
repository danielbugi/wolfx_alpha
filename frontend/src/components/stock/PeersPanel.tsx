'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Chip, Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { clsx } from 'clsx';
import { screenerApi, ScreenerResult, formatters } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { gradeColor } from '@/lib/uiColors';
import LoadingSpinner from '@/components/common/LoadingSpinner';

const UNKNOWN = '—';
const PEER_COUNT = 8;

/** The largest stocks in the same sector (by market cap), from the screener — is the whole sector moving, or just this one? */
export default function PeersPanel({ symbol, sector }: { symbol: string; sector: string | null | undefined }) {
  const router = useRouter();
  const [peers, setPeers] = useState<ScreenerResult[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!sector) return;
    let cancelled = false;
    setPeers(null);
    setFailed(false);
    screenerApi
      .search({ sectors: [sector], sort_by: 'market_cap', sort_order: 'desc', limit: PEER_COUNT + 1 })
      .then((r) => {
        if (!cancelled) setPeers(r.results.filter((p) => p.symbol !== symbol).slice(0, PEER_COUNT));
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, sector]);

  const changes = (peers ?? []).map((p) => p.price_change_pct).filter((v): v is number => v != null);
  const avgChange = changes.length ? changes.reduce((a, v) => a + v, 0) / changes.length : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-slate-900">Peers {sector ? <span className="font-normal text-slate-400">· {sector}</span> : null}</h3>
        {avgChange != null && (
          <p className="text-xs text-slate-500">
            Peers today (avg):{' '}
            <span className={clsx('font-semibold tabular-nums', avgChange >= 0 ? 'text-emerald-600' : 'text-red-600')}>
              {avgChange >= 0 ? '+' : ''}
              {avgChange.toFixed(2)}%
            </span>
          </p>
        )}
      </div>
      {!sector ? (
        <p className="py-6 text-center text-sm text-slate-400">Sector unknown, so there are no peers to compare.</p>
      ) : failed ? (
        <p className="py-6 text-center text-sm text-slate-400">Couldn&apos;t load peers.</p>
      ) : peers == null ? (
        <LoadingSpinner className="py-8" />
      ) : peers.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-400">No other stocks tracked in this sector.</p>
      ) : (
        <Table
          removeWrapper
          aria-label="Sector peers"
          onRowAction={(key) => router.push(`/stock/${key}`)}
          classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8', td: 'text-[13px] py-2 tabular-nums', tr: 'cursor-pointer' }}
        >
          <TableHeader>
            <TableColumn>SYMBOL</TableColumn>
            <TableColumn align="end">PRICE</TableColumn>
            <TableColumn align="end">CHANGE</TableColumn>
            <TableColumn align="end">MKT CAP</TableColumn>
            <TableColumn align="end">P/E</TableColumn>
            <TableColumn align="end">RSI</TableColumn>
            <TableColumn>GRADE</TableColumn>
          </TableHeader>
          <TableBody>
            {peers.map((p) => (
              <TableRow key={p.symbol}>
                <TableCell>
                  <SymbolHoverLink symbol={p.symbol} />
                </TableCell>
                <TableCell className="text-right text-slate-800">${p.current_price.toFixed(2)}</TableCell>
                <TableCell className={clsx('text-right font-semibold', p.price_change_pct >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                  {p.price_change_pct >= 0 ? '+' : ''}
                  {p.price_change_pct.toFixed(2)}%
                </TableCell>
                <TableCell className="text-right text-slate-600">{p.market_cap != null ? formatters.marketCap(p.market_cap) : UNKNOWN}</TableCell>
                <TableCell className="text-right text-slate-600">{p.pe_ratio != null ? p.pe_ratio.toFixed(1) : UNKNOWN}</TableCell>
                <TableCell className="text-right text-slate-600">{p.rsi_14 != null ? p.rsi_14.toFixed(0) : UNKNOWN}</TableCell>
                <TableCell>
                  {p.quality_grade ? <Chip size="sm" className={`text-xs ${gradeColor(p.quality_grade)}`}>{p.quality_grade}</Chip> : <span className="text-slate-400">{UNKNOWN}</span>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
