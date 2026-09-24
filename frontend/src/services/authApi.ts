// File: frontend/src/services/authApi.ts
/**
 * Thin wrapper for /api/auth/* — login (password + emailed 2FA code), refresh, logout, and the Owner-only
 * user/session management used by /users. See backend/routers/auth.py for the matching endpoints.
 */
import axios from 'axios';
import { apiClient } from './api';

export type UserRole = 'owner' | 'collaborator';

export interface AuthUser {
  id: number;
  email: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface LoginChallenge {
  stage: string;
  challenge_token: string;
  expires_in_minutes: number;
}

export interface LoggedIn {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

export interface SessionInfo {
  id: number;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  user_agent: string | null;
  last_used_at: string | null;
}

export interface AuthErrorInfo {
  code: string;
  message: string;
}

/** Same normalization telegramApi.ts's toControlError uses: the backend's structured {code, message} detail, or a generic fallback. */
export function toAuthError(err: unknown): AuthErrorInfo {
  if (axios.isAxiosError(err)) {
    if (!err.response) {
      return { code: 'network', message: 'Cannot reach the API. Is the backend running?' };
    }
    const detail: unknown = err.response.data?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'code' in detail && 'message' in detail) {
      const d = detail as { code: string; message: string };
      return { code: d.code, message: d.message };
    }
    return { code: `http_${err.response.status}`, message: `The request was not accepted (HTTP ${err.response.status}).` };
  }
  return { code: 'unknown', message: err instanceof Error ? err.message : 'Something went wrong.' };
}

export const authApi = {
  login: async (email: string, password: string): Promise<LoginChallenge> => {
    const response = await apiClient.post<LoginChallenge>('/api/auth/login', { email, password });
    return response.data;
  },

  verifyCode: async (challengeToken: string, code: string): Promise<LoggedIn> => {
    const response = await apiClient.post<LoggedIn>('/api/auth/verify-2fa', { challenge_token: challengeToken, code });
    return response.data;
  },

  refresh: async (refreshToken: string): Promise<{ access_token: string }> => {
    const response = await apiClient.post<{ access_token: string }>('/api/auth/refresh', { refresh_token: refreshToken });
    return response.data;
  },

  logout: async (refreshToken: string): Promise<void> => {
    await apiClient.post('/api/auth/logout', { refresh_token: refreshToken });
  },

  me: async (): Promise<AuthUser> => {
    const response = await apiClient.get<AuthUser>('/api/auth/me');
    return response.data;
  },

  // Owner-only

  listUsers: async (): Promise<AuthUser[]> => {
    const response = await apiClient.get<{ users: AuthUser[] }>('/api/auth/users');
    return response.data.users;
  },

  createUser: async (email: string, password: string, role: UserRole): Promise<AuthUser> => {
    const response = await apiClient.post<AuthUser>('/api/auth/users', { email, password, role });
    return response.data;
  },

  deactivateUser: async (userId: number): Promise<AuthUser> => {
    const response = await apiClient.patch<AuthUser>(`/api/auth/users/${userId}/deactivate`);
    return response.data;
  },

  reactivateUser: async (userId: number): Promise<AuthUser> => {
    const response = await apiClient.patch<AuthUser>(`/api/auth/users/${userId}/reactivate`);
    return response.data;
  },

  listUserSessions: async (userId: number): Promise<SessionInfo[]> => {
    const response = await apiClient.get<{ sessions: SessionInfo[] }>(`/api/auth/users/${userId}/sessions`);
    return response.data.sessions;
  },

  revokeUserSession: async (userId: number, sessionId: number): Promise<void> => {
    await apiClient.delete(`/api/auth/users/${userId}/sessions/${sessionId}`);
  },
};
