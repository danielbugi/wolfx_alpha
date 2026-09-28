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
  Chip,
} from '@nextui-org/react';
import { TrackRecordSummary, TrackRecordSignal } from '@/services/api';
import SymbolHoverLink from '@/components/dashboard/SymbolHoverLink';
import { gradeColor } from '@/lib/uiColors';

export interface TrackRecordPanelProps {
  summary: TrackRecordSummary;
  signals: TrackRecordSignal[];
}

const STATUS_LABEL: Record<string, string> = {
  open: 'Open', stopped: 'Stopped', target1: 'Target 1', target2: 'Target 2',
  target3: 'Target 3', expired: 'Expired',
};

const STATUS_COLOR: Record<string, string> = {
  open: 'bg-slate-100 text-slate-600',
  stopped: 'bg-amber-100 text-amber-800',
  target1: 'bg-emerald-100 text-emerald-700',
  target2: 'bg-emerald-100 text-emerald-700',
  target3: 'bg-emerald-100 text-emerald-700',
  expired: 'bg-slate-100 text-slate-500',
};

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex-1 bg-white rounded-lg border border-slate-200 p-4">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-2xl font-semibold text-slate-800 mt-1">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
    </div>
  );
}

/**
 * The signal_ledger track record: every daily breakout signal, tracked forward to a real result
 * and shown back unfiltered -- disclosed history, never a forward promise. Every label here reads
 * as "of N past signals," matching the discipline this repo's channel copy already enforces
 * (see mechanism/alerts/tests/test_channel_content.py's wording guard) rather than implying any
 * predictive edge.
 */
export default function TrackRecordPanel({ summary, signals }: TrackRecordPanelProps) {
  const dateRange = summary.earliest_signal_date && summary.latest_signal_date
    ? `${summary.earliest_signal_date} → ${summary.latest_signal_date}`
    : 'no signals yet';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <StatTile
          label="Resolved signals"
          value={summary.resolved_count.toLocaleString()}
          sub={dateRange}
        />
        <StatTile
          label="Still open"
          value={summary.open_count.toLocaleString()}
          sub="not yet reached a stop, target, or time exit"
        />
        <StatTile
          label="Win rate (resolved only)"
          value={summary.suppressed ? 'Not enough data' : `${summary.win_rate_pct}%`}
          sub={summary.suppressed
            ? `fewer than ${summary.min_sample_size} resolved signals so far`
            : 'share of resolved signals with a positive outcome'}
        />
        <StatTile
          label="Average outcome (R)"
          value={summary.suppressed ? '—' : `${summary.avg_outcome_r! >= 0 ? '+' : ''}${summary.avg_outcome_r}R`}
          sub="R = multiples of the 2xATR stop distance"
        />
      </div>

      {!summary.suppressed && Object.keys(summary.by_status).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(summary.by_status).map(([status, n]) => (
            <Chip key={status} size="sm" className={`text-xs ${STATUS_COLOR[status] ?? 'bg-slate-100 text-slate-600'}`}>
              {STATUS_LABEL[status] ?? status}: {n}
            </Chip>
          ))}
        </div>
      )}

      <Card className="shadow-sm border border-slate-200">
        <CardHeader className="bg-slate-700 text-white p-4">
          <div>
            <h3 className="text-base font-semibold">Recent signals</h3>
            <p className="text-slate-300 text-xs mt-1">
              A disclosed history of what actually happened -- not a prediction of what happens next.
            </p>
          </div>
        </CardHeader>
        <CardBody className="p-0">
          {signals.length === 0 ? (
            <p className="text-center text-slate-400 text-sm py-10">No signals recorded yet.</p>
          ) : (
            <Table
              removeWrapper
              className="text-xs"
              classNames={{ th: 'bg-slate-50 text-slate-600 font-medium text-xs h-8', td: 'text-xs py-2' }}
            >
              <TableHeader>
                <TableColumn>SYMBOL</TableColumn>
                <TableColumn>DATE</TableColumn>
                <TableColumn>DIRECTION</TableColumn>
                <TableColumn align="end">ENTRY</TableColumn>
                <TableColumn>STATUS</TableColumn>
                <TableColumn align="end">OUTCOME (R)</TableColumn>
                <TableColumn>GRADE</TableColumn>
                <TableColumn>SECTOR</TableColumn>
              </TableHeader>
              <TableBody>
                {signals.map((s) => (
                  <TableRow key={`${s.symbol}-${s.signal_date}-${s.direction}`}>
                    <TableCell><SymbolHoverLink symbol={s.symbol} /></TableCell>
                    <TableCell className="text-slate-500">{s.signal_date}</TableCell>
                    <TableCell className={s.direction === 1 ? 'text-emerald-600' : 'text-red-600'}>
                      {s.direction === 1 ? 'Bullish' : 'Bearish'}
                    </TableCell>
                    <TableCell className="text-right text-slate-700">${s.entry_price.toFixed(2)}</TableCell>
                    <TableCell>
                      <Chip size="sm" className={`text-xs ${STATUS_COLOR[s.status] ?? 'bg-slate-100 text-slate-600'}`}>
                        {STATUS_LABEL[s.status] ?? s.status}
                      </Chip>
                    </TableCell>
                    <TableCell className="text-right">
                      {s.outcome_r == null ? '—' : (
                        <span className={s.outcome_r >= 0 ? 'text-emerald-600' : 'text-amber-700'}>
                          {s.outcome_r >= 0 ? '+' : ''}{s.outcome_r.toFixed(2)}R
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      {s.quality_grade ? (
                        <Chip size="sm" className={`text-xs ${gradeColor(s.quality_grade)}`}>{s.quality_grade}</Chip>
                      ) : <span className="text-slate-400">—</span>}
                    </TableCell>
                    <TableCell className="text-slate-500">{s.sector ?? 'Unknown'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
