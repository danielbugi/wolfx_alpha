'use client';

import React from 'react';
import { Card, CardBody } from '@nextui-org/react';
import { clsx } from 'clsx';
import type { MarketIndexEntry, MarketOverview } from '@/services/api';

const UNKNOWN = '—';

const REGIME: Record<string, { label: string; className: string; blurb: string }> = {
  calm: { label: 'Calm', className: 'bg-emerald-100 text-emerald-800', blurb: 'low fear' },
  normal: { label: 'Normal', className: 'bg-sky-100 text-sky-800', blurb: 'typical volatility' },
  elevated: { label: 'Elevated', className: 'bg-amber-100 text-amber-800', blurb: 'nervous market' },
  fear: { label: 'Fear', className: 'bg-red-100 text-red-700', blurb: 'high fear' },
};

function Tile({ label, children, foot }: { label: string; children: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <Card className="border border-black/[0.05] shadow-sm">
      <CardBody className="p-5">
        <p className="text-[13px] font-medium text-slate-500">{label}</p>
        <div className="mt-1">{children}</div>
        {foot && <div className="mt-1.5 text-xs text-slate-400">{foot}</div>}
      </CardBody>
    </Card>
  );
}

const big = 'text-[28px] font-semibold leading-tight tracking-[-0.02em] tabular-nums text-slate-900';

/**
 * The overview's first read: how broad the move is, the market's mood (VIX), and which sectors lead and lag today.
 * Each tile shows "—" until its data arrives; nothing is defaulted. The former "Confirmed today" tile (a count of
 * names on 2+ Momentum Board lists) is signal output and now lives with the board on the internal /momentum page.
 */
export default function SummaryTiles({
  overview,
  indices,
}: {
  overview: MarketOverview | null;
  indices: MarketIndexEntry[];
}) {
  const breadth = overview?.market_breadth;
  const up = breadth?.gainers ?? null;
  const down = breadth?.losers ?? null;
  const upShare = up != null && down != null && up + down > 0 ? (up / (up + down)) * 100 : null;

  const vix = indices.find((i) => i.symbol === '^VIX');
  const regime = vix?.regime ? REGIME[vix.regime] : null;

  const sectors = [...(overview?.sector_performance ?? [])].filter((s) => s.stock_count >= 5).sort((a, b) => b.avg_performance - a.avg_performance);
  const best = sectors[0];
  const worst = sectors.length > 1 ? sectors[sectors.length - 1] : undefined;
  const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;

  return (
    <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3 sm:gap-4">
      <Tile
        label="Market breadth"
        foot={breadth ? `A/D ratio ${breadth.advance_decline_ratio.toFixed(2)} · ${breadth.total_stocks.toLocaleString()} stocks` : undefined}
      >
        <p className={big}>{upShare != null ? `${upShare.toFixed(0)}% up` : UNKNOWN}</p>
        {up != null && down != null && (
          <>
            <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-red-400/80" aria-hidden>
              <div className="h-full bg-emerald-500" style={{ width: `${upShare ?? 0}%` }} />
            </div>
            <p className="mt-1 flex justify-between text-[11px] tabular-nums">
              <span className="text-emerald-600">{up.toLocaleString()} up</span>
              <span className="text-red-600">{down.toLocaleString()} down</span>
            </p>
          </>
        )}
      </Tile>

      <Tile label="Market mood (VIX)" foot={regime ? regime.blurb : undefined}>
        <div className="flex items-baseline gap-2">
          <p className={big}>{vix?.close != null ? vix.close.toFixed(1) : UNKNOWN}</p>
          {regime && <span className={clsx('rounded-full px-2 py-0.5 text-xs font-semibold', regime.className)}>{regime.label}</span>}
        </div>
        {vix?.change_pct != null && (
          <p className={clsx('mt-1 text-xs font-semibold tabular-nums', vix.change_pct > 0 ? 'text-red-600' : 'text-emerald-600')}>
            {pct(vix.change_pct)} today
          </p>
        )}
      </Tile>

      <Tile label="Sectors today" foot={worst ? <>Weakest: {worst.sector} <span className="tabular-nums text-red-500">{pct(worst.avg_performance)}</span></> : undefined}>
        {best ? (
          <>
            <p className="truncate text-[19px] font-semibold leading-tight text-slate-900" title={best.sector}>
              {best.sector}
            </p>
            <p className={clsx('text-sm font-semibold tabular-nums', best.avg_performance >= 0 ? 'text-emerald-600' : 'text-red-600')}>
              {pct(best.avg_performance)} · strongest
            </p>
          </>
        ) : (
          <p className={big}>{UNKNOWN}</p>
        )}
      </Tile>
    </div>
  );
}
