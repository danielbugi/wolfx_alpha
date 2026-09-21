'use client';

import React, { createContext, useCallback, useContext, useRef, useState } from 'react';

interface TopProgressContextValue {
  /** Call when a background fetch starts (auto-refresh, filter change, manual refresh). */
  start: () => void;
  /** Call when that fetch settles, success or failure. Safe to call more than `start()`. */
  done: () => void;
}

const TopProgressContext = createContext<TopProgressContextValue | null>(null);

/**
 * Drives a thin top-of-page progress bar shared across routes, so a
 * background refetch (auto-refresh, a filter change) can signal activity
 * without unmounting whatever is already on screen. Reference-counted so
 * overlapping fetches (e.g. dashboard data + market overview) don't hide
 * the bar until all of them have settled.
 */
export function TopProgressProvider({ children }: { children: React.ReactNode }) {
  const [active, setActive] = useState(false);
  const countRef = useRef(0);

  const start = useCallback(() => {
    countRef.current += 1;
    setActive(true);
  }, []);

  const done = useCallback(() => {
    countRef.current = Math.max(0, countRef.current - 1);
    if (countRef.current === 0) setActive(false);
  }, []);

  return (
    <TopProgressContext.Provider value={{ start, done }}>
      <TopProgressBar active={active} />
      {children}
    </TopProgressContext.Provider>
  );
}

export function useTopProgress() {
  const ctx = useContext(TopProgressContext);
  if (!ctx) {
    throw new Error('useTopProgress must be used within a TopProgressProvider');
  }
  return ctx;
}

function TopProgressBar({ active }: { active: boolean }) {
  return (
    <div
      aria-hidden={!active}
      className={`fixed top-0 left-0 right-0 z-50 h-[2px] overflow-hidden pointer-events-none transition-opacity duration-200 ${
        active ? 'opacity-100' : 'opacity-0'
      }`}
    >
      <div className="h-full w-2/5 bg-cyan-500 top-progress-fill" />
    </div>
  );
}
