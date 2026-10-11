'use client';

import React, { useMemo } from 'react';
import { Chip, Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { clsx } from 'clsx';
import type { EarningsDateEntry, PriceBar } from '@/services/api';

const UNKNOWN = '—';

function daysUntil(isoDate: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((new Date(`${isoDate}T00:00:00`).getTime() - today.getTime()) / 86_400_000);
}

/**
 * The stock's move around a report: close of the last trading day BEFORE the report date to close of the trading day
 * AFTER it. That two-day window captures the reaction whether the company reported before the open or after the
 * close — the report's time of day isn't stored yet, so a one-day window could miss it. Null when the bars don't
 * cover both days.
 */
export function reactionPct(bars: PriceBar[], reportDate: string): number | null {
  const i = bars.findIndex((b) => b.date >= reportDate);
  if (i <= 0 || i + 1 >= bars.length) return null;
  // a report dated on a weekend/holiday lands on the next session; "before" is still the session before that
  return (bars[i + 1].close / bars[i - 1].close - 1) * 100;
}

export default function EarningsPanel({ earnings, bars }: { earnings: EarningsDateEntry[]; bars: PriceBar[] }) {
  const { next, past, stats } = useMemo(() => {
    const sorted = [...earnings].sort((a, b) => a.date.localeCompare(b.date));
    const nextE = sorted.find((e) => daysUntil(e.date) >= 0) ?? null;
    const pastE = sorted
      .filter((e) => daysUntil(e.date) < 0)
      .reverse()
      .slice(0, 8)
      .map((e) => ({
        ...e,
        beat: e.eps_actual != null && e.eps_estimate != null ? e.eps_actual > e.eps_estimate : null,
        reaction: reactionPct(bars, e.date),
      }));
    const judged = pastE.filter((e) => e.beat != null);
    const moves = pastE.map((e) => e.reaction).filter((v): v is number => v != null);
    return {
      next: nextE,
      past: pastE,
      stats: {
        beats: judged.filter((e) => e.beat).length,
        judged: judged.length,
        avgAbsMove: moves.length ? moves.reduce((a, v) => a + Math.abs(v), 0) / moves.length : null,
        ups: moves.filter((v) => v > 0).length,
        downs: moves.filter((v) => v < 0).length,
        moves: moves.length,
      },
    };
  }, [earnings, bars]);

  const days = next ? daysUntil(next.date) : null;

  return (
    <div>
      <h3 className="mb-3 text-[15px] font-semibold text-slate-900">Earnings</h3>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-xl bg-slate-50 px-3 py-2.5">
          <p className="text-[11px] font-medium text-slate-500">Next report</p>
          <p className="text-[15px] font-semibold tabular-nums text-slate-900">{next ? next.date : UNKNOWN}</p>
          <p className="text-[11px] text-slate-400">
            {days == null ? 'not scheduled' : days === 0 ? 'today' : `in ${days} days`}
            {next?.eps_estimate != null && ` · est. EPS $${next.eps_estimate.toFixed(2)}`}
          </p>
        </div>
        <div className="rounded-xl bg-slate-50 px-3 py-2.5">
          <p className="text-[11px] font-medium text-slate-500">Beat estimates</p>
          <p className="text-[15px] font-semibold tabular-nums text-slate-900">{stats.judged ? `${stats.beats} of ${stats.judged}` : UNKNOWN}</p>
          <p className="text-[11px] text-slate-400">last reports</p>
        </div>
        <div className="rounded-xl bg-slate-50 px-3 py-2.5">
          <p className="text-[11px] font-medium text-slate-500">Avg move on earnings</p>
          <p className="text-[15px] font-semibold tabular-nums text-slate-900">{stats.avgAbsMove != null ? `±${stats.avgAbsMove.toFixed(1)}%` : UNKNOWN}</p>
          <p className="text-[11px] text-slate-400">2-day window</p>
        </div>
        <div className="rounded-xl bg-slate-50 px-3 py-2.5">
          <p className="text-[11px] font-medium text-slate-500">Reaction direction</p>
          <p className="text-[15px] font-semibold tabular-nums text-slate-900">
            {stats.moves ? (
              <>
                <span className="text-emerald-600">{stats.ups} up</span> · <span className="text-red-600">{stats.downs} down</span>
              </>
            ) : (
              UNKNOWN
            )}
          </p>
          <p className="text-[11px] text-slate-400">of {stats.moves || 0} with price data</p>
        </div>
      </div>

      {past.length > 0 ? (
        <Table
          removeWrapper
          aria-label="Past earnings"
          className="mt-4"
          classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8', td: 'text-[13px] py-2 tabular-nums' }}
        >
          <TableHeader>
            <TableColumn>REPORT DATE</TableColumn>
            <TableColumn align="end">EPS ESTIMATE</TableColumn>
            <TableColumn align="end">EPS ACTUAL</TableColumn>
            <TableColumn align="end">SURPRISE</TableColumn>
            <TableColumn>RESULT</TableColumn>
            <TableColumn align="end">PRICE REACTION</TableColumn>
          </TableHeader>
          <TableBody>
            {past.map((e) => (
              <TableRow key={e.date}>
                <TableCell className="text-slate-700">{e.date}</TableCell>
                <TableCell className="text-right">{e.eps_estimate != null ? `$${e.eps_estimate.toFixed(2)}` : UNKNOWN}</TableCell>
                <TableCell className="text-right font-semibold text-slate-800">{e.eps_actual != null ? `$${e.eps_actual.toFixed(2)}` : UNKNOWN}</TableCell>
                <TableCell className={clsx('text-right', e.surprise_pct == null ? '' : e.surprise_pct >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                  {e.surprise_pct != null ? `${e.surprise_pct >= 0 ? '+' : ''}${e.surprise_pct.toFixed(1)}%` : UNKNOWN}
                </TableCell>
                <TableCell>
                  {e.beat == null ? (
                    <span className="text-slate-400">{UNKNOWN}</span>
                  ) : (
                    <Chip size="sm" className={`text-xs ${e.beat ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-700'}`}>
                      {e.beat ? 'Beat' : 'Miss'}
                    </Chip>
                  )}
                </TableCell>
                <TableCell className={clsx('text-right font-semibold', e.reaction == null ? 'text-slate-400' : e.reaction >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                  {e.reaction != null ? `${e.reaction >= 0 ? '+' : ''}${e.reaction.toFixed(1)}%` : UNKNOWN}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <p className="mt-4 text-sm text-slate-400">No past earnings reports on record.</p>
      )}
      <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
        Price reaction = close before the report to close the day after (covers both before-open and after-close reports;
        the report time isn&apos;t recorded yet).
      </p>
    </div>
  );
}
