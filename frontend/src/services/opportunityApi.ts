// File: frontend/src/services/opportunityApi.ts
// Read-only, owner-only client for Opportunity Intelligence v0 (GET /api/opportunity/*). Descriptive change briefs: no score, no forecast.
import { apiClient } from '@/services/api';

export type Verdict = 'supports' | 'contradicts' | 'neutral' | 'mixed' | 'unavailable' | 'held' | 'unverified' | 'positive' | 'negative' | 'flat' | 'unreliable';
export type View = 'all' | 'agreeing' | 'discovery';
export type FigureUnit = 'usd' | 'pct' | 'pp' | 'bps' | 'x' | 'usd_per_share';

export interface OIFigure {
  label: string;
  value: number;
  unit: FigureUnit;
  source: string;
  available_at: string;
}

export interface OIFact {
  family: 'revenue' | 'operating' | 'net_income' | 'cash' | 'expectations' | 'market';
  label: string;
  verdict: Verdict;
  figures: OIFigure[];
  note: string;
}

export interface OIBrief {
  version: string;
  symbol: string;
  company_name: string | null;
  quarter_end: string;
  filing: { form: string; accession: string | null; accepted_at: string; source: string };
  first_public: string;
  what_changed: { event_type: string; label: string; lines: string[]; source: string; available_at: string }[];
  evidence: { business: OIFact[]; expectations: OIFact; market: OIFact };
  assessment: {
    dimensions: { business: Verdict; expectations: Verdict; market: Verdict };
    agreeing: string[];
    disagreeing: string[];
    category: string;
    category_label: string;
    converging: boolean;
    business_improved: string[];
    business_worsened: string[];
    summary: string;
  };
  contradictions: { family: string; kind: 'contradicts' | 'caution'; text: string }[];
}

export interface OIReport {
  version: string;
  session: string;
  knowledge_cutoff: string;
  provisional: boolean;
  window_days: number;
  counts: { company_quarters: number; converging: number; by_category: Record<string, number>; expectations_verified: number; market_unreliable: number };
  category_labels: Record<string, string>;
  caveats: string[];
  view: View;
  matching: number;
  pit_exclusions: Record<string, number>;
  returned: number;
  briefs: OIBrief[];
}

export const opportunityApi = {
  async briefs(params: { session?: string; days?: number; view?: View; limit?: number } = {}): Promise<OIReport> {
    const r = await apiClient.get<OIReport>('/api/opportunity/briefs', { params });
    return r.data;
  },
};
