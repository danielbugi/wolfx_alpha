'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { Card, CardBody, Table, TableHeader, TableColumn, TableBody, TableRow, TableCell, Chip } from '@nextui-org/react';
import { ExclamationTriangleIcon, StarIcon as StarSolid } from '@heroicons/react/20/solid';
import { StarIcon as StarOutline } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { HoverTip, ProgressBar } from '@/components/ui/Field';
import {
  stockApi,
  marketApi,
  formatters,
  isHttpStatus,
  Maybe,
  StockDetail,
  PriceBar,
  EarningsDateEntry,
  SectorHistoryEntry,
} from '@/services/api';
import PlanBar from '@/components/strategy/PlanBar';
import StockChart from '@/components/charts/StockChart';
import KeyLevels from '@/components/stock/KeyLevels';
import FinancialGrowth from '@/components/stock/FinancialGrowth';
import EarningsPanel from '@/components/stock/EarningsPanel';
import PeersPanel from '@/components/stock/PeersPanel';
import NewsPanel from '@/components/stock/NewsPanel';
import { reactionPct } from '@/components/stock/EarningsPanel';
import { useWatchlist } from '@/lib/watchlist';
import { StockNavButtons, StockNavCaption, useStockNav } from '@/components/stock/StockNavArrows';
import { positionPct } from '@/lib/priceMath';
import { gradeColor } from '@/lib/uiColors';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import SearchBox from '@/components/layout/SearchBox';

// The chart builds weekly/monthly candles and the 200-day average from daily bars, so load the longest history the
// API serves (backend caps price history at 730 days) once; switching interval or range is then instant.
const HISTORY_DAYS = 730;
const EARNINGS_LIMIT = 16;

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

/** Trailing returns from daily bars: close now vs the close N trading days back (YTD: vs last close of the prior
 * year). Null when the history doesn't reach back that far — never a partial-period figure. */
function trailingReturns(bars: PriceBar[]) {
  const last = bars[bars.length - 1];
  const back = (n: number) => (bars.length > n ? (last.close / bars[bars.length - 1 - n].close - 1) * 100 : null);
  const yearStart = last ? `${last.date.slice(0, 4)}-01-01` : '';
  const prevYearClose = [...bars].reverse().find((b) => b.date < yearStart);
  return [
    { label: '1D', value: back(1) },
    { label: '1W', value: back(5) },
    { label: '1M', value: back(21) },
    { label: '3M', value: back(63) },
    { label: '6M', value: back(126) },
    { label: 'YTD', value: last && prevYearClose ? (last.close / prevYearClose.close - 1) * 100 : null },
    { label: '1Y', value: back(252) },
  ];
}

/** Compact price for the narrow 52-week cell: whole dollars from $100 up, cents below. */
function fmtRange(v: number): string {
  return v >= 100 ? v.toFixed(0) : v.toFixed(2);
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

/** A page section: a card with the page's standard padding. */
function Section({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <Card className={clsx('border border-black/[0.05] shadow-sm', className)}>
      <CardBody className="p-5">{children}</CardBody>
    </Card>
  );
}

export default function StockDetailPage() {
  const params = useParams();
  const symbol = (params?.symbol as string)?.toUpperCase();
  // Previous/next stock in the list this one was opened from (arrows in the header, ← / → keys).
  const nav = useStockNav(symbol);
  const watchlist = useWatchlist();

  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [bars, setBars] = useState<PriceBar[]>([]);
  const [earnings, setEarnings] = useState<EarningsDateEntry[]>([]);
  const [sectorHistory, setSectorHistory] = useState<SectorHistoryEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Guards the un-awaited earnings fetch against landing after navigating to
  // a different symbol.
  const latestSymbolRef = useRef(symbol);
  latestSymbolRef.current = symbol;

  const fetchDetail = useCallback(async () => {
    if (!symbol) return;
    try {
      setLoading(true);
      setError(null);
      // Earnings markers come from yfinance (~1s cold) -- fire them off on
      // their own so the page and chart render as soon as detail + price
      // history are in, and the markers pop in afterwards. Not awaited on
      // purpose; a failure just means no markers.
      setEarnings([]);
      stockApi
        .getEarnings(symbol, EARNINGS_LIMIT)
        .then((r) => {
          if (latestSymbolRef.current === symbol) setEarnings(r.earnings_dates);
        })
        .catch(() => {});

      const [d, ph] = await Promise.all([stockApi.getDetail(symbol), stockApi.getPriceHistory(symbol, HISTORY_DAYS)]);
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

  // Sector history (shared with the dashboard) for "90 days vs sector"; optional — the row shows "—" without it.
  useEffect(() => {
    marketApi
      .getSectorHistory(90)
      .then((r) => setSectorHistory(r.sectors))
      .catch(() => setSectorHistory(null));
  }, []);

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
  const asOfDate = detail.data_source === 'no_signal' ? detail.snapshot.as_of_date : null;

  const priceChangePct = snap.price_change_pct;
  const isUp = (priceChangePct ?? 0) >= 0;

  const nextEarnings = earnings
    .filter((e) => daysUntil(e.date) >= 0)
    .sort((a, b) => a.date.localeCompare(b.date))[0];
  const daysToEarnings = nextEarnings ? daysUntil(nextEarnings.date) : null;
  // Typical size of the move around past reports (same 2-day window as the Earnings section), for the header badge.
  const pastMoves = earnings
    .filter((e) => daysUntil(e.date) < 0)
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, 8)
    .map((e) => reactionPct(bars, e.date))
    .filter((v): v is number => v != null);
  const avgEarningsMove = pastMoves.length >= 3 ? pastMoves.reduce((a, v) => a + Math.abs(v), 0) / pastMoves.length : null;
  const performance = bars.length ? trailingReturns(bars) : [];

  const financials = [...detail.quarterly_financials].reverse();

  // Derived client-side from the same quarterly data shown below -- no
  // separate backend call needed. Mirrors the deep-value scan's turnaround
  // definition (latest quarter profitable, the one before it a loss).
  const [latestQ, previousQ] = detail.quarterly_financials;
  const isTurnaround =
    !!latestQ?.net_income && !!previousQ?.net_income && latestQ.net_income > 0 && previousQ.net_income < 0;

  // 52-week range for the key-numbers strip (same rule as Key levels: needs a full year of bars).
  const year = bars.slice(-252);
  const hasYear = year.length >= 240;
  const yearHighs = year.map((b) => b.high).filter((v): v is number => v != null);
  const yearLows = year.map((b) => b.low).filter((v): v is number => v != null);
  const hi52 = hasYear && yearHighs.length ? Math.max(...yearHighs) : null;
  const lo52 = hasYear && yearLows.length ? Math.min(...yearLows) : null;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-7xl space-y-4">
        {/* Header -- pinned while the rest scrolls beneath it on larger screens. Not on phones: it wraps to several
            lines there and would cover too much of a small screen. */}
        <Card className="material-thick z-20 border border-black/[0.05] shadow-sm md:sticky md:top-[4.25rem] lg:top-3">
          <CardBody className="flex-row flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 py-3.5">
            <div className="flex min-w-0 items-center gap-3">
              <StockNavButtons nav={nav} />
              <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="large-title">{detail.symbol}</h1>
                <button
                  type="button"
                  onClick={() => watchlist.toggle(detail.symbol)}
                  aria-pressed={watchlist.has(detail.symbol)}
                  aria-label={watchlist.has(detail.symbol) ? `Remove ${detail.symbol} from watchlist` : `Add ${detail.symbol} to watchlist`}
                  title={watchlist.has(detail.symbol) ? 'In your watchlist' : 'Add to watchlist'}
                  className="press -ml-1 flex h-8 w-8 items-center justify-center rounded-full hover:bg-black/[0.05]"
                >
                  {watchlist.has(detail.symbol) ? (
                    <StarSolid className="h-5 w-5 text-amber-400" />
                  ) : (
                    <StarOutline className="h-5 w-5 text-slate-400" />
                  )}
                </button>
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
                    {avgEarningsMove != null && ` · avg move ±${avgEarningsMove.toFixed(1)}%`}
                  </Chip>
                )}
              </div>
              <p className="mt-0.5 truncate text-sm text-slate-500">
                {snap.sector || 'Unknown sector'}
                {snap.industry ? ` · ${snap.industry}` : ''}
                {asOfDate ? ` · data as of ${asOfDate}` : ''}
              </p>
              {nav.list && nav.index >= 0 && (
                <p className="mt-0.5 text-xs text-slate-400">
                  <StockNavCaption nav={nav} />
                </p>
              )}
              </div>
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
          <CardBody className="grid grid-cols-2 divide-black/[0.06] p-0 sm:grid-cols-4 lg:grid-cols-8 lg:divide-x">
            <Stat label="Market cap" value={snap.market_cap_formatted || (snap.market_cap != null ? formatters.marketCap(snap.market_cap) : UNKNOWN)} />
            <Stat label="P/E" value={fmtNum(snap.pe_ratio, 1)} />
            <Stat label="52-week range" value={hi52 != null && lo52 != null ? `${fmtRange(lo52)} – ${fmtRange(hi52)}` : UNKNOWN} />
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
          {performance.length > 0 && (
            <div className="grid grid-cols-4 gap-y-2 border-t border-black/[0.06] px-4 py-3 sm:grid-cols-7" aria-label="Performance">
              {performance.map((p) => (
                <div key={p.label} className="text-center">
                  <p className="text-[11px] font-medium text-slate-500">{p.label}</p>
                  <p
                    className={clsx(
                      'text-[13px] font-semibold tabular-nums',
                      p.value == null ? 'text-slate-400' : p.value >= 0 ? 'text-emerald-600' : 'text-red-600',
                    )}
                  >
                    {fmtPct(p.value)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Chart */}
        <Card className="border border-black/[0.05] shadow-sm">
          <div className="px-5 pb-3 pt-4">
            <h3 className="text-[15px] font-semibold text-slate-900">Chart</h3>
          </div>
          <StockChart bars={bars} earnings={earnings} breakouts={track.entries} height={420} />
          <div className="mt-2 grid grid-cols-1 gap-5 border-t border-black/[0.06] px-5 py-4 md:grid-cols-3">
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

        {/* Signal & plan next to key levels */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
          <Section className="lg:col-span-3">
            {hasSignal ? (
              <div className="space-y-5">
                {rating.plan ? (
                  <div>
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
                  </div>
                ) : (
                  <p className="text-sm text-slate-400">Strategy plan unavailable for this signal.</p>
                )}

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <p className="mb-2 text-xs font-semibold text-slate-700">Signal</p>
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

                  {/* The ML score is shown for reference only: it has not shown a reliable edge in validation, so it is
                      labelled as experimental and kept out of the header. */}
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
              <div>
                <SectionTitle>Signal</SectionTitle>
                <p className="text-sm text-slate-600">{rating.reasoning || 'No active breakout signal for this symbol right now.'}</p>
                <p className="mt-2 text-xs text-slate-400">The chart and the sections below show the latest available data.</p>
              </div>
            )}
          </Section>

          <Section className="lg:col-span-2">
            <KeyLevels bars={bars} price={snap.current_price} atr={timeframes.daily.atr} sector={snap.sector} sectorHistory={sectorHistory} />
          </Section>
        </div>

        <Section>
          <NewsPanel symbol={detail.symbol} />
        </Section>

        <Section>
          <FinancialGrowth quarters={financials} />
        </Section>

        <Section>
          <EarningsPanel earnings={earnings} bars={bars} />
        </Section>

        {/* Fundamentals & valuation */}
        <Section>
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
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
          </div>
        </Section>

        <Section>
          <PeersPanel symbol={detail.symbol} sector={snap.sector} />
        </Section>

        {/* Breakout track record */}
        <Section>
          <SectionTitle>Breakout track record</SectionTitle>
          {track.entries.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-400">No historical breakout events recorded for this symbol.</p>
          ) : (
            <>
              <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ['Breakout events', String(track.total_breakouts)],
                  ['Win rate', track.win_rate != null ? `${track.win_rate}%` : UNKNOWN],
                  ['Avg max gain (10d)', fmtPct(track.avg_gain_10d)],
                  ['Avg max loss (10d)', fmtPct(track.avg_loss_10d)],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-xl bg-slate-50 px-3 py-2.5">
                    <p className="text-[11px] font-medium text-slate-500">{label}</p>
                    <p className="text-[15px] font-semibold tabular-nums text-slate-900">{value}</p>
                  </div>
                ))}
              </div>
              <Table
                removeWrapper
                aria-label="Breakout history"
                classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8', td: 'text-[13px] py-2 tabular-nums' }}
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
              {track.entries.length > 25 && <p className="mt-3 text-xs text-slate-400">Showing the latest 25 of {track.entries.length} events.</p>}
            </>
          )}
        </Section>
      </div>
    </div>
  );
}
