'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * A personal watchlist kept in this browser (localStorage) — a per-viewer convenience until a synced, server-side
 * watchlist exists. Every hook instance stays in sync: changes in this tab broadcast an event, changes in other tabs
 * arrive through the `storage` event.
 *
 * Storage format (unchanged since the first version): a JSON array of symbol strings, newest first, under
 * `watchlist_symbols`. Reading is tolerant and never destructive: entries are trimmed and upper-cased, duplicates and
 * invalid entries are skipped, and nothing is written back until the user adds or removes a symbol. The list holds at
 * most WATCHLIST_MAX symbols: a further add is refused (never by evicting an existing symbol) and the caller gets a
 * reason it can show. A failed API request never touches the list.
 */

const KEY = 'watchlist_symbols';
const EVENT = 'watchlist-change';
export const WATCHLIST_MAX = 30;
const SYMBOL_RE = /^[A-Z0-9][A-Z0-9.\-]{0,9}$/;

/** A valid symbol in its canonical (upper-case) form, or null. */
export function normalizeSymbol(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const s = raw.trim().toUpperCase();
  return SYMBOL_RE.test(s) ? s : null;
}

/** Parse the stored value: valid, unique symbols in their saved order. Malformed input yields what can be kept. */
export function parseStored(raw: string | null): string[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const out: string[] = [];
  for (const entry of parsed) {
    const s = normalizeSymbol(entry);
    if (s && !out.includes(s)) out.push(s);
  }
  return out;
}

function read(): string[] {
  try {
    return parseStored(window.localStorage.getItem(KEY));
  } catch {
    return [];
  }
}

function write(symbols: string[]): boolean {
  let saved = true;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(symbols));
  } catch {
    saved = false;
  }
  window.dispatchEvent(new Event(EVENT));
  return saved;
}

export type AddResult = 'added' | 'already' | 'full' | 'invalid' | 'unsaved';
export type ToggleResult = AddResult | 'removed';

/** Pure add rule, shared by the hook and the tests: newest first, no duplicates, refuse when full. */
export function addTo(current: string[], raw: string): { next: string[]; result: Exclude<AddResult, 'unsaved'> } {
  const s = normalizeSymbol(raw);
  if (!s) return { next: current, result: 'invalid' };
  if (current.includes(s)) return { next: current, result: 'already' };
  if (current.length >= WATCHLIST_MAX) return { next: current, result: 'full' };
  return { next: [s, ...current], result: 'added' };
}

/** A short user-facing message for an outcome worth telling the user about (null for the ordinary ones). */
export function watchlistMessage(result: ToggleResult, symbol: string): string | null {
  switch (result) {
    case 'full':
      return `Your watchlist is full (${WATCHLIST_MAX} stocks). Remove one to add ${symbol.toUpperCase()}.`;
    case 'invalid':
      return `${symbol} is not a valid symbol.`;
    case 'unsaved':
      return 'This browser is not allowing the watchlist to be saved (storage is blocked or full).';
    default:
      return null;
  }
}

export function useWatchlist() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const sync = () => setSymbols(read());
    sync();
    const onStorage = (e: StorageEvent) => (e.key === KEY || e.key === null) && sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const add = useCallback((symbol: string): AddResult => {
    const { next, result } = addTo(read(), symbol);
    const final: AddResult = result === 'added' && !write(next) ? 'unsaved' : result;
    setNotice(watchlistMessage(final, symbol));
    return final;
  }, []);

  const remove = useCallback((symbol: string) => {
    const s = normalizeSymbol(symbol) ?? symbol;
    const current = read();
    if (current.includes(s)) write(current.filter((x) => x !== s));
    setNotice(null);
  }, []);

  const toggle = useCallback(
    (symbol: string): ToggleResult => {
      const s = normalizeSymbol(symbol);
      if (s && read().includes(s)) {
        remove(s);
        return 'removed';
      }
      return add(symbol);
    },
    [add, remove],
  );

  return {
    symbols,
    has: (s: string) => symbols.includes(normalizeSymbol(s) ?? s),
    full: symbols.length >= WATCHLIST_MAX,
    max: WATCHLIST_MAX,
    add,
    remove,
    toggle,
    notice,
    clearNotice: () => setNotice(null),
  };
}
