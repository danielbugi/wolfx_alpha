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
  Chip,
  Divider,
  Spinner,
  Input,
  Button,
} from '@nextui-org/react';
import { deepValueApi, DeepValueEntry } from '@/services/api';

function gradeColor(grade: string | null) {
  switch (grade) {
    case 'A':
      return 'bg-emerald-100 text-emerald-800';
    case 'B':
      return 'bg-cyan-100 text-cyan-800';
    case 'C':
      return 'bg-amber-100 text-amber-800';
    default:
      return 'bg-red-100 text-red-700';
  }
}

function fmtMoney(v: number | undefined): string {
  if (v == null) return 'N/A';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '+';
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

export default function AlertsPage() {
  const [nearLowPct, setNearLowPct] = useState(15);
  const [minValuationScore, setMinValuationScore] = useState(12);
  const [minFinancialHealthScore, setMinFinancialHealthScore] = useState(10);

  const [turnaroundAlerts, setTurnaroundAlerts] = useState<DeepValueEntry[]>([]);
  const [deepValueWatch, setDeepValueWatch] = useState<DeepValueEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchScan = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await deepValueApi.scan({
        near_low_pct: nearLowPct,
        min_valuation_score: minValuationScore,
        min_financial_health_score: minFinancialHealthScore,
      });
      setTurnaroundAlerts(resp.turnaround_alerts);
      setDeepValueWatch(resp.deep_value_watch);
    } catch {
      setError('Failed to load deep value scan');
    } finally {
      setLoading(false);
    }
  }, [nearLowPct, minValuationScore, minFinancialHealthScore]);

  useEffect(() => {
    fetchScan();
  }, [fetchScan]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-7xl space-y-4">
        <div className="p-4 bg-white rounded-lg shadow-sm border border-slate-200">
          <h1 className="text-2xl font-bold text-slate-800">Deep Value & Turnaround Alerts</h1>
          <p className="text-slate-600 text-sm mt-1">
            Stocks trading near their 12-month low at a cheap valuation, without being a financial-health
            trap. <span className="font-semibold text-slate-700">Turnaround Alerts</span> are the subset that
            also just flipped from a quarterly net loss to a net profit — the highest-conviction setup.
          </p>
        </div>

        {/* Thresholds */}
        <Card className="shadow-sm border border-slate-200">
          <CardBody className="p-4 flex flex-wrap gap-4 items-end">
            <div className="w-44">
              <Input
                type="number"
                size="sm"
                label="Near Low % (max above 12mo low)"
                value={nearLowPct.toString()}
                min={0}
                max={100}
                step={1}
                onChange={(e) => setNearLowPct(Number(e.target.value) || 0)}
              />
            </div>
            <div className="w-44">
              <Input
                type="number"
                size="sm"
                label="Min Valuation Score (/25)"
                value={minValuationScore.toString()}
                min={0}
                max={25}
                step={1}
                onChange={(e) => setMinValuationScore(Number(e.target.value) || 0)}
              />
            </div>
            <div className="w-48">
              <Input
                type="number"
                size="sm"
                label="Min Financial Health (/25)"
                value={minFinancialHealthScore.toString()}
                min={0}
                max={25}
                step={1}
                onChange={(e) => setMinFinancialHealthScore(Number(e.target.value) || 0)}
              />
            </div>
            <Button size="sm" className="bg-slate-700 text-white" onClick={fetchScan} isLoading={loading}>
              Rescan
            </Button>
            <p className="text-xs text-slate-400 max-w-xs">
              The financial-health floor exists to filter out stocks that are cheap because they&apos;re failing,
              not because they&apos;re undervalued.
            </p>
          </CardBody>
        </Card>

        {loading ? (
          <div className="flex justify-center py-16">
            <Spinner color="primary" />
          </div>
        ) : error ? (
          <p className="text-center text-red-500 text-sm py-16">{error}</p>
        ) : (
          <>
            {/* Turnaround Alerts */}
            <Card className="shadow-sm border-2 border-emerald-300">
              <CardHeader className="bg-emerald-600 text-white p-4">
                <div>
                  <h3 className="text-base font-semibold">🚀 Turnaround Alerts</h3>
                  <p className="text-emerald-100 text-xs mt-1">Near lows + cheap + just flipped loss → profit</p>
                </div>
              </CardHeader>
              <CardBody className="p-0">
                {turnaroundAlerts.length === 0 ? (
                  <p className="text-center text-slate-400 text-sm py-8">
                    No turnaround matches right now — this is expected to be rare. Check back after future
                    pipeline/data runs.
                  </p>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-4">
                    {turnaroundAlerts.map((e) => (
                      <Link
                        key={e.symbol}
                        href={`/stock/${e.symbol}`}
                        className="block border border-emerald-200 rounded-lg p-3 hover:shadow-md transition-shadow"
                      >
                        <div className="flex justify-between items-center mb-1">
                          <span className="font-bold text-slate-800">{e.symbol}</span>
                          {e.quality_grade && (
                            <Chip size="sm" className={`text-xs ${gradeColor(e.quality_grade)}`}>
                              Grade {e.quality_grade}
                            </Chip>
                          )}
                        </div>
                        <p className="text-xs text-slate-500 mb-2">{e.sector} · ${e.current_price.toFixed(2)}</p>
                        <p className="text-sm">
                          <span className="text-red-600 font-semibold">{fmtMoney(e.previous_net_income)}</span>
                          <span className="text-slate-400 mx-1">→</span>
                          <span className="text-emerald-600 font-semibold">{fmtMoney(e.latest_net_income)}</span>
                        </p>
                        <p className="text-[10px] text-slate-400 mt-1">
                          {e.previous_quarter} → {e.latest_quarter}
                        </p>
                        <div className="flex gap-3 mt-2 text-xs text-slate-500">
                          <span>{e.distance_from_low_pct.toFixed(1)}% above 12mo low</span>
                          <span>Valuation {e.valuation_score}/25</span>
                        </div>
                      </Link>
                    ))}
                  </div>
                )}
              </CardBody>
            </Card>

            {/* Deep Value Watch */}
            <Card className="shadow-sm border border-slate-200">
              <CardHeader className="p-3 pb-2">
                <h3 className="text-sm font-semibold text-slate-700">
                  👀 Deep Value Watch {`(${deepValueWatch.length})`}
                </h3>
              </CardHeader>
              <Divider className="bg-slate-200" />
              <CardBody className="p-0">
                {deepValueWatch.length === 0 ? (
                  <p className="text-center text-slate-400 text-sm py-8">No matches for these thresholds.</p>
                ) : (
                  <Table
                    removeWrapper
                    className="text-xs"
                    classNames={{ th: 'bg-slate-50 text-slate-600 font-medium text-xs h-8', td: 'text-xs py-2' }}
                  >
                    <TableHeader>
                      <TableColumn>SYMBOL</TableColumn>
                      <TableColumn>SECTOR</TableColumn>
                      <TableColumn align="end">PRICE</TableColumn>
                      <TableColumn align="end">% ABOVE 12MO LOW</TableColumn>
                      <TableColumn align="end">VALUATION</TableColumn>
                      <TableColumn align="end">FIN. HEALTH</TableColumn>
                      <TableColumn>GRADE</TableColumn>
                      <TableColumn align="end">P/E</TableColumn>
                    </TableHeader>
                    <TableBody>
                      {deepValueWatch.map((e) => (
                        <TableRow key={e.symbol} className="hover:bg-slate-50">
                          <TableCell>
                            <Link href={`/stock/${e.symbol}`} className="font-semibold text-slate-800 hover:text-cyan-700">
                              {e.symbol}
                            </Link>
                          </TableCell>
                          <TableCell className="text-slate-500">{e.sector}</TableCell>
                          <TableCell className="text-right text-slate-700">${e.current_price.toFixed(2)}</TableCell>
                          <TableCell className="text-right font-semibold text-slate-700">
                            {e.distance_from_low_pct.toFixed(1)}%
                          </TableCell>
                          <TableCell className="text-right">{e.valuation_score}/25</TableCell>
                          <TableCell className="text-right">{e.financial_health_score}/25</TableCell>
                          <TableCell>
                            {e.quality_grade && (
                              <Chip size="sm" className={`text-xs ${gradeColor(e.quality_grade)}`}>
                                {e.quality_grade}
                              </Chip>
                            )}
                          </TableCell>
                          <TableCell className="text-right text-slate-600">
                            {e.pe_ratio != null ? e.pe_ratio.toFixed(1) : 'N/A'}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardBody>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
