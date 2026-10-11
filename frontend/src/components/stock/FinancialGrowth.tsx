'use client';

import React from 'react';
import { Table, TableBody, TableCell, TableColumn, TableHeader, TableRow } from '@nextui-org/react';
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { clsx } from 'clsx';
import type { QuarterlyFinancials } from '@/services/api';
import { useResolvedTheme } from '@/lib/theme';

const UNKNOWN = '—';

function money(v: number | null | undefined): string {
  if (v == null) return UNKNOWN;
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(2)}`;
}
function pct(v: number | null | undefined, d = 1) {
  return v == null ? UNKNOWN : `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;
}

const label = (q: QuarterlyFinancials) => q.quarter || `Q${q.fiscal_quarter} ${q.fiscal_year}`;

/** One small chart with a title. Defined at module level: a component declared inside the render would be a new
 * type on every render, remounting the chart (and restarting it) whenever the page updates. */
function Panel({ title, sub, children }: { title: string; sub?: string; children: React.ReactElement }) {
  return (
    <div className="rounded-xl bg-slate-50/60 p-3 ring-1 ring-black/[0.04]">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <p className="text-xs font-semibold text-slate-700">{title}</p>
        {sub && <p className="text-[11px] tabular-nums text-slate-500">{sub}</p>}
      </div>
      <ResponsiveContainer width="100%" height={170}>
        {children}
      </ResponsiveContainer>
    </div>
  );
}

/**
 * Growth over the quarters we hold (up to 8, oldest first): revenue and net income, EPS, year-over-year growth,
 * margins and free cash flow, then the figures as a table. Values are the stored ones (percent fields are already
 * percentages); a missing value is a gap in the chart and "—" in the table, never zero.
 */
export default function FinancialGrowth({ quarters }: { quarters: QuarterlyFinancials[] }) {
  const theme = useResolvedTheme();
  const c =
    theme === 'dark'
      ? { grid: '#2f2f33', tick: '#98989f', blue: '#64d2ff', green: '#30d158', orange: '#ff9f0a', purple: '#bf5af2', red: '#ff453a', tipBg: '#2c2c2e', tipText: '#f5f5f7' }
      : { grid: '#e3e3e8', tick: '#737378', blue: '#0a84ff', green: '#34c759', orange: '#ff9f0a', purple: '#af52de', red: '#ff3b30', tipBg: '#ffffff', tipText: '#1d1d1f' };

  if (quarters.length === 0) {
    return (
      <div>
        <h3 className="mb-3 text-[15px] font-semibold text-slate-900">Financial growth</h3>
        <p className="py-6 text-center text-sm text-slate-400">No quarterly financial data available for this symbol.</p>
      </div>
    );
  }

  const data = quarters.map((q) => ({ ...q, label: label(q) }));
  const axis = { tick: { fontSize: 10, fill: c.tick }, stroke: c.grid };
  const tip = {
    contentStyle: { fontSize: 12, borderRadius: 10, border: 'none', boxShadow: '0 8px 24px rgb(0 0 0 / 0.12)', background: c.tipBg, color: c.tipText },
    cursor: { fill: theme === 'dark' ? 'rgb(255 255 255 / 0.04)' : 'rgb(0 0 0 / 0.04)' },
  };
  const legend = { iconType: 'circle' as const, iconSize: 8, wrapperStyle: { fontSize: 11 } };
  const latest = quarters[quarters.length - 1];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-slate-900">Financial growth</h3>
        <p className="text-xs text-slate-500">
          Latest quarter ({label(latest)}): revenue {pct(latest.revenue_growth_yoy)} YoY · EPS {pct(latest.eps_growth_yoy)} YoY
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Panel title="Revenue & net income" sub={`${money(latest.revenue)} / ${money(latest.net_income)}`}>
          <BarChart data={data} barGap={2}>
            <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
            <XAxis dataKey="label" {...axis} />
            <YAxis {...axis} width={58} tickFormatter={money} />
            <Tooltip formatter={(v) => (typeof v === 'number' ? money(v) : UNKNOWN)} {...tip} />
            <Legend {...legend} />
            <ReferenceLine y={0} stroke={c.tick} strokeOpacity={0.4} />
            <Bar isAnimationActive={false} dataKey="revenue" name="Revenue" fill={c.blue} radius={[3, 3, 0, 0]} />
            <Bar isAnimationActive={false} dataKey="net_income" name="Net income" fill={c.green} radius={[3, 3, 0, 0]} />
          </BarChart>
        </Panel>

        <Panel title="EPS" sub={latest.eps != null ? `latest $${latest.eps.toFixed(2)}` : undefined}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
            <XAxis dataKey="label" {...axis} />
            <YAxis {...axis} width={44} tickFormatter={(v) => `$${v}`} />
            <Tooltip formatter={(v) => (typeof v === 'number' ? `$${v.toFixed(2)}` : UNKNOWN)} {...tip} />
            <ReferenceLine y={0} stroke={c.tick} strokeOpacity={0.4} />
            <Bar isAnimationActive={false} dataKey="eps" name="EPS" radius={[3, 3, 0, 0]}>
              {data.map((d, i) => (
                <Cell key={i} fill={d.eps != null && d.eps < 0 ? c.red : c.purple} />
              ))}
            </Bar>
          </BarChart>
        </Panel>

        <Panel title="Year-over-year growth">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
            <XAxis dataKey="label" {...axis} />
            <YAxis {...axis} width={44} tickFormatter={(v) => `${v}%`} />
            <Tooltip formatter={(v) => (typeof v === 'number' ? pct(v) : UNKNOWN)} {...tip} />
            <Legend {...legend} />
            <ReferenceLine y={0} stroke={c.tick} strokeOpacity={0.4} />
            <Line isAnimationActive={false} type="monotone" dataKey="revenue_growth_yoy" name="Revenue" stroke={c.blue} strokeWidth={2} dot={{ r: 2.5 }} connectNulls={false} />
            <Line isAnimationActive={false} type="monotone" dataKey="eps_growth_yoy" name="EPS" stroke={c.purple} strokeWidth={2} dot={{ r: 2.5 }} connectNulls={false} />
          </LineChart>
        </Panel>

        <Panel title="Margins">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
            <XAxis dataKey="label" {...axis} />
            <YAxis {...axis} width={44} tickFormatter={(v) => `${v}%`} />
            <Tooltip formatter={(v) => (typeof v === 'number' ? `${v.toFixed(1)}%` : UNKNOWN)} {...tip} />
            <Legend {...legend} />
            <Line isAnimationActive={false} type="monotone" dataKey="gross_margin" name="Gross" stroke={c.green} strokeWidth={2} dot={{ r: 2.5 }} connectNulls={false} />
            <Line isAnimationActive={false} type="monotone" dataKey="operating_margin" name="Operating" stroke={c.orange} strokeWidth={2} dot={{ r: 2.5 }} connectNulls={false} />
            <Line isAnimationActive={false} type="monotone" dataKey="net_margin" name="Net" stroke={c.blue} strokeWidth={2} dot={{ r: 2.5 }} connectNulls={false} />
          </LineChart>
        </Panel>
      </div>

      <Table
        removeWrapper
        aria-label="Quarterly financials"
        className="mt-4"
        classNames={{ th: 'bg-slate-50/70 text-slate-500 font-medium text-[11px] h-8', td: 'text-[13px] py-2 tabular-nums' }}
      >
        <TableHeader>
          <TableColumn>QUARTER</TableColumn>
          <TableColumn align="end">REVENUE</TableColumn>
          <TableColumn align="end">REV YOY</TableColumn>
          <TableColumn align="end">EPS</TableColumn>
          <TableColumn align="end">EPS YOY</TableColumn>
          <TableColumn align="end">NET MGN</TableColumn>
          <TableColumn align="end">FREE CASH FLOW</TableColumn>
          <TableColumn align="end">ROE</TableColumn>
          <TableColumn align="end">D/E</TableColumn>
        </TableHeader>
        <TableBody>
          {[...data].reverse().map((q, i) => (
            <TableRow key={i}>
              <TableCell className="font-semibold text-slate-800">{q.label}</TableCell>
              <TableCell className="text-right">{money(q.revenue)}</TableCell>
              <TableCell className={clsx('text-right', q.revenue_growth_yoy == null ? '' : q.revenue_growth_yoy >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                {pct(q.revenue_growth_yoy)}
              </TableCell>
              <TableCell className="text-right">{q.eps != null ? `$${q.eps.toFixed(2)}` : UNKNOWN}</TableCell>
              <TableCell className={clsx('text-right', q.eps_growth_yoy == null ? '' : q.eps_growth_yoy >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                {pct(q.eps_growth_yoy)}
              </TableCell>
              <TableCell className="text-right">{q.net_margin != null ? `${q.net_margin.toFixed(1)}%` : UNKNOWN}</TableCell>
              <TableCell className={clsx('text-right', q.free_cash_flow != null && q.free_cash_flow < 0 && 'text-red-600')}>{money(q.free_cash_flow)}</TableCell>
              <TableCell className="text-right">{q.roe != null ? `${q.roe.toFixed(1)}%` : UNKNOWN}</TableCell>
              <TableCell className="text-right">{q.debt_to_equity != null ? q.debt_to_equity.toFixed(2) : UNKNOWN}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
