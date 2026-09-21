'use client';

import React from 'react';
import { Chip, Spinner } from '@nextui-org/react';
import LightweightCandlestickChart from '@/components/charts/LightweightCandlestickChart';
import { HoverStockSummary } from '@/hooks/useHoverStockSummary';
import { positionPct, pctChange } from '@/lib/priceMath';
import { confidenceColor } from '@/lib/uiColors';

export interface StockHoverCardProps {
  symbol: string;
  data: HoverStockSummary;
}

export default function StockHoverCard({ symbol, data }: StockHoverCardProps) {
  // Each piece renders as soon as it arrives (see HoverStockSummary): the
  // chart never waits on the signal chip or the slow earnings markers.
  const barsLoading = data.bars === null;
  const signalLoaded = data.signal !== undefined;
  const bars = data.bars ?? [];
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];

  const currentPrice = last?.close;
  const changePct = pctChange(last?.close, prev?.close);
  const channelPos = positionPct(
    currentPrice ?? undefined,
    last?.donchian_low_20 ?? undefined,
    last?.donchian_high_20 ?? undefined
  );

  return (
    <div className="w-80 p-1">
      <div className="flex justify-between items-center mb-2">
        <p className="font-bold text-slate-800 text-sm">{symbol}</p>
        {data.signal?.has_signal ? (
          <Chip size="sm" className={`text-xs ${confidenceColor(data.signal.ml_confidence)}`}>
            {data.signal.ml_confidence?.replace('_', ' ') || 'signal'}
          </Chip>
        ) : (
          signalLoaded && (
            <Chip size="sm" className="bg-slate-100 text-slate-500 text-xs">
              no active signal
            </Chip>
          )
        )}
      </div>

      {barsLoading ? (
        <div className="h-[110px] flex items-center justify-center">
          <Spinner size="sm" />
        </div>
      ) : bars.length === 0 ? (
        <div className="h-[110px] flex items-center justify-center">
          <p className="text-xs text-slate-400">No price history available</p>
        </div>
      ) : (
        <>
          <div className="flex justify-between items-baseline mb-1">
            <p className="text-lg font-bold text-slate-800">
              {currentPrice != null ? `$${currentPrice.toFixed(2)}` : 'N/A'}
            </p>
            {changePct != null && (
              <p className={`text-sm font-semibold ${changePct >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                {changePct >= 0 ? '+' : ''}
                {changePct.toFixed(2)}%
              </p>
            )}
          </div>
          <LightweightCandlestickChart
            bars={bars}
            earnings={data.earnings}
            variant="mini"
            height={110}
            showVolume={false}
          />
          <p className="text-xs text-slate-500 mt-1">Channel position: {channelPos.toFixed(0)}%</p>
        </>
      )}
    </div>
  );
}
