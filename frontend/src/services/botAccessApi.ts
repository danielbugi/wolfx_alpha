// File: frontend/src/services/botAccessApi.ts
/**
 * Bot Access Control API (backend/routers/bot_access.py): who may use the First Light private assistant.
 * Owner-only — every call needs a signed-in Owner session (the shared apiClient's bearer token); no separate
 * control token like the channel-post endpoints in telegramApi.ts, since this never touches a public channel.
 */

import { apiClient } from '@/services/api';
import { toControlError } from '@/services/telegramApi';
import type { ControlErrorInfo } from '@/services/telegramApi';

export { toControlError };
export type { ControlErrorInfo };

export interface PendingRequest {
  telegram_user_id: number;
  requested_at: string;
}

export interface FunnelMetrics {
  opened_channel: number;
  requested: number;
  approved: number;
  finished_guide: number;
  activated: number;
  active_7d: number;
  waiting: number;
  declined_cooling: number;
}

export type AccessMode = 'approve' | 'auto' | 'closed';

export interface AccessOverview {
  mode: AccessMode;
  owner_configured: boolean;
  max_members: number;
  max_pending: number;
  active: number;
  revoked: number;
  open_invites: number;
  pending_count: number;
  pending: PendingRequest[];
  funnel_30d: FunnelMetrics;
}

export interface DecisionResult {
  /** approve: 'approved' | 'owner' (that id is the owner's own) | 'none' (no waiting request left, refused with 409/400 by the API).
   *  decline: 'declined' | 'none'. */
  result: string;
  notified: boolean;
}

export interface InviteResult {
  payload: string;
  /** null only if the bot's username could not be fetched from Telegram; the raw payload still works as `/redeem <payload>`-style input. */
  link: string | null;
}

export const botAccessApi = {
  getOverview: async (): Promise<AccessOverview> => (await apiClient.get<AccessOverview>('/api/bot-access/overview')).data,

  approve: async (uid: number): Promise<DecisionResult> =>
    (await apiClient.post<DecisionResult>(`/api/bot-access/requests/${uid}/approve`)).data,

  decline: async (uid: number): Promise<DecisionResult> =>
    (await apiClient.post<DecisionResult>(`/api/bot-access/requests/${uid}/decline`)).data,

  revoke: async (uid: number): Promise<{ message: string }> =>
    (await apiClient.post<{ message: string }>(`/api/bot-access/members/${uid}/revoke`)).data,

  createInvite: async (note: string | null): Promise<InviteResult> =>
    (await apiClient.post<InviteResult>('/api/bot-access/invite', { note })).data,
};
