// File: frontend/src/components/strategies/CandidateFilters.tsx
// Filters the research candidates endpoint supports. Class and grade options are the facets the backend returns for the
// session being viewed (never a hardcoded list, so a second strategy's classes appear without a code change).
import React, { useEffect, useState } from 'react';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import type { CandidatePage, CandidateSort, Direction, GuardStatus } from '@/services/strategyApi';
import { candidateClassLabel } from '@/lib/strategyExtensions';
import { Btn } from '@/components/telegram/ui';

export interface CandidateFilterState {
  session_date: string;
  symbol: string;
  direction: Direction | '';
  guard: GuardStatus | '';
  selected: '' | 'yes' | 'no';
  candidate_class: string;
  grade: string;
  sort: CandidateSort;
}

export const DEFAULT_CANDIDATE_FILTERS: CandidateFilterState = {
  session_date: '', symbol: '', direction: '', guard: '', selected: '', candidate_class: '', grade: '', sort: 'rank',
};

const FIELD =
  'w-full rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm text-slate-700 focus:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300';

const SORTS: { value: CandidateSort; label: string }[] = [
  { value: 'rank', label: 'Session rank' },
  { value: 'combined_desc', label: 'Combined score, high → low' },
  { value: 'alignment_desc', label: 'Alignment, high → low' },
  { value: 'breakout_desc', label: 'Breakout distance (ATR)' },
  { value: 'grade', label: 'Grade (A first)' },
  { value: 'symbol', label: 'Symbol A–Z' },
];

export default function CandidateFilters({ strategyKey, value, onChange, facets, sessionInUse }: {
  strategyKey: string;
  value: CandidateFilterState;
  onChange: (next: CandidateFilterState) => void;
  facets: CandidatePage['facets'] | null;
  /** the session the backend actually listed (the latest captured one when none is chosen) */
  sessionInUse: string | null;
}) {
  const [symbol, setSymbol] = useState(value.symbol);
  useEffect(() => setSymbol(value.symbol), [value.symbol]);
  useEffect(() => {
    if (symbol === value.symbol) return;
    const timer = setTimeout(() => onChange({ ...value, symbol }), 300);
    return () => clearTimeout(timer);
  }, [symbol, value, onChange]);

  const set = <K extends keyof CandidateFilterState>(key: K, v: CandidateFilterState[K]) => onChange({ ...value, [key]: v });
  const active = (Object.keys(DEFAULT_CANDIDATE_FILTERS) as (keyof CandidateFilterState)[])
    .some((k) => k !== 'sort' && value[k] !== DEFAULT_CANDIDATE_FILTERS[k]);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Session
        <input type="date" className={FIELD} value={value.session_date}
               aria-label="Session" title={sessionInUse && !value.session_date ? `Showing the latest captured session (${sessionInUse})` : undefined}
               onChange={(e) => set('session_date', e.target.value)} />
      </label>
      <label className="col-span-2 flex flex-col gap-1 text-xs text-slate-500 sm:col-span-1">
        Symbol
        <span className="relative">
          <MagnifyingGlassIcon className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-slate-400" aria-hidden />
          <input className={`${FIELD} pl-7 uppercase`} value={symbol} maxLength={10} placeholder="starts with…"
                 onChange={(e) => setSymbol(e.target.value.replace(/[^A-Za-z0-9.-]/g, '').toUpperCase())}
                 aria-label="Filter candidates by symbol" />
        </span>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Direction
        <select className={FIELD} value={value.direction} aria-label="Direction" onChange={(e) => set('direction', e.target.value as CandidateFilterState['direction'])}>
          <option value="">Both</option>
          <option value="bullish">Bullish</option>
          <option value="bearish">Bearish</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Guard status
        <select className={FIELD} value={value.guard} aria-label="Guard status" onChange={(e) => set('guard', e.target.value as CandidateFilterState['guard'])}>
          <option value="">Any</option>
          <option value="passed">Passed</option>
          <option value="rejected">Rejected</option>
          <option value="not_evaluated">Not evaluated</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Selected
        <select className={FIELD} value={value.selected} aria-label="Selected" onChange={(e) => set('selected', e.target.value as CandidateFilterState['selected'])}>
          <option value="">Any</option>
          <option value="yes">Selected</option>
          <option value="no">Not selected</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Candidate class
        <select className={FIELD} value={value.candidate_class} aria-label="Candidate class" onChange={(e) => set('candidate_class', e.target.value)}>
          <option value="">Any</option>
          {(facets?.classes ?? []).map((c) => <option key={c.value} value={c.value}>{candidateClassLabel(strategyKey, c.value)} ({c.count.toLocaleString('en-US')})</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Grade
        <select className={FIELD} value={value.grade} aria-label="Grade" onChange={(e) => set('grade', e.target.value)}>
          <option value="">Any</option>
          {(facets?.grades ?? []).map((g) => <option key={g.value} value={g.value}>{g.value} ({g.count.toLocaleString('en-US')})</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Sort
        <select className={FIELD} value={value.sort} aria-label="Sort" onChange={(e) => set('sort', e.target.value as CandidateSort)}>
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </label>
      <div className="flex items-end">
        <Btn size="md" variant="ghost" disabled={!active} onClick={() => onChange({ ...DEFAULT_CANDIDATE_FILTERS, sort: value.sort })}>
          Clear filters
        </Btn>
      </div>
    </div>
  );
}
