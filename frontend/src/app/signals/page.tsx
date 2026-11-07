'use client';

// /signals: the public feed of Fundamental Updates, newest first. Open to everyone (see lib/signals.ts OPEN_PATH_PREFIXES and AuthGate).
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { UpdateSummary } from '@/components/signals/UpdateView';
import { PublicApiError, PublicFeedItem, publicSignalsApi } from '@/services/publicSignalsApi';
import { SECTORS, UPDATE_TYPES } from '@/lib/signals';

const PAGE = 20;

function message(e: unknown): string {
  if (e instanceof PublicApiError && e.status === 429) return 'Too many requests. Wait a minute and try again.';
  if (e instanceof PublicApiError && e.status === 503) return 'Fundamental Updates are not available right now.';
  return 'Could not load updates. Check your connection and try again.';
}

export default function SignalsPage() {
  const [types, setTypes] = useState<string[]>([]);
  const [sector, setSector] = useState<string>('');
  const [items, setItems] = useState<PublicFeedItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'more' | 'error'>('loading');
  const [error, setError] = useState('');
  const req = useRef<AbortController | null>(null);

  const load = useCallback(async (append: boolean, after: string | null) => {
    req.current?.abort();
    const ctrl = new AbortController();
    req.current = ctrl;
    setState(append ? 'more' : 'loading');
    try {
      const page = await publicSignalsApi.feed({ limit: PAGE, cursor: after, types, sector: sector || null }, ctrl.signal);
      setItems((prev) => (append ? [...prev, ...page.items] : page.items));
      setCursor(page.next_cursor);
      setState('ready');
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setError(message(e));
      setState('error');
    }
  }, [types, sector]);

  useEffect(() => {
    load(false, null);
    return () => req.current?.abort();
  }, [load]);

  const toggle = (slug: string) => setTypes((t) => (t.includes(slug) ? t.filter((x) => x !== slug) : [...t, slug]));

  return (
    <div className="mx-auto w-full max-w-3xl space-y-5 px-4 py-6">
      <header className="space-y-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-700">First Light</p>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">Fundamental Updates</h1>
        <p className="max-w-prose text-sm text-slate-600">
          Changes in what companies report: revenue growth, margins, net income and cash flow, each dated by when the figures became public and linked to the filing.
        </p>
      </header>

      <div className="space-y-2" role="group" aria-label="Filters">
        <div className="flex flex-wrap gap-2">
          {UPDATE_TYPES.map((t) => (
            <button key={t.slug} id={`type-${t.slug}`} type="button" aria-pressed={types.includes(t.slug)} onClick={() => toggle(t.slug)}
                    className={clsx('rounded-full border px-3 py-1.5 text-xs font-medium',
                      types.includes(t.slug) ? 'border-amber-600 bg-amber-50 text-amber-800' : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300')}>
              {t.label}
            </button>
          ))}
        </div>
        <label htmlFor="sector" className="sr-only">Sector</label>
        <select id="sector" value={sector} onChange={(e) => setSector(e.target.value)}
                className="h-10 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-800">
          <option value="">All sectors</option>
          {SECTORS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {state === 'loading' && <p className="text-sm text-slate-500">Loading updates…</p>}
      {state === 'error' && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {state !== 'loading' && state !== 'error' && items.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">
          No updates match these filters yet.
        </p>
      )}

      <div className="space-y-3">
        {items.map((i) => <UpdateSummary key={i.update.key} u={i.update} status={i.status} />)}
      </div>

      {cursor && state !== 'loading' && (
        <button type="button" onClick={() => load(true, cursor)} disabled={state === 'more'}
                className="w-full rounded-lg border border-slate-200 bg-white py-2.5 text-sm font-medium text-slate-700 hover:border-slate-300 disabled:opacity-60">
          {state === 'more' ? 'Loading…' : 'Show older updates'}
        </button>
      )}
    </div>
  );
}
