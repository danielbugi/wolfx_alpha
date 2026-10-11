'use client';

import React, { useEffect, useState } from 'react';
import { clsx } from 'clsx';
import { apiClient } from '@/services/api';
import { MarketCalendar, marketStatus, MarketState } from '@/lib/marketSession';

const CLOCKS = [
  { label: 'Israel', timeZone: 'Asia/Jerusalem' },
  { label: 'New York', timeZone: 'America/New_York' },
];
const CALENDAR_REFRESH_MS = 6 * 3600_000;

const DOT: Record<MarketState, string> = {
  open: 'bg-emerald-500',
  pre: 'bg-amber-400',
  after: 'bg-amber-400',
  closed: 'bg-slate-400',
};

function time(now: Date, timeZone: string) {
  return now.toLocaleTimeString('en-GB', { timeZone, hour: '2-digit', minute: '2-digit' });
}
function day(now: Date, timeZone: string) {
  return now.toLocaleDateString('en-US', { timeZone, weekday: 'short', day: 'numeric', month: 'short' });
}

/** Ticks with the wall clock, re-rendering at the start of each minute. */
function useNow(): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      timer = setTimeout(() => {
        setNow(new Date());
        schedule();
      }, 60_000 - (Date.now() % 60_000) + 50);
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);
  return now;
}

/** The official session calendar (holidays + early closes) from the backend; null until loaded or if unavailable. */
function useMarketCalendar(): MarketCalendar | null {
  const [cal, setCal] = useState<MarketCalendar | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      apiClient
        .get<MarketCalendar>('/api/market/calendar')
        .then((r) => !cancelled && setCal(r.data))
        .catch(() => {
          /* clocks still work; the status line just isn't shown */
        });
    load();
    const id = setInterval(load, CALENDAR_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  return cal;
}

/**
 * Israel and New York time, plus the US market's state (open / pre-market / after hours / closed, holidays and early
 * closes included) with the time to the next open or close. `compact` = the collapsed sidebar rail.
 */
export default function MarketClock({ compact = false, className }: { compact?: boolean; className?: string }) {
  const now = useNow();
  const cal = useMarketCalendar();
  if (!now) return null;
  const status = cal ? marketStatus(now, cal) : null;

  if (compact) {
    return (
      <div
        className={clsx('flex flex-col items-center gap-0.5 text-[10px] tabular-nums text-slate-500', className)}
        title={CLOCKS.map((c) => `${c.label} ${time(now, c.timeZone)}`).join(' · ') + (status ? ` · ${status.label}, ${status.detail}` : '')}
      >
        <span className="flex items-center gap-1 font-semibold text-slate-700">
          {status && <span className={clsx('h-1.5 w-1.5 rounded-full', DOT[status.state])} />}
          NY
        </span>
        <span>{time(now, 'America/New_York')}</span>
      </div>
    );
  }

  return (
    <div className={clsx('rounded-xl bg-black/[0.035] px-3 py-2.5', className)}>
      <div className="grid grid-cols-2 gap-2">
        {CLOCKS.map((c) => (
          <div key={c.timeZone} className="min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-[0.05em] text-slate-400">{c.label}</p>
            <p className="text-[17px] font-semibold leading-tight tabular-nums text-slate-900">{time(now, c.timeZone)}</p>
            <p className="truncate text-[10px] text-slate-400">{day(now, c.timeZone)}</p>
          </div>
        ))}
      </div>
      {status && (
        <div className="mt-2 border-t border-black/[0.06] pt-2" title={cal && !cal.holidays_known ? 'Market calendar unavailable: holidays are not verified' : undefined}>
          <p className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-700">
            <span className={clsx('h-2 w-2 rounded-full', DOT[status.state], status.state === 'open' && 'animate-pulse')} />
            {status.label}
          </p>
          <p className="mt-0.5 text-[10.5px] leading-snug text-slate-500">{status.detail}</p>
        </div>
      )}
    </div>
  );
}
