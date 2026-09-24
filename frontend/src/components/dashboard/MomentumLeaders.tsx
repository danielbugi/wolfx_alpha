'use client';

import React from 'react';
import { Card, CardBody, CardHeader, Chip } from '@nextui-org/react';
import { MomentumLeadersBoard, MomentumLeaderRow } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';

const MEDAL = ['🥇', '🥈', '🥉'];

function Row({ row, rank }: { row: MomentumLeaderRow; rank: number }) {
  return (
    <div className="flex items-center justify-between py-1.5 px-1 border-b border-slate-100 last:border-b-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-xs text-slate-400 w-5 shrink-0">{MEDAL[rank] ?? `#${rank + 1}`}</span>
        <SymbolHoverLink symbol={row.symbol} />
        <span className="text-[10px] text-slate-400 shrink-0">
          {row.listed_sessions}× listed
        </span>
        {row.at_day_high && (
          <Chip size="sm" className="bg-cyan-100 text-cyan-800 text-[10px] h-4 px-1 shrink-0">day high</Chip>
        )}
        {row.above_20d_high && (
          <Chip size="sm" className="bg-emerald-100 text-emerald-800 text-[10px] h-4 px-1 shrink-0">20d high</Chip>
        )}
      </div>
      <span className="font-semibold text-emerald-600 text-sm shrink-0 ml-2">
        +{row.ret1_pct.toFixed(2)}%
      </span>
    </div>
  );
}

export interface MomentumLeadersProps {
  board: MomentumLeadersBoard | null;
  sessionDate: string | null;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}

/**
 * "Who's still moving after making an earlier Momentum Board list" -- distinct from Momentum
 * Board, which ranks TODAY's own lists. Not a track record: consecutive days of the same stock
 * overlap, and earlier lists say nothing about later ones (see mechanism/alerts/board.py).
 */
export default function MomentumLeaders({ board, sessionDate, loading, error, onRetry }: MomentumLeadersProps) {
  return (
    <Card className="mb-6 shadow-sm border border-slate-200">
      <CardHeader className="p-3 pb-2">
        <div className="flex justify-between items-center w-full">
          <div>
            <h3 className="text-sm font-semibold text-slate-700">Momentum Leaders</h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Of stocks that made a Momentum Board list in the last few sessions, who moved most today
            </p>
          </div>
          {sessionDate && (
            <Chip size="sm" className="bg-slate-100 text-slate-600 text-xs shrink-0">session {sessionDate}</Chip>
          )}
        </div>
      </CardHeader>
      <CardBody className="p-3 pt-1">
        {loading ? (
          <div className="flex justify-center py-8">
            <LoadingSpinner size="medium" />
          </div>
        ) : error ? (
          <ErrorAlert title="Momentum Leaders unavailable" message="Couldn't load the leaderboard." onRetry={onRetry} />
        ) : !board || board.top.length === 0 ? (
          <p className="text-center text-slate-400 text-sm py-8">
            No leaderboard yet — not enough stored sessions, or nobody in the pool closed higher today.
          </p>
        ) : (
          <>
            <div className="text-[11px] text-slate-500 mb-2">
              Pool of {board.n_measured} stocks (from {board.n_sessions} prior session{board.n_sessions === 1 ? '' : 's'}
              {board.n_excluded > 0 ? `, ${board.n_excluded} excluded — bad or missing data` : ''}):{' '}
              <span className="text-emerald-600 font-medium">{board.higher} higher</span>,{' '}
              <span className="text-red-500 font-medium">{board.lower} lower</span>,{' '}
              {board.at_day_high} at day&apos;s high, {board.above_20d_high} above their 20-day high.
            </div>
            {board.top.map((row, i) => (
              <Row key={row.symbol} row={row} rank={i} />
            ))}
            {board.more.length > 0 && (
              <details className="mt-1">
                <summary className="text-xs text-slate-500 cursor-pointer select-none py-1">
                  More: ranks {board.top.length + 1}-{board.top.length + board.more.length}
                </summary>
                {board.more.map((row, i) => (
                  <Row key={row.symbol} row={row} rank={board.top.length + i} />
                ))}
              </details>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}
