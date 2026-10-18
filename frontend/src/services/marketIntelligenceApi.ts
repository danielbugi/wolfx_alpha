// File: frontend/src/services/marketIntelligenceApi.ts
// Typed client for the read-only Market Intelligence API (backend/routers/market_intelligence.py; payload shape:
// mechanism/market_intelligence/payload.py, schema mi_payload_v1). Observed rows only: the product never asks for
// reconstructed rows. Every field the backend may leave null is typed nullable, and the UI shows it as unavailable.
import { apiClient } from '@/services/api';

export type Provenance = 'observed' | 'reconstructed';
export type RegimeState = 'RISK_ON' | 'RISK_OFF' | 'NEUTRAL' | 'UNAVAILABLE';
export type BreadthState = 'ok' | 'insufficient_eligible' | 'no_universe' | 'no_session';
export type Horizon = '5' | '20' | '60';

export interface CountRecord {
  numerator: number | null;
  denominator: number | null;
  universe: number | null;
  missing: number | null;
  /** 100 * numerator / denominator; null when the denominator is below the minimum (state insufficient_eligible). */
  pct: number | null;
  state: BreadthState;
}

export type BreadthMetric = 'above_sma50' | 'above_sma200' | 'new_high_52w' | 'new_low_52w' | 'positive_ret_20' | 'advancing_1d' | 'declining_1d';

export interface RegimeComponent {
  key: string;
  label: string;
  weight: number;
  present: boolean;
  value: number | null;
  score: number | null;
  missing_reason: string | null;
  raw: Record<string, unknown>;
}

export interface SectorRow {
  sector: string;
  n_members: number;
  rank_20: number | null;
  horizons: Record<Horizon, { n_valid: number | null; n_excluded: number | null; ret: number | null; vs_spx: number | null; vs_univ: number | null }>;
}

export interface SectorMeasurement {
  sector: string;
  n_members?: number;
  strength?: { rank_20?: number | null; n_ranked?: number | null; strength_state?: string | null };
  breadth?: Partial<Record<'above_sma50' | 'above_sma200', CountRecord>>;
}

export interface MarketSnapshotAvailable {
  schema: string;
  available: true;
  session_date: string;
  provenance: Provenance;
  observed: boolean;
  provenance_note: string | null;
  regime: {
    model_version: string;
    state: RegimeState;
    available: boolean;
    score: number | null;
    strength: number | null;
    strength_label: string | null;
    agreement: number | null;
    present_weight: number | null;
    min_present_weight: number;
    thresholds: { risk_on: number; risk_off: number };
    components: RegimeComponent[];
    reasons: string[];
    is_heuristic: boolean;
    disclaimer: string;
  };
  returns: {
    spx: Record<Horizon, number | null>;
    universe_median: Record<Horizon, { ret: number | null; n_valid: number | null }>;
    unit: string;
    universe_definition: string;
  };
  sectors: SectorRow[];
  sector_map: { source: string | null; asof_rule: string | null; pit_safe: boolean; n_symbols: number | null; n_classified: number | null };
  coverage: {
    n_universe?: number;
    n_classified?: number;
    n_unclassified?: number;
    classified_share?: number;
    n_sectors?: number;
    n_sectors_ranked?: number;
    min_universe?: number;
    min_sector_members?: number;
    n_excluded_discontinuity?: Partial<Record<Horizon, number>>;
    sector_map?: { source?: string; pit_safe?: boolean; provenance?: string; caveat?: string | null };
    measurements?: {
      breadth?: { market?: Partial<Record<BreadthMetric, CountRecord>> };
      sectors?: SectorMeasurement[];
    };
  };
  meta: { captured_at: string | null; source: string | null; feature_set_version: string | null };
}

export interface MarketSnapshotUnavailable {
  schema: string;
  available: false;
  reason: string;
  message: string | null;
}

export type MarketSnapshot = MarketSnapshotAvailable | MarketSnapshotUnavailable;

export interface MarketHistoryRow {
  session_date: string;
  provenance: Provenance;
  regime_state: RegimeState;
  regime_score: number | null;
}

export interface MarketHistory {
  available: boolean;
  sessions?: MarketHistoryRow[];
}

export const marketIntelligenceApi = {
  /** The newest observed session (no `session` parameter, no reconstructed rows). */
  async getSnapshot(): Promise<MarketSnapshot> {
    const r = await apiClient.get<MarketSnapshot>('/api/market-intelligence/snapshot');
    return r.data;
  },
  /** Observed sessions, newest first. */
  async getHistory(limit = 30): Promise<MarketHistory> {
    const r = await apiClient.get<MarketHistory>('/api/market-intelligence/history', { params: { limit } });
    return r.data;
  },
};
