'use client';

import React, { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Card, CardBody, CardHeader } from '@nextui-org/react';
import { ChevronLeftIcon, ChevronRightIcon } from '@heroicons/react/20/solid';
import { clsx } from 'clsx';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import { SelectField, TextField } from '@/components/ui/Field';
import EarningsTable from '@/components/earnings/EarningsTable';
import { usePagePerf } from '@/hooks/usePagePerf';
import { requestHistories, useEarningsCalendar } from '@/hooks/useEarningsCalendar';
import {
  addDays,
  buildRows,
  countByDate,
  filterRows,
  formatDay,
  formatWeekRange,
  mondayOf,
  rankRows,
  sessionChecker,
  weekDays,
} from '@/lib/earningsCalendar';

/** Per-symbol details (EPS, beat record) are looked up live, so only this many rows of the selected day load them. */
const DETAIL_ROWS = 40;
const ISO = /^\d{4}-\d{2}-\d{2}$/;
const ALL_SECTORS = '__all__';

/** `useSearchParams()` opts the route out of static prerendering, so Next requires it under a Suspense boundary. */
export default function EarningsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-slate-50">
          <LoadingSpinner size="large" />
        </div>
      }
    >
      <EarningsCalendar />
    </Suspense>
  );
}

function Toggle({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={clsx(
        'press h-[34px] rounded-full border px-3.5 text-[13px] font-medium transition-colors',
        pressed ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 text-slate-700 hover:border-slate-400',
      )}
    >
      {children}
    </button>
  );
}

function EarningsCalendar() {
  const { markLoaded } = usePagePerf('/earnings');
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { anchors, calendar, board, boardState, calendarFailed, watchlist, histories, reload } = useEarningsCalendar();
  const [query, setQuery] = useState('');

  // URL state: week (its Monday), selected day, filters -- shareable and back-button friendly
  const dayParam = params.get('day');
  const weekParam = params.get('week');
  const sector = params.get('sector');
  const watchlistOnly = params.get('watchlist') === '1';
  const signalOnly = params.get('board') === '1';

  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      Object.entries(patch).forEach(([k, v]) => (v == null ? next.delete(k) : next.set(k, v)));
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const weekStart =
    weekParam && ISO.test(weekParam)
      ? mondayOf(weekParam)
      : dayParam && ISO.test(dayParam)
        ? mondayOf(dayParam)
        : anchors?.weekStart ?? null;
  const days = useMemo(() => (weekStart ? weekDays(weekStart) : []), [weekStart]);

  const weekRows = useMemo(() => {
    if (!weekStart) return [];
    return rankRows(buildRows({ board, watchlist, histories, from: weekStart, to: addDays(weekStart, 4) }));
  }, [weekStart, board, watchlist, histories]);

  const filtered = useMemo(
    () => filterRows(weekRows, { query, sector, watchlistOnly, signalOnly }),
    [weekRows, query, sector, watchlistOnly, signalOnly],
  );
  const counts = useMemo(() => countByDate(filtered), [filtered]);
  const sectors = useMemo(
    () => [...new Set(weekRows.map((r) => r.sector).filter((s): s is string => !!s))].sort(),
    [weekRows],
  );

  const selectedDay =
    dayParam && days.includes(dayParam)
      ? dayParam
      : anchors && days.includes(anchors.nyToday)
        ? anchors.nyToday
        : days.find((d) => counts[d]) ?? days[0] ?? null;
  const dayRows = useMemo(() => filtered.filter((r) => r.date === selectedDay), [filtered, selectedDay]);

  const detailKey = dayRows.slice(0, DETAIL_ROWS).map((r) => r.symbol).join(',');
  useEffect(() => {
    if (detailKey) requestHistories(detailKey.split(','));
  }, [detailKey]);

  useEffect(() => {
    if (boardState !== 'loading') markLoaded();
  }, [boardState, markLoaded]);

  const check = useMemo(() => sessionChecker(calendar), [calendar]);
  const isCurrentWeek = anchors ? weekStart === anchors.weekStart : true;
  const loading = !anchors || (boardState === 'loading' && board.length === 0);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="mb-6 px-1 pt-2">
          <h1 className="large-title">Earnings</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
            Who reports, day by day · New York dates · Momentum Board names and your watchlist
          </p>
        </div>

        {/* Week navigator + filters */}
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Previous week"
              disabled={!weekStart}
              onClick={() => weekStart && setParams({ week: addDays(weekStart, -7), day: null })}
              className="press inline-flex h-[34px] w-[34px] items-center justify-center rounded-full border border-slate-300 text-slate-600 hover:border-slate-400"
            >
              <ChevronLeftIcon className="h-5 w-5" />
            </button>
            <button
              type="button"
              aria-label="Next week"
              disabled={!weekStart}
              onClick={() => weekStart && setParams({ week: addDays(weekStart, 7), day: null })}
              className="press inline-flex h-[34px] w-[34px] items-center justify-center rounded-full border border-slate-300 text-slate-600 hover:border-slate-400"
            >
              <ChevronRightIcon className="h-5 w-5" />
            </button>
            <h2 className="ml-1 text-[19px] font-semibold tabular-nums text-slate-900">{weekStart ? formatWeekRange(weekStart) : '—'}</h2>
            {!isCurrentWeek && (
              <button
                type="button"
                onClick={() => setParams({ week: null, day: null })}
                className="ml-1 text-[13px] font-medium text-accent hover:underline"
              >
                This week
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <TextField
              label="Symbol"
              placeholder="Search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-32"
              aria-label="Search symbol"
            />
            <SelectField
              label="Sector"
              value={sector ?? ALL_SECTORS}
              onChange={(v) => setParams({ sector: v === ALL_SECTORS ? null : v })}
              options={[{ value: ALL_SECTORS, label: 'All sectors' }, ...sectors.map((s) => ({ value: s, label: s }))]}
              className="w-44"
            />
            <Toggle pressed={watchlistOnly} onClick={() => setParams({ watchlist: watchlistOnly ? null : '1' })}>
              My watchlist
            </Toggle>
            <Toggle pressed={signalOnly} onClick={() => setParams({ board: signalOnly ? null : '1' })}>
              Breakout names
            </Toggle>
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-20">
            <LoadingSpinner size="large" />
          </div>
        ) : boardState === 'error' && weekRows.length === 0 ? (
          <ErrorAlert title="Earnings unavailable" message="Couldn't load the Momentum Board names." onRetry={reload} />
        ) : (
          <>
            {/* Day strip: Mon-Fri with counts; scrolls sideways on a phone */}
            <div className="-mx-4 mb-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
              <div className="grid min-w-[560px] grid-cols-5 gap-2 sm:gap-3" role="tablist" aria-label="Day of the week">
                {days.map((d) => {
                  const selected = d === selectedDay;
                  const isToday = d === anchors.nyToday;
                  const info = check(d);
                  const holiday = info.verified && !info.session;
                  const preview = filtered.filter((r) => r.date === d).slice(0, 3);
                  return (
                    <button
                      key={d}
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      onClick={() => setParams({ day: d, week: weekStart })}
                      className={clsx(
                        'press rounded-2xl border p-3 text-left transition-colors',
                        selected ? 'border-accent bg-white shadow-sm ring-[3px] ring-accent/15' : 'border-black/[0.05] bg-white hover:border-slate-300',
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <span className={clsx('text-[13px] font-semibold', isToday ? 'text-accent' : 'text-slate-900')}>{formatDay(d)}</span>
                        {isToday && <span className="rounded-full bg-accent-soft px-1.5 text-[10px] font-semibold text-accent">Today</span>}
                      </div>
                      <p className="mt-1 text-[26px] font-semibold leading-tight tabular-nums text-slate-900">
                        {holiday && !counts[d] ? <span className="text-[15px] font-medium text-slate-400">Market closed</span> : counts[d] ?? 0}
                      </p>
                      <p className="mt-0.5 truncate text-[11px] text-slate-500">
                        {preview.length ? preview.map((r) => r.symbol).join(' · ') : counts[d] ? '' : 'no reports'}
                      </p>
                    </button>
                  );
                })}
              </div>
            </div>

            <Card className="border border-black/[0.05] shadow-sm" data-stock-list="Earnings calendar">
              <CardHeader className="flex flex-wrap items-baseline justify-between gap-2 px-5 pt-5 pb-3">
                <h3 className="text-[17px] font-semibold text-slate-900">
                  {selectedDay ? formatDay(selectedDay) : '—'}
                  <span className="ml-2 font-normal text-slate-400">
                    {dayRows.length} {dayRows.length === 1 ? 'report' : 'reports'}
                  </span>
                </h3>
                {dayRows.length > DETAIL_ROWS && (
                  <span className="text-[11px] text-slate-400">EPS details load for the first {DETAIL_ROWS} rows</span>
                )}
              </CardHeader>
              <CardBody className="p-0">
                {dayRows.length === 0 ? (
                  <p className="px-5 py-12 text-center text-sm text-slate-400">
                    {weekRows.length && filtered.length !== weekRows.length
                      ? 'No reports match these filters on this day.'
                      : `No reports on this day among Momentum Board names${watchlist.length ? ' or your watchlist' : ''}.`}
                  </p>
                ) : (
                  <EarningsTable
                    rows={dayRows}
                    histories={histories}
                    showResult={!!selectedDay && selectedDay <= anchors.nyToday}
                    ariaLabel={`Earnings on ${selectedDay}`}
                  />
                )}
              </CardBody>
            </Card>

            <div className="mt-4 space-y-1 px-1 text-[12px] leading-relaxed text-slate-400">
              <p>
                Covers names on today&apos;s Momentum Board (their next report date) and your watchlist (past and upcoming
                reports). Past results for other stocks aren&apos;t listed here yet — open a stock to see its full earnings history.
              </p>
              <p>
                Report time (before the open / after the close) isn&apos;t available yet. Report dates can still move until the
                company confirms them.
                {(calendarFailed || !anchors.verified) && ' Market holidays not verified — days assume Mon–Fri sessions.'}
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
