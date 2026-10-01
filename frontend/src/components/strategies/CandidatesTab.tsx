// File: frontend/src/components/strategies/CandidatesTab.tsx
// Candidate Explorer: every observation the strategy captured for a session, before the universe guards or ranking
// dropped anything. Server-side filtering, sorting and pagination; only stored fields are shown. When research data is
// not collected the tab says so (never "0 candidates").
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeftIcon, ChevronRightIcon } from '@heroicons/react/24/outline';
import { strategyApi, toApiError } from '@/services/strategyApi';
import type { ApiError, CandidateItem, CandidatePage, CandidateQuery } from '@/services/strategyApi';
import { formatCount, formatNumber, formatSession, GUARD_LABEL, GUARD_REASON_LABEL } from '@/lib/strategyFormat';
import { candidateClassLabel } from '@/lib/strategyExtensions';
import { gradeColor } from '@/lib/uiColors';
import ErrorAlert from '@/components/common/ErrorAlert';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { Btn } from '@/components/telegram/ui';
import CandidateFilters, { DEFAULT_CANDIDATE_FILTERS } from '@/components/strategies/CandidateFilters';
import type { CandidateFilterState } from '@/components/strategies/CandidateFilters';
import CandidateDetailDialog from '@/components/strategies/CandidateDetailDialog';
import SignalDetailDialog from '@/components/strategies/SignalDetailDialog';
import { DirectionTag, EmptyState, Section } from '@/components/strategies/ui';
import { AvailabilityNotice } from '@/components/strategies/ResearchUi';

export const CANDIDATE_PAGE_SIZE = 50;

export function toCandidateQuery(f: CandidateFilterState, offset: number): CandidateQuery {
  return {
    session_date: f.session_date || undefined,
    symbol: f.symbol || undefined,
    direction: f.direction || undefined,
    guard: f.guard || undefined,
    selected: f.selected === '' ? undefined : f.selected === 'yes',
    candidate_class: f.candidate_class || undefined,
    grade: f.grade || undefined,
    sort: f.sort,
    limit: CANDIDATE_PAGE_SIZE,
    offset,
  };
}

function Pager({ page, loading, onOffset, className }: {
  page: CandidatePage | null;
  loading: boolean;
  onOffset: (offset: number) => void;
  className?: string;
}) {
  const measured = !!page && page.total !== null;
  const total = page?.total ?? 0;
  const from = measured && total > 0 ? page!.offset + 1 : 0;
  const to = page ? page.offset + page.items.length : 0;
  return (
    <div className={`flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-xs text-slate-500 ${className ?? ''}`}>
      <span aria-live="polite">
        {!page ? 'Loading…'
          : !measured ? 'Not collected'
          : total === 0 ? 'No candidates'
          : `${formatCount(from)}–${formatCount(to)} of ${formatCount(total)}${page.session_date ? ` · session ${formatSession(page.session_date)}` : ''}`}
      </span>
      <div className="flex items-center gap-1">
        <Btn size="sm" variant="ghost" aria-label="Previous page" disabled={loading || !page || page.offset === 0}
             onClick={() => onOffset(Math.max(0, (page?.offset ?? 0) - CANDIDATE_PAGE_SIZE))}>
          <ChevronLeftIcon className="h-4 w-4" /> Prev
        </Btn>
        <Btn size="sm" variant="ghost" aria-label="Next page" disabled={loading || !page || !page.has_more}
             onClick={() => onOffset((page?.offset ?? 0) + CANDIDATE_PAGE_SIZE)}>
          Next <ChevronRightIcon className="h-4 w-4" />
        </Btn>
      </div>
    </div>
  );
}

function GuardCell({ c }: { c: CandidateItem }) {
  const tone = c.guard_status === 'passed' ? 'bg-emerald-50 text-emerald-700'
    : c.guard_status === 'rejected' ? 'bg-red-50 text-red-700' : 'bg-slate-100 text-slate-500';
  return <span className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-semibold ${tone}`}>{GUARD_LABEL[c.guard_status]}</span>;
}

export default function CandidatesTab({ strategyKey, version, strategyName, candidateId, onCandidateChange }: {
  strategyKey: string;
  version: string;
  strategyName: string;
  candidateId: number | null;
  onCandidateChange: (id: number | null) => void;
}) {
  const [filters, setFilters] = useState<CandidateFilterState>(DEFAULT_CANDIDATE_FILTERS);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<CandidatePage | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [signalId, setSignalId] = useState<number | null>(null);
  const latest = useRef(0);
  const tableTop = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    const mine = ++latest.current;
    setLoading(true);
    try {
      const data = await strategyApi.candidates(strategyKey, version, toCandidateQuery(filters, offset));
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

  const changeFilters = useCallback((next: CandidateFilterState) => {
    setFilters(next);
    setOffset(0);
  }, []);
  const goTo = useCallback((next: number) => {
    setOffset(next);
    tableTop.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, []);

  const filtered = (Object.keys(DEFAULT_CANDIDATE_FILTERS) as (keyof CandidateFilterState)[])
    .some((k) => k !== 'sort' && filters[k] !== DEFAULT_CANDIDATE_FILTERS[k]);
  const unavailable = !!page && page.availability.state !== 'ok';

  return (
    <div className="space-y-4">
      <Section
        title="Candidate explorer"
        description="Every candidate the strategy detected in a session — recorded before the universe guards and ranking — with the frozen T0 snapshot taken alongside it. Select a row for the detail."
        actions={<span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600" data-testid="candidates-strategy">{strategyName} {version}</span>}
      >
        <CandidateFilters strategyKey={strategyKey} value={filters} onChange={changeFilters} facets={page?.facets ?? null} sessionInUse={page?.session_date ?? null} />
      </Section>

      <section ref={tableTop} className="scroll-mt-4 rounded-lg border border-slate-200 bg-white shadow-sm" aria-busy={loading}>
        {!unavailable && <Pager page={page} loading={loading} onOffset={goTo} className="border-b border-slate-100" />}

        {error && !page ? (
          <div className="p-4"><ErrorAlert title="Could not load candidates" message={error.message} onRetry={load} /></div>
        ) : !page ? (
          <LoadingSpinner className="py-16" />
        ) : unavailable ? (
          <div className="p-4"><AvailabilityNotice availability={page.availability} /></div>
        ) : page.items.length === 0 ? (
          <EmptyState title={filtered ? 'No candidate matches these filters' : 'No candidates in this session'}>
            {filtered ? 'Try another symbol or clear a filter.' : 'Nothing was captured for the selected session.'}
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            {error && <p className="px-4 py-2 text-xs text-red-600">Refresh failed: {error.message} Showing the last loaded page.</p>}
            <table className="w-full text-sm" aria-label="Candidates">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-left text-xs text-slate-500">
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="hidden px-3 py-2 font-medium sm:table-cell">Session</th>
                  <th className="hidden px-3 py-2 font-medium sm:table-cell">Direction</th>
                  <th className="hidden px-3 py-2 font-medium md:table-cell">Class</th>
                  <th className="px-3 py-2 font-medium">Guard</th>
                  <th className="hidden px-3 py-2 font-medium lg:table-cell">Guard reason</th>
                  <th className="hidden px-3 py-2 text-right font-medium lg:table-cell">Alignment</th>
                  <th className="hidden px-3 py-2 font-medium md:table-cell">Grade</th>
                  <th className="hidden px-3 py-2 text-right font-medium md:table-cell">Combined</th>
                  <th className="px-3 py-2 font-medium">Selected</th>
                  <th className="hidden px-3 py-2 font-medium xl:table-cell">Snapshot</th>
                  <th className="hidden px-3 py-2 font-medium xl:table-cell">Ledger signal</th>
                </tr>
              </thead>
              <tbody className={loading ? 'opacity-60' : undefined}>
                {page.items.map((c) => (
                  <tr key={c.id} className="cursor-pointer border-b border-slate-100 hover:bg-slate-50" onClick={() => onCandidateChange(c.id)}>
                    <td className="px-3 py-2">
                      <button type="button" className="font-semibold text-slate-800 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                              onClick={(e) => { e.stopPropagation(); onCandidateChange(c.id); }} aria-label={`Open ${c.symbol} candidate details`}>
                        {c.symbol}
                      </button>
                      <span className="block sm:hidden"><DirectionTag direction={c.direction} /></span>
                    </td>
                    <td className="hidden whitespace-nowrap px-3 py-2 text-xs text-slate-500 sm:table-cell">{formatSession(c.session_date)}</td>
                    <td className="hidden px-3 py-2 sm:table-cell"><DirectionTag direction={c.direction} /></td>
                    <td className="hidden px-3 py-2 text-xs text-slate-600 md:table-cell">{candidateClassLabel(strategyKey, c.candidate_class)}</td>
                    <td className="px-3 py-2"><GuardCell c={c} /></td>
                    <td className="hidden px-3 py-2 text-xs text-slate-600 lg:table-cell">
                      {c.guard_reasons.length ? c.guard_reasons.map((r) => GUARD_REASON_LABEL[r] ?? r).join(', ') : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="hidden px-3 py-2 text-right tabular-nums text-slate-700 lg:table-cell">{c.alignment_score === null ? <span className="text-slate-300">—</span> : formatNumber(c.alignment_score, 0)}</td>
                    <td className="hidden px-3 py-2 md:table-cell">
                      {c.quality_grade
                        ? <span className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-semibold ${gradeColor(c.quality_grade)}`}>{c.quality_grade}</span>
                        : <span className="text-slate-300">—</span>}
                    </td>
                    <td className="hidden px-3 py-2 text-right tabular-nums text-slate-700 md:table-cell">{c.combined_score === null ? <span className="text-slate-300">—</span> : formatNumber(c.combined_score, 1)}</td>
                    <td className="px-3 py-2 text-xs">{c.selected ? <span className="font-semibold text-emerald-700">Selected</span> : <span className="text-slate-400">No</span>}</td>
                    <td className="hidden px-3 py-2 text-xs xl:table-cell">
                      {c.snapshot_status === 'complete'
                        ? <span className="text-slate-600">Complete</span>
                        : <span className="font-semibold text-amber-800">Partial{c.missing_count ? ` · ${formatCount(c.missing_count)} missing` : ''}</span>}
                    </td>
                    <td className="hidden px-3 py-2 text-xs xl:table-cell">
                      {c.ledger_signal_id === null ? <span className="text-slate-300">—</span> : (
                        <button type="button" className="font-medium text-indigo-700 hover:underline" aria-label={`Open ledger signal ${c.ledger_signal_id} for ${c.symbol}`}
                                onClick={(e) => { e.stopPropagation(); setSignalId(c.ledger_signal_id); }}>
                          #{c.ledger_signal_id}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {page.items.length > 10 && <Pager page={page} loading={loading} onOffset={goTo} />}
          </div>
        )}
      </section>

      <CandidateDetailDialog strategyKey={strategyKey} version={version} candidateId={candidateId}
                             onClose={() => onCandidateChange(null)} onOpenSignal={setSignalId} />
      <SignalDetailDialog strategyKey={strategyKey} version={version} signalId={signalId} onClose={() => setSignalId(null)} />
    </div>
  );
}
