'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
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
} from '@nextui-org/react';
import { HoverTip, TextField } from '@/components/ui/Field';
import { strategyApi, StrategyRankResult } from '@/services/api';
import PlanBar from '@/components/strategy/PlanBar';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SortHeader from '@/components/ui/SortHeader';
import { useTableSort } from '@/hooks/useTableSort';

// Position size is min(cap, accountRisk / stopRisk): for a fixed account it always ranks inversely to stop risk,
// so sorting by 1/risk orders the column correctly without depending on the two inputs above the table.
const ACCESSORS = {
  symbol: (r: StrategyRankResult) => r.symbol,
  dir: (r: StrategyRankResult) => r.direction,
  entry: (r: StrategyRankResult) => r.current_price,
  risk: (r: StrategyRankResult) => r.risk_pct,
  reward: (r: StrategyRankResult) => r.reward_pct_tp3,
  size: (r: StrategyRankResult) => (r.risk_pct > 0 ? 1 / r.risk_pct : null),
  score: (r: StrategyRankResult) => r.strategy_score,
};
type Col = keyof typeof ACCESSORS;

const DIRECTION_OPTIONS: { label: string; value: 'all' | 'long' | 'short' }[] = [
  { label: 'All', value: 'all' },
  { label: 'Long', value: 'long' },
  { label: 'Short', value: 'short' },
];

export default function StrategyPage() {
  const [direction, setDirection] = useState<'all' | 'long' | 'short'>('all');
  const [highConfidenceOnly, setHighConfidenceOnly] = useState(false);
  const [results, setResults] = useState<StrategyRankResult[]>([]);
  const router = useRouter();
  const { sorted, sortKey, sortDir, toggle } = useTableSort(results, ACCESSORS);
  const sortHeader = (key: Col, label: string, align: 'start' | 'end' = 'start') => (
    <SortHeader label={label} align={align} active={sortKey === key} dir={sortDir} onClick={() => toggle(key)} />
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [accountRiskPct, setAccountRiskPct] = useState(1);
  const [maxPositionPct, setMaxPositionPct] = useState(10);

  const fetchResults = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await strategyApi.getRank({
        direction,
        min_confidence: highConfidenceOnly ? 'high' : undefined,
        limit: 50,
      });
      setResults(resp.results);
    } catch {
      setError('Failed to load strategy rankings');
    } finally {
      setLoading(false);
    }
  }, [direction, highConfidenceOnly]);

  useEffect(() => {
    fetchResults();
  }, [fetchResults]);

  const positionSizeFor = (riskPct: number): number => {
    if (!riskPct || riskPct <= 0) return 0;
    return Math.min(maxPositionPct, (accountRiskPct / riskPct) * 100);
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-7xl">
        <div className="mb-6 px-1 pt-2 pb-1">
          <h1 className="large-title">Position Plans</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
            Entry / stop / take-profit plan for every active breakout, ranked by a risk-adjusted score —
            momentum + fundamentals + timeframe alignment, weighted against volatility-based risk.
          </p>
        </div>

        {/* Controls */}
        <Card className="mb-4 border border-black/[0.05] shadow-sm">
          <CardBody className="p-5 flex-row flex-wrap gap-x-5 gap-y-4 items-end">
            <div>
              <p className="mb-1 text-xs font-medium text-slate-600">Direction</p>
              <SegmentedControl
                aria-label="Direction"
                size="md"
                options={DIRECTION_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
                value={direction}
                onChange={setDirection}
              />
            </div>

            <Button
              size="sm"
              variant={highConfidenceOnly ? 'solid' : 'bordered'}
              className={highConfidenceOnly ? 'rounded-full bg-emerald-600 text-white text-xs' : 'rounded-full text-xs border-slate-300 text-slate-700'}
              onClick={() => setHighConfidenceOnly(!highConfidenceOnly)}
            >
              High Confidence Only
            </Button>

            <div className="hidden h-9 w-px bg-slate-200 md:block" />

            <div className="w-40">
              <TextField
                type="number"
                label="Account risk / trade"
                suffix="%"
                value={accountRiskPct.toString()}
                min={0.1}
                max={10}
                step={0.1}
                onChange={(e) => setAccountRiskPct(Number(e.target.value) || 0)}
              />
            </div>
            <div className="w-36">
              <TextField
                type="number"
                label="Max position"
                suffix="%"
                value={maxPositionPct.toString()}
                min={1}
                max={100}
                step={1}
                onChange={(e) => setMaxPositionPct(Number(e.target.value) || 0)}
              />
            </div>
            <p className="text-xs text-slate-400 max-w-xs">
              Position size below = min(Max Position %, Account Risk % ÷ Stop Risk %) — updates instantly, no server round-trip.
            </p>
          </CardBody>
        </Card>

        {/* Results */}
        <Card className="border border-black/[0.05] shadow-sm">
          <CardHeader className="px-5 pt-4 pb-2 flex justify-between items-center">
            <h3 className="text-[15px] font-semibold text-slate-900">Ranked Plans {!loading && `(${results.length})`}</h3>
          </CardHeader>
          <Divider className="bg-slate-200" />
          <CardBody className="p-0">
            {loading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner />
              </div>
            ) : error ? (
              <ErrorAlert title="Couldn’t load position plans" message={error} onRetry={fetchResults} className="m-4" />
            ) : results.length === 0 ? (
              <p className="text-center text-slate-400 text-sm py-12">No active breakout signals match these filters.</p>
            ) : (
              <Table
                removeWrapper
                aria-label="Ranked position plans"
                onRowAction={(key) => router.push(`/stock/${key}`)}
                className="text-xs"
                classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8 first:pl-5 last:pr-5', td: 'text-xs py-2 first:pl-5 last:pr-5', tr: 'cursor-pointer' }}
              >
                <TableHeader>
                  <TableColumn>{sortHeader('symbol', 'Symbol')}</TableColumn>
                  <TableColumn>{sortHeader('dir', 'Dir')}</TableColumn>
                  <TableColumn align="end">{sortHeader('entry', 'Entry', 'end')}</TableColumn>
                  <TableColumn align="end">{sortHeader('risk', 'Stop (risk %)', 'end')}</TableColumn>
                  <TableColumn>PLAN</TableColumn>
                  <TableColumn align="end">{sortHeader('reward', 'TP1/TP2/TP3', 'end')}</TableColumn>
                  <TableColumn align="end">{sortHeader('size', 'Position size', 'end')}</TableColumn>
                  <TableColumn align="end">{sortHeader('score', 'Score', 'end')}</TableColumn>
                </TableHeader>
                <TableBody>
                  {sorted.map((r) => (
                    <TableRow key={r.symbol}>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <Link href={`/stock/${r.symbol}`} className="font-semibold text-slate-800 hover:text-accent">
                            {r.symbol}
                          </Link>
                          {r.is_new && (
                            <Chip size="sm" className="bg-amber-100 text-amber-800 text-[10px] h-4 px-1">
                              NEW
                            </Chip>
                          )}
                        </div>
                        <p className="text-[10px] text-slate-400">{r.sector}</p>
                      </TableCell>
                      <TableCell>
                        <Chip
                          size="sm"
                          className={`text-xs ${r.direction === 'long' ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-700'}`}
                        >
                          {r.direction === 'long' ? 'LONG' : 'SHORT'}
                        </Chip>
                      </TableCell>
                      <TableCell className="text-right text-slate-700">${r.current_price.toFixed(2)}</TableCell>
                      <TableCell className="text-right">
                        <p className="text-red-600 font-semibold">${r.stop_loss_price.toFixed(2)}</p>
                        <p className="text-[10px] text-slate-400">-{r.risk_pct.toFixed(1)}%</p>
                      </TableCell>
                      <TableCell className="min-w-[180px]">
                        <PlanBar
                          plan={{
                            stop_loss_price: r.stop_loss_price,
                            entry_price: r.current_price,
                            tp1: r.tp1,
                            tp2: r.tp2,
                            tp3: r.tp3,
                          }}
                        />
                      </TableCell>
                      <TableCell className="text-right text-[11px] text-slate-600">
                        <p>${r.tp1.toFixed(2)} <span className="text-emerald-600">+{r.reward_pct_tp1.toFixed(1)}%</span></p>
                        <p>${r.tp2.toFixed(2)} <span className="text-emerald-600">+{r.reward_pct_tp2.toFixed(1)}%</span></p>
                        <p>${r.tp3.toFixed(2)} <span className="text-emerald-600">+{r.reward_pct_tp3.toFixed(1)}%</span></p>
                      </TableCell>
                      <TableCell className="text-right font-semibold text-slate-700">
                        {positionSizeFor(r.risk_pct).toFixed(1)}%
                      </TableCell>
                      <TableCell className="text-right">
                        <HoverTip
                          content={
                            <div className="text-xs p-1 space-y-0.5">
                              <p>Momentum: {r.momentum_score == null ? '—' : r.momentum_score.toFixed(0)} × 0.40</p>
                              <p>Fundamentals: {r.fundamentals_score == null ? '—' : r.fundamentals_score.toFixed(0)} × 0.25</p>
                              <p>Alignment: {r.alignment_score == null ? '—' : r.alignment_score.toFixed(0)} × 0.20</p>
                              <p>Risk bonus: {r.risk_bonus.toFixed(0)} × 0.15</p>
                              <div className="my-1 h-px bg-white/20" />
                              <p className="font-semibold">Confidence: {r.ml_confidence}</p>
                            </div>
                          }
                        >
                          <span className="font-bold text-slate-800 cursor-help border-b border-dotted border-slate-400">
                            {r.strategy_score.toFixed(0)}
                          </span>
                        </HoverTip>
                      </TableCell>
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
