'use client';

// File: frontend/src/components/dashboard/SectorTrendChart.tsx
// "Trend" companion to the sector Heatmap treemap on the dashboard.
//
// Every sector has one fixed color (see sectorPalette.ts -- 8 validated hues,
// with the 3 sectors past the 8th distinguished by a dashed stroke on a shared
// hue, and "Unknown" in neutral gray). Because 12 colored lines are still a lot
// to read at once, the chart leans on interaction rather than on the palette
// alone:
//   * the legend below is a ranked list (best -> worst cumulative return) whose
//     swatch mirrors each line's color AND dash, so "which color is what" is
//     answered next to the numbers, not by guessing;
//   * hovering/focusing a legend row spotlights that line and dims the rest;
//   * clicking a row (or a line) selects it -- the same `selectedSector` state
//     the heatmap and Alpha Finder already share;
//   * the tooltip lists every sector at the hovered date, sorted, keyed with the
//     same line swatch.

import React, { useMemo, useState } from 'react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine } from 'recharts';
import { SectorHistoryEntry } from '@/services/api';
import { getSectorStyle } from './sectorPalette';

const SURFACE = '#ffffff';

interface MergedPoint {
  date: string;
  [sector: string]: string | number | null;
}

function buildCumulativeSeries(sectors: SectorHistoryEntry[]): { merged: MergedPoint[]; finalBySector: Record<string, number> } {
  const dateSet = new Set<string>();
  sectors.forEach((s) => s.history.forEach((p) => dateSet.add(p.date)));
  const dates = Array.from(dateSet).sort();

  const cumBySector: Record<string, Record<string, number>> = {};
  const finalBySector: Record<string, number> = {};

  for (const s of sectors) {
    let index = 100;
    const bySector: Record<string, number> = {};
    const byDate = new Map(s.history.map((p) => [p.date, p.avg_performance]));
    for (const date of dates) {
      const pct = byDate.get(date);
      if (pct !== undefined) {
        index = index * (1 + pct / 100);
      }
      bySector[date] = index - 100;
    }
    cumBySector[s.sector] = bySector;
    finalBySector[s.sector] = index - 100;
  }

  const merged: MergedPoint[] = dates.map((date) => {
    const point: MergedPoint = { date };
    for (const s of sectors) {
      point[s.sector] = cumBySector[s.sector][date] ?? null;
    }
    return point;
  });

  return { merged, finalBySector };
}

function fmtPct(v: number, digits = 2): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`;
}

/** Short stroke in the sector's color/dash -- the "line key" used by both the legend and the tooltip. */
function LineKey({ sector, width = 22 }: { sector: string; width?: number }) {
  const { color, dash } = getSectorStyle(sector);
  return (
    <svg width={width} height={8} viewBox={`0 0 ${width} 8`} aria-hidden="true" className="shrink-0">
      <line x1={1} y1={4} x2={width - 1} y2={4} stroke={color} strokeWidth={2.5} strokeLinecap="round" strokeDasharray={dash} />
    </svg>
  );
}

interface TooltipPayloadEntry {
  dataKey?: string | number;
  value?: number | string | null;
}

function CustomTooltip({
  active,
  payload,
  label,
  focusSector,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string | number;
  focusSector: string | null;
}) {
  if (!active || !payload || payload.length === 0) return null;

  const rows = payload
    .filter((p): p is { dataKey: string; value: number } => typeof p.value === 'number' && typeof p.dataKey === 'string')
    .sort((a, b) => b.value - a.value);
  if (rows.length === 0) return null;

  return (
    <div className="bg-white border border-slate-200 rounded shadow-md px-3 py-2 text-xs">
      <p className="text-slate-500 mb-1.5">{label}</p>
      <div className="space-y-0.5">
        {rows.map((r) => (
          <div key={r.dataKey} className={`flex items-center gap-2 ${focusSector === r.dataKey ? 'font-semibold' : ''}`}>
            <LineKey sector={r.dataKey} width={16} />
            <span className="text-slate-900 tabular-nums w-14 text-right">{fmtPct(r.value)}</span>
            <span className="text-slate-500">{r.dataKey}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SectorTrendChart({
  sectors,
  selectedSector,
  onSelectSector,
}: {
  sectors: SectorHistoryEntry[];
  selectedSector: string | null;
  onSelectSector: (sector: string | null) => void;
}) {
  const [hoveredSector, setHoveredSector] = useState<string | null>(null);
  const { merged, finalBySector } = useMemo(() => buildCumulativeSeries(sectors), [sectors]);

  if (sectors.length === 0 || merged.length === 0) {
    return <p className="text-center text-slate-400 text-sm py-10">No sector history yet.</p>;
  }

  // A line needs at least two dates to be drawn -- with one, every series is
  // a single point and (dot={false}) Recharts renders a blank plot area, which
  // reads as a broken chart rather than "not enough history yet".
  if (merged.length < 2) {
    return (
      <p className="text-center text-slate-400 text-sm py-10">
        Only one day of sector history so far ({merged[0].date}) -- the trend needs at least two trading days.
        Run <code>sector_performance_snapshot.py --days 120</code> to backfill.
      </p>
    );
  }

  // Hover previews; selection persists. Hover wins while the pointer is on a row.
  const focusSector = hoveredSector ?? selectedSector;

  const rankedSectors = [...sectors]
    .map((s) => s.sector)
    .sort((a, b) => (finalBySector[b] ?? 0) - (finalBySector[a] ?? 0));

  // Paint the focused line last so it sits on top of the others.
  const drawOrder = focusSector ? [...rankedSectors.filter((s) => s !== focusSector), focusSector] : rankedSectors;

  return (
    <div>
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={merged} margin={{ top: 14, right: 16, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="none" stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: '#64748b' }}
            tickLine={false}
            axisLine={{ stroke: '#e2e8f0' }}
            minTickGap={40}
          />
          <YAxis
            tick={{ fontSize: 10, fill: '#64748b' }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => `${v >= 0 ? '+' : ''}${Number(v.toFixed(1))}%`}
            width={52}
          />
          <ReferenceLine y={0} stroke="#94a3b8" strokeWidth={1} />
          <Tooltip
            content={<CustomTooltip focusSector={focusSector} />}
            cursor={{ stroke: '#94a3b8', strokeDasharray: '3 3' }}
            isAnimationActive={false}
          />
          {drawOrder.map((sector) => {
            const isFocus = focusSector === sector;
            const isDimmed = focusSector !== null && !isFocus;
            const { color, dash } = getSectorStyle(sector);
            return (
              <Line
                key={sector}
                type="monotone"
                dataKey={sector}
                stroke={color}
                strokeWidth={isFocus ? 3 : 2}
                strokeOpacity={isDimmed ? 0.15 : 1}
                strokeDasharray={dash}
                strokeLinecap="round"
                strokeLinejoin="round"
                // End marker + direct label only for the spotlighted sector: a
                // label per line would collide where the lines converge.
                dot={
                  isFocus
                    ? (props: { cx?: number; cy?: number; index?: number }) => {
                        const { cx, cy, index } = props;
                        if (index !== merged.length - 1 || cx === undefined || cy === undefined) {
                          return <g key={`dot-${index}`} />;
                        }
                        const value = finalBySector[sector] ?? 0;
                        return (
                          <g key={`dot-${index}`}>
                            <circle cx={cx} cy={cy} r={5} fill={color} stroke={SURFACE} strokeWidth={2} />
                            <text
                              x={cx - 10}
                              y={cy - 10}
                              textAnchor="end"
                              fontSize={11}
                              fontWeight={600}
                              fill="#0f172a"
                              stroke={SURFACE}
                              strokeWidth={3}
                              paintOrder="stroke"
                            >
                              {sector} {fmtPct(value, 1)}
                            </text>
                          </g>
                        );
                      }
                    : false
                }
                activeDot={isDimmed ? false : { r: 4, stroke: SURFACE, strokeWidth: 2 }}
                isAnimationActive={false}
                onClick={() => onSelectSector(selectedSector === sector ? null : sector)}
                style={{ cursor: 'pointer' }}
              />
            );
          })}
        </LineChart>
      </ResponsiveContainer>

      <p className="text-[11px] text-slate-400 mt-1 px-1">
        Cumulative return over the period, best to worst. Hover a sector to spotlight it, click to filter Alpha Finder.
        Dashed lines share a color with a solid one; gray dotted is symbols without a sector tag yet.
      </p>

      <ul className="grid grid-cols-1 min-[480px]:grid-cols-2 lg:grid-cols-3 gap-0 mt-2">
        {rankedSectors.map((sector) => {
          const isSelected = selectedSector === sector;
          const isDimmed = focusSector !== null && focusSector !== sector;
          const value = finalBySector[sector] ?? 0;
          return (
            <li key={sector}>
              <button
                type="button"
                aria-pressed={isSelected}
                onClick={() => onSelectSector(isSelected ? null : sector)}
                onMouseEnter={() => setHoveredSector(sector)}
                onMouseLeave={() => setHoveredSector(null)}
                onFocus={() => setHoveredSector(sector)}
                onBlur={() => setHoveredSector(null)}
                className={`w-full flex items-center gap-2 px-2 py-1 rounded text-xs text-left transition-colors hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-slate-400 ${
                  isSelected ? 'bg-slate-100 font-semibold' : ''
                } ${isDimmed ? 'opacity-50' : ''}`}
              >
                <LineKey sector={sector} />
                <span className="text-slate-700 truncate">{sector}</span>
                <span className="ml-auto text-slate-900 tabular-nums">{fmtPct(value, 1)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
