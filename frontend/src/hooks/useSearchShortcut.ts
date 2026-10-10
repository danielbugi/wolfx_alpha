'use client';

import { useEffect } from 'react';

const DESKTOP_QUERY = '(min-width: 1024px)';

/**
 * "/" or ⌘K / Ctrl+K jumps to symbol search from anywhere. Sidebar (desktop) and MobileNav (< lg) both mount at every
 * width, so each passes the layout it owns and only the visible one responds. "/" is ignored while typing in a field.
 */
export function useSearchShortcut(onTrigger: () => void, layout: 'desktop' | 'mobile') {
  useEffect(() => {
    function handle(e: KeyboardEvent) {
      const isDesktop = window.matchMedia(DESKTOP_QUERY).matches;
      if ((layout === 'desktop') !== isDesktop) return;
      const target = e.target as HTMLElement | null;
      const typing = !!target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName));
      const commandK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
      const slash = e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !e.altKey;
      if (!commandK && !slash) return;
      e.preventDefault();
      onTrigger();
    }
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [onTrigger, layout]);
}

/** "⌘K" on Apple platforms, "Ctrl K" elsewhere — for the hint shown in the search field. */
export function shortcutLabel(): string {
  if (typeof navigator === 'undefined') return 'Ctrl K';
  return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? '⌘K' : 'Ctrl K';
}
