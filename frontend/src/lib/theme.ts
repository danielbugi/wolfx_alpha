'use client';

import { useCallback, useEffect, useState } from 'react';

export type ThemePreference = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

import { DARK_QUERY, THEME_STORAGE_KEY as KEY } from '@/lib/themeScript';

function resolve(pref: ThemePreference): ResolvedTheme {
  if (pref === 'system') return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
  return pref;
}

function apply(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
}

/** The viewer's theme choice (per browser, a convenience — storage may be unavailable, and then it just isn't kept). */
export function useThemePreference() {
  const [preference, setPreferenceState] = useState<ThemePreference>('light');

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(KEY);
      if (stored === 'light' || stored === 'dark' || stored === 'system') setPreferenceState(stored);
    } catch {
      /* no storage: stay on the default */
    }
  }, []);

  // Follow the OS while on "system".
  useEffect(() => {
    if (preference !== 'system') return;
    const media = window.matchMedia(DARK_QUERY);
    const onChange = () => apply(resolve('system'));
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, [preference]);

  const setPreference = useCallback((pref: ThemePreference) => {
    setPreferenceState(pref);
    // Ease the switch (globals.css .theme-transition) rather than flashing the whole screen at once.
    const root = document.documentElement;
    root.classList.add('theme-transition');
    window.setTimeout(() => root.classList.remove('theme-transition'), 300);
    apply(resolve(pref));
    try {
      window.localStorage.setItem(KEY, pref);
    } catch {
      /* applied for this view, just not remembered */
    }
  }, []);

  return { preference, setPreference };
}

/** The theme actually on screen, kept in sync with <html data-theme> (for code that must pick colors itself, e.g. canvas charts). */
export function useResolvedTheme(): ResolvedTheme {
  const [theme, setTheme] = useState<ResolvedTheme>('light');
  useEffect(() => {
    const root = document.documentElement;
    const read = () => setTheme(root.dataset.theme === 'dark' ? 'dark' : 'light');
    read();
    const observer = new MutationObserver(read);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return theme;
}
