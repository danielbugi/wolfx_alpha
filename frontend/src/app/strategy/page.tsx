'use client';

import React, { useState, useEffect, useCallback } from 'react';
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
  Input,
  Tooltip,
} from '@nextui-org/react';
import { strategyApi, StrategyRankResult } from '@/services/api';
import PlanBar from '@/components/strategy/PlanBar';

const DIRECTION_OPTIONS: { label: string; value: 'all' | 'long' | 'short' }[] = [
  { label: 'All', value: 'all' },
  { label: 'Long', value: 'long' },
  { label: 'Short', value: 'short' },
];

export default function StrategyPage() {
  const [direction, setDirection] = useState<'all' | 'long' | 'short'>('all');
  const [highConfidenceOnly, setHighConfidenceOnly] = useState(false);
  const [results, setResults] = useState<StrategyRankResult[]>([]);
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
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-7xl">
        <div className="mb-6 p-4 bg-white rounded-lg shadow-sm border border-slate-200">
          <h1 className="text-2xl font-bold text-slate-800">Trading Strategy</h1>
          <p className="text-slate-600 text-sm mt-1">
            Entry / stop / take-profit plan for every active breakout, ranked by a risk-adjusted score —
            momentum + fundamentals + timeframe alignment, weighted against volatility-based risk.
          </p>
        </div>

        {/* Controls */}
        <Card className="mb-4 shadow-sm border border-slate-200">
          <CardBody className="p-4 flex flex-wrap gap-4 items-end">
            <div>
              <p className="text-xs text-slate-500 mb-1.5 uppercase tracking-wide">Direction</p>
              <div className="flex gap-1">
                {DIRECTION_OPTIONS.map((o) => (
                  <Button
                    key={o.value}
                    size="sm"
                    variant={direction === o.value ? 'solid' : 'bordered'}
                    className={direction === o.value ? 'bg-slate-700 text-white text-xs' : 'text-xs border-slate-300'}
                    onClick={() => setDirection(o.value)}
                  >
                    {o.label}
                  </Button>
                ))}
              </div>
            </div>

            <Button
              size="sm"
              variant={highConfidenceOnly ? 'solid' : 'bordered'}
              className={highConfidenceOnly ? 'bg-emerald-600 text-white text-xs' : 'text-xs border-slate-300'}
              onClick={() => setHighConfidenceOnly(!highConfidenceOnly)}
            >
              High Confidence Only
            </Button>

            <Divider orientation="vertical" className="h-10 hidden md:block" />

            <div className="w-36">
              <Input
                type="number"
                size="sm"
                label="Account Risk % / trade"
                value={accountRiskPct.toString()}
                min={0.1}
                max={10}
                step={0.1}
                onChange={(e) => setAccountRiskPct(Number(e.target.value) || 0)}
              />
            </div>
            <div className="w-32">
              <Input
                type="number"
                size="sm"
                label="Max Position %"
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
        <Card className="shadow-sm border border-slate-200">
          <CardHeader className="p-3 pb-2 flex justify-between items-center">
            <h3 className="text-sm font-semibold text-slate-700">Ranked Plans {!loading && `(${results.length})`}</h3>
          </CardHeader>
          <Divider className="bg-slate-200" />
          <CardBody className="p-0">
            {loading ? (
              <div className="flex justify-center py-12">
                <Spinner color="primary" />
              </div>
            ) : error ? (
              <p className="text-center text-red-500 text-sm py-12">{error}</p>
            ) : results.length === 0 ? (
              <p className="text-center text-slate-400 text-sm py-12">No active breakout signals match these filters.</p>
            ) : (
              <Table
                removeWrapper
                className="text-xs"
                classNames={{ th: 'bg-slate-50 text-slate-600 font-medium text-xs h-8', td: 'text-xs py-2' }}
              >
                <TableHeader>
                  <TableColumn>SYMBOL</TableColumn>
                  <TableColumn>DIR</TableColumn>
                  <TableColumn align="end">ENTRY</TableColumn>
                  <TableColumn align="end">STOP (RISK%)</TableColumn>
                  <TableColumn>PLAN</TableColumn>
                  <TableColumn align="end">TP1/TP2/TP3</TableColumn>
                  <TableColumn align="end">POSITION SIZE</TableColumn>
                  <TableColumn align="end">SCORE</TableColumn>
                </TableHeader>
                <TableBody>
                  {results.map((r) => (
                    <TableRow key={r.symbol}>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <Link href={`/stock/${r.symbol}`} className="font-semibold text-slate-800 hover:text-cyan-700">
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
                        <Tooltip
                          content={
                            <div className="text-xs p-1 space-y-0.5">
                              <p>Momentum: {r.momentum_score == null ? '—' : r.momentum_score.toFixed(0)} × 0.40</p>
                              <p>Fundamentals: {r.fundamentals_score == null ? '—' : r.fundamentals_score.toFixed(0)} × 0.25</p>
                              <p>Alignment: {r.alignment_score == null ? '—' : r.alignment_score.toFixed(0)} × 0.20</p>
                              <p>Risk bonus: {r.risk_bonus.toFixed(0)} × 0.15</p>
                              <Divider className="my-1" />
                              <p className="font-semibold">Confidence: {r.ml_confidence}</p>
                            </div>
                          }
                        >
                          <span className="font-bold text-slate-800 cursor-help border-b border-dotted border-slate-400">
                            {r.strategy_score.toFixed(0)}
                          </span>
                        </Tooltip>
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
