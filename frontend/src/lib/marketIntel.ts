// File: frontend/src/lib/marketIntel.ts
// Pure helpers for the Market Overview cards. They describe stored values and never fill a gap: anything missing comes
// back as null and is rendered as unavailable.
import type { CountRecord, Horizon, MarketSnapshotAvailable, RegimeState, SectorRow } from '@/services/marketIntelligenceApi';

/** Neutral, descriptive wording for the heuristic's state. The model's own code is kept for the tooltip. */
export const REGIME_LABEL: Record<RegimeState, string> = {
  RISK_ON: 'Broadly positive',
  NEUTRAL: 'Mixed',
  RISK_OFF: 'Broadly negative',
  UNAVAILABLE: 'Unavailable',
};

export const HORIZONS: readonly Horizon[] = ['5', '20', '60'];

/** Each breadth row with its measurement window, shown under the bar: what is counted and over how long. The base
 * (denominator) is always the stocks for which the measure is computable on the session. */
export const BREADTH_ROWS: { label: string; key: 'above_sma50' | 'above_sma200' | 'positive_ret_20'; window: string }[] = [
  { label: 'Above 50-day average', key: 'above_sma50', window: 'close above its 50-session simple average' },
  { label: 'Above 200-day average', key: 'above_sma200', window: 'close above its 200-session simple average' },
  { label: 'Up over 20 sessions', key: 'positive_ret_20', window: 'close above its close 20 sessions earlier' },
];

export const fmtSigned = (v: number | null | undefined, digits = 1, unit = '%'): string =>
  v == null || !Number.isFinite(v) ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}${unit}`;

export const fmtInt = (v: number | null | undefined): string => (v == null || !Number.isFinite(v) ? '—' : Math.round(v).toLocaleString('en-US'));

/** "861 of 3,053 (28.2%)" -- or why there is no percentage. Never a bare percentage. */
export function describeCount(r: CountRecord | undefined | null): { ratio: string; pct: string | null; note: string | null } {
  if (!r) return { ratio: '—', pct: null, note: 'not measured' };
  const ratio = r.numerator != null && r.denominator != null ? `${fmtInt(r.numerator)} of ${fmtInt(r.denominator)}` : '—';
  if (r.state === 'ok' && r.pct != null) return { ratio, pct: `${r.pct.toFixed(1)}%`, note: r.missing ? `${fmtInt(r.missing)} not evaluable` : null };
  const note =
    r.state === 'insufficient_eligible'
      ? `too few evaluable stocks (${fmtInt(r.denominator)}) for a percentage`
      : r.state === 'no_universe'
        ? 'no stocks in the universe'
        : 'no market session';
  return { ratio, pct: null, note };
}

/** The snapshot is older than the newest stored price session (the price date the rest of the page shows). Compared as
 * ISO dates only; nothing is inferred from the wall clock. Unknown either side -> null (cannot tell). */
export function isOlderThan(sessionDate: string | null | undefined, priceDate: string | null | undefined): boolean | null {
  const iso = /^\d{4}-\d{2}-\d{2}/;
  if (!sessionDate || !priceDate || !iso.test(sessionDate) || !iso.test(priceDate)) return null;
  return sessionDate.slice(0, 10) < priceDate.slice(0, 10);
}

/** Position of the score on a -1..+1 bar, as 0..100 (null when the score is unavailable). */
export function scorePosition(score: number | null | undefined): number | null {
  if (score == null || !Number.isFinite(score)) return null;
  return Math.max(0, Math.min(100, ((score + 1) / 2) * 100));
}

export interface SectorLeaderRow {
  sector: string;
  rank: number | null;
  members: number;
  ret20: number | null;
  vsUniverse20: number | null;
  vsSpx20: number | null;
  nValid20: number | null;
  strengthState: string | null;
  aboveSma50: CountRecord | null;
}

/** The ranked sector table: rank order as stored (unranked sectors last), joined with the sector measurements for the
 * strength state and breadth. A sector without a measurement keeps nulls. */
export function sectorLeaders(snap: MarketSnapshotAvailable): SectorLeaderRow[] {
  const meas = new Map((snap.coverage.measurements?.sectors ?? []).map((m) => [m.sector, m]));
  return [...snap.sectors]
    .sort((a: SectorRow, b: SectorRow) => (a.rank_20 == null ? 1 : 0) - (b.rank_20 == null ? 1 : 0) || (a.rank_20 ?? 0) - (b.rank_20 ?? 0) || a.sector.localeCompare(b.sector))
    .map((s) => {
      const m = meas.get(s.sector);
      const h = s.horizons?.['20'];
      return {
        sector: s.sector,
        rank: s.rank_20,
        members: s.n_members,
        ret20: h?.ret ?? null,
        vsUniverse20: h?.vs_univ ?? null,
        vsSpx20: h?.vs_spx ?? null,
        nValid20: h?.n_valid ?? null,
        strengthState: m?.strength?.strength_state ?? null,
        aboveSma50: m?.breadth?.above_sma50 ?? null,
      };
    });
}

/** "leading" / "in_line" / "lagging" -> words; anything else is shown as stored, unknown as unavailable. */
export function strengthWord(state: string | null): string {
  if (!state || state === 'not_available') return '—';
  return { leading: 'Leading', in_line: 'In line', lagging: 'Lagging' }[state] ?? state.replace(/_/g, ' ');
}

/** A regime component's stored value in its own unit (see risk_regime.py); missing -> "—". */
export function componentValue(key: string, value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—';
  switch (key) {
    case 'c1_index_trend':
      return `${Math.round(value)} of 3 conditions`;
    case 'c2_index_momentum':
      return fmtSigned(value, 1);
    case 'c3_breadth_sma50':
    case 'c4_breadth_sma200':
      return `${value.toFixed(1)}% of stocks`;
    case 'c5_net_highs_lows':
      return `${fmtSigned(value, 1, '')} pp of stocks`;
    case 'c6_volatility':
      return value.toFixed(1);
    case 'c7_risk_appetite':
      return `${fmtSigned(value, 1, '')} pp`;
    default:
      return value.toFixed(2);
  }
}

/** "captured 2026-10-17 06:46 (UTC+03)" from the stored timestamp text, keeping its own offset; unparseable -> null. */
export function capturedLabel(ts: string | null | undefined): string | null {
  if (!ts) return null;
  const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}(?::?\d{2})?)?$/.exec(ts.trim());
  if (!m) return null;
  const off = m[3] ? (m[3] === 'Z' ? 'UTC' : `UTC${m[3].slice(0, 3)}`) : null;
  return `captured ${m[1]} ${m[2]}${off ? ` (${off})` : ''}`;
}
