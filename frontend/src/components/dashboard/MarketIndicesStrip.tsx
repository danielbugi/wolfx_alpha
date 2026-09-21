'use client';

// File: frontend/src/components/dashboard/MarketIndicesStrip.tsx
// Macro strip: real index/commodity/rates data (S&P 500, Nasdaq, Russell
// 2000, Dow, VIX, 10Y yield, Gold, Crude, DXY, BTC) -- backed by
// market_index_prices via /api/market/indices, not the internal stock
// universe. Sits above everything else on the dashboard: traders orient
// top-down (macro regime -> sector -> single name), see CLAUDE.md's
// 2026-09-19 dashboard data-representation review.

import React from 'react';
import { Card, CardBody } from '@nextui-org/react';
import { ResponsiveContainer, AreaChart, Area } from 'recharts';
import { MarketIndexEntry } from '@/services/api';

const POSITIVE = '#059669'; // emerald-600, matches sectorColor()/top-gainers styling elsewhere
const NEGATIVE = '#dc2626'; // red-600, matches sectorColor()/top-losers styling elsewhere

const VIX_REGIME_STYLE: Record<string, { label: string; className: string }> = {
  calm: { label: 'Calm', className: 'bg-emerald-100 text-emerald-800' },
  normal: { label: 'Normal', className: 'bg-cyan-100 text-cyan-800' },
  elevated: { label: 'Elevated', className: 'bg-amber-100 text-amber-800' },
  fear: { label: 'Fear', className: 'bg-red-100 text-red-700' },
};

function formatClose(symbol: string, close: number | null): string {
  if (close === null) return '—';
  if (symbol === '^TNX') return `${close.toFixed(2)}%`;
  if (symbol === '^VIX') return close.toFixed(2);
  return close.toLocaleString('en-US', { maximumFractionDigits: close >= 1000 ? 0 : 2 });
}

function IndexCard({ entry }: { entry: MarketIndexEntry }) {
  const isUp = (entry.change_pct ?? 0) >= 0;
  const color = isUp ? POSITIVE : NEGATIVE;
  const sparklineData = entry.sparkline.map((value, i) => ({ i, value }));
  const regime = entry.regime ? VIX_REGIME_STYLE[entry.regime] : null;

  return (
    <div className="flex flex-col min-w-[128px] px-3 py-2 border-r border-slate-200 last:border-r-0">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-slate-700 truncate">{entry.display_name}</span>
        {regime && (
          <span className={`text-[9px] font-medium px-1.5 py-0.5 rounded ${regime.className}`}>
            {regime.label}
          </span>
        )}
      </div>
      <div className="flex items-end justify-between gap-2 mt-1">
        <div>
          <p className="text-sm font-bold text-slate-800 leading-tight">{formatClose(entry.symbol, entry.close)}</p>
          {entry.change_pct !== null && (
            <p className="text-[11px] font-semibold leading-tight" style={{ color }}>
              {isUp ? '+' : ''}
              {entry.change_pct.toFixed(2)}%
            </p>
          )}
        </div>
        {sparklineData.length > 1 && (
          <div className="w-16 h-8">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={sparklineData} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id={`spark-${entry.symbol}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={color} stopOpacity={0.25} />
                    <stop offset="100%" stopColor={color} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke={color}
                  strokeWidth={1.5}
                  fill={`url(#spark-${entry.symbol})`}
                  isAnimationActive={false}
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}

export default function MarketIndicesStrip({
  indices,
  loading,
  error,
}: {
  indices: MarketIndexEntry[];
  loading: boolean;
  error: boolean;
}) {
  if (loading && indices.length === 0) {
    return (
      <Card className="mb-4 shadow-sm border border-slate-200">
        <CardBody className="p-3">
          <div className="h-16 flex items-center justify-center text-xs text-slate-400">
            Loading market data…
          </div>
        </CardBody>
      </Card>
    );
  }

  if (error && indices.length === 0) {
    return null; // Non-critical strip -- fail quietly rather than push an ErrorAlert above the header.
  }

  return (
    <Card className="mb-4 shadow-sm border border-slate-200">
      <CardBody className="p-0">
        <div className="flex overflow-x-auto">
          {indices.map((entry) => (
            <IndexCard key={entry.symbol} entry={entry} />
          ))}
        </div>
      </CardBody>
    </Card>
  );
}
