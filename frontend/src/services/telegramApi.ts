// File: frontend/src/services/telegramApi.ts
/**
 * Telegram Control Center API (backend/routers/telegram_control.py). Uses the shared axios client from api.ts.
 * Reading is open; edit / delete send the control token in `X-Control-Token`. Anything the server does not know is `null`.
 */

import axios from 'axios';
import { apiClient } from '@/services/api';

export type ChannelTarget = 'dev' | 'prod';
/** Filter values: `sent` = still up (edited or not), `edited` = still up and edited at least once, `deleted`. */
export type StatusFilter = 'sent' | 'edited' | 'deleted';

export interface Decision {
  ok: boolean;
  code: string | null;
  reason: string | null;
}

export interface TargetOverview {
  target: ChannelTarget;
  label: string;
  configured: boolean;
  chat_hint: string | null;
  locked: boolean;
  sent_today: number;
  recorded: number;
  live: number;
  edited: number;
  deleted: number;
  last_sent_at: string | null;
}

export interface ConfigItem {
  group: string;
  key: string;
  label: string;
  value: string;
  note: string | null;
}

export interface KindCount {
  kind: string;
  count: number;
  label: string;
}

export interface ControlOverview {
  generated_at: string;
  timezone: string;
  tracking_since: string | null;
  controls_enabled: boolean;
  targets: TargetOverview[];
  config: ConfigItem[];
  kinds: KindCount[];
  warnings: string[];
}

export interface MessageSummary {
  id: number;
  target: ChannelTarget;
  message_id: number;
  kind: string;
  kind_label: string;
  content_type: 'text' | 'photo';
  status: 'sent' | 'deleted';
  preview: string;
  text_length: number;
  silent: boolean;
  pinned: boolean;
  edit_count: number;
  sent_at: string;
  edited_at: string | null;
  deleted_at: string | null;
  deletable_until: string | null;
  can_edit: Decision;
  can_delete: Decision;
  can_pin: Decision;
}

export interface AuditEntry {
  at: string;
  action: 'edit' | 'delete';
  outcome: 'done' | 'unchanged' | 'gone' | 'refused' | 'failed';
  detail: string | null;
}

export interface KeyboardButton {
  text: string | null;
  url: string | null;
}

export interface MessageDetail extends MessageSummary {
  text: string;
  original_text: string;
  disable_preview: boolean;
  buttons: KeyboardButton[][];
  /** Telegram's limit for this message (text 4096, photo caption 1024), in characters after tags are removed. */
  limit: number;
  audit: AuditEntry[];
}

/** A message just composed and sent. `message` is null only in the rare case the send reached Telegram but the ledger write has not landed
 * yet (see channel_control.compose's docstring) — the send itself still succeeded. */
export interface ComposeResult {
  result: string;
  target: ChannelTarget;
  message_id: number;
  message: MessageDetail | null;
}

export type ScheduleStatus = 'done' | 'pending' | 'overdue' | 'skipped';

export interface ScheduleJobRow {
  key: string;
  label: string;
  local_time: string;
  status: ScheduleStatus;
  reason: string;
}

export interface ScheduleReport {
  target: ChannelTarget;
  timezone: string;
  jobs: ScheduleJobRow[];
}

export interface MessageList {
  total: number;
  limit: number;
  offset: number;
  timezone: string;
  items: MessageSummary[];
}

export interface MessageFilters {
  target?: ChannelTarget;
  kind?: string;
  status?: StatusFilter;
  /** YYYY-MM-DD in the configured timezone. */
  day?: string;
  q?: string;
  limit: number;
  offset: number;
}

export interface ActionResult {
  /** Telegram's answer: edited | unchanged | missing (edit) · deleted | missing (delete). */
  result: string;
  message: MessageDetail;
}

/** A refused or failed request, in the shape the page shows. `code` is stable; the UI branches on it. */
export interface ControlErrorInfo {
  code: string;
  message: string;
  warnings: string[];
}

export function toControlError(err: unknown): ControlErrorInfo {
  if (axios.isAxiosError(err)) {
    if (!err.response) {
      return { code: 'network', message: 'Cannot reach the API. Is the backend running?', warnings: [] };
    }
    const detail: unknown = err.response.data?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'code' in detail && 'message' in detail) {
      const d = detail as { code: string; message: string; warnings?: string[] };
      return { code: d.code, message: d.message, warnings: d.warnings ?? [] };
    }
    return { code: `http_${err.response.status}`, message: `The request was not accepted (HTTP ${err.response.status}).`, warnings: [] };
  }
  return { code: 'unknown', message: err instanceof Error ? err.message : 'Something went wrong.', warnings: [] };
}

function cleanParams(filters: MessageFilters): Record<string, string | number> {
  const params: Record<string, string | number> = { limit: filters.limit, offset: filters.offset };
  if (filters.target) params.target = filters.target;
  if (filters.kind) params.kind = filters.kind;
  if (filters.status) params.status = filters.status;
  if (filters.day) params.day = filters.day;
  if (filters.q && filters.q.trim()) params.q = filters.q.trim();
  return params;
}

// The server retries Telegram on 429 / 5xx for several seconds; the shared 30 s default would report a slow success as an unreachable API.
const MUTATION_TIMEOUT_MS = 90_000;
const mutation = (token: string) => ({ headers: { 'X-Control-Token': token }, timeout: MUTATION_TIMEOUT_MS });
const mutationMultipart = (token: string) => ({ headers: { 'X-Control-Token': token }, timeout: MUTATION_TIMEOUT_MS });

export const telegramApi = {
  getOverview: async (): Promise<ControlOverview> => (await apiClient.get<ControlOverview>('/api/telegram/overview')).data,

  getSchedule: async (target: ChannelTarget): Promise<ScheduleReport> =>
    (await apiClient.get<ScheduleReport>('/api/telegram/schedule', { params: { target } })).data,

  listMessages: async (filters: MessageFilters): Promise<MessageList> =>
    (await apiClient.get<MessageList>('/api/telegram/messages', { params: cleanParams(filters) })).data,

  getMessage: async (id: number): Promise<MessageDetail> => (await apiClient.get<MessageDetail>(`/api/telegram/messages/${id}`)).data,

  editMessage: async (id: number, text: string, acknowledgeWording: boolean, token: string): Promise<ActionResult> =>
    (await apiClient.patch<ActionResult>(`/api/telegram/messages/${id}`, { text, acknowledge_wording: acknowledgeWording }, mutation(token))).data,

  deleteMessage: async (id: number, token: string): Promise<ActionResult> =>
    (await apiClient.delete<ActionResult>(`/api/telegram/messages/${id}`, mutation(token))).data,

  pinMessage: async (id: number, token: string): Promise<ActionResult> =>
    (await apiClient.post<ActionResult>(`/api/telegram/messages/${id}/pin`, null, mutation(token))).data,

  unpinMessage: async (id: number, token: string): Promise<ActionResult> =>
    (await apiClient.post<ActionResult>(`/api/telegram/messages/${id}/unpin`, null, mutation(token))).data,

  replacePhoto: async (id: number, photo: File, caption: string, acknowledgeWording: boolean, token: string): Promise<ActionResult> => {
    const form = new FormData();
    form.append('photo', photo);
    form.append('caption', caption);
    form.append('acknowledge_wording', String(acknowledgeWording));
    return (await apiClient.patch<ActionResult>(`/api/telegram/messages/${id}/photo`, form, mutationMultipart(token))).data;
  },

  composeText: async (
    target: ChannelTarget,
    text: string,
    kind: string | null,
    silent: boolean,
    disablePreview: boolean,
    acknowledgeWording: boolean,
    token: string,
  ): Promise<ComposeResult> =>
    (
      await apiClient.post<ComposeResult>(
        '/api/telegram/messages',
        { target, text, kind, silent, disable_preview: disablePreview, acknowledge_wording: acknowledgeWording },
        mutation(token),
      )
    ).data,

  composePhoto: async (
    target: ChannelTarget,
    photo: File,
    caption: string,
    kind: string | null,
    silent: boolean,
    acknowledgeWording: boolean,
    token: string,
  ): Promise<ComposeResult> => {
    const form = new FormData();
    form.append('target', target);
    form.append('photo', photo);
    form.append('caption', caption);
    if (kind) form.append('kind', kind);
    form.append('silent', String(silent));
    form.append('acknowledge_wording', String(acknowledgeWording));
    return (await apiClient.post<ComposeResult>('/api/telegram/messages/photo', form, mutationMultipart(token))).data;
  },
};
