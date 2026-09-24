// File: frontend/src/components/telegram/MessageFilters.tsx
import React, { useEffect, useState } from 'react';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import type { ChannelTarget, KindCount, StatusFilter } from '@/services/telegramApi';
import { Btn } from '@/components/telegram/ui';

export interface FilterState {
  target: ChannelTarget | '';
  kind: string;
  status: StatusFilter | '';
  day: string;
  q: string;
}

export const NO_FILTERS: FilterState = { target: '', kind: '', status: '', day: '', q: '' };

const FIELD =
  'rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-sm text-slate-700 focus:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300';

interface Props {
  value: FilterState;
  kinds: KindCount[];
  onChange: (next: FilterState) => void;
}

export default function MessageFilters({ value, kinds, onChange }: Props) {
  // The search box is debounced so typing does not fire a request per keystroke.
  const [q, setQ] = useState(value.q);
  useEffect(() => setQ(value.q), [value.q]);
  useEffect(() => {
    if (q === value.q) return;
    const timer = setTimeout(() => onChange({ ...value, q }), 300);
    return () => clearTimeout(timer);
  }, [q, value, onChange]);

  const set = <K extends keyof FilterState>(key: K, v: FilterState[K]) => onChange({ ...value, [key]: v });
  const active = Object.values(value).some((v) => v !== '');

  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Channel
        <select className={FIELD} value={value.target} onChange={(e) => set('target', e.target.value as FilterState['target'])}>
          <option value="">Both</option>
          <option value="dev">Dev</option>
          <option value="prod">Production</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Kind of post
        <select className={FIELD} value={value.kind} onChange={(e) => set('kind', e.target.value)}>
          <option value="">All kinds</option>
          {kinds.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label} ({k.count})
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Status
        <select className={FIELD} value={value.status} onChange={(e) => set('status', e.target.value as FilterState['status'])}>
          <option value="">Any</option>
          <option value="sent">Live (edited or not)</option>
          <option value="edited">Edited</option>
          <option value="deleted">Deleted</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Day
        <input type="date" className={FIELD} value={value.day} onChange={(e) => set('day', e.target.value)} />
      </label>
      <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-xs text-slate-500">
        Search the text
        <span className="relative">
          <MagnifyingGlassIcon className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" aria-hidden />
          <input
            type="search"
            className={`${FIELD} w-full pl-8`}
            value={q}
            maxLength={100}
            onChange={(e) => setQ(e.target.value)}
            placeholder="e.g. breakout"
          />
        </span>
      </label>
      <Btn variant="ghost" size="sm" disabled={!active} onClick={() => onChange(NO_FILTERS)}>
        Clear filters
      </Btn>
    </div>
  );
}
