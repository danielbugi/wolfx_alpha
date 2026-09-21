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
  Divider,
  Spinner,
  Progress,
  Tooltip as NextUITooltip,
} from '@nextui-org/react';
import {
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  BarChart,
  Bar,
} from 'recharts';
import { stockApi, formatters, isHttpStatus, Maybe, StockDetail, PriceBar, EarningsDateEntry } from '@/services/api';
import PlanBar from '@/components/strategy/PlanBar';
import LightweightCandlestickChart from '@/components/charts/LightweightCandlestickChart';
import { positionPct, nearestBarDate } from '@/lib/priceMath';
import { confidenceColor, gradeColor } from '@/lib/uiColors';

const RANGE_OPTIONS = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
];

function fmtPct(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined) return 'N/A';
  return `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;
}

function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return 'N/A';
  return v.toFixed(decimals);
}

function fmtBig(v: number | null | undefined): string {
  if (v === null || v === undefined) return 'N/A';
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
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

function TimeframeCard({
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
  const pos = positionPct(price ?? undefined, donchianLow ?? undefined, donchianHigh ?? undefined);
  return (
    <Card className="shadow-sm border border-slate-200">
      <CardBody className="p-4">
        <div className="flex justify-between items-center mb-2">
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide">{title}</p>
          {trend && (
            <Chip
              size="sm"
              className={
                trend === 'bullish'
                  ? 'bg-emerald-100 text-emerald-800 text-xs'
                  : trend === 'bearish'
                  ? 'bg-red-100 text-red-700 text-xs'
                  : 'bg-slate-100 text-slate-600 text-xs'
              }
            >
              {trend}
            </Chip>
          )}
        </div>
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>Low {donchianLow != null ? `$${donchianLow.toFixed(2)}` : 'N/A'}</span>
          <span>High {donchianHigh != null ? `$${donchianHigh.toFixed(2)}` : 'N/A'}</span>
        </div>
        <Progress value={pos} className="mb-1" classNames={{ indicator: 'bg-slate-700' }} size="sm" />
        <p className="text-xs text-slate-500">Position in channel: {pos.toFixed(0)}%</p>
        {extra && <p className="text-xs text-slate-500 mt-1">{extra}</p>}
      </CardBody>
    </Card>
  );
}

function ScoreBar({ label, value }: { label: string; value: number | null | undefined }) {
  const v = value ?? 0;
  const color = v >= 70 ? 'bg-emerald-500' : v >= 45 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="mb-2.5">
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-600">{label}</span>
        <span className="font-semibold text-slate-800">{value != null ? value.toFixed(0) : 'N/A'}</span>
      </div>
      <Progress value={v} classNames={{ indicator: color }} size="sm" />
    </div>
  );
}

export default function StockDetailPage() {
  const params = useParams();
  const symbol = (params?.symbol as string)?.toUpperCase();

  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [bars, setBars] = useState<PriceBar[]>([]);
  const [earnings, setEarnings] = useState<EarningsDateEntry[]>([]);
  const [rangeDays, setRangeDays] = useState(180);
  const [loading, setLoading] = useState(true);
  const [chartLoading, setChartLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDonchian, setShowDonchian] = useState(true);
  const [showVolume, setShowVolume] = useState(true);

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
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 to-cyan-50">
        <div className="text-center">
          <Spinner size="lg" color="primary" className="mb-4" />
          <p className="text-slate-600 text-sm">Loading {symbol}...</p>
        </div>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 to-cyan-50 p-4">
        <Card className="max-w-md w-full shadow-lg">
          <CardBody className="text-center p-8">
            <div className="text-red-500 text-4xl mb-4">!</div>
            <h2 className="text-xl font-semibold text-slate-800 mb-2">{error || 'Not found'}</h2>
            <Button color="primary" variant="solid" onClick={fetchDetail} className="w-full">
              Retry
            </Button>
          </CardBody>
        </Card>
      </div>
    );
  }

  const snap = detail.snapshot;
  const rating = detail.ai_rating;
  const timeframes = extractTimeframes(detail);
  const hasSignal = detail.data_source === 'active_signal';
  const track = detail.breakout_track_record;

  const priceChangePct = snap.price_change_pct ?? 0;
  const isUp = priceChangePct >= 0;

  // Earnings markers must land on an existing bar (see nearestBarDate) --
  // a date too far past the last bar (e.g. an upcoming report) can't be
  // placed on the chart, so surface it as a caption instead of silently
  // dropping it.
  const unplottedUpcomingEarnings = earnings.find(
    (e) => nearestBarDate(bars, e.date, 3) === null && new Date(e.date) > new Date(bars[bars.length - 1]?.date || 0)
  );

  const financials = [...detail.quarterly_financials].reverse();

  // Derived client-side from the same quarterly data shown below -- no
  // separate backend call needed. Mirrors the deep-value scan's turnaround
  // definition (latest quarter profitable, the one before it a loss).
  const [latestQ, previousQ] = detail.quarterly_financials;
  const isTurnaround =
    !!latestQ?.net_income && !!previousQ?.net_income && latestQ.net_income > 0 && previousQ.net_income < 0;

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-7xl space-y-4">
        {/* Header */}
        <Card className="shadow-sm border border-slate-200">
          <CardBody className="p-4">
            <div className="flex flex-wrap justify-between items-center gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <h1 className="text-2xl font-bold text-slate-800">{detail.symbol}</h1>
                  {snap.quality_grade && (
                    <Chip size="sm" className={`${gradeColor(snap.quality_grade)} text-xs`}>
                      Grade {snap.quality_grade}
                    </Chip>
                  )}
                  <Chip
                    size="sm"
                    className={`text-xs ${hasSignal ? confidenceColor(rating.ml_confidence) : 'bg-slate-100 text-slate-500'}`}
                  >
                    {hasSignal ? `AI: ${rating.ml_confidence?.replace('_', ' ')}` : 'No active signal'}
                  </Chip>
                  {isTurnaround && (
                    <Chip size="sm" className="bg-emerald-100 text-emerald-800 text-xs">
                      🔄 Turnaround
                    </Chip>
                  )}
                </div>
                <p className="text-slate-500 text-sm mt-1">
                  {snap.sector || 'Unknown sector'}
                  {snap.industry ? ` · ${snap.industry}` : ''}
                </p>
              </div>
              <div className="text-right">
                <p className="text-2xl font-bold text-slate-800">${fmtNum(snap.current_price)}</p>
                <p className={`text-sm font-semibold ${isUp ? 'text-emerald-600' : 'text-red-600'}`}>
                  {fmtPct(priceChangePct)}
                </p>
              </div>
            </div>
          </CardBody>
        </Card>

        {/* Price chart */}
        <Card className="shadow-sm border border-slate-200">
          <CardHeader className="p-3 pb-2 flex flex-wrap justify-between items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-700">Price & Donchian Channel</h3>
            <div className="flex flex-wrap gap-1">
              <Button
                size="sm"
                variant={showDonchian ? 'solid' : 'light'}
                className={showDonchian ? 'bg-slate-700 text-white text-xs' : 'text-xs'}
                onClick={() => setShowDonchian((v) => !v)}
              >
                Donchian
              </Button>
              <Button
                size="sm"
                variant={showVolume ? 'solid' : 'light'}
                className={showVolume ? 'bg-slate-700 text-white text-xs' : 'text-xs'}
                onClick={() => setShowVolume((v) => !v)}
              >
                Volume
              </Button>
              <Divider orientation="vertical" className="h-6 mx-1 bg-slate-200" />
              {RANGE_OPTIONS.map((r) => (
                <Button
                  key={r.days}
                  size="sm"
                  variant={rangeDays === r.days ? 'solid' : 'light'}
                  className={rangeDays === r.days ? 'bg-slate-700 text-white text-xs' : 'text-xs'}
                  onClick={() => changeRange(r.days)}
                  isLoading={chartLoading && rangeDays === r.days}
                >
                  {r.label}
                </Button>
              ))}
            </div>
          </CardHeader>
          <Divider className="bg-slate-200" />
          <CardBody className="p-2">
            <LightweightCandlestickChart
              bars={bars}
              earnings={earnings}
              variant="full"
              height={380}
              showDonchian={showDonchian}
              showVolume={showVolume}
            />
            {unplottedUpcomingEarnings && (
              <p className="text-xs text-slate-500 mt-1 px-1">
                Next earnings: {unplottedUpcomingEarnings.date} (beyond the charted range)
              </p>
            )}
          </CardBody>
        </Card>

        {/* Multi-timeframe */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <TimeframeCard
            title="Daily"
            donchianHigh={timeframes.daily.donchianHigh}
            donchianLow={timeframes.daily.donchianLow}
            price={snap.current_price}
            extra={timeframes.daily.rsi != null ? `RSI ${timeframes.daily.rsi.toFixed(1)}` : undefined}
          />
          <TimeframeCard
            title="Weekly"
            donchianHigh={timeframes.weekly.donchianHigh}
            donchianLow={timeframes.weekly.donchianLow}
            price={timeframes.weekly.close ?? snap.current_price}
            trend={timeframes.weekly.trend}
            extra={timeframes.weekly.rsi != null ? `RSI ${timeframes.weekly.rsi.toFixed(1)}` : undefined}
          />
          <TimeframeCard
            title="Monthly"
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

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* AI Rating & Confidence */}
          <Card className="shadow-sm border border-slate-200">
            <CardHeader className="bg-slate-700 text-white p-3">
              <h3 className="text-sm font-semibold">AI Rating & Confidence</h3>
            </CardHeader>
            <CardBody className="p-4">
              {hasSignal ? (
                <>
                  <div className="flex flex-wrap gap-2 mb-3">
                    <Chip size="sm" className="bg-slate-100 text-slate-700 text-xs">
                      {rating.signal_type?.replace(/_/g, ' ')}
                    </Chip>
                    <Chip size="sm" className={`${confidenceColor(rating.ml_confidence)} text-xs`}>
                      Confidence: {rating.ml_confidence?.replace('_', ' ')}
                    </Chip>
                    {rating.ml_momentum_probability != null && (
                      <Chip size="sm" className="bg-cyan-100 text-cyan-800 text-xs">
                        ML score: {rating.ml_momentum_probability.toFixed(0)}%
                      </Chip>
                    )}
                    {rating.alignment_grade && (
                      <Chip size="sm" className="bg-slate-100 text-slate-700 text-xs">
                        Alignment: {rating.alignment_score}% ({rating.alignment_grade})
                      </Chip>
                    )}
                  </div>
                  <p className="text-sm text-slate-600 mb-3">{rating.reasoning}</p>

                  {rating.plan ? (
                    <>
                      <div className="flex justify-between items-center mb-1">
                        <Chip size="sm" className={`text-xs ${rating.plan.direction === 'long' ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-700'}`}>
                          {rating.plan.direction === 'long' ? 'LONG' : 'SHORT'}
                        </Chip>
                        <NextUITooltip
                          content={
                            <div className="text-xs p-1 space-y-0.5">
                              <p>Momentum: {rating.plan.momentum_score == null ? '—' : rating.plan.momentum_score.toFixed(0)} × 0.40</p>
                              <p>Fundamentals: {rating.plan.fundamentals_score == null ? '—' : rating.plan.fundamentals_score.toFixed(0)} × 0.25</p>
                              <p>Alignment: {rating.plan.alignment_score == null ? '—' : rating.plan.alignment_score.toFixed(0)} × 0.20</p>
                              <p>Risk bonus: {rating.plan.risk_bonus.toFixed(0)} × 0.15</p>
                            </div>
                          }
                        >
                          <span className="text-xs font-bold text-slate-700 cursor-help border-b border-dotted border-slate-400">
                            Strategy Score: {rating.plan.strategy_score.toFixed(0)}
                          </span>
                        </NextUITooltip>
                      </div>

                      <PlanBar
                        plan={{
                          stop_loss_price: rating.plan.stop_loss_price,
                          entry_price: rating.plan.entry_price,
                          tp1: rating.plan.tp1,
                          tp2: rating.plan.tp2,
                          tp3: rating.plan.tp3,
                        }}
                      />

                      <div className="grid grid-cols-4 gap-2 text-center mt-2">
                        <div className="bg-slate-50 rounded p-2">
                          <p className="text-xs text-slate-500">Stop</p>
                          <p className="text-sm font-semibold text-red-600">${fmtNum(rating.plan.stop_loss_price)}</p>
                          <p className="text-[10px] text-slate-400">-{rating.plan.risk_pct.toFixed(1)}%</p>
                        </div>
                        <div className="bg-slate-50 rounded p-2">
                          <p className="text-xs text-slate-500">TP1</p>
                          <p className="text-sm font-semibold text-emerald-600">${fmtNum(rating.plan.tp1)}</p>
                          <p className="text-[10px] text-slate-400">+{rating.plan.reward_pct_tp1.toFixed(1)}%</p>
                        </div>
                        <div className="bg-slate-50 rounded p-2">
                          <p className="text-xs text-slate-500">TP2</p>
                          <p className="text-sm font-semibold text-emerald-600">${fmtNum(rating.plan.tp2)}</p>
                          <p className="text-[10px] text-slate-400">+{rating.plan.reward_pct_tp2.toFixed(1)}%</p>
                        </div>
                        <div className="bg-slate-50 rounded p-2">
                          <p className="text-xs text-slate-500">TP3</p>
                          <p className="text-sm font-semibold text-emerald-600">${fmtNum(rating.plan.tp3)}</p>
                          <p className="text-[10px] text-slate-400">+{rating.plan.reward_pct_tp3.toFixed(1)}%</p>
                        </div>
                      </div>
                      <p className="text-xs text-slate-500 mt-3">
                        Suggested position size at 1% account risk: {Math.min(10, 1 / rating.plan.risk_pct * 100).toFixed(1)}%
                        of portfolio (capped at 10%) —{' '}
                        <Link href="/strategy" className="underline hover:text-cyan-700">
                          adjust assumptions on the Strategy page
                        </Link>
                      </p>
                    </>
                  ) : (
                    <p className="text-xs text-slate-400">Strategy plan unavailable for this signal.</p>
                  )}
                </>
              ) : (
                <div className="text-center py-4">
                  <p className="text-slate-500 text-sm">{rating.reasoning}</p>
                  <p className="text-xs text-slate-400 mt-2">
                    Shown below is the latest available technical/fundamental snapshot for this symbol.
                  </p>
                </div>
              )}
            </CardBody>
          </Card>

          {/* Fundamentals & Quality */}
          <Card className="shadow-sm border border-slate-200">
            <CardHeader className="p-3 pb-2">
              <h3 className="text-sm font-semibold text-slate-700">Fundamentals & Quality</h3>
            </CardHeader>
            <Divider className="bg-slate-200" />
            <CardBody className="p-4">
              <ScoreBar label="Growth" value={snap.growth_score} />
              <ScoreBar label="Profitability" value={snap.profitability_score} />
              <ScoreBar label="Financial Health" value={snap.financial_health_score} />
              <ScoreBar label="Valuation" value={snap.valuation_score} />
              <Divider className="my-2 bg-slate-100" />
              <ScoreBar label="Overall Quality" value={snap.overall_quality_score} />

              <div className="grid grid-cols-3 gap-x-4 gap-y-2 mt-3 text-xs">
                <div><span className="text-slate-500">P/E</span> <span className="font-semibold text-slate-800">{fmtNum(snap.pe_ratio)}</span></div>
                <div><span className="text-slate-500">P/B</span> <span className="font-semibold text-slate-800">{fmtNum(snap.pb_ratio)}</span></div>
                <div><span className="text-slate-500">P/S</span> <span className="font-semibold text-slate-800">{fmtNum(snap.ps_ratio)}</span></div>
                <div><span className="text-slate-500">PEG</span> <span className="font-semibold text-slate-800">{fmtNum(snap.peg_ratio)}</span></div>
                <div><span className="text-slate-500">Beta</span> <span className="font-semibold text-slate-800">{fmtNum(snap.beta)}</span></div>
                <div><span className="text-slate-500">Div Yield</span> <span className="font-semibold text-slate-800">{fmtNum(snap.dividend_yield)}%</span></div>
                <div><span className="text-slate-500">Mkt Cap</span> <span className="font-semibold text-slate-800">{snap.market_cap_formatted || formatters.marketCap(snap.market_cap)}</span></div>
              </div>
            </CardBody>
          </Card>
        </div>

        {/* Financials trend */}
        <Card className="shadow-sm border border-slate-200">
          <CardHeader className="p-3 pb-2">
            <h3 className="text-sm font-semibold text-slate-700">Quarterly Financials</h3>
          </CardHeader>
          <Divider className="bg-slate-200" />
          <CardBody className="p-3">
            {financials.length === 0 ? (
              <p className="text-sm text-slate-400 text-center py-6">No quarterly financial data available for this symbol.</p>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={financials}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="quarter" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} width={60} tickFormatter={(v) => fmtBig(v)} />
                    <Tooltip formatter={(v) => (typeof v === 'number' ? fmtBig(v) : 'N/A')} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                    <Bar dataKey="revenue" fill="#0891b2" name="Revenue" />
                    <Bar dataKey="net_income" fill="#10b981" name="Net Income" />
                  </BarChart>
                </ResponsiveContainer>
                <Table
                  removeWrapper
                  className="text-xs mt-3"
                  classNames={{ th: 'bg-slate-50 text-slate-600 font-medium text-xs h-7', td: 'text-xs py-1.5' }}
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

        {/* Breakout track record */}
        <Card className="shadow-sm border border-slate-200">
          <CardHeader className="p-3 pb-2">
            <div className="flex justify-between items-center w-full">
              <h3 className="text-sm font-semibold text-slate-700">Breakout Track Record</h3>
              {track.total_breakouts > 0 && (
                <div className="flex gap-4 text-xs text-slate-500">
                  <span>{track.total_breakouts} events</span>
                  {track.win_rate != null && <span className="font-semibold text-emerald-600">{track.win_rate}% win rate</span>}
                  {track.avg_gain_10d != null && <span>avg gain {fmtPct(track.avg_gain_10d)}</span>}
                </div>
              )}
            </div>
          </CardHeader>
          <Divider className="bg-slate-200" />
          <CardBody className="p-0">
            {track.entries.length === 0 ? (
              <p className="text-sm text-slate-400 text-center py-6">No historical breakout events recorded for this symbol.</p>
            ) : (
              <Table
                removeWrapper
                className="text-xs"
                classNames={{ th: 'bg-slate-50 text-slate-600 font-medium text-xs h-7', td: 'text-xs py-1.5' }}
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
                      <TableCell className="text-slate-600">{e.breakout_type}</TableCell>
                      <TableCell className="text-right">${fmtNum(e.entry_price)}</TableCell>
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
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
