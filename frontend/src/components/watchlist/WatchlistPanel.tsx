// File: frontend/src/components/watchlist/WatchlistPanel.tsx
'use client';

import React, { useRef, useState } from 'react';
import { clsx } from 'clsx';
import { PencilIcon, PlusIcon, TrashIcon, XMarkIcon } from '@heroicons/react/20/solid';
import type { ScreenerRow } from '@/services/screenerV2Api';
import { formatValue } from '@/lib/screenerV2';
import { MAX_LISTS, MAIN_LIST_ID, useWatchlist } from '@/lib/watchlist';
import { ScoreBadge } from '@/components/screener/StrengthScore';

type Mode = { kind: 'idle' } | { kind: 'new' } | { kind: 'rename' } | { kind: 'delete' };

const LIST_ERRORS: Record<string, string> = {
  invalid_name: 'A list name needs 1 to 30 characters.',
  too_many: `Up to ${MAX_LISTS} lists.`,
  last_list: 'The first list cannot be deleted.',
  unsaved: 'This browser is not allowing the watchlist to be saved.',
};

/**
 * The right-hand watchlist panel (TradingView-style): pick / create / rename / delete a list, add symbols, and the rows of
 * the active list with last close and day change from ONE Screener request (passed in). Clicking a row (or arrow keys)
 * selects the symbol shown in the chart.
 */
export default function WatchlistPanel({
  rows,
  session,
  loading,
  unavailable,
  selected,
  onSelect,
}: {
  rows: Record<string, ScreenerRow>;
  session: string | null;
  loading: boolean;
  unavailable: string | null;
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  const wl = useWatchlist();
  const [mode, setMode] = useState<Mode>({ kind: 'idle' });
  const [name, setName] = useState('');
  const [listError, setListError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const listRef = useRef<HTMLUListElement>(null);

  const submitList = (e: React.FormEvent) => {
    e.preventDefault();
    const r = mode.kind === 'new' ? wl.createList(name) : wl.renameList(wl.activeId, name);
    if (r === 'ok') {
      setMode({ kind: 'idle' });
      setListError(null);
    } else setListError(LIST_ERRORS[r]);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const i = selected ? wl.symbols.indexOf(selected) : -1;
    const next = wl.symbols[Math.max(0, Math.min(wl.symbols.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
    if (next) {
      onSelect(next);
      listRef.current?.querySelector<HTMLElement>(`[data-symbol="${next}"]`)?.focus();
    }
  };

  const iconBtn =
    'press inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-black/[0.05] hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-30';

  return (
    <aside className="flex flex-col rounded-2xl border border-black/[0.05] bg-white shadow-sm lg:sticky lg:top-3 lg:max-h-[calc(100vh-1.5rem)]" aria-label="Watchlist">
      {/* list picker */}
      <div className="border-b border-black/[0.05] p-3">
        <div className="flex items-center gap-1">
          <select
            aria-label="Watchlist"
            value={wl.activeId}
            onChange={(e) => {
              wl.setActive(e.target.value);
              setMode({ kind: 'idle' });
            }}
            className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-[14px] font-semibold text-slate-900 focus:border-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          >
            {wl.lists.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} ({l.count})
              </option>
            ))}
          </select>
          <button type="button" className={iconBtn} aria-label="New list" title="New list" disabled={wl.lists.length >= MAX_LISTS} onClick={() => { setMode({ kind: 'new' }); setName(''); setListError(null); }}>
            <PlusIcon className="h-4 w-4" />
          </button>
          <button type="button" className={iconBtn} aria-label="Rename list" title="Rename list" onClick={() => { setMode({ kind: 'rename' }); setName(wl.activeName); setListError(null); }}>
            <PencilIcon className="h-4 w-4" />
          </button>
          <button type="button" className={iconBtn} aria-label="Delete list" title="Delete list" disabled={wl.activeId === MAIN_LIST_ID} onClick={() => { setMode({ kind: 'delete' }); setListError(null); }}>
            <TrashIcon className="h-4 w-4" />
          </button>
        </div>
        {(mode.kind === 'new' || mode.kind === 'rename') && (
          <form className="mt-2 flex gap-1.5" onSubmit={submitList} aria-label={mode.kind === 'new' ? 'New list' : 'Rename list'}>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={30}
              placeholder={mode.kind === 'new' ? 'List name, e.g. Semis' : 'New name'}
              aria-label="List name"
              className="min-w-0 flex-1 rounded-lg border border-slate-300 px-2 py-1 text-[13px] focus:border-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            />
            <button type="submit" className="press rounded-lg bg-slate-900 px-3 text-[13px] font-medium text-white">
              {mode.kind === 'new' ? 'Create' : 'Save'}
            </button>
            <button type="button" className="press rounded-lg px-2 text-[13px] text-slate-500 hover:bg-black/[0.04]" onClick={() => setMode({ kind: 'idle' })}>
              Cancel
            </button>
          </form>
        )}
        {mode.kind === 'delete' && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px]" role="alertdialog" aria-label="Delete list">
            <span className="text-slate-600">Delete &quot;{wl.activeName}&quot; and its {wl.symbols.length} symbols?</span>
            <button
              type="button"
              className="press rounded-lg bg-red-600 px-3 py-1 font-medium text-white"
              onClick={() => {
                const r = wl.deleteList(wl.activeId);
                if (r === 'ok') setMode({ kind: 'idle' });
                else setListError(LIST_ERRORS[r]);
              }}
            >
              Delete
            </button>
            <button type="button" className="press rounded-lg px-2 py-1 text-slate-500 hover:bg-black/[0.04]" onClick={() => setMode({ kind: 'idle' })}>
              Keep
            </button>
          </div>
        )}
        {listError && <p className="mt-1 text-[12px] text-amber-700" role="status">{listError}</p>}

        {/* add symbol */}
        <form
          className="mt-2 flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            const sym = draft.trim().toUpperCase();
            if (wl.add(draft) === 'added') {
              setDraft('');
              onSelect(sym);
            }
          }}
          aria-label="Add a symbol"
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={10}
            placeholder="Add symbol"
            aria-label="Symbol to add"
            className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-[13px] uppercase placeholder:normal-case placeholder:text-slate-400 focus:border-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          />
          <button type="submit" disabled={!draft.trim()} className="press rounded-lg bg-slate-900 px-3 text-[13px] font-medium text-white disabled:opacity-40">
            Add
          </button>
        </form>
        {wl.notice && (
          <p role="status" className="mt-1 text-[12px] text-amber-700" data-testid="watchlist-notice">
            {wl.notice}
          </p>
        )}
      </div>

      {/* rows */}
      <div className="grid grid-cols-[minmax(0,1fr)_2.4rem_5rem_4.5rem_2rem] gap-x-2 border-b border-black/[0.05] px-3 py-1.5 text-[11px] font-medium text-slate-400">
        <span>Symbol</span>
        <span className="text-center" title="Strength Score 0-100">Score</span>
        <span className="text-right">Last</span>
        <span className="text-right">Chg%</span>
        <span className="sr-only">Remove</span>
      </div>
      {wl.symbols.length === 0 ? (
        <p className="px-4 py-8 text-center text-[13px] text-slate-400" data-testid="panel-empty">
          This list is empty. Add a symbol above, or use the star on the Screener or a stock page.
        </p>
      ) : (
        <ul ref={listRef} className="min-h-0 flex-1 overflow-y-auto py-1" onKeyDown={onKeyDown} aria-label={`Symbols in ${wl.activeName}`}>
          {wl.symbols.map((s) => {
            const r = rows[s];
            const chg = r?.change_1d ?? null;
            const on = s === selected;
            return (
              <li key={s} className={clsx('grid grid-cols-[minmax(0,1fr)_2.4rem_5rem_4.5rem_2rem] items-center gap-x-2 px-3', on ? 'bg-accent-soft' : 'hover:bg-black/[0.03]')} data-testid="panel-row">
                <button
                  type="button"
                  data-symbol={s}
                  aria-current={on ? 'true' : undefined}
                  onClick={() => onSelect(s)}
                  className="min-w-0 truncate py-2 text-left text-[13px] font-semibold text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  title={r?.sector ? `${r.sector}${r.industry ? ` · ${r.industry}` : ''}` : undefined}
                >
                  {s}
                  <span className="block truncate text-[11px] font-normal text-slate-400">
                    {r?.industry ?? (unavailable ? '' : session && !loading && !r ? `no bar on ${session}` : '')}
                  </span>
                </button>
                <span className="text-center">{r ? <ScoreBadge row={r} size="sm" /> : <span className="text-[12px] text-slate-300">—</span>}</span>
                <span className="text-right text-[13px] tabular-nums text-slate-800">{r?.close != null ? formatValue(r.close, '$') : loading ? '…' : '—'}</span>
                <span className={clsx('text-right text-[13px] font-semibold tabular-nums', chg == null ? 'text-slate-400' : chg >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                  {chg != null ? formatValue(chg, '%') : loading ? '' : '—'}
                </span>
                <button
                  type="button"
                  aria-label={`Remove ${s} from watchlist`}
                  onClick={() => wl.remove(s)}
                  className="press inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-300 hover:bg-black/[0.06] hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  <XMarkIcon className="h-4 w-4" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <p className="border-t border-black/[0.05] px-3 py-2 text-[11px] text-slate-400" data-testid="panel-footer">
        {unavailable
          ? unavailable
          : session
            ? `Closes for the ${session} session · ${wl.symbols.length} of ${wl.max} · saved in this browser`
            : `${wl.symbols.length} of ${wl.max} · saved in this browser`}
      </p>
    </aside>
  );
}
