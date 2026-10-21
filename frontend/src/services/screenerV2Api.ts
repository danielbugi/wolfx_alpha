// File: frontend/src/services/screenerV2Api.ts
// Client for the Smart Screener v2 endpoints (backend/routers/screener.py /v2/*, services/discovery_service.py).
// One explicit session per response; missing values arrive as null and are shown as unavailable.
import { apiClient } from '@/services/api';

export type MetricFamily = 'observed' | 'derived' | 'reference';
export type FundamentalsCutoff = 'post_session' | 'session_close';

export interface MetricDefinition {
  key: string;
  label: string;
  unit: string;
  family: MetricFamily;
  definition: string;
  numeric: boolean;
}

export interface PresetDefinition {
  key: string;
  label: string;
  description: string;
  sort: [string, 'asc' | 'desc'];
}

export interface ScreenerDefinitions {
  metrics: MetricDefinition[];
  families: Record<MetricFamily, string>;
  presets: PresetDefinition[];
  sort_keys: string[];
  limits: { max_page_size: number; max_export_rows: number };
}

export interface ScreenerRow {
  symbol: string;
  sector: string | null;
  sector_source: 'observed' | 'reference' | null;
  industry: string | null;
  close: number | null;
  change_1d: number | null;
  ret_5: number | null;
  ret_20: number | null;
  ret_60: number | null;
  vs_spx_20: number | null;
  vs_spx_60: number | null;
  vs_sector_20: number | null;
  rs_20: number | null;
  rs_60: number | null;
  breakout_20: boolean | null;
  new_high_20: boolean | null;
  dist_sma20: number | null;
  dist_sma50: number | null;
  channel_pos: number | null;
  channel_width: number | null;
  channel_width_pctile: number | null;
  atr_pct: number | null;
  atr_pct_pctile: number | null;
  volume_ratio: number | null;
  dollar_vol_20: number | null;
  market_cap: number | null;
  /** Company name from the companies reference table (null when not stored). */
  company_name?: string | null;
  /** Reference data from the same fundamentals row as market cap (ingestion-time, not verified point-in-time). */
  pe_ratio?: number | null;
  ps_ratio?: number | null;
  quality_score?: number | null;
  quality_grade?: string | null;
  float_shares?: number | null;
  /** Earnings calendar as stored: calendar days to the next report after the session / since the last one on or before it. */
  days_to_earnings?: number | null;
  days_since_earnings?: number | null;
  earnings_surprise_pct?: number | null;
  next_earnings_date?: string | null;
  last_earnings_date?: string | null;
  /** Strength Score 0-100 (shared engine); null with strength_reason when the data-health gate withholds it */
  strength_score?: number | null;
  strength_rs?: number | null;
  strength_trend?: number | null;
  strength_momentum?: number | null;
  strength_volume?: number | null;
  strength_liquidity?: number | null;
  strength_reason?: string | null;
  dv_bars: number;
  fundamentals_date: string | null;
  /** when our pipeline ingested the fundamentals row (UTC); the original publication time is unknown */
  fundamentals_ingested_at: string | null;
  /** true when that row was ingested after the session's close (it relies on the post-session reporting convention) */
  fundamentals_after_close: boolean | null;
}

export interface ScreenerCoverage {
  rs: number;
  rs_20: number;
  rs_60: number;
  technical: number;
  breakout_20: number;
  atr_pct: number;
  dollar_vol_20: number;
  dollar_vol_insufficient: number;
  dollar_vol_no_bars: number;
  fundamentals: number;
  fundamentals_after_close: number;
  market_cap: number;
  sector: number;
  sector_observed: number;
  change_1d: number;
}

export interface ScreenerResult {
  session: string;
  previous_session: string | null;
  dollar_volume_window: { from: string | null; to: string; sessions: number };
  fundamentals_cutoff_utc: string;
  fundamentals_basis: {
    type: 'ingestion_time_reference';
    publication_time: 'unknown';
    point_in_time_verified: false;
    cutoff_mode: FundamentalsCutoff;
    cutoff_utc: string;
    session_close_utc: string;
    note: string;
  };
  recent_sessions: string[];
  universe: number;
  sectors: string[];
  coverage: ScreenerCoverage;
  total: number;
  page: number;
  page_size: number;
  sort: string;
  order: 'asc' | 'desc';
  preset: string | null;
  rows: ScreenerRow[];
  truncated: boolean;
}

export type QueryPairs = [string, string][];

function toSearchParams(pairs: QueryPairs): URLSearchParams {
  const p = new URLSearchParams();
  for (const [k, v] of pairs) p.append(k, v);
  return p;
}

export const screenerV2Api = {
  async definitions(): Promise<ScreenerDefinitions> {
    return (await apiClient.get<ScreenerDefinitions>('/api/screener/v2/definitions')).data;
  },
  async search(pairs: QueryPairs): Promise<ScreenerResult> {
    return (await apiClient.get<ScreenerResult>('/api/screener/v2/search', { params: toSearchParams(pairs) })).data;
  },
  /** The CSV as a Blob (fetched with the session's token, so a plain link would not be authorised). */
  async exportCsv(pairs: QueryPairs): Promise<Blob> {
    const r = await apiClient.get('/api/screener/v2/export.csv', { params: toSearchParams(pairs.filter(([k]) => k !== 'page' && k !== 'page_size')), responseType: 'blob' });
    return r.data as Blob;
  },
};
