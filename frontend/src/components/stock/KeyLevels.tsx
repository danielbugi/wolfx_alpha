'use client';

import React, { useMemo } from 'react';
import { clsx } from 'clsx';
import type { PriceBar, SectorHistoryEntry } from '@/services/api';
import { sma } from '@/lib/indicators';
import { ProgressBar } from '@/components/ui/Field';

const UNKNOWN = '—';
const TRADING_DAYS_YEAR = 252;

function pct(v: number | null, d = 1) {
  return v == null ? UNKNOWN : `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;
}
function money(v: number | null | undefined, d = 2) {
  return v == null ? UNKNOWN : `$${v.toFixed(d)}`;
}

function Row({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: 'up' | 'down' | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-right">
        <span className={clsx('text-[13px] font-semibold tabular-nums', tone === 'up' ? 'text-emerald-600' : tone === 'down' ? 'text-red-600' : 'text-slate-900')}>
          {value}
        </span>
        {sub && <span className="ml-1.5 text-[11px] tabular-nums text-slate-400">{sub}</span>}
      </span>
    </div>
  );
}

/**
 * Where price stands against the levels a trader watches: 52-week range, the 20-day channel (the breakout level),
 * the 50/200-day averages, volume vs normal, ATR, and the last 90 days against its sector. All derived from the daily
 * bars already loaded (and the dashboard's sector history); anything without enough data shows "—".
 */
export default function KeyLevels({
  bars,
  price,
  atr,
  sector,
  sectorHistory,
}: {
  bars: PriceBar[];
  price: number | null | undefined;
  atr: number | null | undefined;
  sector: string | null | undefined;
  sectorHistory: SectorHistoryEntry[] | null;
}) {
  const m = useMemo(() => {
    const last = bars[bars.length - 1];
    const px = price ?? last?.close ?? null;
    const year = bars.slice(-TRADING_DAYS_YEAR);
    const fullYear = year.length >= TRADING_DAYS_YEAR * 0.95; // a few holidays short is still a year
    const highs = year.map((b) => b.high).filter((v): v is number => v != null);
    const lows = year.map((b) => b.low).filter((v): v is number => v != null);
    const hi52 = fullYear && highs.length ? Math.max(...highs) : null;
    const lo52 = fullYear && lows.length ? Math.min(...lows) : null;

    const closes = bars.map((b) => b.close);
    const sma50 = sma(closes, 50).at(-1) ?? null;
    const sma200 = sma(closes, 200).at(-1) ?? null;

    const vols = bars.slice(-51, -1).map((b) => b.volume);
    const avgVol50 = vols.length === 50 && vols.every((v) => v != null) ? (vols as number[]).reduce((a, v) => a + v, 0) / 50 : null;
    const relVol = avgVol50 && last?.volume != null ? last.volume / avgVol50 : null;

    // 90 days vs sector: compound the sector's daily average changes over its window, and take the stock's close
    // from the bar before that window starts to the window's last date.
    let stock90: number | null = null;
    let sector90: number | null = null;
    const hist = sectorHistory?.find((s) => s.sector === sector)?.history ?? [];
    if (hist.length >= 20) {
      const first = hist[0].date;
      const lastDate = hist[hist.length - 1].date;
      sector90 = (hist.reduce((idx, p) => idx * (1 + p.avg_performance / 100), 1) - 1) * 100;
      const startIdx = bars.findIndex((b) => b.date >= first) - 1;
      const end = [...bars].reverse().find((b) => b.date <= lastDate);
      if (startIdx >= 0 && end) stock90 = (end.close / bars[startIdx].close - 1) * 100;
    }

    return { last, px, hi52, lo52, sma50, sma200, avgVol50, relVol, stock90, sector90 };
  }, [bars, price, sector, sectorHistory]);

  const { last, px, hi52, lo52, sma50, sma200, avgVol50, relVol, stock90, sector90 } = m;
  const rangePos = px != null && hi52 != null && lo52 != null && hi52 > lo52 ? ((px - lo52) / (hi52 - lo52)) * 100 : null;
  const fromHigh = px != null && hi52 != null ? (px / hi52 - 1) * 100 : null;
  const dcHigh = last?.donchian_high_20 ?? null;
  const dcLow = last?.donchian_low_20 ?? null;
  const toBreakout = px != null && dcHigh != null ? (dcHigh / px - 1) * 100 : null;
  const vs = (level: number | null) => (px != null && level != null ? (px / level - 1) * 100 : null);

  return (
    <div>
      <h3 className="mb-3 text-[15px] font-semibold text-slate-900">Key levels</h3>

      <div className="mb-3 rounded-xl bg-slate-50 p-3">
        <div className="mb-1.5 flex items-baseline justify-between text-xs">
          <span className="text-slate-500">52-week range</span>
          <span className={clsx('font-semibold tabular-nums', fromHigh == null ? 'text-slate-400' : 'text-slate-700')}>
            {fromHigh == null ? UNKNOWN : fromHigh >= -0.05 ? 'at the high' : `${fromHigh.toFixed(1)}% from high`}
          </span>
        </div>
        <ProgressBar value={rangePos ?? 0} className={clsx(rangePos == null && 'opacity-40')} barClassName="bg-accent" />
        <div className="mt-1 flex justify-between text-[11px] tabular-nums text-slate-500">
          <span>{money(lo52)}</span>
          <span>{money(hi52)}</span>
        </div>
      </div>

      <div className="divide-y divide-black/[0.06]">
        <Row label="20-day channel (breakout level)" value={`${money(dcLow)} – ${money(dcHigh)}`} sub={toBreakout != null ? (toBreakout <= 0 ? 'at/above high' : `${toBreakout.toFixed(1)}% to high`) : undefined} />
        <Row label="vs 50-day average" value={pct(vs(sma50))} sub={money(sma50)} tone={vs(sma50) == null ? null : (vs(sma50) as number) >= 0 ? 'up' : 'down'} />
        <Row
          label="vs 200-day average"
          value={pct(vs(sma200))}
          sub={money(sma200)}
          tone={vs(sma200) == null ? null : (vs(sma200) as number) >= 0 ? 'up' : 'down'}
        />
        {sma50 != null && sma200 != null && (
          <Row label="50 vs 200-day trend" value={sma50 > sma200 ? 'Uptrend (50 above 200)' : 'Downtrend (50 below 200)'} tone={sma50 > sma200 ? 'up' : 'down'} />
        )}
        <Row
          label="Volume vs 50-day average"
          value={relVol == null ? UNKNOWN : `${relVol.toFixed(1)}×`}
          sub={avgVol50 != null ? `avg ${(avgVol50 / 1e6).toFixed(1)}M` : undefined}
          tone={relVol != null && relVol >= 1.5 ? 'up' : null}
        />
        <Row label="ATR (14d)" value={money(atr)} sub={atr != null && px ? `${((atr / px) * 100).toFixed(1)}% of price` : undefined} />
        <Row
          label={`90 days vs ${sector || 'sector'}`}
          value={pct(stock90)}
          sub={sector90 != null ? `sector ${pct(sector90)}` : undefined}
          tone={stock90 != null && sector90 != null ? (stock90 >= sector90 ? 'up' : 'down') : null}
        />
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
        Sector = equal-weighted average of the stocks we track in it. Levels from daily closes.
      </p>
    </div>
  );
}
