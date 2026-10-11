// File: frontend/src/services/labApi.ts
// Read-only client for the Quant Lab research reports (GET /api/lab/reports). Everything returned is research evidence with its provenance and caveats.
import { apiClient } from '@/services/api';

export interface LabReportMeta {
  id: string;
  kind: 'strategy_lab' | 'ml' | 'catalyst';
  title: string;
  headline: string;
  generated_at: string;
  code_ref: string;
  status: 'research_only';
}

export interface LabReportList {
  available: boolean;
  notice: string;
  reports: LabReportMeta[];
  invalid: { id: string; problems: string[] }[];
}

export interface LabReport extends LabReportMeta {
  schema: string;
  use: string;
  caveats: string[];
  provenance: { source: string; evidence_files?: string[]; [k: string]: unknown };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>;
}

export const labApi = {
  list: async (): Promise<LabReportList> => (await apiClient.get<LabReportList>('/api/lab/reports')).data,
  get: async (id: string): Promise<LabReport> => (await apiClient.get<LabReport>(`/api/lab/reports/${encodeURIComponent(id)}`)).data,
};
