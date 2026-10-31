'use client';

import React, { useMemo } from 'react';
import { Chip } from '@nextui-org/react';
import { clsx } from 'clsx';
import type { EarningsDateEntry, PriceBar } from '@/services/api';
import { latestReport, statementsPending } from '@/lib/latestReport';

const UNKNOWN = '—';
const usd = (v: number) => `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`;
const signed = (v: number, d = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
      <p className="text-[11px] font-medium text-slate-500">{label}</p>
      <p className={clsx('text-[17px] font-semibold tabular-nums', tone ?? 'text-slate-900')}>{value}</p>
      {sub && <p className="text-[11px] tabular-nums text-slate-400">{sub}</p>}
    </div>
  );
}

/**
 * The latest earnings report that has results, next to the price action since. The EPS figures come from the
 * earnings calendar (available the day a company reports); the full statements (revenue, net income) are filed later,
 * so when they have not arrived yet the card says so instead of leaving the older quarter to look like the latest.
 */
export default function LatestReportCard({
  earnings,
  bars,
  latestStatementQuarter,
}: {
  earnings: EarningsDateEntry[];
  bars: PriceBar[];
  latestStatementQuarter: string | null;
}) {
  const r = useMemo(() => latestReport(earnings, bars), [earnings, bars]);
  if (!r) return null;

  const when = r.daysAgo === 0 ? 'today' : r.daysAgo === 1 ? 'yesterday' : `${r.daysAgo} days ago`;
  const pending = statementsPending(r.date, latestStatementQuarter);
  const yoy = r.yearAgo ? r.epsActual - r.yearAgo.eps : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="inline-flex items-center gap-2 text-[15px] font-semibold text-slate-900">
          Latest report
          {r.beat != null && (
            <Chip size="sm" className={`text-xs ${r.beat ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-700'}`}>
              {r.beat ? 'Beat' : 'Miss'}
            </Chip>
          )}
        </h3>
        <span className="text-xs tabular-nums text-slate-500">
          {r.date} · {when}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="EPS reported" value={usd(r.epsActual)} sub={r.epsEstimate != null ? `est. ${usd(r.epsEstimate)}` : 'no estimate on record'} />
        <Tile
          label="Surprise"
          value={r.surprisePct != null ? signed(r.surprisePct) : UNKNOWN}
          tone={r.surprisePct == null ? 'text-slate-400' : r.surprisePct >= 0 ? 'text-emerald-600' : 'text-red-600'}
        />
        <Tile
          label="vs year-ago quarter"
          value={yoy != null ? `${yoy >= 0 ? '+' : '-'}${usd(Math.abs(yoy))}` : UNKNOWN}
          sub={r.yearAgo ? `was ${usd(r.yearAgo.eps)}` : 'not on record'}
          tone={yoy == null ? 'text-slate-400' : yoy >= 0 ? 'text-emerald-600' : 'text-red-600'}
        />
        <Tile
          label="Price since report"
          value={r.sinceReportPct != null ? signed(r.sinceReportPct) : UNKNOWN}
          sub="last close vs close before"
          tone={r.sinceReportPct == null ? 'text-slate-400' : r.sinceReportPct >= 0 ? 'text-emerald-600' : 'text-red-600'}
        />
      </div>

      {r.previous.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
          <span>Earlier:</span>
          {r.previous.map((p) => (
            <span
              key={p.date}
              className={clsx(
                'rounded-full px-2 py-0.5 tabular-nums',
                p.beat == null ? 'bg-slate-100 text-slate-500' : p.beat ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'
              )}
            >
              {p.date.slice(0, 7)} {p.surprisePct != null ? signed(p.surprisePct, 0) : UNKNOWN}
            </span>
          ))}
        </div>
      )}

      {pending && (
        <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[12px] leading-relaxed text-amber-900">
          Full results (revenue, net income) aren&apos;t in yet. The Financials tab still shows the quarter ended{' '}
          {latestStatementQuarter ?? 'n/a'}; it updates automatically once the new quarter&apos;s statements are published.
        </p>
      )}
    </div>
  );
}
