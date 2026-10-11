'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * A personal watchlist kept in this browser (localStorage) — a per-viewer convenience until a synced, server-side
 * watchlist exists. Every hook instance stays in sync: changes in this tab broadcast an event, changes in other tabs
 * arrive through the `storage` event. If storage is unavailable the list is simply empty and starring does nothing
 * persistent.
 */

const KEY = 'watchlist_symbols';
const EVENT = 'watchlist-change';
export const WATCHLIST_MAX = 30;

function read(): string[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((s): s is string => typeof s === 'string') : [];
  } catch {
    return [];
  }
}

function write(symbols: string[]) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(symbols));
  } catch {
    /* not persisted */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function useWatchlist() {
  const [symbols, setSymbols] = useState<string[]>([]);

  useEffect(() => {
    const sync = () => setSymbols(read());
    sync();
    const onStorage = (e: StorageEvent) => e.key === KEY && sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const toggle = useCallback((symbol: string) => {
    const current = read();
    const next = current.includes(symbol)
      ? current.filter((s) => s !== symbol)
      : [symbol, ...current].slice(0, WATCHLIST_MAX); // newest first
    write(next);
  }, []);

  const remove = useCallback((symbol: string) => write(read().filter((s) => s !== symbol)), []);

  return { symbols, has: (s: string) => symbols.includes(s), toggle, remove };
}
