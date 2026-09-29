// File: frontend/src/components/strategies/SignalsTab.tsx
// The signal explorer: server-side filtering, sorting and pagination (never the whole ledger in the browser).
// Narrow screens keep symbol, direction, status and R; the rest moves into the detail dialog.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeftIcon, ChevronRightIcon } from '@heroicons/react/24/outline';
import { strategyApi, toApiError } from '@/services/strategyApi';
import type { ApiError, SignalPage, SignalQuery } from '@/services/strategyApi';
import { formatCount, formatPrice, formatR, formatSession } from '@/lib/strategyFormat';
import { gradeColor } from '@/lib/uiColors';
import ErrorAlert from '@/components/common/ErrorAlert';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { Btn } from '@/components/telegram/ui';
import SignalFilters, { DEFAULT_SIGNAL_FILTERS } from '@/components/strategies/SignalFilters';
import type { SignalFilterState } from '@/components/strategies/SignalFilters';
import SignalDetailDialog from '@/components/strategies/SignalDetailDialog';
import { DirectionTag, EmptyState, Section, StatusChip } from '@/components/strategies/ui';

export const SIGNAL_PAGE_SIZE = 50;

export function toSignalQuery(f: SignalFilterState, offset: number): SignalQuery {
  return {
    symbol: f.symbol || undefined,
    direction: f.direction || undefined,
    lifecycle: f.lifecycle || undefined,
    status: f.status || undefined,
    quality_grade: f.quality_grade || undefined,
    evaluation_flag: f.flag === 'split_suspect' ? 'split_suspect' : undefined,
    resolution_flag: f.flag === 'same_bar_stop_and_target' ? 'same_bar_stop_and_target' : undefined,
    date_from: f.date_from || undefined,
    date_to: f.date_to || undefined,
    sort: f.sort,
    limit: SIGNAL_PAGE_SIZE,
    offset,
  };
}

function Pager({ page, loading, onOffset, className }: {
  page: SignalPage | null;
  loading: boolean;
  onOffset: (offset: number) => void;
  className?: string;
}) {
  const from = page && page.total > 0 ? page.offset + 1 : 0;
  const to = page ? page.offset + page.items.length : 0;
  return (
    <div className={`flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-xs text-slate-500 ${className ?? ''}`}>
      <span aria-live="polite">
        {page ? (page.total === 0 ? 'No signals' : `${formatCount(from)}–${formatCount(to)} of ${formatCount(page.total)}`) : 'Loading…'}
      </span>
      <div className="flex items-center gap-1">
        <Btn size="sm" variant="ghost" aria-label="Previous page" disabled={loading || !page || page.offset === 0}
             onClick={() => onOffset(Math.max(0, (page?.offset ?? 0) - SIGNAL_PAGE_SIZE))}>
          <ChevronLeftIcon className="h-4 w-4" /> Prev
        </Btn>
        <Btn size="sm" variant="ghost" aria-label="Next page" disabled={loading || !page || !page.has_more}
             onClick={() => onOffset((page?.offset ?? 0) + SIGNAL_PAGE_SIZE)}>
          Next <ChevronRightIcon className="h-4 w-4" />
        </Btn>
      </div>
    </div>
  );
}

export default function SignalsTab({ strategyKey, version }: { strategyKey: string; version: string }) {
  const [filters, setFilters] = useState<SignalFilterState>(DEFAULT_SIGNAL_FILTERS);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<SignalPage | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState<number | null>(null);
  const latest = useRef(0);
  const tableTop = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    const mine = ++latest.current;                       // a slow answer to an older filter must never overwrite a newer one
    setLoading(true);
    try {
      const data = await strategyApi.signals(strategyKey, version, toSignalQuery(filters, offset));
      if (mine !== latest.current) return;
      setPage(data);
      setError(null);
    } catch (e) {
      if (mine === latest.current) setError(toApiError(e));
    } finally {
      if (mine === latest.current) setLoading(false);
    }
  }, [strategyKey, version, filters, offset]);

  useEffect(() => { load(); }, [load]);

  const changeFilters = useCallback((next: SignalFilterState) => {
    setFilters(next);
    setOffset(0);
  }, []);

  const filtered = (Object.keys(DEFAULT_SIGNAL_FILTERS) as (keyof SignalFilterState)[])
    .some((k) => k !== 'sort' && filters[k] !== DEFAULT_SIGNAL_FILTERS[k]);
  const goTo = useCallback((next: number) => {
    setOffset(next);
    tableTop.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, []);

  return (
    <div className="space-y-4">
      <Section title="Signal explorer" description="Every recorded signal, filtered and sorted on the server. Select a row for the full trade plan and lifecycle.">
        <SignalFilters value={filters} onChange={changeFilters} />
      </Section>

      <section ref={tableTop} className="scroll-mt-4 rounded-lg border border-slate-200 bg-white shadow-sm" aria-busy={loading}>
        <Pager page={page} loading={loading} onOffset={goTo} className="border-b border-slate-100" />

        {error && !page ? (
          <div className="p-4"><ErrorAlert title="Could not load signals" message={error.message} onRetry={load} /></div>
        ) : !page ? (
          <LoadingSpinner className="py-16" />
        ) : page.items.length === 0 ? (
          <EmptyState title={filtered ? 'No signal matches these filters' : 'No signals recorded yet'}>
            {filtered ? 'Try another symbol or clear a filter.' : 'Signals appear here after the daily screener records them.'}
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            {error && <p className="px-4 py-2 text-xs text-red-600">Refresh failed: {error.message} Showing the last loaded page.</p>}
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500">
                  <th className="hidden px-3 py-2 font-medium sm:table-cell">Signal date</th>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="hidden px-3 py-2 font-medium md:table-cell">Grade</th>
                  <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">Entry</th>
                  <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">Stop</th>
                  <th className="hidden px-3 py-2 text-right font-medium xl:table-cell">Target 1</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 text-right font-medium">Result</th>
                  <th className="hidden px-3 py-2 text-right font-medium sm:table-cell" title="Trading sessions held">Sessions</th>
                </tr>
              </thead>
              <tbody className={loading ? 'opacity-60' : undefined}>
                {page.items.map((s) => (
                  <tr key={s.id} className="cursor-pointer border-b border-slate-100 hover:bg-slate-50" onClick={() => setOpenId(s.id)}>
                    <td className="hidden whitespace-nowrap px-3 py-2 text-xs text-slate-500 sm:table-cell">{formatSession(s.signal_date)}</td>
                    <td className="px-3 py-2">
                      <button type="button" className="font-semibold text-slate-800 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                              onClick={(e) => { e.stopPropagation(); setOpenId(s.id); }} aria-label={`Open ${s.symbol} signal details`}>
                        {s.symbol}
                      </button>
                      <span className="block"><DirectionTag direction={s.direction} /></span>
                    </td>
                    <td className="hidden px-3 py-2 md:table-cell">
                      {s.quality_grade
                        ? <span className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-semibold ${gradeColor(s.quality_grade)}`}>{s.quality_grade}</span>
                        : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="hidden px-3 py-2 text-right tabular-nums text-slate-700 lg:table-cell">{formatPrice(s.entry_price)}</td>
                    <td className="hidden px-3 py-2 text-right tabular-nums text-slate-500 lg:table-cell">{formatPrice(s.stop_price)}</td>
                    <td className="hidden px-3 py-2 text-right tabular-nums text-slate-500 xl:table-cell">{formatPrice(s.target1_price)}</td>
                    <td className="px-3 py-2"><StatusChip status={s.status} held={s.lifecycle === 'held'} ambiguous={!!s.resolution_flag} /></td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {s.outcome_r === null
                        ? <span className="text-slate-300">—</span>
                        : <span className={s.outcome_r > 0 ? 'font-semibold text-emerald-700' : s.outcome_r < 0 ? 'font-semibold text-red-700' : 'text-slate-700'}>{formatR(s.outcome_r)}</span>}
                    </td>
                    <td className="hidden px-3 py-2 text-right tabular-nums text-slate-600 sm:table-cell">{s.bars_held ?? <span className="text-slate-300">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {page.items.length > 10 && <Pager page={page} loading={loading} onOffset={goTo} />}
          </div>
        )}
      </section>

      <SignalDetailDialog strategyKey={strategyKey} version={version} signalId={openId} onClose={() => setOpenId(null)} />
    </div>
  );
}
