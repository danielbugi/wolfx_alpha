// Sector pages: the shapes served by /api/market/sectors and /api/market/sectors/{slug}, plus small pure helpers.
// Every figure is computed by the backend from stored prices/fundamentals (backend/services/sector_service.py).

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
  industry_count: number;
  market_cap: number | null;
  change: Record<SectorWindow, ChangeSummary>;
  above_sma50_pct: number | null;
  /** equal-weighted index levels, last ~3 months */
  sparkline: number[];
}

export interface SectorsResponse {
  as_of: string | null;
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
