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

/** The person's public Telegram name, mirrored by the bot. Both null until migration 34 is applied (or if they have none). */
export interface TelegramName {
  first_name: string | null;
  username: string | null;
}

export interface PendingRequest extends TelegramName {
  telegram_user_id: number;
  requested_at: string;
}

export type MemberStatus = 'active' | 'revoked';

export interface Member extends TelegramName {
  telegram_user_id: number;
  status: MemberStatus;
  approved_at: string | null;
  revoked_at: string | null;
  /** Last time they used the bot; null if they never opened it after being let in. */
  last_seen: string | null;
  finished_guide: boolean;
  tracked: number;
  morning_on: boolean;
  /** invite | auto | request | manual; null for members let in before the audit trail recorded it. */
  joined_via: string | null;
}

export interface AuditEntry {
  at: string | null;
  actor: number;
  action: string;
  target: number | null;
  detail: string | null;
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
  /** true = the mode was set on this dashboard; false = the bot's .env BOT_ACCESS_MODE applies. */
  mode_from_dashboard: boolean;
  /** false until migration 34 is applied: names are not stored yet and the mode/cap cannot be switched here. */
  settings_available: boolean;
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

  getMembers: async (status: MemberStatus): Promise<Member[]> =>
    (await apiClient.get<{ members: Member[] }>('/api/bot-access/members', { params: { status } })).data.members,

  restore: async (uid: number): Promise<DecisionResult> =>
    (await apiClient.post<DecisionResult>(`/api/bot-access/members/${uid}/restore`)).data,

  getAudit: async (limit = 30): Promise<AuditEntry[]> =>
    (await apiClient.get<{ entries: AuditEntry[] }>('/api/bot-access/audit', { params: { limit } })).data.entries,

  updateSettings: async (body: { mode?: AccessMode; max_members?: number }): Promise<{ mode: AccessMode; max_members: number }> =>
    (await apiClient.put<{ mode: AccessMode; max_members: number }>('/api/bot-access/settings', body)).data,
};

/** "Dana (@dana)", "@dana", "Dana", or null when the bot has no name for them. */
export function displayName(p: TelegramName): string | null {
  if (p.first_name && p.username) return `${p.first_name} (@${p.username})`;
  if (p.username) return `@${p.username}`;
  return p.first_name;
}
