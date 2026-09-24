// File: frontend/src/hooks/useControlToken.ts
'use client';

import { useCallback, useEffect, useState } from 'react';

const KEY = 'telegram_control_token';

/**
 * The Telegram Control Center's token (TELEGRAM_CONTROL_TOKEN in .env), needed for edit and delete. Kept in sessionStorage only: it dies with the
 * browser tab, is never put in a URL, and is never read by anything except the request that changes a post. Storage can be blocked (private
 * windows), so every access is guarded and the page still works read-only without it.
 */
export function useControlToken() {
  const [token, setTokenState] = useState<string | null>(null);

  useEffect(() => {
    try {
      setTokenState(window.sessionStorage.getItem(KEY));
    } catch {
      setTokenState(null);
    }
  }, []);

  const setToken = useCallback((value: string) => {
    const clean = value.trim();
    if (!clean) return;
    try {
      window.sessionStorage.setItem(KEY, clean);
    } catch {
      /* storage blocked: the token lives in memory for this page view only */
    }
    setTokenState(clean);
  }, []);

  const clearToken = useCallback(() => {
    try {
      window.sessionStorage.removeItem(KEY);
    } catch {
      /* nothing to clear */
    }
    setTokenState(null);
  }, []);

  return { token, setToken, clearToken };
}
