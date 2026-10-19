'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Card, CardBody, CardHeader } from '@nextui-org/react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';
import SegmentedControl from '@/components/ui/SegmentedControl';
import EarningsTable from '@/components/earnings/EarningsTable';
import { requestHistories, useEarningsCalendar } from '@/hooks/useEarningsCalendar';
import { buildRows, formatDay, rankRows, type EarningsRow } from '@/lib/earningsCalendar';

type Tab = 'yesterday' | 'today' | 'tomorrow' | 'week';

const MAX_ROWS = 8;

/**
 * Dashboard card: who reports yesterday / today / tomorrow / this week, as New York trading sessions (Monday's
 * "yesterday" is Friday, Friday's "tomorrow" is Monday, holidays skipped). Your watchlist names first, then a daily
 * coverage list (the internal board's lists, used here only to choose which stocks to cover; no signals are shown).
 */
export default function EarningsCard() {
  const { anchors, board, boardState, calendarFailed, watchlist, histories, reload } = useEarningsCalendar();
  const [tab, setTab] = useState<Tab | null>(null);

  const groups = useMemo(() => {
    if (!anchors) return null;
    const from = anchors.yesterday ?? anchors.nyToday;
    const lastWeekDay = anchors.weekDays[4];
    const to = anchors.tomorrow && anchors.tomorrow > lastWeekDay ? anchors.tomorrow : lastWeekDay;
    const rows = rankRows(buildRows({ board, watchlist, histories, from, to }));
    const on = (d: string | null) => rows.filter((r) => r.date === d);
    const week = new Set(anchors.weekDays);
    return {
      yesterday: on(anchors.yesterday),
      today: on(anchors.nyToday),
      tomorrow: on(anchors.tomorrow),
      week: rows.filter((r) => week.has(r.date)),
    } satisfies Record<Tab, EarningsRow[]>;
  }, [anchors, board, watchlist, histories]);

  // open on the most useful tab once the board has loaded: today, else tomorrow, else the week
  useEffect(() => {
    if (tab || !groups || boardState === 'loading') return;
    setTab(groups.today.length ? 'today' : groups.tomorrow.length ? 'tomorrow' : 'week');
  }, [tab, groups, boardState]);

  const active: Tab = tab ?? 'today';
  const shown = groups ? groups[active].slice(0, MAX_ROWS) : [];
  const shownKey = shown.map((r) => r.symbol).join(',');
  useEffect(() => {
    requestHistories(shown.map((r) => r.symbol));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shownKey]);

  const weekIsNext = anchors ? anchors.weekStart > anchors.nyToday : false;
  const dayOf: Record<Tab, string | null> = {
    yesterday: anchors?.yesterday ?? null,
    today: anchors?.nyToday ?? null,
    tomorrow: anchors?.tomorrow ?? null,
    week: null,
  };
  const label = (t: Tab, text: string) => (
    <>
      {text}
      {groups && boardState !== 'loading' && <span className="ml-1 tabular-nums text-slate-400">{groups[t].length}</span>}
    </>
  );
  const calendarHref =
    active === 'week' || !dayOf[active] ? `/earnings${anchors ? `?week=${anchors.weekStart}` : ''}` : `/earnings?day=${dayOf[active]}`;

  const emptyText = (): string => {
    if (!anchors) return '';
    if (active === 'today' && !anchors.todayIsSession) return `Market closed today (${formatDay(anchors.nyToday)}).`;
    if (active === 'yesterday' && watchlist.length === 0)
      return 'Reported results show for your watchlist names — star a stock on its page to follow its reports here.';
    const where = active === 'week' ? (weekIsNext ? 'next week' : 'this week') : `on ${formatDay(dayOf[active] as string)}`;
    return `No reports ${where} among the covered stocks${watchlist.length ? ' or your watchlist' : ''}.`;
  };

  return (
    <Card className="mb-6 border border-black/[0.05] shadow-sm" data-stock-list="Earnings">
      <CardHeader className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5 pb-3">
        <div>
          <h3 className="text-[17px] font-semibold text-slate-900">Earnings</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            US stocks with notable recent price moves and your watchlist · New York sessions
            {anchors?.yesterday && active === 'yesterday' && ` · ${formatDay(anchors.yesterday)}`}
            {anchors?.tomorrow && active === 'tomorrow' && ` · ${formatDay(anchors.tomorrow)}`}
            {anchors && active === 'week' && ` · ${formatDay(anchors.weekDays[0])} – ${formatDay(anchors.weekDays[4])}`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <SegmentedControl
            aria-label="Earnings day"
            options={[
              { value: 'yesterday', label: label('yesterday', 'Yesterday') },
              { value: 'today', label: label('today', 'Today') },
              { value: 'tomorrow', label: label('tomorrow', 'Tomorrow') },
              { value: 'week', label: label('week', weekIsNext ? 'Next week' : 'This week') },
            ]}
            value={active}
            onChange={setTab}
          />
          <Link href={calendarHref} className="whitespace-nowrap text-[13px] font-medium text-accent hover:underline">
            Full calendar →
          </Link>
        </div>
      </CardHeader>
      <CardBody className="p-0">
        {!groups || (boardState === 'loading' && board.length === 0) ? (
          <div className="flex justify-center py-10">
            <LoadingSpinner size="medium" />
          </div>
        ) : boardState === 'error' && groups[active].length === 0 ? (
          <ErrorAlert title="Earnings unavailable" message="Couldn't load the list of covered stocks." onRetry={reload} className="m-4" />
        ) : shown.length === 0 ? (
          <p className="px-5 py-10 text-center text-sm text-slate-400">{emptyText()}</p>
        ) : (
          <>
            <EarningsTable
              rows={shown}
              histories={histories}
              showDate={active === 'week'}
              showResult={active === 'yesterday' || active === 'today'}
              ariaLabel="Earnings"
            />
            {groups[active].length > MAX_ROWS && (
              <Link href={calendarHref} className="block border-t border-slate-100 px-5 py-2.5 text-center text-[13px] font-medium text-accent hover:underline">
                {groups[active].length - MAX_ROWS} more in the calendar →
              </Link>
            )}
          </>
        )}
        <p className="border-t border-slate-100 px-5 py-2 text-[11px] text-slate-400">
          Report time (before the open / after the close) isn&apos;t available yet.
          {(calendarFailed || (anchors && !anchors.verified)) && ' Market holidays not verified — dates assume Mon–Fri sessions.'}
        </p>
      </CardBody>
    </Card>
  );
}
