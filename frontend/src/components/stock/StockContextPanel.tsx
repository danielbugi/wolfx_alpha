// File: frontend/src/components/stock/StockContextPanel.tsx
'use client';

import React, { useEffect, useState } from 'react';
import { clsx } from 'clsx';
import { stockContextApi, StockContext } from '@/services/stockContextApi';
import { screenerV2Api, MetricDefinition, ScreenerDefinitions, ScreenerRow } from '@/services/screenerV2Api';
import DataStamp from '@/components/common/DataStamp';
import { ScoreBadge, ScoreBreakdown } from '@/components/screener/StrengthScore';
import { FAMILY_LABEL, formatCell, formatValue } from '@/lib/screenerV2';

const FAMILY_TONE = { observed: 'bg-sky-50 text-sky-700', derived: 'bg-slate-100 text-slate-600', reference: 'bg-amber-50 text-amber-700' } as const;

type Item = { key: keyof ScreenerRow; caption?: (v: unknown) => string | null };

/** Plain-language one-liners (neutral, descriptive). Missing values get no caption. */
const pctlCaption = (window: string) => (v: unknown) =>
  typeof v === 'number' ? `${window}-session return higher than ${Math.round(v)}% of stocks` : null;

const GROUPS: { title: string; blurb: string; items: Item[] }[] = [
  {
    title: 'Relative strength',
    blurb: 'How this stock moved compared with every other stock, the S&P 500 and its sector.',
    items: [
      { key: 'rs_20', caption: pctlCaption('20') },
      { key: 'rs_60', caption: pctlCaption('60') },
      { key: 'ret_5' },
      { key: 'ret_20' },
      { key: 'ret_60' },
      { key: 'vs_spx_20' },
      { key: 'vs_spx_60' },
      { key: 'vs_sector_20' },
    ],
  },
  {
    title: 'Trend and channel',
    blurb: 'Where the close sits against its moving averages and its 20-day high-low channel.',
    items: [
      { key: 'dist_sma20' },
      { key: 'dist_sma50' },
      { key: 'channel_pos', caption: (v) => (typeof v === 'number' ? (v >= 80 ? 'near the top of its 20-day range' : v <= 20 ? 'near the bottom of its 20-day range' : 'inside its 20-day range') : null) },
      { key: 'breakout_20' },
      { key: 'new_high_20' },
    ],
  },
  {
    title: 'Volatility and liquidity',
    blurb: 'How much the stock typically moves in a day and how much of it trades.',
    items: [
      { key: 'atr_pct' },
      { key: 'atr_pct_pctile', caption: (v) => (typeof v === 'number' ? `more volatile than ${Math.round(v)}% of stocks` : null) },
      { key: 'channel_width_pctile', caption: (v) => (typeof v === 'number' ? (v <= 20 ? 'one of the tighter ranges' : v >= 80 ? 'one of the wider ranges' : null) : null) },
      { key: 'volume_ratio' },
      { key: 'dollar_vol_20' },
    ],
  },
];

function Metric({ def, row, item }: { def?: MetricDefinition; row: ScreenerRow; item: Item }) {
  const text = formatCell(row, item.key, def?.unit ?? '');
  const v = row[item.key];
  const signed = def?.unit === '%' || def?.unit === 'pp';
  const caption = item.caption ? item.caption(v) : null;
  const shortfall = item.key === 'dollar_vol_20' && v == null ? `${row.dv_bars} of 20 sessions with bars` : null;
  return (
    <div className="flex items-start justify-between gap-3 py-1.5" title={def ? `${def.label} (${FAMILY_LABEL[def.family].toLowerCase()}): ${def.definition}` : undefined} data-testid={`ctx-${String(item.key)}`}>
      <div className="min-w-0">
        <p className="text-[13px] text-slate-700">{def?.label ?? String(item.key)}</p>
        {(caption || shortfall) && <p className="text-[11px] text-slate-400">{caption ?? shortfall}</p>}
      </div>
      <p
        className={clsx(
          'shrink-0 text-[14px] font-semibold tabular-nums',
          text === '—' ? 'text-slate-400' : signed && typeof v === 'number' && !['channel_pos', 'atr_pct', 'channel_width'].includes(String(item.key)) ? (v >= 0 ? 'text-emerald-600' : 'text-red-600') : 'text-slate-900',
        )}
      >
        {text}
      </p>
    </div>
  );
}

/**
 * Stock Context: the Smart Screener v2 metrics for this stock (the same query, session and provenance as the Screener),
 * grouped and explained in plain language, plus the stock-only 200-session average. The discovery session is shown
 * separately from the chart's newest bar, because the chart can be a session ahead of the captured metrics.
 */
export default function StockContextPanel({ symbol, chartDate }: { symbol: string; chartDate: string | null }) {
  const [ctx, setCtx] = useState<StockContext | null>(null);
  const [defs, setDefs] = useState<ScreenerDefinitions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCtx(null);
    setError(null);
    stockContextApi
      .get(symbol)
      .then((c) => !cancelled && setCtx(c))
      .catch((e) => {
        if (cancelled) return;
        setError(e?.response?.status === 404 ? 'Stock context is not available on this server yet.' : 'Stock context is unavailable right now.');
      });
    screenerV2Api.definitions().then((d) => !cancelled && setDefs(d)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const box = 'rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm';
  if (error) {
    return (
      <section className={box} data-testid="ctx-error">
        <h3 className="text-[15px] font-semibold text-slate-900">Stock context</h3>
        <p className="mt-1 text-sm text-slate-500">{error}</p>
      </section>
    );
  }
  if (!ctx) {
    return <div className="h-56 animate-pulse rounded-2xl bg-black/[0.04]" aria-busy="true" />;
  }
  const def = (k: string) => defs?.metrics.find((m) => m.key === k);
  const chartAhead = !!chartDate && chartDate > ctx.session;
  const ma = ctx.stock_only.dist_sma200;
  return (
    <section className={box} aria-labelledby="ctx-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="ctx-title" className="text-[17px] font-semibold text-slate-900">
          Stock context
        </h3>
        <DataStamp label="Metrics for session" asOf={ctx.session} source="same data as the Screener" />
      </div>
      {chartAhead && (
        <p className="mt-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="ctx-chart-ahead">
          The chart shows prices to {chartDate}; these metrics describe the {ctx.session} session (the newest one with captured metrics).
        </p>
      )}
      {!ctx.in_universe || !ctx.row ? (
        <p className="mt-3 text-sm text-slate-500" data-testid="ctx-not-in-universe">
          {ctx.unavailable_reason ?? 'No data for this stock on the session.'} Metrics are not taken from another session.
        </p>
      ) : (
        <>
          {ctx.coverage.rs === 0 && (
            <p className="mt-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="ctx-no-rs">
              No observed relative-strength snapshot exists for this session, so relative strength and returns are unavailable.
            </p>
          )}
          <div className="mt-3 grid grid-cols-1 items-center gap-4 rounded-xl bg-slate-50 p-3 sm:grid-cols-[auto_minmax(0,1fr)]" data-testid="ctx-strength">
            <div className="flex items-center gap-3">
              <ScoreBadge row={ctx.row} size="lg" />
              <div>
                <p className="text-[13px] font-semibold text-slate-900">Strength Score</p>
                <p className="text-[11px] text-slate-500">0-100 from this session&apos;s data · describes the session only</p>
              </div>
            </div>
            <ScoreBreakdown row={ctx.row} />
          </div>
          <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-3">
            {GROUPS.map((g) => (
              <div key={g.title} className="min-w-0">
                <h4 className="text-[13px] font-semibold text-slate-900">{g.title}</h4>
                <p className="mb-1 text-[11px] text-slate-400">{g.blurb}</p>
                <div className="divide-y divide-black/[0.05]">
                  {g.items.map((it) => (
                    <Metric key={String(it.key)} def={def(String(it.key))} row={ctx.row!} item={it} />
                  ))}
                  {g.title === 'Trend and channel' && ma && (
                    <div className="flex items-start justify-between gap-3 py-1.5" title={ma.definition} data-testid="ctx-dist_sma200">
                      <div className="min-w-0">
                        <p className="text-[13px] text-slate-700">
                          {ma.label} <span className="text-[10px] text-slate-400">(this page only)</span>
                        </p>
                        <p className="text-[11px] text-slate-400">
                          {ma.state === 'ok' ? `${ma.window_from} to ${ma.window_to}, 200 of 200 sessions` : ma.reason}
                        </p>
                      </div>
                      <p className={clsx('shrink-0 text-[14px] font-semibold tabular-nums', ma.value == null ? 'text-slate-400' : ma.value >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                        {formatValue(ma.value, '%')}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t border-black/[0.05] pt-3 text-[11px] text-slate-400">
            {(['observed', 'derived', 'reference'] as const).map((f) => (
              <span key={f} className="inline-flex items-center gap-1.5">
                <span className={clsx('rounded-full px-1.5 py-0.5 font-semibold', FAMILY_TONE[f])}>{FAMILY_LABEL[f]}</span>
                {defs?.families[f]}
              </span>
            ))}
            <span data-testid="ctx-fundamentals">
              Market cap {formatValue(ctx.row.market_cap, '$')}
              {ctx.row.fundamentals_ingested_at ? ` (row ${ctx.row.fundamentals_date}, ingested ${ctx.row.fundamentals_ingested_at}${ctx.row.fundamentals_after_close ? ', after the close' : ''})` : ''} ·{' '}
              {ctx.fundamentals_basis.note}
            </span>
          </div>
        </>
      )}
    </section>
  );
}
