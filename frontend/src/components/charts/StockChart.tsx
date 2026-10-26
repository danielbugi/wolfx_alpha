'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { isUnverified, previouslyReported } from '@/lib/earningsConfidence';
import {
  createChart,
  IChartApi,
  ISeriesApi,
  IPriceLine,
  AutoscaleInfo,
  LogicalRange,
  Logical,
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
import { AdjustmentsHorizontalIcon } from '@heroicons/react/20/solid';
import IndicatorsDialog from '@/components/charts/IndicatorsDialog';
import { useIndicatorPrefs } from '@/lib/chartIndicators';

/**
 * The stock page's main chart: daily / weekly / monthly candles built from the daily bars, overlays (SMA 20/50/200,
 * Bollinger, Donchian, volume), RSI and MACD panels underneath kept in sync with the price chart, and breakout +
 * earnings markers. All indicators are computed in the browser (lib/indicators.ts) from the bars the API returns.
 */

type Overlay = 'sma20' | 'sma50' | 'sma200' | 'bollinger' | 'donchian' | 'volume' | 'breakouts' | 'plan';
type PanelKey = 'rsi' | 'macd';

const OVERLAYS: { key: Overlay; label: string; color: string }[] = [
  { key: 'sma20', label: 'SMA 20', color: '#0a84ff' },
  { key: 'sma50', label: 'SMA 50', color: '#ff9f0a' },
  { key: 'sma200', label: 'SMA 200', color: '#bf5af2' },
  { key: 'bollinger', label: 'Bollinger', color: '#8e8e93' },
  { key: 'donchian', label: 'Donchian', color: '#94a3b8' },
  { key: 'volume', label: 'Volume', color: '#8e8e93' },
  // Past breakout signals as markers — off by default: on a busy chart they crowd the candles.
  { key: 'breakouts', label: 'Breakouts', color: '#10b981' },
  // The active signal's entry / stop / targets as price lines — only offered when the stock has a plan.
  { key: 'plan', label: 'Trade plan', color: '#0a84ff' },
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

/** How many candles ahead of the last one `date` falls: trading days (weekdays) for daily, weeks, or months. */
export function periodsAhead(lastDate: string, date: string, interval: Interval): number {
  const a = new Date(`${lastDate}T00:00:00Z`);
  const b = new Date(`${date}T00:00:00Z`);
  if (b <= a) return 0;
  if (interval === 'monthly') return (b.getUTCFullYear() - a.getUTCFullYear()) * 12 + (b.getUTCMonth() - a.getUTCMonth());
  if (interval === 'weekly') return Math.max(1, Math.round((b.getTime() - a.getTime()) / (7 * 86_400_000)));
  let n = 0;
  for (const d = new Date(a); d < b; ) {
    d.setUTCDate(d.getUTCDate() + 1);
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) n++;
  }
  return n;
}

// Show the next report ahead of the last candle when it's this close (in candles); further out it would squash the chart.
const MAX_AHEAD: Record<Interval, number> = { daily: 45, weekly: 12, monthly: 3 };

interface EarningsBadge {
  x: number;
  next: boolean;
  label: string;
  title: string;
}

function earningsTitle(e: EarningsDateEntry, next: boolean): string {
  const eps = (v: number | null) => (v == null ? '—' : `$${v.toFixed(2)}`);
  if (next && isUnverified(e.confidence)) return previouslyReported(e.date);
  if (next) return `Next earnings ${e.date}${e.eps_estimate != null ? ` · est. EPS ${eps(e.eps_estimate)}` : ''}`;
  const surprise = e.surprise_pct != null ? ` (${e.surprise_pct >= 0 ? '+' : ''}${e.surprise_pct.toFixed(1)}%)` : '';
  return `Earnings ${e.date} · EPS ${eps(e.eps_actual)} vs est. ${eps(e.eps_estimate)}${surprise}`;
}

function fmt(v: number | null | undefined, d = 2) {
  return v == null ? '—' : v.toFixed(d);
}

export interface StockChartProps {
  /** Daily bars, oldest first (load the longest history available: weekly/monthly and SMA 200 need it). */
  bars: PriceBar[];
  earnings?: EarningsDateEntry[];
  breakouts?: BreakoutHistoryEntry[];
  /** The active signal's plan, drawn as price lines by the "Trade plan" toggle (no toggle without one). */
  plan?: { entry_price: number; stop_loss_price: number; tp1: number; tp2: number; tp3: number } | null;
  height?: number;
  /** Decimals for prices on the axis and in the readout (e.g. 4 for EUR/USD); 2 for stocks. */
  precision?: number;
}

// Stable defaults: a fresh `[]` per render would re-run the effects keyed on these props on every render.
const NO_EARNINGS: EarningsDateEntry[] = [];
const NO_BREAKOUTS: BreakoutHistoryEntry[] = [];

export default function StockChart({
  bars,
  earnings = NO_EARNINGS,
  breakouts = NO_BREAKOUTS,
  plan = null,
  height = 420,
  precision = 2,
}: StockChartProps) {
  const theme = useResolvedTheme();
  const [interval, setCandleInterval] = useState<Interval>('daily');
  const [range, setRange] = useState(DEFAULT_RANGE.daily);
  // Indicators come from the saved choice (lib/chartIndicators, shared by every chart); breakout markers and a trade plan
  // belong to one stock and are toggled for this view only.
  const { prefs, save } = useIndicatorPrefs();
  const [local, setLocal] = useState<Set<Overlay>>(() => new Set<Overlay>());
  const [dialogOpen, setDialogOpen] = useState(false);
  const [notSaved, setNotSaved] = useState(false);
  const overlays = useMemo(() => new Set<Overlay>([...prefs.overlays, ...local]), [prefs.overlays, local]);
  const panels = useMemo(() => new Set<PanelKey>(prefs.panels), [prefs.panels]);
  const [hover, setHover] = useState<number | null>(null);

  const candles = useMemo(() => {
    const agg = aggregateBars(bars.filter((b) => b.close != null), interval);
    // Daily keeps the pipeline's stored channel (when the bars carry one); otherwise it's computed on the candles shown.
    return interval === 'daily' && agg.some((b) => b.donchian_high_20 != null) ? agg : withDonchian(agg, 20);
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
    // The library's attribution link opens a new tab without rel=noopener; add it.
    el.querySelectorAll('a[target="_blank"]').forEach((a) => a.setAttribute('rel', 'noopener noreferrer'));
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

  useEffect(() => {
    const priceFormat = { type: 'price' as const, precision, minMove: 1 / 10 ** precision };
    const { candles: cs, sma20, sma50, sma200, bbUpper, bbMid, bbLower, dcHigh, dcLow } = series.current;
    for (const s of [cs, sma20, sma50, sma200, bbUpper, bbMid, bbLower, dcHigh, dcLow]) s?.applyOptions({ priceFormat });
  }, [precision]);

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

    // Markers: past breakout signals (B) below the candle — green worked, red failed, gray pending. Earnings are drawn
    // in the strip along the bottom instead (see earningsBadges).
    const markers: SeriesMarker<Time>[] = [];
    for (const b of on('breakouts') ? breakouts : []) {
      const i = candleIndexFor(candles, b.date, interval);
      if (i < 0) continue;
      const color = b.success == null ? '#8e8e93' : b.success ? t.up : t.down;
      markers.push({ time: toTime(candles[i].date), position: 'belowBar', color, shape: 'arrowUp', text: 'B' });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    s.candles.setMarkers(markers);
  }, [candles, calc, overlays, theme, breakouts, interval, showRsi, showMacd]);

  // --- trade plan price lines ----------------------------------------------------------------------------------------
  useEffect(() => {
    const target = series.current.candles;
    if (!target || !plan || !overlays.has('plan')) return;
    const t = THEME[theme];
    const lines: IPriceLine[] = [
      { price: plan.entry_price, color: '#0a84ff', title: 'Entry' },
      { price: plan.stop_loss_price, color: t.down, title: 'Stop' },
      { price: plan.tp1, color: t.up, title: 'TP1' },
      { price: plan.tp2, color: t.up, title: 'TP2' },
      { price: plan.tp3, color: t.up, title: 'TP3' },
    ].map((l) => target.createPriceLine({ ...l, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }));
    // Price lines don't count toward autoscale, so far targets (TP2/TP3) or a deep stop would sit off-screen: widen
    // the price range to include the whole plan while it's shown.
    const lo = Math.min(plan.stop_loss_price, plan.entry_price, plan.tp1, plan.tp2, plan.tp3);
    const hi = Math.max(plan.stop_loss_price, plan.entry_price, plan.tp1, plan.tp2, plan.tp3);
    target.applyOptions({
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const base = original();
        if (!base?.priceRange) return base;
        return {
          ...base,
          priceRange: { minValue: Math.min(base.priceRange.minValue, lo), maxValue: Math.max(base.priceRange.maxValue, hi) },
        };
      },
    });
    return () => {
      lines.forEach((l) => target.removePriceLine(l));
      target.applyOptions({ autoscaleInfoProvider: undefined });
    };
  }, [plan, overlays, theme, candles]);

  // --- earnings strip (Finviz-style, along the bottom) -------------------------------------------------------------
  // The next report sits AHEAD of the last candle (placed by counting trading days / weeks / months), so the chart
  // leaves room on the right for it when it's close enough to matter.
  const nextEarnings = useMemo(() => {
    const last = candles[candles.length - 1];
    if (!last) return null;
    const upcoming = [...earnings].sort((a, b) => a.date.localeCompare(b.date)).find((e) => e.date > last.date);
    if (!upcoming) return null;
    const ahead = periodsAhead(last.date, upcoming.date, interval);
    return ahead >= 1 && ahead <= MAX_AHEAD[interval] ? { entry: upcoming, ahead } : null;
  }, [candles, earnings, interval]);

  const [earningsBadges, setEarningsBadges] = useState<EarningsBadge[]>([]);
  useEffect(() => {
    const main = charts.current.main;
    if (!main) return;
    const layout = () => {
      const ts = main.timeScale();
      const width = mainRef.current?.clientWidth ?? 0;
      const inView = (x: number | null): x is number => x != null && x >= 0 && x <= width - PRICE_SCALE_WIDTH;
      const out: EarningsBadge[] = [];
      const lastDate = candles[candles.length - 1]?.date ?? '';
      for (const e of earnings) {
        if (e.date > lastDate) continue; // only the nearest upcoming report is drawn, below
        const i = candleIndexFor(candles, e.date, interval);
        const x = i >= 0 ? ts.logicalToCoordinate(i as Logical) : null;
        if (inView(x)) out.push({ x, next: false, label: 'E', title: earningsTitle(e, false) });
      }
      if (nextEarnings) {
        const x = ts.logicalToCoordinate((candles.length - 1 + nextEarnings.ahead) as Logical);
        if (inView(x)) {
          const d = new Date(`${nextEarnings.entry.date}T00:00:00Z`);
          out.push({
            x,
            next: true,
            // a previously reported, unconfirmed date keeps a "?" -- never drawn like a scheduled report
            label: `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })}${isUnverified(nextEarnings.entry.confidence) ? '?' : ''}`,
            title: earningsTitle(nextEarnings.entry, true),
          });
        }
      }
      setEarningsBadges(out);
    };
    layout();
    main.timeScale().subscribeVisibleLogicalRangeChange(layout);
    const ro = new ResizeObserver(() => layout());
    if (mainRef.current) ro.observe(mainRef.current);
    return () => {
      main.timeScale().unsubscribeVisibleLogicalRangeChange(layout);
      ro.disconnect();
    };
  }, [candles, earnings, interval, nextEarnings, showRsi, showMacd]);

  // --- visible range + panel sync ----------------------------------------------------------------------------------
  useEffect(() => {
    const main = charts.current.main;
    if (!main || !candles.length) return;
    const n = RANGES[interval].find((r) => r.value === range)?.bars ?? null;
    const from = n == null ? 0 : Math.max(0, candles.length - n);
    const room = nextEarnings ? Math.max(4, nextEarnings.ahead + 3) : 4; // space on the right for the next report
    const r: LogicalRange = { from, to: candles.length - 1 + room } as LogicalRange;
    main.timeScale().setVisibleLogicalRange(r);
    charts.current.rsi?.timeScale().setVisibleLogicalRange(r);
    charts.current.macd?.timeScale().setVisibleLogicalRange(r);
  }, [candles, range, interval, showRsi, showMacd, nextEarnings]);

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

  const toggleLocal = (key: Overlay) => {
    const next = new Set(local);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setLocal(next);
  };
  const extras = [
    ...(breakouts.length > 0
      ? [{ key: 'breakouts', label: 'Past breakout markers', description: 'Dates this stock closed above its 20-day channel.', on: local.has('breakouts'), onToggle: () => toggleLocal('breakouts') }]
      : []),
    ...(plan ? [{ key: 'plan', label: 'Trade plan', description: 'Entry, stop and targets of the active plan.', on: local.has('plan'), onToggle: () => toggleLocal('plan') }] : []),
  ];
  const activeLabels = [
    ...OVERLAYS.filter((o) => overlays.has(o.key) && (o.key !== 'plan' || plan) && (o.key !== 'breakouts' || breakouts.length > 0)).map((o) => ({ key: o.key, label: o.label, color: o.key === 'donchian' ? t.donchian : o.color })),
    ...PANELS.filter((p) => panels.has(p.key)).map((p) => ({ key: p.key, label: p.label, color: null as string | null })),
  ];

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
        <button
          type="button"
          onClick={() => setDialogOpen(true)}
          aria-haspopup="dialog"
          className="press inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-black/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <AdjustmentsHorizontalIcon className="h-4 w-4" />
          Indicators <span className="tabular-nums text-slate-400">{activeLabels.length}</span>
        </button>
        <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] text-slate-500" aria-label="Active indicators" data-testid="active-indicators">
          {activeLabels.map((a) => (
            <span key={a.key} className="inline-flex items-center gap-1">
              {a.color && <span className="h-1.5 w-1.5 rounded-full" style={{ background: a.color }} aria-hidden="true" />}
              {a.label}
            </span>
          ))}
        </div>
      </div>
      {dialogOpen && (
        <IndicatorsDialog
          prefs={prefs}
          onChange={(p) => setNotSaved(!save(p))}
          onClose={() => setDialogOpen(false)}
          extras={extras}
          notSaved={notSaved}
        />
      )}

      {/* Live readout for the candle under the cursor (or the latest one). */}
      <div className="flex min-h-[20px] flex-wrap items-center gap-x-3 gap-y-0.5 px-5 pb-2 text-[11px] tabular-nums text-slate-500">
        {c && (
          <>
            <span className="font-medium text-slate-700">{c.date}</span>
            <span>O {fmt(c.open, precision)}</span>
            <span>H {fmt(c.high, precision)}</span>
            <span>L {fmt(c.low, precision)}</span>
            <span>C {fmt(c.close, precision)}</span>
            {change != null && <span className={change >= 0 ? 'text-emerald-600' : 'text-red-600'}>{change >= 0 ? '+' : ''}{change.toFixed(2)}%</span>}
            {overlays.has('sma20') && <span style={{ color: '#0a84ff' }}>SMA20 {fmt(calc.sma20[idx], precision)}</span>}
            {overlays.has('sma50') && <span style={{ color: '#ff9f0a' }}>SMA50 {fmt(calc.sma50[idx], precision)}</span>}
            {overlays.has('sma200') && <span style={{ color: '#bf5af2' }}>SMA200 {fmt(calc.sma200[idx], precision)}</span>}
            {overlays.has('donchian') && <span>Donchian {fmt(c.donchian_low_20, precision)} – {fmt(c.donchian_high_20, precision)}</span>}
          </>
        )}
      </div>

      <div className="px-2">
        <div className="relative">
          <div ref={mainRef} style={{ height }} />
          {/* Earnings strip: past reports as outlined E badges, the next one filled with its date. */}
          <div
            className="pointer-events-none absolute inset-x-0 z-10"
            style={{ bottom: showRsi || showMacd ? 6 : 34 }}
            aria-label="Earnings dates"
          >
            {earningsBadges.map((b, i) => (
              <span
                key={i}
                title={b.title}
                data-next={b.next || undefined}
                className={clsx(
                  'pointer-events-auto absolute flex -translate-x-1/2 cursor-default items-center justify-center rounded-full text-[10px] font-bold leading-none',
                  b.next
                    ? 'h-5 bg-violet-600 px-1.5 text-white shadow-sm ring-2 ring-violet-600/25'
                    : 'h-[18px] w-[18px] border border-violet-500 bg-white text-violet-600',
                )}
                style={{ left: b.x }}
              >
                {b.next ? `E · ${b.label}` : 'E'}
              </span>
            ))}
          </div>
        </div>
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
        {earnings.length > 0 && (
          <span>
            <span className="font-semibold text-violet-600">E</span> earnings (bottom of chart; filled = next report)
          </span>
        )}
        {overlays.has('breakouts') && breakouts.length > 0 && (
          <span>
            <span className="font-semibold" style={{ color: t.up }}>B</span>/<span className="font-semibold" style={{ color: t.down }}>B</span>/
            <span className="font-semibold text-slate-400">B</span> past breakout signal: worked / failed / pending
          </span>
        )}
        {interval !== 'daily' && <span>Donchian on {interval} candles: 20 {interval === 'weekly' ? 'weeks' : 'months'}</span>}
      </div>
    </div>
  );
}
