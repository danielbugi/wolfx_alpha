// File: frontend/src/contexts/AuthContext.tsx
'use client';

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi, AuthUser, UserRole } from '@/services/authApi';
import { refreshAccessToken, setAccessToken, setOnSessionExpired, setRefreshToken as setApiRefreshToken } from '@/services/api';

export type { UserRole };

const REFRESH_TOKEN_KEY = 'refresh_token';

type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  /** Step 1: password. Resolves with a challenge_token to pass to verifyCode. Throws on bad credentials. */
  login: (email: string, password: string) => Promise<{ challengeToken: string; expiresInMinutes: number }>;
  /** Step 2: the emailed code. On success, the session is established. Throws on a bad/expired code. */
  verifyCode: (challengeToken: string, code: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Lightweight auth state, matching this app's existing style (no state library — see useControlToken.ts).
 * The access token lives only in memory (services/api.ts's module state); the refresh token persists in
 * localStorage so a page reload/restart can silently re-authenticate instead of forcing a fresh login.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [user, setUser] = useState<AuthUser | null>(null);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    setApiRefreshToken(null);
    try {
      window.localStorage.removeItem(REFRESH_TOKEN_KEY);
    } catch {
      /* storage blocked: nothing persisted to clear */
    }
    setUser(null);
    setStatus('unauthenticated');
  }, []);

  // On mount: try to silently resume a session from the stored refresh token.
  useEffect(() => {
    setOnSessionExpired(clearSession);

    let storedRefreshToken: string | null = null;
    try {
      storedRefreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY);
    } catch {
      storedRefreshToken = null;
    }

    if (!storedRefreshToken) {
      setStatus('unauthenticated');
      return;
    }

    setApiRefreshToken(storedRefreshToken);
    (async () => {
      const newAccessToken = await refreshAccessToken();
      if (!newAccessToken) {
        clearSession();
        return;
      }
      try {
        const me = await authApi.me();
        setUser(me);
        setStatus('authenticated');
      } catch {
        clearSession();
      }
    })();

    return () => setOnSessionExpired(null);
  }, [clearSession]);

  const login = useCallback(async (email: string, password: string) => {
    const challenge = await authApi.login(email, password);
    return { challengeToken: challenge.challenge_token, expiresInMinutes: challenge.expires_in_minutes };
  }, []);

  const verifyCode = useCallback(async (challengeToken: string, code: string) => {
    const result = await authApi.verifyCode(challengeToken, code);
    setAccessToken(result.access_token);
    setApiRefreshToken(result.refresh_token);
    try {
      window.localStorage.setItem(REFRESH_TOKEN_KEY, result.refresh_token);
    } catch {
      /* storage blocked: the session still works for this page view, just won't survive a reload */
    }
    setUser(result.user);
    setStatus('authenticated');
  }, []);

  const logout = useCallback(async () => {
    let storedRefreshToken: string | null = null;
    try {
      storedRefreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY);
    } catch {
      storedRefreshToken = null;
    }
    if (storedRefreshToken) {
      try {
        await authApi.logout(storedRefreshToken);
      } catch {
        /* best-effort: the client-side session is cleared regardless */
      }
    }
    clearSession();
  }, [clearSession]);

  const value = useMemo<AuthContextValue>(() => ({ status, user, login, verifyCode, logout }), [status, user, login, verifyCode, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside an AuthProvider');
  return ctx;
}
