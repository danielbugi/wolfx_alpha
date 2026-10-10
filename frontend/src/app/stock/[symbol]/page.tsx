'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
  Card,
  CardBody,
  CardHeader,
  Table,
  TableHeader,
  TableColumn,
  TableBody,
  TableRow,
  TableCell,
  Button,
  Chip,
} from '@nextui-org/react';
import { ExclamationTriangleIcon } from '@heroicons/react/20/solid';
import { clsx } from 'clsx';
import { HoverTip, ProgressBar } from '@/components/ui/Field';
import {
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
  Legend,
} from 'recharts';
import { stockApi, formatters, isHttpStatus, Maybe, StockDetail, PriceBar, EarningsDateEntry } from '@/services/api';
import PlanBar from '@/components/strategy/PlanBar';
import LightweightCandlestickChart from '@/components/charts/LightweightCandlestickChart';
import { positionPct, nearestBarDate } from '@/lib/priceMath';
import { gradeColor } from '@/lib/uiColors';
import { useResolvedTheme } from '@/lib/theme';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import SearchBox from '@/components/layout/SearchBox';
import SegmentedControl from '@/components/ui/SegmentedControl';

const RANGE_OPTIONS = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
];

type DetailTab = 'plan' | 'fundamentals' | 'quarterly' | 'track';

// "—" for unknown everywhere on this page: a missing value is never shown as 0, "N/A%" or a default.
const UNKNOWN = '—';

function fmtPct(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined) return UNKNOWN;
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;
}

function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return UNKNOWN;
  return v.toFixed(decimals);
}

function fmtBig(v: number | null | undefined): string {
  if (v === null || v === undefined) return UNKNOWN;
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
}

/** Whole days from today (local) to an ISO date; negative = in the past. */
function daysUntil(isoDate: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((new Date(`${isoDate}T00:00:00`).getTime() - today.getTime()) / 86_400_000);
}

/** The daily/weekly/monthly panel's inputs, whichever snapshot shape they came from. */
interface Timeframes {
  daily: {
    rsi: Maybe<number>;
    donchianHigh: Maybe<number>;
    donchianLow: Maybe<number>;
    pricePosition: Maybe<number>;
    sma20: Maybe<number>;
    sma50: Maybe<number>;
    atr: Maybe<number>;
  };
  weekly: {
    donchianHigh: Maybe<number>;
    donchianLow: Maybe<number>;
    rsi: Maybe<number>;
    trend: Maybe<string>;
    close: Maybe<number>;
  };
  monthly: {
    donchianHigh: Maybe<number>;
    donchianLow: Maybe<number>;
    trend: Maybe<string>;
    trendStrength: Maybe<number>;
    close: Maybe<number>;
  };
}

/** Trend label from the 20-week channel position. Unknown position => unknown trend
 * (null), never a default "sideways". */
function trendFromChannelPosition(position: Maybe<number>): string | null {
  if (position == null) return null;
  return position > 60 ? 'bullish' : position < 40 ? 'bearish' : 'sideways';
}

/** The active-signal and no-signal snapshot shapes differ for multi-timeframe
 * technicals; normalize both into one shape for the daily/weekly/monthly panel. */
function extractTimeframes(detail: StockDetail): Timeframes {
  if (detail.data_source === 'active_signal') {
    const snap = detail.snapshot;
    const weekly = snap.weekly_context;
    const monthly = snap.monthly_context;
    return {
      daily: {
        rsi: snap.rsi_14,
        donchianHigh: snap.donchian_high,
        donchianLow: snap.donchian_low,
        pricePosition: snap.price_position_in_channel,
        sma20: snap.sma_20,
        sma50: snap.sma_50,
        atr: snap.atr_14,
      },
      weekly: {
        donchianHigh: weekly?.weekly_donchian_high,
        donchianLow: weekly?.weekly_donchian_low,
        rsi: weekly?.weekly_rsi,
        trend: weekly?.weekly_trend,
        close: weekly?.weekly_close,
      },
      monthly: {
        donchianHigh: monthly?.monthly_donchian_high,
        donchianLow: monthly?.monthly_donchian_low,
        trend: monthly?.monthly_trend,
        trendStrength: monthly?.trend_strength,
        close: monthly?.monthly_close,
      },
    };
  }

  const { daily, weekly, monthly } = detail.snapshot;
  return {
    daily: {
      rsi: daily?.rsi_14,
      donchianHigh: daily?.donchian_high_20,
      donchianLow: daily?.donchian_low_20,
      pricePosition: daily?.price_position,
      sma20: daily?.sma_20,
      sma50: daily?.sma_50,
      atr: daily?.atr_14,
    },
    weekly: {
      donchianHigh: weekly?.donchian_high_20w,
      donchianLow: weekly?.donchian_low_20w,
      rsi: weekly?.rsi_14w,
      trend: trendFromChannelPosition(weekly?.price_position_weekly),
      close: weekly?.weekly_close,
    },
    monthly: {
      donchianHigh: monthly?.donchian_high_12m,
      donchianLow: monthly?.donchian_low_12m,
      trend: monthly?.trend_direction,
      trendStrength: monthly?.trend_strength_6m,
      close: monthly?.monthly_close,
    },
  };
}

function trendChipClass(trend: string): string {
  return trend === 'bullish'
    ? 'bg-emerald-100 text-emerald-800'
    : trend === 'bearish'
      ? 'bg-red-100 text-red-700'
      : 'bg-slate-100 text-slate-600';
}

/** One timeframe's channel: where price sits between the channel low and high. Unknown inputs show "—" and an empty
 * track — never a made-up midpoint. */
function ChannelColumn({
  title,
  donchianHigh,
  donchianLow,
  price,
  trend,
  extra,
}: {
  title: string;
  donchianHigh?: number | null;
  donchianLow?: number | null;
  price?: number | null;
  trend?: string | null;
  extra?: string;
}) {
  const pos = positionPct(price, donchianLow, donchianHigh);
  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-slate-700">{title}</p>
        {trend && (
          <Chip size="sm" className={`h-5 text-[11px] capitalize ${trendChipClass(trend)}`}>
            {trend}
          </Chip>
        )}
      </div>
      <ProgressBar value={pos ?? 0} className={clsx(pos == null && 'opacity-40')} barClassName="bg-accent" />
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-slate-500">
        <span>{donchianLow != null ? `$${donchianLow.toFixed(2)}` : UNKNOWN}</span>
        <span className="font-semibold text-slate-700">{pos != null ? `${pos.toFixed(0)}%` : UNKNOWN}</span>
        <span>{donchianHigh != null ? `$${donchianHigh.toFixed(2)}` : UNKNOWN}</span>
      </div>
      {extra && <p className="mt-0.5 text-[11px] text-slate-500">{extra}</p>}
    </div>
  );
}

function ScoreBar({ label, value }: { label: string; value: number | null | undefined }) {
  const color = value == null ? 'bg-slate-300' : value >= 70 ? 'bg-emerald-500' : value >= 45 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="mb-3">
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-slate-600">{label}</span>
        <span className={clsx('font-semibold tabular-nums', value == null ? 'text-slate-400' : 'text-slate-800')}>
          {value != null ? value.toFixed(0) : UNKNOWN}
        </span>
      </div>
      <ProgressBar value={value ?? 0} barClassName={color} />
    </div>
  );
}

/** One figure in a strip of key numbers. */
function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <p className="truncate text-[11px] font-medium text-slate-500">{label}</p>
      <p className="mt-0.5 truncate text-[15px] font-semibold tabular-nums text-slate-900">{value}</p>
      {hint && <p className="truncate text-[11px] text-slate-400">{hint}</p>}
    </div>
  );
}

function SectionTitle({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="text-[15px] font-semibold text-slate-900">{children}</h3>
      {aside}
    </div>
  );
}

export default function StockDetailPage() {
  const params = useParams();
  const symbol = (params?.symbol as string)?.toUpperCase();
  const theme = useResolvedTheme();

  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [bars, setBars] = useState<PriceBar[]>([]);
  const [earnings, setEarnings] = useState<EarningsDateEntry[]>([]);
  const [rangeDays, setRangeDays] = useState(180);
  const [loading, setLoading] = useState(true);
  const [chartLoading, setChartLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDonchian, setShowDonchian] = useState(true);
  const [showVolume, setShowVolume] = useState(true);
  // null = not chosen yet: opens on the trade plan when there's an active signal, otherwise on fundamentals.
  const [tab, setTab] = useState<DetailTab | null>(null);

  // Guards the un-awaited earnings fetch against landing after navigating to
  // a different symbol.
  const latestSymbolRef = useRef(symbol);
  latestSymbolRef.current = symbol;

  // fetchDetail (also wired to the Retry button) must always load the range the
  // user has selected *now*, not the one captured when `symbol` last changed --
  // and a range change must not re-run it (changeRange fetches bars itself).
  const rangeDaysRef = useRef(rangeDays);
  rangeDaysRef.current = rangeDays;

  const fetchDetail = useCallback(async () => {
    if (!symbol) return;
    try {
      setLoading(true);
      setError(null);
      setTab(null);
      // Earnings markers come from yfinance (~1s cold) -- fire them off on
      // their own so the page and chart render as soon as detail + price
      // history are in, and the markers pop in afterwards. Not awaited on
      // purpose; a failure just means no markers.
      setEarnings([]);
      stockApi
        .getEarnings(symbol)
        .then((r) => {
          if (latestSymbolRef.current === symbol) setEarnings(r.earnings_dates);
        })
        .catch(() => {});

      const [d, ph] = await Promise.all([
        stockApi.getDetail(symbol),
        stockApi.getPriceHistory(symbol, rangeDaysRef.current),
      ]);
      setDetail(d);
      setBars(ph.bars);
    } catch (err: unknown) {
      setError(isHttpStatus(err, 404) ? `Symbol '${symbol}' not found` : 'Failed to load stock data');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchDetail();
  }, [fetchDetail]);

  const changeRange = async (days: number) => {
    setRangeDays(days);
    if (!symbol) return;
    try {
      setChartLoading(true);
      const ph = await stockApi.getPriceHistory(symbol, days);
      setBars(ph.bars);
    } finally {
      setChartLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center">
          <LoadingSpinner size="large" className="mb-4" />
          <p className="text-slate-600 text-sm">Loading {symbol}…</p>
        </div>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
        <div className="w-full max-w-sm text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-white text-2xl font-bold text-slate-400 shadow-sm ring-1 ring-black/[0.05]">
            ?
          </div>
          <h1 className="text-[22px] font-bold text-slate-900">{error || 'Not found'}</h1>
          <p className="mt-1 text-sm text-slate-500">Check the ticker, or search for another symbol.</p>
          <div className="mt-5 text-left">
            <SearchBox autoFocus />
          </div>
          <div className="mt-4 flex items-center justify-center gap-4 text-[13px] font-medium">
            <button type="button" onClick={fetchDetail} className="text-accent hover:underline">
              Try again
            </button>
            <Link href="/" className="text-slate-500 hover:text-slate-800">
              Back to Dashboard
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const snap = detail.snapshot;
  const rating = detail.ai_rating;
  const timeframes = extractTimeframes(detail);
  const hasSignal = detail.data_source === 'active_signal';
  const track = detail.breakout_track_record;
  const activeTab: DetailTab = tab ?? (hasSignal ? 'plan' : 'fundamentals');
  const asOfDate = detail.data_source === 'no_signal' ? detail.snapshot.as_of_date : null;

  const priceChangePct = snap.price_change_pct;
  const isUp = (priceChangePct ?? 0) >= 0;

  // Earnings markers must land on an existing bar (see nearestBarDate) --
  // a date too far past the last bar (e.g. an upcoming report) can't be
  // placed on the chart, so surface it as a caption instead of silently
  // dropping it.
  const unplottedUpcomingEarnings = earnings.find(
    (e) => nearestBarDate(bars, e.date, 3) === null && new Date(e.date) > new Date(bars[bars.length - 1]?.date || 0)
  );
  const nextEarnings = earnings
    .filter((e) => daysUntil(e.date) >= 0)
    .sort((a, b) => a.date.localeCompare(b.date))[0];
  const daysToEarnings = nextEarnings ? daysUntil(nextEarnings.date) : null;

  const financials = [...detail.quarterly_financials].reverse();

  // Derived client-side from the same quarterly data shown below -- no
  // separate backend call needed. Mirrors the deep-value scan's turnaround
  // definition (latest quarter profitable, the one before it a loss).
  const [latestQ, previousQ] = detail.quarterly_financials;
  const isTurnaround =
    !!latestQ?.net_income && !!previousQ?.net_income && latestQ.net_income > 0 && previousQ.net_income < 0;

  const chartColors =
    theme === 'dark'
      ? { grid: '#2f2f33', tick: '#98989f', revenue: '#64d2ff', income: '#30d158' }
      : { grid: '#e3e3e8', tick: '#737378', revenue: '#0a84ff', income: '#34c759' };

  const tabs: { value: DetailTab; label: string }[] = [
    { value: 'plan', label: hasSignal ? 'Signal & plan' : 'Signal' },
    { value: 'fundamentals', label: 'Fundamentals' },
    { value: 'quarterly', label: 'Quarterly' },
    { value: 'track', label: `Track record${track.total_breakouts > 0 ? ` (${track.total_breakouts})` : ''}` },
  ];

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-7xl space-y-4">
        {/* Header -- pinned while the chart and details scroll beneath it on larger screens. Not on phones: it wraps
            to several lines there and would cover too much of a small screen. */}
        <Card className="material-thick z-20 border border-black/[0.05] shadow-sm md:sticky md:top-[4.25rem] lg:top-3">
          <CardBody className="flex-row flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 py-3.5">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="large-title">{detail.symbol}</h1>
                {hasSignal ? (
                  <Chip size="sm" className="bg-accent-soft text-accent text-xs font-medium">
                    Active breakout signal
                  </Chip>
                ) : (
                  <Chip size="sm" className="bg-slate-100 text-slate-500 text-xs">
                    No active signal
                  </Chip>
                )}
                {snap.quality_grade && (
                  <Chip size="sm" className={`${gradeColor(snap.quality_grade)} text-xs`}>
                    Grade {snap.quality_grade}
                  </Chip>
                )}
                {isTurnaround && (
                  <Chip size="sm" className="bg-emerald-100 text-emerald-800 text-xs">
                    Turnaround
                  </Chip>
                )}
                {daysToEarnings != null && daysToEarnings <= 21 && (
                  <Chip
                    size="sm"
                    title={`Reports ${nextEarnings?.date}`}
                    className={`text-xs ${daysToEarnings <= 3 ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800'}`}
                  >
                    Earnings {daysToEarnings === 0 ? 'today' : `in ${daysToEarnings}d`}
                  </Chip>
                )}
              </div>
              <p className="mt-0.5 truncate text-sm text-slate-500">
                {snap.sector || 'Unknown sector'}
                {snap.industry ? ` · ${snap.industry}` : ''}
                {asOfDate ? ` · data as of ${asOfDate}` : ''}
              </p>
            </div>
            <div className="sm:text-right">
              <p className="large-title tabular-nums">{snap.current_price != null ? `$${fmtNum(snap.current_price)}` : UNKNOWN}</p>
              <p className={clsx('text-sm font-semibold tabular-nums', priceChangePct == null ? 'text-slate-400' : isUp ? 'text-emerald-600' : 'text-red-600')}>
                {fmtPct(priceChangePct, 2)}
              </p>
            </div>
          </CardBody>
        </Card>

        {/* Key numbers */}
        <Card className="border border-black/[0.05] shadow-sm">
          <CardBody className="grid grid-cols-2 divide-black/[0.06] p-0 sm:grid-cols-4 lg:grid-cols-7 lg:divide-x">
            <Stat label="Market cap" value={snap.market_cap_formatted || (snap.market_cap != null ? formatters.marketCap(snap.market_cap) : UNKNOWN)} />
            <Stat label="P/E" value={fmtNum(snap.pe_ratio, 1)} />
            <Stat label="Beta" value={fmtNum(snap.beta)} />
            <Stat label="RSI (14d)" value={fmtNum(timeframes.daily.rsi, 1)} />
            <Stat label="ATR (14d)" value={timeframes.daily.atr != null ? `$${fmtNum(timeframes.daily.atr)}` : UNKNOWN} />
            <Stat label="Dividend yield" value={snap.dividend_yield != null ? `${fmtNum(snap.dividend_yield)}%` : UNKNOWN} />
            <Stat
              label="Next earnings"
              value={nextEarnings ? nextEarnings.date : UNKNOWN}
              hint={daysToEarnings != null ? (daysToEarnings === 0 ? 'today' : `in ${daysToEarnings} days`) : undefined}
            />
          </CardBody>
        </Card>

        {/* Price chart + where price sits in each timeframe's channel */}
        <Card className="border border-black/[0.05] shadow-sm">
          <CardHeader className="px-5 pt-4 pb-2 flex flex-wrap justify-between items-center gap-2">
            <h3 className="text-[15px] font-semibold text-slate-900">Price & Donchian channel</h3>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant={showDonchian ? 'solid' : 'light'}
                className={showDonchian ? 'rounded-full bg-slate-900 text-white text-xs' : 'rounded-full text-xs text-slate-700'}
                onClick={() => setShowDonchian((v) => !v)}
              >
                Donchian
              </Button>
              <Button
                size="sm"
                variant={showVolume ? 'solid' : 'light'}
                className={showVolume ? 'rounded-full bg-slate-900 text-white text-xs' : 'rounded-full text-xs text-slate-700'}
                onClick={() => setShowVolume((v) => !v)}
              >
                Volume
              </Button>
              <div className="mx-1 h-6 w-px bg-slate-200" />
              <SegmentedControl
                aria-label="Chart range"
                options={RANGE_OPTIONS.map((r) => ({ value: String(r.days), label: r.label }))}
                value={String(rangeDays)}
                onChange={(v) => changeRange(Number(v))}
              />
              {chartLoading && <LoadingSpinner size="small" className="ml-1" />}
            </div>
          </CardHeader>
          <CardBody className="px-2 pb-2 pt-0">
            <LightweightCandlestickChart
              bars={bars}
              earnings={earnings}
              variant="full"
              height={420}
              showDonchian={showDonchian}
              showVolume={showVolume}
            />
            {unplottedUpcomingEarnings && (
              <p className="text-xs text-slate-500 mt-1 px-1">
                Next earnings: {unplottedUpcomingEarnings.date} (beyond the charted range)
              </p>
            )}
          </CardBody>
          <div className="grid grid-cols-1 gap-5 border-t border-black/[0.06] px-5 py-4 md:grid-cols-3">
            <ChannelColumn
              title="Daily · 20-day channel"
              donchianHigh={timeframes.daily.donchianHigh}
              donchianLow={timeframes.daily.donchianLow}
              price={snap.current_price}
              extra={timeframes.daily.rsi != null ? `RSI ${timeframes.daily.rsi.toFixed(1)}` : undefined}
            />
            <ChannelColumn
              title="Weekly · 20-week channel"
              donchianHigh={timeframes.weekly.donchianHigh}
              donchianLow={timeframes.weekly.donchianLow}
              price={timeframes.weekly.close ?? snap.current_price}
              trend={timeframes.weekly.trend}
              extra={timeframes.weekly.rsi != null ? `RSI ${timeframes.weekly.rsi.toFixed(1)}` : undefined}
            />
            <ChannelColumn
              title="Monthly · 12-month channel"
              donchianHigh={timeframes.monthly.donchianHigh}
              donchianLow={timeframes.monthly.donchianLow}
              price={timeframes.monthly.close ?? snap.current_price}
              trend={timeframes.monthly.trend}
              extra={
                timeframes.monthly.trendStrength != null
                  ? `Trend strength ${Number(timeframes.monthly.trendStrength).toFixed(1)}`
                  : undefined
              }
            />
          </div>
        </Card>

        {/* Details, one subject at a time */}
        <div className="overflow-x-auto pb-1">
          <SegmentedControl aria-label="Stock details" size="md" options={tabs} value={activeTab} onChange={setTab} />
        </div>

        {activeTab === 'plan' && (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="p-5">
              {hasSignal ? (
                <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
                  <div className="lg:col-span-3">
                    {rating.plan ? (
                      <>
                        <SectionTitle
                          aside={
                            <HoverTip
                              content={
                                <div className="space-y-0.5">
                                  <p>Momentum: {rating.plan.momentum_score == null ? UNKNOWN : rating.plan.momentum_score.toFixed(0)} × 0.40</p>
                                  <p>Fundamentals: {rating.plan.fundamentals_score == null ? UNKNOWN : rating.plan.fundamentals_score.toFixed(0)} × 0.25</p>
                                  <p>Alignment: {rating.plan.alignment_score == null ? UNKNOWN : rating.plan.alignment_score.toFixed(0)} × 0.20</p>
                                  <p>Risk bonus: {rating.plan.risk_bonus.toFixed(0)} × 0.15</p>
                                </div>
                              }
                            >
                              <span className="cursor-help border-b border-dotted border-slate-400 text-xs font-semibold text-slate-700">
                                Strategy score {rating.plan.strategy_score.toFixed(0)}
                              </span>
                            </HoverTip>
                          }
                        >
                          <span className="inline-flex items-center gap-2">
                            Trade plan
                            <Chip
                              size="sm"
                              className={`text-xs ${rating.plan.direction === 'long' ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-700'}`}
                            >
                              {rating.plan.direction === 'long' ? 'Long' : 'Short'}
                            </Chip>
                          </span>
                        </SectionTitle>

                        <PlanBar
                          plan={{
                            stop_loss_price: rating.plan.stop_loss_price,
                            entry_price: rating.plan.entry_price,
                            tp1: rating.plan.tp1,
                            tp2: rating.plan.tp2,
                            tp3: rating.plan.tp3,
                          }}
                        />

                        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
                          {[
                            { label: 'Entry', price: rating.plan.entry_price, pct: null, tone: 'text-slate-900' },
                            { label: 'Stop', price: rating.plan.stop_loss_price, pct: -rating.plan.risk_pct, tone: 'text-red-600' },
                            { label: 'TP1', price: rating.plan.tp1, pct: rating.plan.reward_pct_tp1, tone: 'text-emerald-600' },
                            { label: 'TP2', price: rating.plan.tp2, pct: rating.plan.reward_pct_tp2, tone: 'text-emerald-600' },
                            { label: 'TP3', price: rating.plan.tp3, pct: rating.plan.reward_pct_tp3, tone: 'text-emerald-600' },
                          ].map((level) => (
                            <div key={level.label} className="rounded-xl bg-slate-50 px-3 py-2.5">
                              <p className="text-[11px] font-medium text-slate-500">{level.label}</p>
                              <p className={`text-[15px] font-semibold tabular-nums ${level.tone}`}>${fmtNum(level.price)}</p>
                              <p className="text-[11px] tabular-nums text-slate-400">{level.pct != null ? fmtPct(level.pct) : 'current price'}</p>
                            </div>
                          ))}
                        </div>
                        <p className="mt-3 text-xs text-slate-500">
                          Suggested position size at 1% account risk:{' '}
                          <span className="font-semibold text-slate-700">{Math.min(10, (1 / rating.plan.risk_pct) * 100).toFixed(1)}%</span> of
                          portfolio (capped at 10%) —{' '}
                          <Link href="/strategy" className="text-accent hover:underline">
                            adjust on Position Plans
                          </Link>
                        </p>
                      </>
                    ) : (
                      <p className="text-sm text-slate-400">Strategy plan unavailable for this signal.</p>
                    )}
                  </div>

                  <div className="space-y-4 lg:col-span-2">
                    <div>
                      <SectionTitle>Signal</SectionTitle>
                      <div className="flex flex-wrap gap-2">
                        {rating.signal_type && (
                          <Chip size="sm" className="bg-slate-100 text-slate-700 text-xs capitalize">
                            {rating.signal_type.replace(/_/g, ' ')}
                          </Chip>
                        )}
                        {rating.alignment_grade && (
                          <Chip size="sm" className="bg-slate-100 text-slate-700 text-xs">
                            Timeframe alignment {rating.alignment_score}% ({rating.alignment_grade})
                          </Chip>
                        )}
                      </div>
                      {rating.reasoning && <p className="mt-2 text-sm leading-relaxed text-slate-600">{rating.reasoning}</p>}
                    </div>

                    {/* The ML score is shown for reference only: it has not shown a reliable edge in validation, so it
                        is labelled as experimental and kept out of the header. */}
                    {(rating.ml_momentum_probability != null || rating.ml_confidence) && (
                      <div className="rounded-xl border border-amber-200/70 bg-amber-50/60 p-3.5">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs font-semibold text-slate-700">Model score · experimental</p>
                          <p className="text-[15px] font-semibold tabular-nums text-slate-900">
                            {rating.ml_momentum_probability != null ? `${rating.ml_momentum_probability.toFixed(0)}%` : UNKNOWN}
                            {rating.ml_confidence && (
                              <span className="ml-1.5 text-xs font-medium capitalize text-slate-500">({rating.ml_confidence.replace('_', ' ')})</span>
                            )}
                          </p>
                        </div>
                        <p className="mt-1.5 flex gap-1.5 text-[11px] leading-relaxed text-slate-600">
                          <ExclamationTriangleIcon className="mt-px h-3.5 w-3.5 shrink-0 text-amber-500" />
                          Not yet a proven predictor — in testing it hasn&apos;t shown a reliable edge. Shown for reference only.
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="py-6 text-center">
                  <p className="text-sm text-slate-600">{rating.reasoning || 'No active breakout signal for this symbol right now.'}</p>
                  <p className="mt-2 text-xs text-slate-400">The chart and the other tabs show the latest available snapshot.</p>
                </div>
              )}
            </CardBody>
          </Card>
        )}

        {activeTab === 'fundamentals' && (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="grid grid-cols-1 gap-8 p-5 lg:grid-cols-2">
              <div>
                <SectionTitle>Quality scores</SectionTitle>
                <ScoreBar label="Growth" value={snap.growth_score} />
                <ScoreBar label="Profitability" value={snap.profitability_score} />
                <ScoreBar label="Financial health" value={snap.financial_health_score} />
                <ScoreBar label="Valuation" value={snap.valuation_score} />
                <div className="my-3 h-px bg-black/[0.06]" />
                <ScoreBar label="Overall quality" value={snap.overall_quality_score} />
              </div>
              <div>
                <SectionTitle>Valuation</SectionTitle>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {[
                    ['P/E', fmtNum(snap.pe_ratio, 1)],
                    ['P/B', fmtNum(snap.pb_ratio, 1)],
                    ['P/S', fmtNum(snap.ps_ratio, 1)],
                    ['PEG', fmtNum(snap.peg_ratio)],
                    ['Beta', fmtNum(snap.beta)],
                    ['Dividend yield', snap.dividend_yield != null ? `${fmtNum(snap.dividend_yield)}%` : UNKNOWN],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-xl bg-slate-50 px-3 py-2.5">
                      <p className="text-[11px] font-medium text-slate-500">{label}</p>
                      <p className={clsx('text-[15px] font-semibold tabular-nums', value === UNKNOWN ? 'text-slate-400' : 'text-slate-900')}>{value}</p>
                    </div>
                  ))}
                </div>
              </div>
            </CardBody>
          </Card>
        )}

        {activeTab === 'quarterly' && (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="p-5">
              {financials.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-6">No quarterly financial data available for this symbol.</p>
              ) : (
                <>
                  <SectionTitle>Revenue & net income</SectionTitle>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={financials} barGap={2}>
                      <CartesianGrid strokeDasharray="3 3" stroke={chartColors.grid} vertical={false} />
                      <XAxis dataKey="quarter" tick={{ fontSize: 10, fill: chartColors.tick }} stroke={chartColors.grid} />
                      <YAxis tick={{ fontSize: 10, fill: chartColors.tick }} width={64} tickFormatter={(v) => fmtBig(v)} stroke={chartColors.grid} />
                      <Tooltip
                        formatter={(v) => (typeof v === 'number' ? fmtBig(v) : UNKNOWN)}
                        cursor={{ fill: theme === 'dark' ? 'rgb(255 255 255 / 0.04)' : 'rgb(0 0 0 / 0.04)' }}
                        contentStyle={{ fontSize: 12, borderRadius: 10, border: 'none', boxShadow: '0 8px 24px rgb(0 0 0 / 0.12)', background: theme === 'dark' ? '#2c2c2e' : '#fff', color: theme === 'dark' ? '#f5f5f7' : '#1d1d1f' }}
                      />
                      <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                      <Bar dataKey="revenue" fill={chartColors.revenue} name="Revenue" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="net_income" fill={chartColors.income} name="Net income" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                  <Table
                    removeWrapper
                    aria-label="Quarterly margins and returns"
                    className="mt-4"
                    classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8', td: 'text-[13px] py-2 tabular-nums' }}
                  >
                    <TableHeader>
                      <TableColumn>QUARTER</TableColumn>
                      <TableColumn align="end">GROSS MGN</TableColumn>
                      <TableColumn align="end">OP MGN</TableColumn>
                      <TableColumn align="end">NET MGN</TableColumn>
                      <TableColumn align="end">ROE</TableColumn>
                      <TableColumn align="end">ROA</TableColumn>
                      <TableColumn align="end">D/E</TableColumn>
                    </TableHeader>
                    <TableBody>
                      {financials.map((q, i) => (
                        <TableRow key={i}>
                          <TableCell className="font-semibold text-slate-800">{q.quarter || `Q${q.fiscal_quarter} ${q.fiscal_year}`}</TableCell>
                          <TableCell className="text-right">{fmtPct(q.gross_margin)}</TableCell>
                          <TableCell className="text-right">{fmtPct(q.operating_margin)}</TableCell>
                          <TableCell className="text-right">{fmtPct(q.net_margin)}</TableCell>
                          <TableCell className="text-right">{fmtPct(q.roe)}</TableCell>
                          <TableCell className="text-right">{fmtPct(q.roa)}</TableCell>
                          <TableCell className="text-right">{fmtNum(q.debt_to_equity)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </>
              )}
            </CardBody>
          </Card>
        )}

        {activeTab === 'track' && (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="p-0">
              {track.entries.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-10">No historical breakout events recorded for this symbol.</p>
              ) : (
                <>
                  <div className="grid grid-cols-2 divide-black/[0.06] border-b border-black/[0.06] sm:grid-cols-4 sm:divide-x">
                    <Stat label="Breakout events" value={track.total_breakouts} />
                    <Stat label="Win rate" value={track.win_rate != null ? `${track.win_rate}%` : UNKNOWN} />
                    <Stat label="Avg max gain (10d)" value={fmtPct(track.avg_gain_10d)} />
                    <Stat label="Avg max loss (10d)" value={fmtPct(track.avg_loss_10d)} />
                  </div>
                  <Table
                    removeWrapper
                    aria-label="Breakout history"
                    classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8 first:pl-5 last:pr-5', td: 'text-[13px] py-2 tabular-nums first:pl-5 last:pr-5' }}
                  >
                    <TableHeader>
                      <TableColumn>DATE</TableColumn>
                      <TableColumn>TYPE</TableColumn>
                      <TableColumn align="end">ENTRY</TableColumn>
                      <TableColumn>OUTCOME</TableColumn>
                      <TableColumn align="end">MAX GAIN 10D</TableColumn>
                      <TableColumn align="end">MAX LOSS 10D</TableColumn>
                    </TableHeader>
                    <TableBody>
                      {track.entries.slice(0, 25).map((e, i) => (
                        <TableRow key={i}>
                          <TableCell className="text-slate-600">{e.date}</TableCell>
                          <TableCell className="text-slate-600">{e.breakout_type ?? UNKNOWN}</TableCell>
                          <TableCell className="text-right">{e.entry_price != null ? `$${fmtNum(e.entry_price)}` : UNKNOWN}</TableCell>
                          <TableCell>
                            {e.success === null ? (
                              <Chip size="sm" className="bg-slate-100 text-slate-500 text-xs">pending</Chip>
                            ) : e.success ? (
                              <Chip size="sm" className="bg-emerald-100 text-emerald-800 text-xs">success</Chip>
                            ) : (
                              <Chip size="sm" className="bg-red-100 text-red-700 text-xs">failed</Chip>
                            )}
                          </TableCell>
                          <TableCell className="text-right text-emerald-600">{fmtPct(e.max_gain_10d)}</TableCell>
                          <TableCell className="text-right text-red-600">{fmtPct(e.max_loss_10d)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  {track.entries.length > 25 && (
                    <p className="px-5 py-3 text-xs text-slate-400">Showing the latest 25 of {track.entries.length} events.</p>
                  )}
                </>
              )}
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  );
}
