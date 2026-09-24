// File: frontend/src/hooks/useSidebarCollapsed.ts
'use client';

import { useCallback, useEffect, useState } from 'react';

const KEY = 'sidebar_collapsed';

/**
 * Whether the desktop sidebar is collapsed to an icon rail — a per-browser convenience, kept in localStorage.
 * Server and first client render both use the default (expanded) so hydration never mismatches; a post-mount
 * effect applies the saved value, which can cause one harmless reflow on load. Storage can be blocked (private
 * windows): every access is guarded and the sidebar still works, just without memory, if it is.
 */
export function useSidebarCollapsed() {
  const [collapsed, setCollapsedState] = useState(false);

  useEffect(() => {
    try {
      setCollapsedState(window.localStorage.getItem(KEY) === '1');
    } catch {
      /* no memory available: stay expanded */
    }
  }, []);

  const setCollapsed = useCallback((value: boolean) => {
    setCollapsedState(value);
    try {
      window.localStorage.setItem(KEY, value ? '1' : '0');
    } catch {
      /* not persisted this time; the toggle still works for the current view */
    }
  }, []);

  return { collapsed, setCollapsed };
}
