// File: frontend/src/services/inflectionApi.ts
// Read-only client for the Fundamental Inflection engine (GET /api/inflection/*). Research signals with their provenance: nothing here is a recommendation.
import { apiClient } from '@/services/api';

export type InflectionSeverity = 'low' | 'medium' | 'high';

export interface InflectionKeyMetric {
  label: string;
  current: number | null;
  previous: number | null;
  unit: 'pct' | 'money' | 'bps' | 'ratio' | 'score';
}

export interface InflectionEvent {
  id: number;
  symbol: string;
  company_name: string | null;
  sector: string | null;
  event_type: string;
  label: string;
  strength: number;
  severity: InflectionSeverity;
  score: number;
  fiscal_year: number | null;
  fiscal_quarter: number | null;
  quarter_end: string;
  available_at: string;
  freshness_days: number;
  availability_basis: string;
  source: string | null;
  data_quality: 'ok' | 'partial';
  revision_count: number;
  method_version: string | null;
  explanation: string[];
  key_metrics: InflectionKeyMetric[];
  reaction_2d: number | null;
  excess_vs_sector_since_publication: number | null;
  market_cap: number | null;
  valuation: {
    ps_ratio: number | null;
    pe_ratio: number | null;
    ps_percentile_own_history: number | null;
    ps_vs_sector_median: number | null;
    ps_ratio_at_publication: number | null;
    snapshot_date: string | null;
  };
}

export interface InflectionEventsResponse {
  method_version: string;
  notice?: string;
  available?: boolean;
  reason?: string;
  total: number;
  count: number;
  offset: number;
  events: InflectionEvent[];
}

export interface InflectionTrendPoint {
  quarter_end: string;
  fiscal_year: number | null;
  fiscal_quarter: number | null;
  available_at: string;
  availability_basis: string;
  revenue: number | null;
  operating_income: number | null;
  net_income: number | null;
  free_cash_flow: number | null;
  op_margin: number | null;
  rev_yoy: number | null;
  rev_accel_pp: number | null;
  cash_conversion: number | null;
  score: number;
  data_quality: 'ok' | 'partial';
  notes: string[];
}

export interface InflectionStockResponse {
  symbol: string;
  method_version: string;
  notice?: string;
  available: boolean;
  reason?: string;
  latest_score: number | null;
  latest_quarter_end: string | null;
  events: InflectionEvent[];
  trend: InflectionTrendPoint[];
  data_status?: { quarters: number; partial_quarters: number; source: string | null };
}

export interface InflectionSummary {
  method_version: string;
  notice?: string;
  available: boolean;
  reason?: string;
  features: number;
  events: number;
  newest_event?: string | null;
  window_days?: number;
  by_type: { event_type: string; label: string; high: number; medium: number; low: number; total: number }[];
  last_run: { status: string; finished_at: string | null; asof: string; symbols_processed: number; events_new: number; errors: number } | null;
}

export interface InflectionQuery {
  event_type?: string[];
  severity?: InflectionSeverity;
  sector?: string;
  min_strength?: number;
  min_market_cap?: number;
  max_market_cap?: number;
  since?: string;
  q?: string;
  sort?: 'strength' | 'available_at' | 'score' | 'symbol';
  limit?: number;
  offset?: number;
}

export const inflectionApi = {
  events: async (params: InflectionQuery): Promise<InflectionEventsResponse> =>
    (await apiClient.get<InflectionEventsResponse>('/api/inflection/events', { params, paramsSerializer: { indexes: null } })).data,
  summary: async (days = 90): Promise<InflectionSummary> => (await apiClient.get<InflectionSummary>('/api/inflection/summary', { params: { days } })).data,
  stock: async (symbol: string): Promise<InflectionStockResponse> => (await apiClient.get<InflectionStockResponse>(`/api/inflection/stock/${encodeURIComponent(symbol)}`)).data,
};
