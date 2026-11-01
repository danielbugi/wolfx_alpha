// File: frontend/src/services/dataQualityApi.ts
// Read-only client for the Quant Lab data-quality summary (GET /api/lab/data-quality): SEC coverage, SEC-vs-yfinance reconciliation, and which event families are point-in-time.
import { apiClient } from '@/services/api';

export interface DataQualityPayload {
  schema: string;
  available: boolean;
  reason?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  [k: string]: any;
}

export const dataQualityApi = {
  get: async (): Promise<DataQualityPayload> => (await apiClient.get<DataQualityPayload>('/api/lab/data-quality')).data,
};
