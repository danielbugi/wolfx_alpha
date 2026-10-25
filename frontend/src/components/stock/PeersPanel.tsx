'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Chip, Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { clsx } from 'clsx';
import { formatters } from '@/services/api';
import { screenerV2Api, ScreenerRow } from '@/services/screenerV2Api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import DataStamp from '@/components/common/DataStamp';
import { gradeColor } from '@/lib/uiColors';
import { peersScreenerHref, peersState, toQueryPairs } from '@/lib/screenerV2';
import LoadingSpinner from '@/components/common/LoadingSpinner';

const UNKNOWN = '—';
export const PEER_COUNT = 8;

/**
 * The largest stocks in the same sector (by market cap) -- is the whole sector moving, or just this one? It is the
 * Smart Screener's own query (same engine, metric definitions and sector membership as the Screener) for the stock's
 * Screener sector on the stock's Screener session, so every value matches the Screener row by row. `sector` and
 * `session` come from the stock's Screener context; a null sector means the stock has no row for the session.
 */
export default function PeersPanel({
  symbol,
  sector,
  session,
  contextState,
}: {
  symbol: string;
  sector: string | null | undefined;
  session: string | null | undefined;
  /** 'loading' until the stock's Screener context arrives; 'unavailable' when it could not be loaded */
  contextState: 'loading' | 'ready' | 'unavailable';
}) {
  const router = useRouter();
  const [peers, setPeers] = useState<ScreenerRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!sector || !session) return;
    let cancelled = false;
    setPeers(null);
    setFailed(false);
    screenerV2Api
      .search(toQueryPairs(peersState(sector, session, PEER_COUNT + 1)))
      .then((r) => {
        if (!cancelled) setPeers(r.rows.filter((p) => p.symbol !== symbol).slice(0, PEER_COUNT));
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, sector, session]);

  const changes = (peers ?? []).map((p) => p.change_1d).filter((v): v is number => v != null);
  const avgChange = changes.length ? changes.reduce((a, v) => a + v, 0) / changes.length : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-slate-900">Peers {sector ? <span className="font-normal text-slate-400">· {sector}</span> : null}</h3>
        {avgChange != null && (
          <p className="text-xs text-slate-500" data-testid="peers-avg">
            Peers on the session (avg of {changes.length}):{' '}
            <span className={clsx('font-semibold tabular-nums', avgChange >= 0 ? 'text-emerald-600' : 'text-red-600')}>
              {avgChange >= 0 ? '+' : ''}
              {avgChange.toFixed(2)}%
            </span>
          </p>
        )}
      </div>
      {contextState === 'loading' ? (
        <LoadingSpinner className="py-8" />
      ) : contextState === 'unavailable' ? (
        <p className="py-6 text-center text-sm text-slate-400">Peers are unavailable right now.</p>
      ) : !sector || !session ? (
        <p className="py-6 text-center text-sm text-slate-400">No Screener sector for this stock on the latest session, so there are no peers to compare.</p>
      ) : failed ? (
        <p className="py-6 text-center text-sm text-slate-400">Couldn&apos;t load peers.</p>
      ) : peers == null ? (
        <LoadingSpinner className="py-8" />
      ) : peers.length === 0 ? (
        <p className="py-6 text-center text-sm text-slate-400">No other stocks in this sector on {session}.</p>
      ) : (
        <>
          <Table
            removeWrapper
            aria-label="Sector peers"
            onRowAction={(key) => router.push(`/stock/${key}`)}
            classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8', td: 'text-[13px] py-2 tabular-nums', tr: 'cursor-pointer' }}
          >
            <TableHeader>
              <TableColumn>SYMBOL</TableColumn>
              <TableColumn align="end">CLOSE</TableColumn>
              <TableColumn align="end">1D</TableColumn>
              <TableColumn align="end">MKT CAP</TableColumn>
              <TableColumn align="end">P/E</TableColumn>
              <TableColumn align="end">RS 20</TableColumn>
              <TableColumn>GRADE</TableColumn>
            </TableHeader>
            <TableBody>
              {peers.map((p) => (
                <TableRow key={p.symbol} data-testid="peer-row">
                  <TableCell>
                    <SymbolHoverLink symbol={p.symbol} />
                  </TableCell>
                  <TableCell className="text-right text-slate-800">{p.close != null ? `$${p.close.toFixed(2)}` : UNKNOWN}</TableCell>
                  <TableCell className={clsx('text-right font-semibold', p.change_1d == null ? 'text-slate-400' : p.change_1d >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                    {p.change_1d == null ? UNKNOWN : `${p.change_1d >= 0 ? '+' : ''}${p.change_1d.toFixed(2)}%`}
                  </TableCell>
                  <TableCell className="text-right text-slate-600">{p.market_cap != null ? formatters.marketCap(p.market_cap) : UNKNOWN}</TableCell>
                  <TableCell className="text-right text-slate-600">{p.pe_ratio != null ? p.pe_ratio.toFixed(1) : UNKNOWN}</TableCell>
                  <TableCell className="text-right text-slate-600">{p.rs_20 != null ? p.rs_20.toFixed(0) : UNKNOWN}</TableCell>
                  <TableCell>
                    {p.quality_grade ? <Chip size="sm" className={`text-xs ${gradeColor(p.quality_grade)}`}>{p.quality_grade}</Chip> : <span className="text-slate-400">{UNKNOWN}</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
            <DataStamp asOf={session} source="same data as the Screener" />
            <Link href={peersScreenerHref(sector, session)} className="text-xs font-medium text-accent hover:underline" data-testid="peers-screener-link">
              All {sector} stocks in the Screener →
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
