// File: frontend/src/components/screener/StrengthMap.tsx
'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { clsx } from 'clsx';
import { CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts';
import type { ScreenerRow } from '@/services/screenerV2Api';
import { formatValue } from '@/lib/screenerV2';
import { useResolvedTheme } from '@/lib/theme';
import { MIDPOINT, QUADRANT_ORDER, QUADRANT_TONE, QUADRANTS, Quadrant, quadrant, quadrantCounts } from '@/lib/strength';

const SECTOR_COLORS = ['#0a84ff', '#ff9f0a', '#30b158', '#bf5af2', '#ff375f', '#00a0b4', '#a2845e', '#5e5ce6', '#ffcc00', '#8e8e93', '#ff6b22'];

interface Point {
  symbol: string;
  x: number;
  y: number;
  z: number;
  sector: string;
  row: ScreenerRow;
}

function TipCard({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const q = quadrant(p.row.rs_20, p.row.rs_60) as Quadrant;
  return (
    <div className="max-w-[220px] rounded-xl bg-white px-3 py-2 text-[12px] shadow-lg ring-1 ring-black/5">
      <p className="flex items-center justify-between gap-2">
        <span className="text-[14px] font-semibold text-slate-900">{p.symbol}</span>
        <span className={clsx('rounded-full px-1.5 py-0.5 text-[10px] font-semibold', QUADRANT_TONE[q])}>{QUADRANTS[q].label}</span>
      </p>
      <p className="truncate text-slate-500">{p.sector}</p>
      <p className="mt-1 tabular-nums text-slate-700">RS 20: {Math.round(p.y)} · RS 60: {Math.round(p.x)}</p>
      <p className="tabular-nums text-slate-500">
        20 sessions {formatValue(p.row.ret_20, '%')} · vs S&amp;P {formatValue(p.row.vs_spx_20, 'pp')}
      </p>
      <p className="text-slate-400">$ vol {formatValue(p.row.dollar_vol_20, '$')} · click to open</p>
    </div>
  );
}

/**
 * Strength Map: every stock of the current screen as a dot -- across, strength over the last 60 sessions; up, over the last
 * 20 (both the observed relative-strength percentiles for the session). The four corners name where it sits. Dot size is
 * liquidity (20-session dollar volume, log scale), colour its sector. Stocks without both percentiles are not plotted
 * (counted). Descriptive only.
 */
export default function StrengthMap({ rows, total, session }: { rows: ScreenerRow[]; total: number; session: string }) {
  const router = useRouter();
  const theme = useResolvedTheme();
  const [focus, setFocus] = useState<Quadrant | null>(null);
  // smaller dots on a phone so 200 stocks stay readable
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const mq = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(max-width: 640px)') : null;
    if (!mq) return;
    const on = () => setCompact(mq.matches);
    on();
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  const c = theme === 'dark'
    ? { grid: '#2f2f33', tick: '#98989f', mid: '#636366', tint: 0.06 }
    : { grid: '#e3e3e8', tick: '#737378', mid: '#aeaeb2', tint: 0.05 };

  const sectors = useMemo(() => [...new Set(rows.map((r) => r.sector ?? 'Unclassified'))].sort(), [rows]);
  const color = (s: string) => SECTOR_COLORS[sectors.indexOf(s) % SECTOR_COLORS.length];
  const points = useMemo<Point[]>(
    () =>
      rows
        .filter((r) => quadrant(r.rs_20, r.rs_60) != null)
        .filter((r) => !focus || quadrant(r.rs_20, r.rs_60) === focus)
        .map((r) => ({ symbol: r.symbol, x: r.rs_60 as number, y: r.rs_20 as number, z: r.dollar_vol_20 && r.dollar_vol_20 > 0 ? Math.log10(r.dollar_vol_20) : 5, sector: r.sector ?? 'Unclassified', row: r })),
    [rows, focus],
  );
  const counts = quadrantCounts(rows);
  const bySector = useMemo(() => sectors.map((s) => ({ s, pts: points.filter((p) => p.sector === s) })).filter((g) => g.pts.length), [sectors, points]);

  return (
    <div className="px-2 pb-3 sm:px-4" data-testid="strength-map">
      {/* corner counts, also a filter */}
      <div className="flex flex-wrap gap-1.5 px-2 pb-2" role="group" aria-label="Strength Map corners">
        {QUADRANT_ORDER.map((q) => (
          <button
            key={q}
            type="button"
            aria-pressed={focus === q}
            onClick={() => setFocus(focus === q ? null : q)}
            title={QUADRANTS[q].description}
            className={clsx(
              'press inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[12px] font-semibold ring-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
              QUADRANT_TONE[q],
              focus === q ? 'ring-slate-900' : 'ring-transparent',
            )}
            data-testid={`corner-${q}`}
          >
            {QUADRANTS[q].label} <span className="tabular-nums">{counts[q]}</span>
          </button>
        ))}
        {focus && (
          <button type="button" onClick={() => setFocus(null)} className="press rounded-full px-2 py-1 text-[12px] text-accent hover:underline">
            Show all
          </button>
        )}
      </div>

      <div className="h-[340px] sm:h-[480px]">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 12, bottom: 24, left: 4 }}>
            <CartesianGrid stroke={c.grid} />
            <ReferenceArea x1={MIDPOINT} x2={100} y1={MIDPOINT} y2={100} fill="#30b158" fillOpacity={c.tint} label={{ value: 'Leading', position: 'insideTopRight', fill: '#1f8a42', fontSize: 12, fontWeight: 600 }} />
            <ReferenceArea x1={0} x2={MIDPOINT} y1={MIDPOINT} y2={100} fill="#0a84ff" fillOpacity={c.tint} label={{ value: 'Improving', position: 'insideTopLeft', fill: '#0a6cd6', fontSize: 12, fontWeight: 600 }} />
            <ReferenceArea x1={MIDPOINT} x2={100} y1={0} y2={MIDPOINT} fill="#ff9f0a" fillOpacity={c.tint} label={{ value: 'Fading', position: 'insideBottomRight', fill: '#b36b00', fontSize: 12, fontWeight: 600 }} />
            <ReferenceArea x1={0} x2={MIDPOINT} y1={0} y2={MIDPOINT} fill="#ff375f" fillOpacity={c.tint} label={{ value: 'Lagging', position: 'insideBottomLeft', fill: '#c4223f', fontSize: 12, fontWeight: 600 }} />
            <ReferenceLine x={MIDPOINT} stroke={c.mid} strokeDasharray="4 3" />
            <ReferenceLine y={MIDPOINT} stroke={c.mid} strokeDasharray="4 3" />
            <XAxis
              type="number"
              dataKey="x"
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tick={{ fill: c.tick, fontSize: 11 }}
              label={{ value: 'Strength over 60 sessions (percentile) →', position: 'insideBottom', offset: -14, fill: c.tick, fontSize: 11 }}
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              width={36}
              tick={{ fill: c.tick, fontSize: 11 }}
              label={{ value: 'Last 20 sessions ↑', angle: -90, position: 'insideLeft', offset: 10, fill: c.tick, fontSize: 11 }}
            />
            <ZAxis type="number" dataKey="z" range={compact ? [8, 90] : [18, 260]} domain={[5, 11]} />
            <Tooltip content={<TipCard />} cursor={{ strokeDasharray: '3 3' }} />
            {bySector.map((g) => (
              <Scatter
                key={g.s}
                name={g.s}
                data={g.pts}
                fill={color(g.s)}
                fillOpacity={0.75}
                isAnimationActive={false}
                cursor="pointer"
                onClick={(d: { payload?: Point } & Partial<Point>) => {
                  const sym = d?.payload?.symbol ?? d?.symbol;
                  if (sym) router.push(`/stock/${sym}`);
                }}
              />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {/* sector legend + honest notes */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 px-2 pt-1 text-[11px] text-slate-500" aria-label="Sectors">
        {sectors.map((s) => (
          <span key={s} className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full" style={{ background: color(s) }} aria-hidden="true" />
            {s}
          </span>
        ))}
      </div>
      <p className="px-2 pt-2 text-[11px] text-slate-400" data-testid="map-note">
        {points.length} plotted · showing {rows.length} of {total.toLocaleString('en-US')} matching stocks (the first {rows.length} by the current sort)
        {counts.unavailable ? ` · ${counts.unavailable} without relative strength for ${session} not plotted` : ''}. Dot size = liquidity. Session {session}; describes past
        sessions only.
      </p>
    </div>
  );
}
