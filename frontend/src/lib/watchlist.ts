'use client';

import { useCallback, useEffect, useState } from 'react';

/**
 * Personal watchlists kept in this browser (localStorage) — a per-viewer convenience until synced, server-side
 * watchlists exist. Every hook instance stays in sync: changes in this tab broadcast an event, changes in other tabs
 * arrive through the `storage` event.
 *
 * Storage (backward compatible, nothing is migrated):
 * - `watchlist_symbols`: the MAIN list, exactly the format of the first version (a JSON array of symbols, newest first).
 *   An older build, or a rollback, keeps reading and writing it unchanged.
 * - `watchlist_lists`: the additional lists, `[{ id, name, symbols }]`; `watchlist_main_name`: the main list's name.
 * - `watchlist_active`: the id of the active list ("main" when absent or unknown).
 * Stars on the Screener and stock pages, the dashboard card and the Earnings filter all use the ACTIVE list.
 *
 * Reading is tolerant and never destructive: entries are trimmed and upper-cased, duplicates and invalid entries are
 * skipped, and nothing is written back until the user changes something. Each list holds at most WATCHLIST_MAX symbols:
 * a further add is refused (never by evicting an existing symbol) with a reason the caller can show. At most MAX_LISTS
 * lists. A failed API request never touches any list.
 */

const KEY = 'watchlist_symbols';
const LISTS_KEY = 'watchlist_lists';
const MAIN_NAME_KEY = 'watchlist_main_name';
const ACTIVE_KEY = 'watchlist_active';
const ALL_KEYS = [KEY, LISTS_KEY, MAIN_NAME_KEY, ACTIVE_KEY];
const EVENT = 'watchlist-change';
export const WATCHLIST_MAX = 30;
export const MAX_LISTS = 10;
export const MAIN_LIST_ID = 'main';
export const DEFAULT_MAIN_NAME = 'My watchlist';
const SYMBOL_RE = /^[A-Z0-9][A-Z0-9.\-]{0,9}$/;

/** A valid symbol in its canonical (upper-case) form, or null. */
export function normalizeSymbol(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const s = raw.trim().toUpperCase();
  return SYMBOL_RE.test(s) ? s : null;
}

/** Parse a stored symbol array: valid, unique symbols in their saved order. Malformed input yields what can be kept. */
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

export interface WatchlistList {
  id: string;
  name: string;
  symbols: string[];
}

/** A list name: trimmed, 1-30 characters, or null. */
export function normalizeListName(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const s = raw.trim().replace(/\s+/g, ' ');
  return s.length >= 1 && s.length <= 30 ? s : null;
}

/** Parse the additional lists: valid entries only (unique ids, valid names, cleaned symbols), at most MAX_LISTS - 1. */
export function parseLists(raw: string | null): WatchlistList[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const out: WatchlistList[] = [];
  for (const e of parsed) {
    if (!e || typeof e !== 'object') continue;
    const { id, name, symbols } = e as Record<string, unknown>;
    const n = normalizeListName(name);
    if (typeof id !== 'string' || !/^[a-z0-9-]{1,40}$/.test(id) || id === MAIN_LIST_ID || !n || out.some((l) => l.id === id)) continue;
    out.push({ id, name: n, symbols: parseStored(JSON.stringify(Array.isArray(symbols) ? symbols : [])) });
    if (out.length >= MAX_LISTS - 1) break;
  }
  return out;
}

const get = (k: string): string | null => {
  try {
    return window.localStorage.getItem(k);
  } catch {
    return null;
  }
};

interface State {
  lists: WatchlistList[];
  activeId: string;
}

function readState(): State {
  const main: WatchlistList = { id: MAIN_LIST_ID, name: normalizeListName(get(MAIN_NAME_KEY)) ?? DEFAULT_MAIN_NAME, symbols: parseStored(get(KEY)) };
  const lists = [main, ...parseLists(get(LISTS_KEY))];
  const want = get(ACTIVE_KEY);
  return { lists, activeId: lists.some((l) => l.id === want) ? (want as string) : MAIN_LIST_ID };
}

/** Write one or more keys; false when the browser refuses (nothing is pretended). Always notifies other instances. */
function writeKeys(entries: [string, string][]): boolean {
  let saved = true;
  try {
    for (const [k, v] of entries) window.localStorage.setItem(k, v);
  } catch {
    saved = false;
  }
  window.dispatchEvent(new Event(EVENT));
  return saved;
}

function writeSymbols(state: State, listId: string, symbols: string[]): boolean {
  if (listId === MAIN_LIST_ID) return writeKeys([[KEY, JSON.stringify(symbols)]]);
  const extra = state.lists.filter((l) => l.id !== MAIN_LIST_ID).map((l) => (l.id === listId ? { ...l, symbols } : l));
  return writeKeys([[LISTS_KEY, JSON.stringify(extra)]]);
}

export type AddResult = 'added' | 'already' | 'full' | 'invalid' | 'unsaved';
export type ToggleResult = AddResult | 'removed';
export type ListResult = 'ok' | 'invalid_name' | 'too_many' | 'last_list' | 'unsaved';

/** Pure add rule, shared by the hook and the tests: newest first, no duplicates, refuse when full. */
export function addTo(current: string[], raw: string): { next: string[]; result: Exclude<AddResult, 'unsaved'> } {
  const s = normalizeSymbol(raw);
  if (!s) return { next: current, result: 'invalid' };
  if (current.includes(s)) return { next: current, result: 'already' };
  if (current.length >= WATCHLIST_MAX) return { next: current, result: 'full' };
  return { next: [s, ...current], result: 'added' };
}

/** A short user-facing message for an outcome worth telling the user about (null for the ordinary ones). */
export function watchlistMessage(result: ToggleResult, symbol: string, listName?: string): string | null {
  switch (result) {
    case 'full':
      return `${listName ? `"${listName}"` : 'Your watchlist'} is full (${WATCHLIST_MAX} stocks). Remove one to add ${symbol.toUpperCase()}.`;
    case 'invalid':
      return `${symbol} is not a valid symbol.`;
    case 'unsaved':
      return 'This browser is not allowing the watchlist to be saved (storage is blocked or full).';
    default:
      return null;
  }
}

const newId = () => `l-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;

export function useWatchlist() {
  const [state, setState] = useState<State>({ lists: [{ id: MAIN_LIST_ID, name: DEFAULT_MAIN_NAME, symbols: [] }], activeId: MAIN_LIST_ID });
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const sync = () => setState(readState());
    sync();
    const onStorage = (e: StorageEvent) => (e.key === null || ALL_KEYS.includes(e.key)) && sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const active = state.lists.find((l) => l.id === state.activeId) ?? state.lists[0];
  const symbols = active.symbols;

  const add = useCallback((symbol: string): AddResult => {
    const st = readState();
    const list = st.lists.find((l) => l.id === st.activeId) ?? st.lists[0];
    const { next, result } = addTo(list.symbols, symbol);
    const final: AddResult = result === 'added' && !writeSymbols(st, list.id, next) ? 'unsaved' : result;
    setNotice(watchlistMessage(final, symbol, st.lists.length > 1 ? list.name : undefined));
    return final;
  }, []);

  const remove = useCallback((symbol: string) => {
    const s = normalizeSymbol(symbol) ?? symbol;
    const st = readState();
    const list = st.lists.find((l) => l.id === st.activeId) ?? st.lists[0];
    if (list.symbols.includes(s)) writeSymbols(st, list.id, list.symbols.filter((x) => x !== s));
    setNotice(null);
  }, []);

  const toggle = useCallback(
    (symbol: string): ToggleResult => {
      const s = normalizeSymbol(symbol);
      const st = readState();
      const list = st.lists.find((l) => l.id === st.activeId) ?? st.lists[0];
      if (s && list.symbols.includes(s)) {
        remove(s);
        return 'removed';
      }
      return add(symbol);
    },
    [add, remove],
  );

  const setActive = useCallback((id: string) => {
    if (readState().lists.some((l) => l.id === id)) writeKeys([[ACTIVE_KEY, id]]);
    setNotice(null);
  }, []);

  const createList = useCallback((name: string): ListResult => {
    const n = normalizeListName(name);
    if (!n) return 'invalid_name';
    const st = readState();
    if (st.lists.length >= MAX_LISTS) return 'too_many';
    const id = newId();
    const extra = [...st.lists.filter((l) => l.id !== MAIN_LIST_ID), { id, name: n, symbols: [] }];
    return writeKeys([[LISTS_KEY, JSON.stringify(extra)], [ACTIVE_KEY, id]]) ? 'ok' : 'unsaved';
  }, []);

  const renameList = useCallback((id: string, name: string): ListResult => {
    const n = normalizeListName(name);
    if (!n) return 'invalid_name';
    const st = readState();
    if (id === MAIN_LIST_ID) return writeKeys([[MAIN_NAME_KEY, n]]) ? 'ok' : 'unsaved';
    const extra = st.lists.filter((l) => l.id !== MAIN_LIST_ID).map((l) => (l.id === id ? { ...l, name: n } : l));
    return writeKeys([[LISTS_KEY, JSON.stringify(extra)]]) ? 'ok' : 'unsaved';
  }, []);

  /** Delete a list (its symbols go with it). The main list cannot be deleted; empty it instead. */
  const deleteList = useCallback((id: string): ListResult => {
    if (id === MAIN_LIST_ID) return 'last_list';
    const st = readState();
    const extra = st.lists.filter((l) => l.id !== MAIN_LIST_ID && l.id !== id);
    const entries: [string, string][] = [[LISTS_KEY, JSON.stringify(extra)]];
    if (st.activeId === id) entries.push([ACTIVE_KEY, MAIN_LIST_ID]);
    return writeKeys(entries) ? 'ok' : 'unsaved';
  }, []);

  return {
    symbols,
    has: (s: string) => symbols.includes(normalizeSymbol(s) ?? s),
    full: symbols.length >= WATCHLIST_MAX,
    max: WATCHLIST_MAX,
    lists: state.lists.map((l) => ({ id: l.id, name: l.name, count: l.symbols.length })),
    activeId: active.id,
    activeName: active.name,
    setActive,
    createList,
    renameList,
    deleteList,
    add,
    remove,
    toggle,
    notice,
    clearNotice: () => setNotice(null),
  };
}
