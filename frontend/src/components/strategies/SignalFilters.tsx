// File: frontend/src/components/strategies/SignalFilters.tsx
// Only filters the Strategy Intelligence API actually supports. Sector and model version are API filters too but have
// no options list to choose from yet (and model_version is empty for every Release-A signal), so they are not offered.
import React, { useEffect, useState } from 'react';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import type { Direction, Lifecycle, SignalSort, SignalStatus } from '@/services/strategyApi';
import { Btn } from '@/components/telegram/ui';

export interface SignalFilterState {
  symbol: string;
  direction: Direction | '';
  lifecycle: Lifecycle | '';
  status: SignalStatus | '';
  quality_grade: string;
  flag: '' | 'split_suspect' | 'same_bar_stop_and_target';
  date_from: string;
  date_to: string;
  sort: SignalSort;
}

export const DEFAULT_SIGNAL_FILTERS: SignalFilterState = {
  symbol: '', direction: '', lifecycle: '', status: '', quality_grade: '', flag: '', date_from: '', date_to: '', sort: 'newest',
};

const FIELD =
  'w-full rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm text-slate-700 focus:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300';

const SORTS: { value: SignalSort; label: string }[] = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'symbol', label: 'Symbol A–Z' },
  { value: 'grade', label: 'Grade (A first)' },
  { value: 'r_desc', label: 'Result R, high → low' },
  { value: 'r_asc', label: 'Result R, low → high' },
  { value: 'holding_desc', label: 'Sessions held, most' },
  { value: 'holding_asc', label: 'Sessions held, fewest' },
];

export default function SignalFilters({ value, onChange }: { value: SignalFilterState; onChange: (next: SignalFilterState) => void }) {
  // The symbol box is debounced so typing does not fire a request per keystroke.
  const [symbol, setSymbol] = useState(value.symbol);
  useEffect(() => setSymbol(value.symbol), [value.symbol]);
  useEffect(() => {
    if (symbol === value.symbol) return;
    const timer = setTimeout(() => onChange({ ...value, symbol }), 300);
    return () => clearTimeout(timer);
  }, [symbol, value, onChange]);

  const set = <K extends keyof SignalFilterState>(key: K, v: SignalFilterState[K]) => onChange({ ...value, [key]: v });
  const active = (Object.keys(DEFAULT_SIGNAL_FILTERS) as (keyof SignalFilterState)[])
    .some((k) => k !== 'sort' && value[k] !== DEFAULT_SIGNAL_FILTERS[k]);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <label className="col-span-2 flex flex-col gap-1 text-xs text-slate-500 sm:col-span-1">
        Symbol
        <span className="relative">
          <MagnifyingGlassIcon className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-slate-400" aria-hidden />
          <input
            className={`${FIELD} pl-7 uppercase`}
            value={symbol}
            maxLength={10}
            placeholder="e.g. AAPL"
            onChange={(e) => setSymbol(e.target.value.replace(/[^A-Za-z0-9.-]/g, '').toUpperCase())}
            aria-label="Filter signals by symbol"
          />
        </span>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Direction
        <select className={FIELD} value={value.direction} onChange={(e) => set('direction', e.target.value as SignalFilterState['direction'])}>
          <option value="">Both</option>
          <option value="bullish">Bullish</option>
          <option value="bearish">Bearish</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Lifecycle
        <select className={FIELD} value={value.lifecycle} onChange={(e) => set('lifecycle', e.target.value as SignalFilterState['lifecycle'])}>
          <option value="">All</option>
          <option value="open">Open</option>
          <option value="held">Held</option>
          <option value="resolved">Resolved</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Outcome
        <select className={FIELD} value={value.status} onChange={(e) => set('status', e.target.value as SignalFilterState['status'])}>
          <option value="">Any</option>
          <option value="open">Open</option>
          <option value="target1">Target 1</option>
          <option value="target2">Target 2</option>
          <option value="target3">Target 3</option>
          <option value="stopped">Stopped</option>
          <option value="expired">Expired</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Grade
        <select className={FIELD} value={value.quality_grade} onChange={(e) => set('quality_grade', e.target.value)}>
          <option value="">Any</option>
          {['A', 'B', 'C', 'D', 'F'].map((g) => <option key={g} value={g}>{g}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Flag
        <select className={FIELD} value={value.flag} onChange={(e) => set('flag', e.target.value as SignalFilterState['flag'])}>
          <option value="">Any</option>
          <option value="split_suspect">Split suspect (held)</option>
          <option value="same_bar_stop_and_target">Ambiguous resolution</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Signal date from
        <input type="date" className={FIELD} value={value.date_from} onChange={(e) => set('date_from', e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Signal date to
        <input type="date" className={FIELD} value={value.date_to} onChange={(e) => set('date_to', e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Sort
        <select className={FIELD} value={value.sort} onChange={(e) => set('sort', e.target.value as SignalSort)}>
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
      </label>
      <div className="flex items-end">
        <Btn size="md" variant="ghost" disabled={!active} onClick={() => onChange({ ...DEFAULT_SIGNAL_FILTERS, sort: value.sort })}>
          Clear filters
        </Btn>
      </div>
    </div>
  );
}
