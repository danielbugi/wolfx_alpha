'use client';

// /signals/{SYMBOL}/{quarter_end}/{type}: one public Fundamental Update, at its permanent natural-key URL. Open to everyone.
import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { UpdateDetail } from '@/components/signals/UpdateView';
import { PublicApiError, PublicDetail, publicSignalsApi } from '@/services/publicSignalsApi';
import { parseSignalParams } from '@/lib/signals';

type State = { kind: 'loading' } | { kind: 'ready'; data: PublicDetail } | { kind: 'missing' } | { kind: 'error'; text: string };

export default function SignalPage() {
  const params = useParams<{ symbol: string; quarter: string; type: string }>();
  const parsed = parseSignalParams(params?.symbol ?? '', params?.quarter ?? '', params?.type ?? '');
  const [state, setState] = useState<State>(parsed ? { kind: 'loading' } : { kind: 'missing' });

  const symbol = parsed?.symbol, quarter = parsed?.quarter, slug = parsed?.slug;
  useEffect(() => {
    if (!symbol || !quarter || !slug) {
      setState({ kind: 'missing' });
      return;
    }
    const ctrl = new AbortController();
    setState({ kind: 'loading' });
    publicSignalsApi.detail(symbol, quarter, slug, ctrl.signal)
      .then((data) => setState({ kind: 'ready', data }))
      .catch((e) => {
        if ((e as Error).name === 'AbortError') return;
        if (e instanceof PublicApiError && (e.status === 404 || e.status === 422)) setState({ kind: 'missing' });
        else if (e instanceof PublicApiError && e.status === 429) setState({ kind: 'error', text: 'Too many requests. Wait a minute and try again.' });
        else setState({ kind: 'error', text: 'Could not load this update. Try again later.' });
      });
    return () => ctrl.abort();
  }, [symbol, quarter, slug]);

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-6">
      <Link href="/signals" className="text-sm text-slate-500 hover:text-slate-800">← All Fundamental Updates</Link>
      <div className="mt-4">
        {state.kind === 'loading' && <p className="text-sm text-slate-500">Loading…</p>}
        {state.kind === 'missing' && (
          <div className="space-y-1">
            <h1 className="text-xl font-semibold text-slate-900">Update not found</h1>
            <p className="text-sm text-slate-600">This address does not match a published Fundamental Update.</p>
          </div>
        )}
        {state.kind === 'error' && <p role="alert" className="text-sm text-red-700">{state.text}</p>}
        {state.kind === 'ready' && (
          <UpdateDetail u={state.data.update} status={state.data.status} corrections={state.data.corrections} firstPublishedAt={state.data.first_published_at} />
        )}
      </div>
    </div>
  );
}
