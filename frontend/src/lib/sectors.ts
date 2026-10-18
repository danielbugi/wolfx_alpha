// Sector pages: the shapes served by /api/market/sectors and /api/market/sectors/{slug}, plus small pure helpers.
// Every figure is computed by the backend from stored prices/fundamentals (backend/services/sector_service.py) using
// TODAY'S sector membership, so past windows and the index are not point-in-time. The observed, point-in-time sector
// context comes from the market-intelligence snapshot instead (see observedSector below).
import type { CountRecord, MarketSnapshotAvailable, SectorRow } from '@/services/marketIntelligenceApi';

/** How the backend defined membership. `point_in_time: false` = today's assignment applied to the whole history. */
export interface MembershipInfo {
  basis: string;
  point_in_time: boolean;
  source: string;
  note: string;
}

export type SectorWindow = '1d' | '1w' | '1m' | '3m' | 'ytd' | '1y';

/** Median change of the members that have a value, and the share of them that rose; nulls when none do. */
export interface ChangeSummary {
  median: number | null;
  up_share: number | null;
  n: number;
}

export interface SectorSummary {
  sector: string;
  slug: string;
  stock_count: number;
  /** members with a close on the newest price session (the rest have no current price) */
  priced_count?: number;
  industry_count: number;
  market_cap: number | null;
  change: Record<SectorWindow, ChangeSummary>;
  above_sma50_pct: number | null;
  /** equal-weighted index levels, last ~3 months */
  sparkline: number[];
}

export interface SectorsResponse {
  as_of: string | null;
  membership?: MembershipInfo;
  sectors: SectorSummary[];
}

export interface IndustryRow {
  industry: string;
  stock_count: number;
  market_cap: number | null;
  change: Record<SectorWindow, ChangeSummary>;
  above_sma50_pct: number | null;
}

export interface SectorStock {
  symbol: string;
  industry: string | null;
  close: number | null;
  change_pct: Record<SectorWindow, number | null>;
  market_cap: number | null;
  pe_ratio: number | null;
  quality_grade: string | null;
  rsi_14: number | null;
  volume_ratio: number | null;
  above_sma50: boolean | null;
}

export interface SectorDetail {
  as_of: string | null;
  sector: string;
  slug: string;
  stock_count: number;
  priced_count?: number;
  membership?: MembershipInfo;
  market_cap: number | null;
  change: Record<SectorWindow, ChangeSummary>;
  above_sma50_pct: number | null;
  industries: IndustryRow[];
  stocks: SectorStock[];
  /** equal-weighted index of current members (level, from 100), ~1 year */
  index: { date: string; level: number }[];
  spx: { date: string; close: number }[];
}

export const SECTOR_WINDOWS: { key: SectorWindow; label: string }[] = [
  { key: '1d', label: '1D' },
  { key: '1w', label: '1W' },
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
];

/** The sector page for a sector name, or null for the "Unknown" bucket (stocks without a sector have no page). */
export function sectorHref(sector: string | null | undefined): string | null {
  return sector && sector !== 'Unknown' ? `/sectors/${sectorSlug(sector)}` : null;
}

/** The URL slug for a sector name -- the same rule as the backend's slugify. */
export function sectorSlug(sector: string): string {
  return sector.toLowerCase().replace(/&/g, 'and').replace(/ /g, '-');
}

/**
 * The sector's index and the S&P 500 over the same dates, each as % change from the first shared date on/after
 * `from`. Only dates both series have are kept, so neither line is padded.
 */
export function rebasedComparison(
  index: { date: string; level: number }[],
  spx: { date: string; close: number }[],
  from: string,
): { date: string; sector: number; spx: number }[] {
  const spxByDate = new Map(spx.map((p) => [p.date, p.close]));
  const shared = index.filter((p) => p.date >= from && spxByDate.has(p.date));
  if (shared.length === 0) return [];
  const s0 = shared[0].level;
  const m0 = spxByDate.get(shared[0].date)!;
  return shared.map((p) => ({
    date: p.date,
    sector: (p.level / s0 - 1) * 100,
    spx: (spxByDate.get(p.date)! / m0 - 1) * 100,
  }));
}

/** Shown when an older backend sends no `membership` block: the same statement, never a claim of point-in-time data. */
export const DEFAULT_MEMBERSHIP: MembershipInfo = {
  basis: 'current',
  point_in_time: false,
  source: 'latest stored sector assignment per stock',
  note: "Membership is today's sector assignment. Past windows and the sector index use today's members for the whole period; they are not a record of the sector as it was.",
};

export const EXPECTED_SECTORS = 11;

export interface ObservedSector {
  row: SectorRow;
  rankOf: number | null;
  strengthState: string | null;
  aboveSma50: CountRecord | null;
  aboveSma200: CountRecord | null;
}

/** The observed (point-in-time) snapshot entry for one sector, matched by name; null when the snapshot has none. */
export function observedSector(snap: MarketSnapshotAvailable, sector: string): ObservedSector | null {
  const row = snap.sectors.find((s) => s.sector === sector);
  if (!row) return null;
  const m = (snap.coverage.measurements?.sectors ?? []).find((x) => x.sector === sector);
  return {
    row,
    rankOf: m?.strength?.n_ranked ?? snap.coverage.n_sectors_ranked ?? null,
    strengthState: m?.strength?.strength_state ?? null,
    aboveSma50: m?.breadth?.above_sma50 ?? null,
    aboveSma200: m?.breadth?.above_sma200 ?? null,
  };
}
