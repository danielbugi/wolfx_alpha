// File: frontend/src/services/forwardTrackerApi.ts
// Read-only client for the forward paper tracker summary (GET /api/lab/forward-tracker). Paper observation only: aggregates of stored forward records, never historical replay results.
import { apiClient } from '@/services/api';

export interface ForwardTrackerPayload {
  schema: string;
  source: 'forward';
  available: boolean;
  reason?: string;
  sessions_processed: number;
  since?: string;
  banner?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
}

export const forwardTrackerApi = {
  get: async (): Promise<ForwardTrackerPayload> => (await apiClient.get<ForwardTrackerPayload>('/api/lab/forward-tracker')).data,
};
