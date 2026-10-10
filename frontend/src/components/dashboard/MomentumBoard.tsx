'use client';

import React from 'react';
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
import { MomentumBoardResult } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { gradeColor } from '@/lib/uiColors';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import SegmentedControl from '@/components/ui/SegmentedControl';
import SortHeader from '@/components/ui/SortHeader';
import { useTableSort } from '@/hooks/useTableSort';
import { useRouter } from 'next/navigation';

export type BoardCategory = 'breakout' | 'near_breakout' | 'all';

const CATEGORY_FILTERS: { label: string; value: BoardCategory }[] = [
  { label: 'Breakout', value: 'breakout' },
  { label: 'Near breakout', value: 'near_breakout' },
  { label: 'All', value: 'all' },
];

const LIST_LABEL: Record<string, string> = { gainers: 'Gainers', atr: 'ATR', volume: 'Volume' };

const GRADE_RANK: Record<string, number> = { A: 5, B: 4, C: 3, D: 2, F: 1 };

const ACCESSORS = {
  symbol: (r: MomentumBoardResult) => r.symbol,
  price: (r: MomentumBoardResult) => r.close,
  ret1: (r: MomentumBoardResult) => r.ret1_pct,
  rvol: (r: MomentumBoardResult) => r.rvol,
  atr: (r: MomentumBoardResult) => r.range_atr,
  grade: (r: MomentumBoardResult) => (r.quality_grade ? GRADE_RANK[r.quality_grade] ?? null : null),
  sector: (r: MomentumBoardResult) => r.sector,
  group: (r: MomentumBoardResult) => r.category,
};
type Col = keyof typeof ACCESSORS;

function daysAgo(dateStr: string | null): number | null {
  if (!dateStr) return null;
  const ms = Date.now() - new Date(`${dateStr}T00:00:00`).getTime();
  return Math.floor(ms / (24 * 60 * 60 * 1000));
}

export interface MomentumBoardProps {
  results: MomentumBoardResult[];
  loading: boolean;
  error: boolean;
  sessionDate: string | null;
  universeN: number;
  counts: Record<string, number>;
  category: BoardCategory;
  onCategoryChange: (c: BoardCategory) => void;
  minQualityGrade: string | undefined;
  onMinQualityGradeChange: (g: string | undefined) => void;
  onRetry: () => void;
}

/**
 * Replaces the old "Alpha Finder" widget. Instead of an ML/alignment score with no proven
 * relationship to outcomes, this reuses the exact confluence rule already validated and
 * running for the Telegram channel: a stock earns the star when it lands in the top ranks
 * of 2+ of the day's three fact lists (biggest gainer, biggest ATR expansion, biggest volume
 * surge) among breakout / near-breakout names. See CLAUDE.md's 2026-09-20 tail-economics
 * studies for the evidence behind ranking by these facts rather than a composite score.
 */
export default function MomentumBoard({
  results,
  loading,
  error,
  sessionDate,
  universeN,
  counts,
  category,
  onCategoryChange,
  minQualityGrade,
  onMinQualityGradeChange,
  onRetry,
}: MomentumBoardProps) {
  const router = useRouter();
  const { sorted, sortKey, sortDir, toggle } = useTableSort(results, ACCESSORS);
  const sortHeader = (key: Col, label: string, align: 'start' | 'end' = 'start') => (
    <SortHeader label={label} align={align} active={sortKey === key} dir={sortDir} onClick={() => toggle(key)} />
  );
  const age = daysAgo(sessionDate);
  const stale = age !== null && age > 3;

  return (
    <Card className="mb-6 border border-black/[0.05] shadow-sm">
      <CardHeader className="px-5 pt-5 pb-3">
        <div className="flex flex-wrap justify-between items-center gap-3 w-full">
          <div>
            <h3 className="text-[17px] font-semibold text-slate-900">Momentum Board</h3>
            <p className="text-slate-500 text-xs mt-0.5">
              Breakout / near-breakout names confirmed by 2+ of today&apos;s gainers / ATR-expansion / volume-surge lists
            </p>
          </div>
          <div className="flex items-center gap-2">
            {sessionDate && (
              <Chip
                size="sm"
                className={`text-xs ${stale ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}
              >
                session {sessionDate}{stale ? ' · stale' : ''}
              </Chip>
            )}
            <Chip size="sm" className="bg-accent-soft text-accent text-xs font-medium">
              {results.length} results
            </Chip>
          </div>
        </div>
      </CardHeader>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 px-5 pb-4 border-b border-slate-100">
        <SegmentedControl
          aria-label="Breakout category"
          options={CATEGORY_FILTERS.map((f) => ({
            value: f.value,
            label: (
              <>
                {f.label}
                {counts[f.value] != null && <span className="ml-1 tabular-nums text-slate-400">{counts[f.value]}</span>}
              </>
            ),
          }))}
          value={category}
          onChange={onCategoryChange}
        />
        <div className="w-px h-5 bg-slate-200 mx-1" />
        {['A', 'B', 'C'].map((g) => (
          <Button
            key={g}
            size="sm"
            variant={minQualityGrade === g ? 'solid' : 'bordered'}
            className={minQualityGrade === g ? 'rounded-full bg-emerald-600 text-white text-xs' : 'rounded-full text-xs border-slate-300 text-slate-700'}
            onClick={() => onMinQualityGradeChange(minQualityGrade === g ? undefined : g)}
          >
            {g}+ quality
          </Button>
        ))}
        <span className="text-[11px] text-slate-400 ml-auto">
          of {universeN.toLocaleString()} liquid stocks screened
        </span>
      </div>

      <CardBody className="p-0">
        {loading ? (
          <div className="flex justify-center py-10">
            <LoadingSpinner size="medium" />
          </div>
        ) : error ? (
          <ErrorAlert
            title="Momentum Board unavailable"
            message="Couldn't load today's confluence board."
            onRetry={onRetry}
            className="m-4"
          />
        ) : results.length === 0 ? (
          <p className="text-center text-slate-400 text-sm py-10">
            No stocks match these filters{sessionDate ? '' : ' — no digest snapshot found yet'}.
          </p>
        ) : (
          <Table
            removeWrapper
            aria-label="Momentum Board"
            onRowAction={(key) => router.push(`/stock/${key}`)}
            className="text-xs"
            classNames={{
              th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8 first:pl-5 last:pr-5',
              td: 'text-[13px] py-2 first:pl-5 last:pr-5',
              tr: 'cursor-pointer',
            }}
          >
            <TableHeader>
              <TableColumn>{sortHeader('symbol', 'Symbol')}</TableColumn>
              <TableColumn align="end">{sortHeader('price', 'Price', 'end')}</TableColumn>
              <TableColumn align="end">{sortHeader('ret1', '1D %', 'end')}</TableColumn>
              <TableColumn align="end">{sortHeader('rvol', 'Vol ×', 'end')}</TableColumn>
              <TableColumn align="end">{sortHeader('atr', 'ATR ×', 'end')}</TableColumn>
              <TableColumn>CONFIRMED BY</TableColumn>
              <TableColumn>{sortHeader('grade', 'Grade')}</TableColumn>
              <TableColumn>{sortHeader('sector', 'Sector')}</TableColumn>
              <TableColumn>{sortHeader('group', 'Group')}</TableColumn>
            </TableHeader>
            <TableBody>
              {sorted.map((r) => (
                <TableRow key={r.symbol}>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      {r.starred && <span title="Confirmed by 2+ lists" className="text-amber-500">★</span>}
                      <SymbolHoverLink symbol={r.symbol} />
                      {r.days_to_earnings != null && r.days_to_earnings <= 21 && (
                        <Chip
                          size="sm"
                          title={`Reports ${r.next_earnings_date}`}
                          className={`text-[10px] h-4 px-1 ${
                            r.days_to_earnings <= 3 ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800'
                          }`}
                        >
                          Earnings {r.days_to_earnings <= 0 ? 'today' : `${r.days_to_earnings}d`}
                        </Chip>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-slate-800">
                    {r.close != null ? `$${r.close.toFixed(2)}` : '—'}
                  </TableCell>
                  <TableCell className="text-right">
                    <span className={`font-semibold ${(r.ret1_pct ?? 0) >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                      {r.ret1_pct != null ? `${r.ret1_pct >= 0 ? '+' : ''}${r.ret1_pct.toFixed(2)}%` : '—'}
                    </span>
                  </TableCell>
                  <TableCell className="text-right text-slate-600">
                    {r.rvol != null ? `${r.rvol.toFixed(1)}x` : '—'}
                  </TableCell>
                  <TableCell className="text-right text-slate-600">
                    {r.range_atr != null ? `${r.range_atr.toFixed(1)}x` : '—'}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {Object.keys(r.list_ranks).length === 0 && <span className="text-slate-400">—</span>}
                      {Object.entries(r.list_ranks).map(([name, rank]) => (
                        <Chip key={name} size="sm" className="bg-slate-100 text-slate-600 text-[10px] h-4 px-1">
                          {LIST_LABEL[name] ?? name} #{rank}
                        </Chip>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    {r.quality_grade ? (
                      <Chip size="sm" className={`text-xs ${gradeColor(r.quality_grade)}`}>
                        {r.quality_grade}
                      </Chip>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-slate-500">{r.sector ?? 'Unknown'}</TableCell>
                  <TableCell className="text-slate-500">
                    {r.category === 'breakout' ? 'Breakout' : 'Near breakout'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}
