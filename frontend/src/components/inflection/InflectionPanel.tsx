'use client';

import React, { useEffect, useState } from 'react';
import { clsx } from 'clsx';
import { inflectionApi, InflectionStockResponse } from '@/services/inflectionApi';
import { DASH, fmtPct } from '@/lib/inflectionFormat';
import EventCard from '@/components/inflection/EventCard';
import TrendCharts from '@/components/inflection/TrendCharts';

function Compare({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-2.5 py-2">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className={clsx('text-sm font-semibold tabular-nums', tone ?? 'text-slate-800')}>{value}</p>
      {sub && <p className="text-[11px] text-slate-400">{sub}</p>}
    </div>
  );
}

const tone = (v: number | null | undefined) => (v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-600' : v < 0 ? 'text-red-600' : 'text-slate-800');

/** The stock's fundamental-inflection view: how the business is changing next to how the price has responded, why it was flagged, and the quarterly trend. */
export default function InflectionPanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<InflectionStockResponse | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    inflectionApi
      .stock(symbol)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setState('ready');
        }
      })
      .catch(() => {
        if (!cancelled) setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const latest = data?.trend[data.trend.length - 1];
  const top = data?.events[0];

  return (
    <section aria-labelledby="inflection-h" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="inflection-h" className="text-[15px] font-semibold text-slate-900">Fundamental inflection</h3>
        <span className="rounded bg-slate-900 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">Research signal</span>
      </div>

      {state === 'loading' && <p className="text-sm text-slate-400">Loading…</p>}
      {state === 'error' && <p className="text-sm text-red-500">The inflection data could not be loaded.</p>}
      {state === 'ready' && data && !data.available && (
        <p className="text-sm text-slate-500">
          {data.reason ?? 'No fundamentals have been analysed for this stock yet.'} Nothing is shown rather than a guess.
        </p>
      )}

      {state === 'ready' && data?.available && latest && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Compare label="Inflection score" value={data.latest_score != null ? data.latest_score.toFixed(0) : DASH} sub={`quarter ending ${data.latest_quarter_end}`} />
            <Compare label="Revenue growth (YoY)" value={fmtPct(latest.rev_yoy)} tone={tone(latest.rev_yoy)} sub={latest.rev_accel_pp != null ? `${latest.rev_accel_pp > 0 ? '+' : ''}${latest.rev_accel_pp.toFixed(1)} pp vs the quarter before` : undefined} />
            <Compare label="Price, 2 sessions around the report" value={fmtPct(top?.reaction_2d ?? null)} tone={tone(top?.reaction_2d)} sub={top ? `after ${top.label.toLowerCase()}` : 'no inflection event to measure'} />
            <Compare label="Price vs sector since" value={fmtPct(top?.excess_vs_sector_since_publication ?? null)} tone={tone(top?.excess_vs_sector_since_publication)} sub={top ? `since ${top.available_at}` : undefined} />
          </div>
          <p className="text-xs text-slate-500">
            {top
              ? top.excess_vs_sector_since_publication != null && top.excess_vs_sector_since_publication <= 0.03
                ? 'The business improved and the stock has not outperformed its sector since: the improvement is not obviously in the price yet.'
                : 'The business improved and the stock has outperformed its sector since the figures became public.'
              : 'No inflection event is flagged for the latest quarter; the trend below is the stored history.'}
          </p>

          {data.events.length > 0 && (
            <div className="space-y-2">
              {data.events.slice(0, 4).map((e) => (
                <EventCard key={e.id} e={e} showStockLink={false} />
              ))}
            </div>
          )}

          <TrendCharts trend={data.trend} />
          <p className="text-[11px] leading-relaxed text-slate-400">
            {data.data_status ? `${data.data_status.quarters} quarters stored (${data.data_status.partial_quarters} with partial data), source ${data.data_status.source ?? DASH}. ` : ''}
            {data.notice}
          </p>
        </>
      )}
    </section>
  );
}
