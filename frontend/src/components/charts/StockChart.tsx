'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createChart,
  IChartApi,
  ISeriesApi,
  LogicalRange,
  SeriesMarker,
  Time,
  UTCTimestamp,
  MouseEventParams,
  LineStyle,
  CrosshairMode,
} from 'lightweight-charts';
import { clsx } from 'clsx';
import type { BreakoutHistoryEntry, EarningsDateEntry, PriceBar } from '@/services/api';
import { aggregateBars, bollinger, Interval, macd, rsi, sma, withDonchian } from '@/lib/indicators';
import { nearestBarDate } from '@/lib/priceMath';
import { useResolvedTheme } from '@/lib/theme';
import SegmentedControl from '@/components/ui/SegmentedControl';

/**
 * The stock page's main chart: daily / weekly / monthly candles built from the daily bars, overlays (SMA 20/50/200,
 * Bollinger, Donchian, volume), RSI and MACD panels underneath kept in sync with the price chart, and breakout +
 * earnings markers. All indicators are computed in the browser (lib/indicators.ts) from the bars the API returns.
 */

type Overlay = 'sma20' | 'sma50' | 'sma200' | 'bollinger' | 'donchian' | 'volume';
type PanelKey = 'rsi' | 'macd';

const OVERLAYS: { key: Overlay; label: string; color: string }[] = [
  { key: 'sma20', label: 'SMA 20', color: '#0a84ff' },
  { key: 'sma50', label: 'SMA 50', color: '#ff9f0a' },
  { key: 'sma200', label: 'SMA 200', color: '#bf5af2' },
  { key: 'bollinger', label: 'Bollinger', color: '#8e8e93' },
  { key: 'donchian', label: 'Donchian', color: '#94a3b8' },
  { key: 'volume', label: 'Volume', color: '#8e8e93' },
];
const PANELS: { key: PanelKey; label: string }[] = [
  { key: 'rsi', label: 'RSI 14' },
  { key: 'macd', label: 'MACD' },
];

// How many candles each range shows, per interval. "All" = everything loaded (the API serves up to ~2 years).
const RANGES: Record<Interval, { value: string; label: string; bars: number | null }[]> = {
  daily: [
    { value: '1m', label: '1M', bars: 21 },
    { value: '3m', label: '3M', bars: 63 },
    { value: '6m', label: '6M', bars: 126 },
    { value: '1y', label: '1Y', bars: 252 },
    { value: 'all', label: '2Y', bars: null },
  ],
  weekly: [
    { value: '6m', label: '6M', bars: 26 },
    { value: '1y', label: '1Y', bars: 52 },
    { value: 'all', label: '2Y', bars: null },
  ],
  monthly: [{ value: 'all', label: '2Y', bars: null }],
};
const DEFAULT_RANGE: Record<Interval, string> = { daily: '6m', weekly: '1y', monthly: 'all' };

const THEME = {
  light: { bg: '#ffffff', grid: '#eef0f3', text: '#737378', border: '#e3e3e8', up: '#10b981', down: '#ef4444', donchian: '#94a3b8' },
  dark: { bg: '#1c1c1e', grid: '#2a2a2d', text: '#98989f', border: '#2f2f33', up: '#30d158', down: '#ff453a', donchian: '#74747a' },
} as const;

const PRICE_SCALE_WIDTH = 64; // same on every chart so the panels line up with the price chart

function toTime(date: string): UTCTimestamp {
  return (Date.parse(`${date}T00:00:00Z`) / 1000) as UTCTimestamp;
}

/** One point per candle — a value, or a gap (whitespace) during warm-up — so every series spans the same bars and
 * the price chart and panels share logical indexes for syncing. */
function line(candles: PriceBar[], values: (number | null)[]) {
  return candles.map((c, i) => (values[i] == null ? { time: toTime(c.date) } : { time: toTime(c.date), value: values[i] as number }));
}

/** Index of the candle a dated event belongs to: daily snaps to the nearest bar; weekly/monthly to the candle whose
 * period contains the date (candles are dated by their last trading day). */
function candleIndexFor(candles: PriceBar[], date: string, interval: Interval): number {
  if (interval === 'daily') {
    const snapped = nearestBarDate(candles, date, 3);
    return snapped ? candles.findIndex((c) => c.date === snapped) : -1;
  }
  const i = candles.findIndex((c) => c.date >= date);
  if (i < 0) return -1;
  // the event must fall in this candle's period, not in a gap before it
  const prevEnd = i > 0 ? candles[i - 1].date : '';
  return date > prevEnd ? i : -1;
}

function fmt(v: number | null | undefined, d = 2) {
  return v == null ? '—' : v.toFixed(d);
}

export interface StockChartProps {
  /** Daily bars, oldest first (load the longest history available: weekly/monthly and SMA 200 need it). */
  bars: PriceBar[];
  earnings?: EarningsDateEntry[];
  breakouts?: BreakoutHistoryEntry[];
  height?: number;
}

export default function StockChart({ bars, earnings = [], breakouts = [], height = 420 }: StockChartProps) {
  const theme = useResolvedTheme();
  const [interval, setCandleInterval] = useState<Interval>('daily');
  const [range, setRange] = useState(DEFAULT_RANGE.daily);
  const [overlays, setOverlays] = useState<Set<Overlay>>(() => new Set<Overlay>(['sma20', 'sma50', 'donchian', 'volume']));
  const [panels, setPanels] = useState<Set<PanelKey>>(() => new Set<PanelKey>(['rsi']));
  const [hover, setHover] = useState<number | null>(null);

  const candles = useMemo(() => {
    const agg = aggregateBars(bars.filter((b) => b.close != null), interval);
    // Daily keeps the pipeline's stored channel; weekly/monthly get it recomputed on their own candles.
    return interval === 'daily' ? agg : withDonchian(agg, 20);
  }, [bars, interval]);

  const calc = useMemo(() => {
    const closes = candles.map((c) => c.close);
    return {
      sma20: sma(closes, 20),
      sma50: sma(closes, 50),
      sma200: sma(closes, 200),
      bb: bollinger(closes, 20, 2),
      rsi: rsi(closes, 14),
      macd: macd(closes),
    };
  }, [candles]);

  const mainRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const macdRef = useRef<HTMLDivElement>(null);
  const charts = useRef<{ main?: IChartApi; rsi?: IChartApi; macd?: IChartApi }>({});
  const series = useRef<{
    candles?: ISeriesApi<'Candlestick'>;
    volume?: ISeriesApi<'Histogram'>;
    sma20?: ISeriesApi<'Line'>;
    sma50?: ISeriesApi<'Line'>;
    sma200?: ISeriesApi<'Line'>;
    bbUpper?: ISeriesApi<'Line'>;
    bbMid?: ISeriesApi<'Line'>;
    bbLower?: ISeriesApi<'Line'>;
    dcHigh?: ISeriesApi<'Line'>;
    dcLow?: ISeriesApi<'Line'>;
    rsi?: ISeriesApi<'Line'>;
    macdHist?: ISeriesApi<'Histogram'>;
    macdLine?: ISeriesApi<'Line'>;
    macdSignal?: ISeriesApi<'Line'>;
  }>({});

  const showRsi = panels.has('rsi');
  const showMacd = panels.has('macd');

  // --- chart creation -------------------------------------------------------------------------------------------
  // The price chart is created once; each panel chart is created when its panel is switched on.
  // attribution: the library's logo stays on the price chart (its required attribution); panels don't repeat it.
  function baseOptions(container: HTMLDivElement, h: number, attribution = false) {
    const t = THEME[theme];
    return {
      width: container.clientWidth,
      height: h,
      layout: { background: { color: t.bg }, textColor: t.text, fontSize: 11, fontFamily: 'inherit', attributionLogo: attribution },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.border, minimumWidth: PRICE_SCALE_WIDTH },
      timeScale: { borderColor: t.border, rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal },
      // English dates to match the rest of the UI (the default follows the browser's language).
      localization: { locale: 'en-US' },
    };
  }

  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const registry = charts.current;
    const chart = createChart(el, baseOptions(el, height, true));
    const addLine = (color: string, width: 1 | 2 = 2, style: LineStyle = LineStyle.Solid) =>
      chart.addLineSeries({ color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const s = series.current;
    s.volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    s.bbUpper = addLine('#8e8e93', 1, LineStyle.Dashed);
    s.bbMid = addLine('#8e8e93', 1, LineStyle.Dotted);
    s.bbLower = addLine('#8e8e93', 1, LineStyle.Dashed);
    s.dcHigh = addLine(THEME[theme].donchian, 1, LineStyle.Dashed);
    s.dcLow = addLine(THEME[theme].donchian, 1, LineStyle.Dashed);
    s.candles = chart.addCandlestickSeries({ borderVisible: false });
    s.sma20 = addLine('#0a84ff');
    s.sma50 = addLine('#ff9f0a');
    s.sma200 = addLine('#bf5af2');
    charts.current.main = chart;

    const onMove = (p: MouseEventParams) => setHover(p.logical == null || p.point == null ? null : Math.round(p.logical));
    chart.subscribeCrosshairMove(onMove);
    const ro = new ResizeObserver((e) => chart.applyOptions({ width: e[0].contentRect.width }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      registry.main = undefined;
      series.current = {};
    };
    // created once; theme/height changes are applied below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!showRsi || !rsiRef.current) return;
    const el = rsiRef.current;
    const registry = charts.current;
    const chart = createChart(el, baseOptions(el, 110));
    series.current.rsi = chart.addLineSeries({ color: '#bf5af2', lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
    for (const level of [70, 30]) {
      series.current.rsi.createPriceLine({ price: level, color: THEME[theme].donchian, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: '' });
    }
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });
    charts.current.rsi = chart;
    const ro = new ResizeObserver((e) => chart.applyOptions({ width: e[0].contentRect.width }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      registry.rsi = undefined;
      series.current.rsi = undefined;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showRsi]);

  useEffect(() => {
    if (!showMacd || !macdRef.current) return;
    const el = macdRef.current;
    const registry = charts.current;
    const chart = createChart(el, baseOptions(el, 120));
    series.current.macdHist = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    series.current.macdLine = chart.addLineSeries({ color: '#0a84ff', lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    series.current.macdSignal = chart.addLineSeries({ color: '#ff9f0a', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    charts.current.macd = chart;
    const ro = new ResizeObserver((e) => chart.applyOptions({ width: e[0].contentRect.width }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      registry.macd = undefined;
      series.current.macdHist = undefined;
      series.current.macdLine = undefined;
      series.current.macdSignal = undefined;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showMacd]);

  // --- theme -------------------------------------------------------------------------------------------------------
  useEffect(() => {
    const t = THEME[theme];
    for (const c of Object.values(charts.current)) {
      c?.applyOptions({
        layout: { background: { color: t.bg }, textColor: t.text },
        grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
        rightPriceScale: { borderColor: t.border },
        timeScale: { borderColor: t.border },
      });
    }
    series.current.candles?.applyOptions({ upColor: t.up, downColor: t.down, wickUpColor: t.up, wickDownColor: t.down });
    series.current.dcHigh?.applyOptions({ color: t.donchian });
    series.current.dcLow?.applyOptions({ color: t.donchian });
  }, [theme, showRsi, showMacd]);

  // Only the bottom-most chart shows dates, so the stack reads as one chart.
  useEffect(() => {
    const bottom = showMacd ? 'macd' : showRsi ? 'rsi' : 'main';
    (['main', 'rsi', 'macd'] as const).forEach((k) => charts.current[k]?.applyOptions({ timeScale: { visible: k === bottom } }));
  }, [showRsi, showMacd]);

  // --- data ----------------------------------------------------------------------------------------------------------
  useEffect(() => {
    const s = series.current;
    if (!s.candles) return;
    const t = THEME[theme];
    const on = (k: Overlay) => overlays.has(k);

    s.candles.setData(
      candles.map((c) =>
        c.open != null && c.high != null && c.low != null
          ? { time: toTime(c.date), open: c.open, high: c.high, low: c.low, close: c.close }
          : { time: toTime(c.date) },
      ),
    );
    s.volume?.setData(
      on('volume')
        ? candles.map((c) =>
            c.volume == null
              ? { time: toTime(c.date) }
              : { time: toTime(c.date), value: c.volume, color: (c.close >= (c.open ?? c.close) ? t.up : t.down) + '55' },
          )
        : [],
    );
    s.sma20?.setData(on('sma20') ? line(candles, calc.sma20) : []);
    s.sma50?.setData(on('sma50') ? line(candles, calc.sma50) : []);
    s.sma200?.setData(on('sma200') ? line(candles, calc.sma200) : []);
    s.bbUpper?.setData(on('bollinger') ? line(candles, calc.bb.upper) : []);
    s.bbMid?.setData(on('bollinger') ? line(candles, calc.bb.mid) : []);
    s.bbLower?.setData(on('bollinger') ? line(candles, calc.bb.lower) : []);
    s.dcHigh?.setData(on('donchian') ? line(candles, candles.map((c) => c.donchian_high_20)) : []);
    s.dcLow?.setData(on('donchian') ? line(candles, candles.map((c) => c.donchian_low_20)) : []);
    s.rsi?.setData(line(candles, calc.rsi));
    s.macdLine?.setData(line(candles, calc.macd.line));
    s.macdSignal?.setData(line(candles, calc.macd.signal));
    s.macdHist?.setData(
      candles.map((c, i) => {
        const v = calc.macd.hist[i];
        return v == null ? { time: toTime(c.date) } : { time: toTime(c.date), value: v, color: (v >= 0 ? t.up : t.down) + '99' };
      }),
    );

    // Markers: earnings (E) above the candle, past breakout signals (B) below — green worked, red failed, gray pending.
    const markers: SeriesMarker<Time>[] = [];
    for (const e of earnings) {
      const i = candleIndexFor(candles, e.date, interval);
      if (i >= 0) markers.push({ time: toTime(candles[i].date), position: 'aboveBar', color: '#7c3aed', shape: 'circle', text: 'E' });
    }
    for (const b of breakouts) {
      const i = candleIndexFor(candles, b.date, interval);
      if (i < 0) continue;
      const color = b.success == null ? '#8e8e93' : b.success ? t.up : t.down;
      markers.push({ time: toTime(candles[i].date), position: 'belowBar', color, shape: 'arrowUp', text: 'B' });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    s.candles.setMarkers(markers);
  }, [candles, calc, overlays, theme, earnings, breakouts, interval, showRsi, showMacd]);

  // --- visible range + panel sync ----------------------------------------------------------------------------------
  useEffect(() => {
    const main = charts.current.main;
    if (!main || !candles.length) return;
    const n = RANGES[interval].find((r) => r.value === range)?.bars ?? null;
    const from = n == null ? 0 : Math.max(0, candles.length - n);
    const r: LogicalRange = { from, to: candles.length - 1 + 4 } as LogicalRange;
    main.timeScale().setVisibleLogicalRange(r);
    charts.current.rsi?.timeScale().setVisibleLogicalRange(r);
    charts.current.macd?.timeScale().setVisibleLogicalRange(r);
  }, [candles, range, interval, showRsi, showMacd]);

  useEffect(() => {
    const list = (['main', 'rsi', 'macd'] as const).map((k) => charts.current[k]).filter((c): c is IChartApi => !!c);
    if (list.length < 2) return;
    let syncing = false;
    const handlers = list.map((src) => {
      const h = (r: LogicalRange | null) => {
        if (syncing || !r) return;
        syncing = true;
        list.forEach((dst) => dst !== src && dst.timeScale().setVisibleLogicalRange(r));
        syncing = false;
      };
      src.timeScale().subscribeVisibleLogicalRangeChange(h);
      return { src, h };
    });
    return () => handlers.forEach(({ src, h }) => src.timeScale().unsubscribeVisibleLogicalRangeChange(h));
  }, [showRsi, showMacd]);

  // --- legend --------------------------------------------------------------------------------------------------------
  const idx = hover != null && hover >= 0 && hover < candles.length ? hover : candles.length - 1;
  const c = candles[idx];
  const prev = idx > 0 ? candles[idx - 1] : undefined;
  const change = c && prev ? ((c.close - prev.close) / prev.close) * 100 : null;
  const t = THEME[theme];

  const toggle = <T,>(set: Set<T>, key: T, setter: (s: Set<T>) => void) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setter(next);
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-5 pb-3">
        <SegmentedControl
          aria-label="Candle interval"
          options={[
            { value: 'daily', label: 'Daily' },
            { value: 'weekly', label: 'Weekly' },
            { value: 'monthly', label: 'Monthly' },
          ]}
          value={interval}
          onChange={(v) => {
            setCandleInterval(v);
            setRange(DEFAULT_RANGE[v]);
          }}
        />
        {RANGES[interval].length > 1 && (
          <SegmentedControl aria-label="Chart range" options={RANGES[interval].map(({ value, label }) => ({ value, label }))} value={range} onChange={setRange} />
        )}
        <div className="mx-1 hidden h-6 w-px bg-slate-200 sm:block" />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Indicators">
          {OVERLAYS.map((o) => {
            const active = overlays.has(o.key);
            return (
              <button
                key={o.key}
                type="button"
                aria-pressed={active}
                onClick={() => toggle(overlays, o.key, setOverlays)}
                className={clsx(
                  'press inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
                  active ? 'border-transparent bg-slate-900 text-white' : 'border-slate-300 text-slate-600 hover:bg-black/[0.04]',
                )}
              >
                <span className="h-2 w-2 rounded-full" style={{ background: o.key === 'donchian' ? t.donchian : o.color }} />
                {o.label}
              </button>
            );
          })}
          {PANELS.map((p) => {
            const active = panels.has(p.key);
            return (
              <button
                key={p.key}
                type="button"
                aria-pressed={active}
                onClick={() => toggle(panels, p.key, setPanels)}
                className={clsx(
                  'press rounded-full border px-2.5 py-1 text-xs font-medium',
                  active ? 'border-transparent bg-accent text-white' : 'border-slate-300 text-slate-600 hover:bg-black/[0.04]',
                )}
              >
                {p.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Live readout for the candle under the cursor (or the latest one). */}
      <div className="flex min-h-[20px] flex-wrap items-center gap-x-3 gap-y-0.5 px-5 pb-2 text-[11px] tabular-nums text-slate-500">
        {c && (
          <>
            <span className="font-medium text-slate-700">{c.date}</span>
            <span>O {fmt(c.open)}</span>
            <span>H {fmt(c.high)}</span>
            <span>L {fmt(c.low)}</span>
            <span>C {fmt(c.close)}</span>
            {change != null && <span className={change >= 0 ? 'text-emerald-600' : 'text-red-600'}>{change >= 0 ? '+' : ''}{change.toFixed(2)}%</span>}
            {overlays.has('sma20') && <span style={{ color: '#0a84ff' }}>SMA20 {fmt(calc.sma20[idx])}</span>}
            {overlays.has('sma50') && <span style={{ color: '#ff9f0a' }}>SMA50 {fmt(calc.sma50[idx])}</span>}
            {overlays.has('sma200') && <span style={{ color: '#bf5af2' }}>SMA200 {fmt(calc.sma200[idx])}</span>}
            {overlays.has('donchian') && <span>Donchian {fmt(c.donchian_low_20)} – {fmt(c.donchian_high_20)}</span>}
          </>
        )}
      </div>

      <div className="px-2">
        <div ref={mainRef} style={{ height }} />
        {showRsi && (
          <div className="relative mt-1 border-t border-black/[0.06]">
            <span className="pointer-events-none absolute left-2 top-1 z-10 text-[10px] font-medium text-slate-500">
              RSI 14 <span style={{ color: '#bf5af2' }}>{fmt(calc.rsi[idx], 1)}</span>
            </span>
            <div ref={rsiRef} style={{ height: 110 }} />
          </div>
        )}
        {showMacd && (
          <div className="relative mt-1 border-t border-black/[0.06]">
            <span className="pointer-events-none absolute left-2 top-1 z-10 text-[10px] font-medium text-slate-500">
              MACD 12,26,9 <span style={{ color: '#0a84ff' }}>{fmt(calc.macd.line[idx])}</span>{' '}
              <span style={{ color: '#ff9f0a' }}>{fmt(calc.macd.signal[idx])}</span>
            </span>
            <div ref={macdRef} style={{ height: 120 }} />
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 px-5 pb-1 pt-2 text-[11px] text-slate-500">
        <span>
          <span className="font-semibold text-violet-600">E</span> earnings
        </span>
        <span>
          <span className="font-semibold" style={{ color: t.up }}>B</span>/<span className="font-semibold" style={{ color: t.down }}>B</span>/
          <span className="font-semibold text-slate-400">B</span> past breakout signal: worked / failed / pending
        </span>
        {interval !== 'daily' && <span>Donchian on {interval} candles: 20 {interval === 'weekly' ? 'weeks' : 'months'}</span>}
      </div>
    </div>
  );
}
