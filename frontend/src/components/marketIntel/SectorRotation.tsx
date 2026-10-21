// File: frontend/src/components/marketIntel/SectorRotation.tsx
'use client';

import React, { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { clsx } from 'clsx';
import { CartesianGrid, LabelList, ReferenceArea, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';
import type { MarketSnapshotAvailable, SectorRow } from '@/services/marketIntelligenceApi';
import { fmtSigned } from '@/lib/marketIntel';
import { sectorHref } from '@/lib/sectors';
import { QUADRANT_ORDER, QUADRANT_TONE, QUADRANTS, Quadrant } from '@/lib/strength';

export interface SessionSectors {
  session: string;
  sectors: SectorRow[];
}

const COLORS = ['#0a84ff', '#ff9f0a', '#30b158', '#bf5af2', '#ff375f', '#00a0b4', '#a2845e', '#5e5ce6', '#d4a500', '#8e8e93', '#ff6b22'];

/** A sector's corner from its two differences vs the median stock: 0 is the line ("in line"); unavailable never guessed. */
export function sectorQuadrant(recent: number | null | undefined, longer: number | null | undefined): Quadrant | null {
  if (recent == null || longer == null || !Number.isFinite(recent) || !Number.isFinite(longer)) return null;
  return recent >= 0 && longer >= 0 ? 'leading' : recent >= 0 ? 'improving' : longer >= 0 ? 'fading' : 'lagging';
}

interface Pt {
  sector: string;
  session: string;
  x: number;
  y: number;
  head: boolean;
  label: string;
}

/** Each sector's position per recorded session (oldest first), ending with the current snapshot; points missing a value are skipped. */
export function rotationTrails(current: MarketSnapshotAvailable, history: SessionSectors[]): Map<string, Pt[]> {
  const sessions = history.filter((h) => h.session !== current.session_date).concat([{ session: current.session_date, sectors: current.sectors ?? [] }]);
  const out = new Map<string, Pt[]>();
  for (const s of current.sectors ?? []) out.set(s.sector, []);
  for (const h of sessions) {
    for (const r of h.sectors) {
      const x = r.horizons?.['60']?.vs_univ;
      const y = r.horizons?.['20']?.vs_univ;
      if (!out.has(r.sector) || x == null || y == null) continue;
      out.get(r.sector)!.push({ sector: r.sector, session: h.session, x, y, head: false, label: '' });
    }
  }
  for (const [name, pts] of out) {
    if (pts.length) {
      const last = pts[pts.length - 1];
      if (last.session === current.session_date) Object.assign(last, { head: true, label: name.split(' ')[0] });
    }
  }
  return out;
}

function Dot(props: { cx?: number; cy?: number; fill?: string; payload?: Pt }) {
  const { cx = 0, cy = 0, fill, payload } = props;
  return payload?.head ? (
    <circle cx={cx} cy={cy} r={6} fill={fill} stroke="#fff" strokeWidth={1.5} />
  ) : (
    <circle cx={cx} cy={cy} r={2.5} fill={fill} fillOpacity={0.55} />
  );
}

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Pt }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const q = sectorQuadrant(p.y, p.x) as Quadrant;
  return (
    <div className="rounded-xl bg-white px-3 py-2 text-[12px] shadow-lg ring-1 ring-black/5">
      <p className="flex items-center justify-between gap-2">
        <span className="font-semibold text-slate-900">{p.sector}</span>
        <span className={clsx('rounded-full px-1.5 py-0.5 text-[10px] font-semibold', QUADRANT_TONE[q])}>{QUADRANTS[q].label}</span>
      </p>
      <p className="text-slate-500">session {p.session}</p>
      <p className="tabular-nums text-slate-700">
        20 sessions {fmtSigned(p.y, 1, ' pp')} · 60 sessions {fmtSigned(p.x, 1, ' pp')}
      </p>
      <p className="text-slate-400">vs the median stock</p>
    </div>
  );
}

/**
 * Sector rotation: each sector placed by how its median member did against the median stock over the last 60 sessions
 * (across) and the last 20 (up), with a trail through the recorded sessions ending at the current one. Corners: Leading /
 * Improving / Fading / Lagging (0 = in line). Current members (not point-in-time). Descriptive only.
 */
export default function SectorRotation({ snap, history }: { snap: MarketSnapshotAvailable; history: SessionSectors[] }) {
  const router = useRouter();
  const [focus, setFocus] = useState<string | null>(null);
  const trails = useMemo(() => rotationTrails(snap, history), [snap, history]);
  const names = [...trails.keys()].sort();
  const color = (s: string) => COLORS[names.indexOf(s) % COLORS.length];
  const all = [...trails.values()].flat();
  const lim = Math.max(2, Math.ceil((Math.max(...all.map((p) => Math.max(Math.abs(p.x), Math.abs(p.y))), 0) * 1.15) / 2) * 2);
  const sessions = new Set(all.map((p) => p.session)).size;
  const counts = QUADRANT_ORDER.reduce(
    (acc, q) => ({ ...acc, [q]: (snap.sectors ?? []).filter((s) => sectorQuadrant(s.horizons?.['20']?.vs_univ, s.horizons?.['60']?.vs_univ) === q).length }),
    {} as Record<Quadrant, number>,
  );

  return (
    <section aria-labelledby="rotation-title" className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm sm:p-5" data-testid="sector-rotation">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id="rotation-title" className="text-[17px] font-semibold text-slate-900">
            Sector rotation
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">
            Each sector vs the median stock · across: last 60 sessions · up: last 20 · trail: {sessions} recorded session{sessions === 1 ? '' : 's'} to {snap.session_date}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {QUADRANT_ORDER.map((q) => (
            <span key={q} title={QUADRANTS[q].description} className={clsx('rounded-full px-2.5 py-0.5 text-[12px] font-semibold', QUADRANT_TONE[q])} data-testid={`rot-${q}`}>
              {QUADRANTS[q].label} {counts[q]}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-2 h-[340px] sm:h-[420px]">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 16, bottom: 24, left: 4 }}>
            <CartesianGrid stroke="#e3e3e8" strokeOpacity={0.6} />
            <ReferenceArea x1={0} x2={lim} y1={0} y2={lim} fill="#30b158" fillOpacity={0.05} label={{ value: 'Leading', position: 'insideTopRight', fill: '#1f8a42', fontSize: 12, fontWeight: 600 }} />
            <ReferenceArea x1={-lim} x2={0} y1={0} y2={lim} fill="#0a84ff" fillOpacity={0.05} label={{ value: 'Improving', position: 'insideTopLeft', fill: '#0a6cd6', fontSize: 12, fontWeight: 600 }} />
            <ReferenceArea x1={0} x2={lim} y1={-lim} y2={0} fill="#ff9f0a" fillOpacity={0.05} label={{ value: 'Fading', position: 'insideBottomRight', fill: '#b36b00', fontSize: 12, fontWeight: 600 }} />
            <ReferenceArea x1={-lim} x2={0} y1={-lim} y2={0} fill="#ff375f" fillOpacity={0.05} label={{ value: 'Lagging', position: 'insideBottomLeft', fill: '#c4223f', fontSize: 12, fontWeight: 600 }} />
            <ReferenceLine x={0} stroke="#aeaeb2" />
            <ReferenceLine y={0} stroke="#aeaeb2" />
            <XAxis type="number" dataKey="x" domain={[-lim, lim]} tick={{ fill: '#737378', fontSize: 11 }} tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v}`} label={{ value: '60 sessions vs median stock (pp) →', position: 'insideBottom', offset: -14, fill: '#737378', fontSize: 11 }} />
            <YAxis type="number" dataKey="y" domain={[-lim, lim]} width={40} tick={{ fill: '#737378', fontSize: 11 }} tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v}`} label={{ value: '20 sessions ↑', angle: -90, position: 'insideLeft', offset: 12, fill: '#737378', fontSize: 11 }} />
            <Tooltip content={<Tip />} cursor={{ strokeDasharray: '3 3' }} />
            {names.map((s) => {
              const pts = trails.get(s) ?? [];
              if (!pts.length) return null;
              const dim = focus != null && focus !== s;
              return (
                <Scatter
                  key={s}
                  name={s}
                  data={pts}
                  fill={color(s)}
                  line={{ stroke: color(s), strokeWidth: 1.5, strokeOpacity: dim ? 0.15 : 0.7 }}
                  opacity={dim ? 0.25 : 1}
                  shape={<Dot />}
                  isAnimationActive={false}
                  cursor="pointer"
                  onMouseEnter={() => setFocus(s)}
                  onMouseLeave={() => setFocus(null)}
                  onClick={() => {
                    const href = sectorHref(s);
                    if (href) router.push(href);
                  }}
                >
                  <LabelList dataKey="label" position="top" style={{ fontSize: 10, fill: '#334155', fontWeight: 600 }} />
                </Scatter>
              );
            })}
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500" aria-label="Sectors">
        {names.map((s) => (
          <button
            key={s}
            type="button"
            onMouseEnter={() => setFocus(s)}
            onMouseLeave={() => setFocus(null)}
            onFocus={() => setFocus(s)}
            onBlur={() => setFocus(null)}
            onClick={() => {
              const href = sectorHref(s);
              if (href) router.push(href);
            }}
            className="inline-flex items-center gap-1 rounded hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span className="h-2 w-2 rounded-full" style={{ background: color(s) }} aria-hidden="true" />
            {s}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-slate-400" data-testid="rotation-note">
        Big dot = {snap.session_date}; small dots = earlier recorded sessions. Sector = median of its current members (not point-in-time). Describes past
        sessions only.
      </p>
    </section>
  );
}
