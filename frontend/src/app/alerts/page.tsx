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
  Button,
} from '@nextui-org/react';
import { TextField } from '@/components/ui/Field';
import { deepValueApi, DeepValueEntry, BalanceSheetAssessment, BalanceSheetCheckStatus } from '@/services/api';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import DataStamp from '@/components/common/DataStamp';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';

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

// Thresholds mirror backend/services/deep_value_service.py -- keep in sync.
type CheckKey = keyof BalanceSheetAssessment['checks'];

const BALANCE_SHEET_RULES: { key: CheckKey; label: string; rule: string }[] = [
  { key: 'equity', label: 'Eq', rule: 'Shareholders’ equity > 0' },
  { key: 'leverage', label: 'D/E', rule: 'Debt / equity ≤ 2.0 (n/a for Financial Services)' },
  { key: 'liquidity', label: 'CR', rule: 'Current ratio ≥ 1.0 (n/a for Financial Services)' },
  { key: 'cash_flow', label: 'CF', rule: 'Operating cash flow > 0, or cash ≥ 4 quarters of FCF burn' },
];

function checkColor(status: BalanceSheetCheckStatus) {
  switch (status) {
    case 'pass':
      return 'bg-emerald-100 text-emerald-800';
    case 'warn':
      return 'bg-amber-100 text-amber-800';
    case 'fail':
      return 'bg-red-100 text-red-700';
    default:
      return 'bg-slate-100 text-slate-500';
  }
}

function checkDetail(bs: BalanceSheetAssessment, key: CheckKey): string {
  const v = bs.values;
  const status = bs.checks[key];
  if (key === 'leverage' && v.debt_to_equity != null) return `D/E ${v.debt_to_equity.toFixed(2)} (${status})`;
  if (key === 'liquidity' && v.current_ratio != null) return `Current ratio ${v.current_ratio.toFixed(2)} (${status})`;
  if (key === 'cash_flow' && v.cash_runway_quarters != null) return `Cash runway ${v.cash_runway_quarters.toFixed(1)} quarters (${status})`;
  return status;
}

function BalanceSheetChips({ e }: { e: DeepValueEntry }) {
  const bs = e.balance_sheet;
  if (!bs) return <span className="text-slate-400">—</span>; // backend predates the balance-sheet checks
  return (
    <div className="flex gap-1" title={`Quarter ending ${bs.quarter ?? '—'}`}>
      {BALANCE_SHEET_RULES.map((r) => (
        <span
          key={r.key}
          title={`${r.rule}: ${checkDetail(bs, r.key)}`}
          className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${checkColor(bs.checks[r.key])}`}
        >
          {r.label}
        </span>
      ))}
    </div>
  );
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

/** Rows rendered before "Show more": the full scan can hold hundreds of matches, too many for one page on a phone. */
export const TURNAROUND_PAGE = 24;
export const WATCH_PAGE = 50;

function ShowMore({ shown, total, step, onMore, label }: { shown: number; total: number; step: number; onMore: () => void; label: string }) {
  if (shown >= total) return null;
  return (
    <div className="flex flex-wrap items-center justify-center gap-3 border-t border-black/[0.05] px-4 py-3 text-[13px] text-slate-500">
      <span>
        Showing {shown} of {total} {label}
      </span>
      <button
        type="button"
        onClick={onMore}
        className="press rounded-full bg-slate-900 px-4 py-1.5 font-medium text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
      >
        Show {Math.min(step, total - shown)} more
      </button>
    </div>
  );
}

export default function AlertsPage() {
  const [nearLowPct, setNearLowPct] = useState(15);
  const [minValuationScore, setMinValuationScore] = useState(12);

  const [turnaroundAlerts, setTurnaroundAlerts] = useState<DeepValueEntry[]>([]);
  const [deepValueWatch, setDeepValueWatch] = useState<DeepValueEntry[]>([]);
  const [excludedBalanceSheet, setExcludedBalanceSheet] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [turnShown, setTurnShown] = useState(TURNAROUND_PAGE);
  const [watchShown, setWatchShown] = useState(WATCH_PAGE);

  const fetchScan = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const resp = await deepValueApi.scan({
        near_low_pct: nearLowPct,
        min_valuation_score: minValuationScore,
      });
      setTurnaroundAlerts(resp.turnaround_alerts);
      setDeepValueWatch(resp.deep_value_watch);
      setExcludedBalanceSheet(resp.excluded_balance_sheet ?? null);
      setTurnShown(TURNAROUND_PAGE);
      setWatchShown(WATCH_PAGE);
    } catch {
      setError('Failed to load deep value scan');
    } finally {
      setLoading(false);
    }
  }, [nearLowPct, minValuationScore]);

  useEffect(() => {
    fetchScan();
  }, [fetchScan]);

  const pricesAsOf = [...turnaroundAlerts, ...deepValueWatch].reduce<string | null>((m, e) => (e.price_date && (!m || e.price_date > m) ? e.price_date : m), null);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-7xl space-y-4">
        <div className="px-1 pt-2 pb-1">
          <h1 className="large-title">Deep Value & Turnaround Screen</h1>
          {!loading && !error && pricesAsOf && <DataStamp className="mt-1" label="Closes as of" asOf={pricesAsOf} source="latest quarterly fundamentals" />}
          <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
            Stocks trading near their 12-month low at a cheap valuation that don&apos;t fail a balance-sheet
            check. <span className="font-semibold text-slate-700">Turnaround flips</span> are the subset that
            also just flipped from a quarterly net loss to a net profit in two consecutive quarters. These are
            screening rules that describe the current data, not a prediction or a recommendation, and there is no
            track record for this screen. In one historical test (about 15 months, a single market period)
            stocks matching these rules did not outperform the market.
          </p>
        </div>

        {/* Thresholds */}
        <Card className="border border-black/[0.05] shadow-sm">
          <CardBody className="p-5 flex-row flex-wrap gap-x-5 gap-y-4 items-end">
            <div className="w-44">
              <TextField
                type="number"
                label="Near low (max above 12-mo low)"
                suffix="%"
                value={nearLowPct.toString()}
                min={0}
                max={100}
                step={1}
                onChange={(e) => setNearLowPct(Number(e.target.value) || 0)}
              />
            </div>
            <div className="w-44">
              <TextField
                type="number"
                label="Min valuation score (of 25)"
                value={minValuationScore.toString()}
                min={0}
                max={25}
                step={1}
                onChange={(e) => setMinValuationScore(Number(e.target.value) || 0)}
              />
            </div>
            <Button size="sm" className="rounded-full bg-slate-900 px-4 font-medium text-white" onClick={fetchScan} isLoading={loading}>
              Rescan
            </Button>
            <div className="text-xs text-slate-500 max-w-md">
              <p className="font-semibold text-slate-600 mb-1">Balance-sheet checks (latest quarter)</p>
              <ul className="space-y-0.5">
                {BALANCE_SHEET_RULES.map((r) => (
                  <li key={r.key}>
                    <span className="font-medium">{r.label}</span> — {r.rule}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-slate-400">
                Green = pass. Amber = breached but operating cash flow is positive (or unreported) — shown,
                not excluded. A breach with operating cash flow ≤ 0, or a short cash runway, is a fail and
                excludes the stock. Grey = data not reported or n/a, never assumed.{!loading && !error && excludedBalanceSheet != null && ` ${excludedBalanceSheet} otherwise-matching stocks excluded.`}
              </p>
            </div>
          </CardBody>
        </Card>

        {loading ? (
          <div className="flex justify-center py-16">
            <LoadingSpinner />
          </div>
        ) : error ? (
          <ErrorAlert title="Couldn’t run the scan" message={error} onRetry={fetchScan} className="my-4" />
        ) : (
          <>
            {/* Turnaround flips */}
            <Card className="border border-black/[0.05] shadow-sm">
              <CardHeader className="p-4">
                <div>
                  <h3 className="text-base font-semibold">Turnaround flips</h3>
                  <p className="text-slate-500 text-xs mt-1">Near lows + cheap valuation + latest quarter flipped from a loss to a profit</p>
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
                    {turnaroundAlerts.slice(0, turnShown).map((e) => (
                      <Link
                        key={e.symbol}
                        href={`/stock/${e.symbol}`}
                        className="block border border-black/[0.06] rounded-lg p-3 hover:shadow-md transition-shadow"
                      >
                        <div className="flex justify-between items-center mb-1">
                          <SymbolHoverLink symbol={e.symbol} asText className="font-bold text-slate-800" />
                          {e.quality_grade && (
                            <Chip size="sm" className={`text-xs ${gradeColor(e.quality_grade)}`}>
                              Grade {e.quality_grade}
                            </Chip>
                          )}
                        </div>
                        <p className="text-xs text-slate-500 mb-2">
                          {e.sector} · ${e.current_price.toFixed(2)}
                          {e.price_date ? ` close ${e.price_date}` : ''}
                        </p>
                        <p className="text-sm">
                          <span className="text-red-600 font-semibold">{fmtMoney(e.previous_net_income)}</span>
                          <span className="text-slate-400 mx-1">→</span>
                          <span className="text-emerald-600 font-semibold">{fmtMoney(e.latest_net_income)}</span>
                        </p>
                        <p className="text-[10px] text-slate-400 mt-1">
                          {e.previous_quarter} → {e.latest_quarter}
                        </p>
                        <p className="text-[11px] mt-1">
                          {e.year_ago_net_income == null ? (
                            <span className="text-slate-400">Same quarter last year: —</span>
                          ) : e.year_ago_net_income > 0 ? (
                            <span className="text-amber-700">
                              Same quarter last year ({e.year_ago_quarter}) was also profitable (
                              {fmtMoney(e.year_ago_net_income)}) — the flip may be seasonal
                            </span>
                          ) : (
                            <span className="text-slate-500">
                              Same quarter last year ({e.year_ago_quarter}): {fmtMoney(e.year_ago_net_income)}
                            </span>
                          )}
                        </p>
                        <div className="flex gap-3 mt-2 text-xs text-slate-500 items-center">
                          <span>{e.distance_from_low_pct.toFixed(1)}% above 12mo low</span>
                          <span>Valuation {e.valuation_score}/25</span>
                          <BalanceSheetChips e={e} />
                        </div>
                      </Link>
                    ))}
                  </div>
                )}
                <ShowMore shown={Math.min(turnShown, turnaroundAlerts.length)} total={turnaroundAlerts.length} step={TURNAROUND_PAGE} onMore={() => setTurnShown((n) => n + TURNAROUND_PAGE)} label="turnaround flips" />
              </CardBody>
            </Card>

            {/* Deep Value Watch */}
            <Card className="border border-black/[0.05] shadow-sm">
              <CardHeader className="px-5 pt-4 pb-2">
                <h3 className="text-[15px] font-semibold text-slate-900">
                  👀 Deep Value Watch {`(${deepValueWatch.length})`}
                </h3>
              </CardHeader>
              <Divider className="bg-slate-200" />
              <CardBody className="p-0">
                {deepValueWatch.length === 0 ? (
                  <p className="text-center text-slate-400 text-sm py-8">No matches for these thresholds.</p>
                ) : (
                  <div className="relative overflow-x-auto">
                  <Table
                    aria-label="Deep value watch"
                    removeWrapper
                    className="min-w-[760px] text-xs"
                    classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8', td: 'text-xs py-2' }}
                  >
                    <TableHeader>
                      <TableColumn>SYMBOL</TableColumn>
                      <TableColumn>SECTOR</TableColumn>
                      <TableColumn align="end">PRICE</TableColumn>
                      <TableColumn align="end">% ABOVE 12MO LOW</TableColumn>
                      <TableColumn align="end">VALUATION</TableColumn>
                      <TableColumn>BALANCE SHEET</TableColumn>
                      <TableColumn>GRADE</TableColumn>
                      <TableColumn align="end">P/E</TableColumn>
                    </TableHeader>
                    <TableBody>
                      {deepValueWatch.slice(0, watchShown).map((e) => (
                        <TableRow key={e.symbol} className="hover:bg-slate-50">
                          <TableCell>
                            <SymbolHoverLink symbol={e.symbol} />
                          </TableCell>
                          <TableCell className="text-slate-500">{e.sector}</TableCell>
                          <TableCell className="text-right text-slate-700" title={e.price_date ? `Close ${e.price_date}` : undefined}>
                            ${e.current_price.toFixed(2)}
                          </TableCell>
                          <TableCell className="text-right font-semibold text-slate-700">
                            {e.distance_from_low_pct.toFixed(1)}%
                          </TableCell>
                          <TableCell className="text-right">{e.valuation_score}/25</TableCell>
                          <TableCell>
                            <BalanceSheetChips e={e} />
                          </TableCell>
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
                  </div>
                )}
                <ShowMore shown={Math.min(watchShown, deepValueWatch.length)} total={deepValueWatch.length} step={WATCH_PAGE} onMore={() => setWatchShown((n) => n + WATCH_PAGE)} label="stocks" />
              </CardBody>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
