// File: frontend/src/services/researchMissionsApi.ts
// Owner-only client for Research Missions (backend/routers/research_missions.py). Creating a mission makes a DRAFT; running one is a manual
// CLI step in the MVP, so nothing here can spend money.
import { apiClient } from '@/services/api';

export type MissionStatus = 'draft' | 'queued' | 'running' | 'completed' | 'failed' | 'stopped_budget';

export interface MissionSummary {
  id: number;
  kind: 'company' | 'thematic';
  title: string;
  domain: string;
  status: MissionStatus;
  spent_usd: number;
  budget_usd: number;
  created_at: string;
  updated_at: string;
}

export interface DomainInfo {
  key: string;
  label: string;
  thematic: boolean;
  sources: string[];
}

export interface Claim {
  label: string;
  kind: 'fact' | 'inference' | 'forecast';
  text: string;
  confidence: 'low' | 'medium' | 'high';
  status: 'unverified' | 'verified' | 'partial' | 'unsupported';
  verifier_note: string | null;
  evidence: string[];
}

export interface Evidence {
  label: string;
  source: string;
  title: string | null;
  publisher: string | null;
  url: string;
  published_at: string | null;
  license: 'public' | 'internal_only';
  quote: string;
}

export interface MissionCompany {
  symbol: string;
  name: string | null;
  in_universe: boolean;
  role: string;
  exposure: string;
  claim_labels: string[];
  risk_labels: string[];
}

export interface MissionDetail {
  mission: MissionSummary & { question: string; params: Record<string, unknown>; error: string | null };
  steps: { step: string; status: string; detail: Record<string, unknown>; started_at: string; finished_at: string | null }[];
  evidence: Evidence[];
  claims: Claim[];
  companies: MissionCompany[];
  relations: { from_entity: string; to_entity: string; relation: string; claim_labels: string[] }[];
  report_markdown: string | null;
  report_version: number | null;
  usage: { calls: number; refused: number; cost_usd: number };
}

export interface ResearchBudget {
  month: string;
  cap_usd: number;
  mission_cap_usd: number;
  spent_usd: number;
  reserved_usd: number;
  remaining_usd: number;
  paid_calls_enabled: boolean;
}

export interface DraftMission {
  kind: 'company' | 'thematic';
  title: string;
  question: string;
  domain: string;
  params: Record<string, unknown>;
  budget_usd?: number;
}

export const researchMissionsApi = {
  list: async (): Promise<{ missions: MissionSummary[]; domains: DomainInfo[] }> => (await apiClient.get('/api/research/missions')).data,
  get: async (id: number): Promise<MissionDetail> => (await apiClient.get(`/api/research/missions/${id}`)).data,
  budget: async (): Promise<ResearchBudget> => (await apiClient.get('/api/research/budget')).data,
  createDraft: async (body: DraftMission): Promise<{ id: number; status: string; budget_usd: number }> => (await apiClient.post('/api/research/missions', body)).data,
};
