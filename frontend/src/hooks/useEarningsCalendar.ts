'use client';

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { apiClient, momentumBoardApi, stockApi, type EarningsDateEntry, type MomentumBoardResult } from '@/services/api';
import type { MarketCalendar } from '@/lib/marketSession';
import { nyDateOf, sessionAnchors, type HistoryState, type SessionAnchors } from '@/lib/earningsCalendar';
import { useWatchlist } from '@/lib/watchlist';

// ---------------------------------------------------------------------------------------------------------------
// Per-symbol earnings history, shared by every consumer on the page. The endpoint behind it looks each symbol up live
// (yfinance, cached server-side for an hour), so requests are de-duplicated, capped at MAX_CONCURRENT in flight and
// kept for HISTORY_TTL_MS -- the dashboard card and the calendar page never fan out more than the rows on screen.

const MAX_CONCURRENT = 4;
const HISTORY_TTL_MS = 30 * 60 * 1000;

const store = new Map<string, { data: EarningsDateEntry[] | 'error'; ts: number }>();
const pending = new Set<string>();
const queue: string[] = [];
const listeners = new Set<() => void>();
let active = 0;
let snapshot: Record<string, HistoryState> = {};

function publish() {
  snapshot = Object.fromEntries([...store].map(([s, v]) => [s, v.data]));
  listeners.forEach((l) => l());
}

function pump() {
  while (active < MAX_CONCURRENT && queue.length > 0) {
    const symbol = queue.shift() as string;
    active++;
    stockApi
      .getEarnings(symbol, 12)
      .then((r) => store.set(symbol, { data: r.earnings_dates, ts: Date.now() }))
      .catch(() => store.set(symbol, { data: 'error', ts: Date.now() }))
      .finally(() => {
        active--;
        pending.delete(symbol);
        publish();
        pump();
      });
  }
}

export function requestHistories(symbols: string[]) {
  const now = Date.now();
  for (const s of symbols) {
    const hit = store.get(s);
    if (pending.has(s) || (hit && now - hit.ts < HISTORY_TTL_MS)) continue;
    pending.add(s);
    queue.push(s);
  }
  pump();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const getSnapshot = () => snapshot;
const EMPTY: Record<string, HistoryState> = {};
const getServerSnapshot = () => EMPTY;

/** Every history loaded so far (re-renders as more arrive). Ask for symbols with `requestHistories`. */
export function useEarningsHistories(): Record<string, HistoryState> {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

// ---------------------------------------------------------------------------------------------------------------
// Board + session calendar

const REFRESH_MS = 5 * 60 * 1000;

export type LoadState = 'loading' | 'ok' | 'error';

export interface EarningsCalendarData {
  /** null until the first client render computes the New York date */
  anchors: SessionAnchors | null;
  /** the official session calendar, null until loaded or when unavailable */
  calendar: MarketCalendar | null;
  board: MomentumBoardResult[];
  boardState: LoadState;
  /** the official session calendar couldn't be loaded: dates fall back to Mon-Fri, holidays unverified */
  calendarFailed: boolean;
  watchlist: string[];
  histories: Record<string, HistoryState>;
  reload: () => void;
}

export function useEarningsCalendar(): EarningsCalendarData {
  const { symbols: watchlist } = useWatchlist();
  const histories = useEarningsHistories();
  const [nyToday, setNyToday] = useState<string | null>(null);
  const [cal, setCal] = useState<MarketCalendar | null>(null);
  const [calendarFailed, setCalendarFailed] = useState(false);
  const [board, setBoard] = useState<MomentumBoardResult[]>([]);
  const [boardState, setBoardState] = useState<LoadState>('loading');

  const load = useCallback(() => {
    setNyToday(nyDateOf(new Date()));
    apiClient
      .get<MarketCalendar>('/api/market/calendar')
      .then((r) => {
        setCal(r.data);
        setCalendarFailed(false);
      })
      .catch(() => setCalendarFailed(true));
    momentumBoardApi
      .getBoard({ category: 'all', limit: 500 })
      .then((r) => {
        setBoard(r.results);
        setBoardState('ok');
      })
      // keep the last good board on a failed background refresh
      .catch(() => setBoardState((s) => (s === 'ok' ? s : 'error')));
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  const watchKey = watchlist.join(',');
  useEffect(() => {
    requestHistories(watchlist);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchKey]);

  const anchors = nyToday ? sessionAnchors(nyToday, cal) : null;

  return { anchors, calendar: cal, board, boardState, calendarFailed, watchlist, histories, reload: load };
}
