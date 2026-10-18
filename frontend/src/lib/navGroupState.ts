// File: frontend/src/lib/navGroupState.ts
'use client';

import { useCallback, useEffect, useState } from 'react';
import { isActiveRoute, type NavGroup } from '@/lib/navigation';

const KEY = 'nav_internal_open';

/** Open/closed state of the collapsible Internal nav group, remembered in this browser (a per-viewer convenience:
 * storage may be unavailable, then it simply starts closed). It is forced open while the current page is one of its
 * items, so the active page is never hidden. Non-internal groups are always open. */
export function useNavGroupOpen(group: NavGroup, pathname: string): { open: boolean; toggle: () => void; collapsible: boolean } {
  const collapsible = !!group.internal;
  const [stored, setStored] = useState(false);
  useEffect(() => {
    if (!collapsible) return;
    try {
      setStored(window.localStorage.getItem(KEY) === '1');
    } catch {
      /* no storage: closed */
    }
  }, [collapsible]);
  const containsActive = group.items.some((item) => isActiveRoute(pathname, item.href));
  const toggle = useCallback(() => {
    setStored((v) => {
      const next = !v;
      try {
        window.localStorage.setItem(KEY, next ? '1' : '0');
      } catch {
        /* this view only */
      }
      return next;
    });
  }, []);
  return { open: !collapsible || stored || containsActive, toggle, collapsible };
}
