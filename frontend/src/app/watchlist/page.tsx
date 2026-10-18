// File: frontend/src/app/watchlist/page.tsx
'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import { XMarkIcon } from '@heroicons/react/20/solid';
import { screenerV2Api, ScreenerDefinitions, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';
import DataStamp from '@/components/common/DataStamp';
import SortHeader from '@/components/ui/SortHeader';
import { useWatchlist } from '@/lib/watchlist';
import { sectorHref } from '@/lib/sectors';
import { FAMILY_LABEL, formatCell } from '@/lib/screenerV2';
import {
  COMPARE_MAX, COMPARE_ROWS, WATCH_COLUMNS, WATCH_SHORT, WATCH_SIGNED, WatchSort, sortSymbols, staleness, watchlistPairs,
} from '@/lib/watchlistView';

const todayIso = () => new Date().toISOString().slice(0, 10);

/**
 * Watchlist: the stocks saved in this browser, with the Screener's own metrics for one market session (one request,
 * exact-symbol filter on the Screener query, so every value, missing state and provenance matches the Screener and the
 * stock page). Saved symbols stay visible and removable when the analytics are unavailable; a failed request never
 * changes the list.
 */
export default function WatchlistPage() {
  const watchlist = useWatchlist();
  const { symbols } = watchlist;
  const [session, setSession] = useState<string | null>(null);
  const [defs, setDefs] = useState<ScreenerDefinitions | null>(null);
  const [result, setResult] = useState<ScreenerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sort, setSort] = useState<WatchSort>({ key: 'added', order: 'desc' });
  const [compare, setCompare] = useState<string[]>([]);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    screenerV2Api.definitions().then(setDefs).catch(() => setDefs(null));
  }, []);

  const key = JSON.stringify([symbols, session]);
  useEffect(() => {
    const [syms, sess] = JSON.parse(key) as [string[], string | null];
    if (syms.length === 0) {
      setResult(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    screenerV2Api
      .search(watchlistPairs(syms, sess))
      .then((r) => {
        if (cancelled) return;
        setResult(r);
        setError(null);
      })
      .catch((e) => {
        if (cancelled) return;
        const status = e?.response?.status;
        setResult(null);
        setError(
          status === 404 || status === 422
            ? 'Watchlist analytics are not available on this server yet.'
            : 'Watchlist analytics are unavailable right now.',
        );
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [key]);

  // keep the comparison to symbols still saved
  useEffect(() => setCompare((c) => c.filter((s) => symbols.includes(s))), [symbols]);

  const rows = useMemo(() => Object.fromEntries((result?.rows ?? []).map((r) => [r.symbol, r])) as Record<string, ScreenerRow>, [result]);
  const ordered = useMemo(() => sortSymbols(symbols, rows, sort), [symbols, rows, sort]);
  const metric = (k: string) => defs?.metrics.find((m) => m.key === k);
  const sortBy = (k: WatchSort['key']) => setSort((s) => ({ key: k, order: s.key === k && s.order === 'desc' ? 'asc' : 'desc' }));
  const stale = result ? staleness(result.session, result.recent_sessions[0] ?? null, todayIso()) : null;
  const toggleCompare = (s: string) =>
    setCompare((c) => (c.includes(s) ? c.filter((x) => x !== s) : c.length >= COMPARE_MAX ? c : [...c, s]));

  const cell = (r: ScreenerRow | undefined, c: keyof ScreenerRow) => {
    if (!r) return { text: '—', tone: 'text-slate-400' };
    const text = formatCell(r, c, metric(c as string)?.unit ?? '');
    const v = r[c];
    const tone = text === '—' ? 'text-slate-400' : WATCH_SIGNED.has(c as string) && typeof v === 'number' ? (v >= 0 ? 'text-emerald-600' : 'text-red-600') : 'text-slate-700';
    return { text, tone };
  };
  const missingNote = (s: string) =>
    error ? 'analytics unavailable' : result && !rows[s] ? `no bar on the ${result.session} session` : null;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-[1500px] space-y-4 px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-end justify-between gap-3 px-1 pt-2">
          <div className="min-w-0">
            <h1 className="large-title">Watchlist</h1>
            <p className="mt-1.5 text-[15px] text-slate-500">
              Stocks you saved in this browser ({symbols.length} of {watchlist.max}), with the Screener&apos;s numbers for one market session.
            </p>
            {result && <DataStamp className="mt-1" label="Session" asOf={result.session} source="same data as the Screener" stale={!!stale?.notUpdatedDays} />}
          </div>
          {result && result.recent_sessions.length > 0 && (
            <label className="text-xs text-slate-500">
              Session{' '}
              <select
                className="ml-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-[13px] text-slate-800"
                value={session ?? ''}
                onChange={(e) => setSession(e.target.value || null)}
                aria-label="Market session"
              >
                <option value="">Newest ({result.recent_sessions[0]})</option>
                {result.recent_sessions.slice(1).map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <form
          className="flex flex-wrap items-center gap-2 px-1"
          onSubmit={(e) => {
            e.preventDefault();
            if (watchlist.add(draft) === 'added') setDraft('');
          }}
          aria-label="Add a symbol"
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={10}
            placeholder="Add a symbol, e.g. MSFT"
            aria-label="Symbol to add"
            className="w-48 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] uppercase placeholder:normal-case placeholder:text-slate-400 focus:border-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          />
          <button type="submit" disabled={!draft.trim()} className="press rounded-lg bg-slate-900 px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-40">
            Add
          </button>
          {watchlist.notice && (
            <p role="status" className="text-[13px] text-amber-700" data-testid="watchlist-notice">
              {watchlist.notice}
            </p>
          )}
        </form>

        {stale?.olderSession && (
          <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="older-session">
            You are viewing the {result!.session} session; the newest is {result!.recent_sessions[0]}.
          </p>
        )}
        {stale?.notUpdatedDays != null && (
          <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="not-updated">
            The newest session with metrics is {result!.recent_sessions[0]} ({stale.notUpdatedDays} days ago); the data has not been updated since.
          </p>
        )}
        {result && result.coverage.rs === 0 && (
          <p className="rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="no-rs">
            No observed relative-strength snapshot exists for this session, so relative strength and returns are unavailable.
          </p>
        )}
        {error && (
          <p className="rounded-xl bg-slate-100 px-3 py-2 text-[13px] text-slate-600" role="alert" data-testid="analytics-error">
            {error} Your saved symbols are kept and can still be opened or removed.
          </p>
        )}

        {symbols.length === 0 ? (
          <div className="rounded-2xl border border-black/[0.05] bg-white p-8 text-center shadow-sm" data-testid="watchlist-empty">
            <p className="text-[15px] font-medium text-slate-800">Your watchlist is empty</p>
            <p className="mt-1 text-sm text-slate-500">
              Use the star on the <Link href="/screener" className="text-accent hover:underline">Screener</Link> or on a stock page, or add a symbol above. Up to {watchlist.max} stocks, saved in this browser only.
            </p>
          </div>
        ) : (
          <div className="rounded-2xl border border-black/[0.05] bg-white shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm text-slate-600">
              <p aria-live="polite">{loading ? 'Loading…' : `${symbols.length} saved · ${result ? `${result.rows.length} with data for ${result.session}` : 'no analytics'}`}</p>
              <div className="flex items-center gap-3">
                {sort.key !== 'added' && (
                  <button type="button" onClick={() => setSort({ key: 'added', order: 'desc' })} className="press rounded-lg px-2 py-1 text-xs text-accent hover:bg-black/[0.04]">
                    Back to saved order
                  </button>
                )}
                <p className="text-xs text-slate-400">Tick up to {COMPARE_MAX} stocks to compare them side by side.</p>
              </div>
            </div>
            <div className="relative overflow-x-auto">
              <table className="w-full min-w-[1180px] text-[13px]">
                <thead className="bg-slate-50/70 text-[11px] text-slate-500">
                  <tr>
                    <th className="w-8 px-2 py-2">
                      <span className="sr-only">Compare</span>
                    </th>
                    <th className="sticky left-0 z-10 bg-slate-50 px-3 py-2 text-left">
                      <SortHeader label="Symbol" active={sort.key === 'symbol'} dir={sort.key === 'symbol' ? sort.order : null} onClick={() => sortBy('symbol')} />
                    </th>
                    <th className="px-3 py-2 text-left">
                      <SortHeader label="Sector · industry" active={sort.key === 'sector'} dir={sort.key === 'sector' ? sort.order : null} onClick={() => sortBy('sector')} />
                    </th>
                    {WATCH_COLUMNS.map((c) => {
                      const m = metric(c as string);
                      return (
                        <th key={c} className="whitespace-nowrap px-3 py-2 text-right font-medium" title={m ? `${m.label} (${FAMILY_LABEL[m.family].toLowerCase()}): ${m.definition}` : undefined}>
                          <SortHeader label={WATCH_SHORT[c] ?? String(c)} active={sort.key === c} dir={sort.key === c ? sort.order : null} onClick={() => sortBy(c)} align="end" />
                        </th>
                      );
                    })}
                    <th className="px-3 py-2">
                      <span className="sr-only">Remove</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {ordered.map((s) => {
                    const r = rows[s];
                    const note = missingNote(s);
                    return (
                      <tr key={s} className="border-t border-black/[0.05] hover:bg-black/[0.015]" data-testid="watchlist-row">
                        <td className="px-2 py-1 text-center">
                          <input
                            type="checkbox"
                            className="h-5 w-5 cursor-pointer accent-slate-900"
                            aria-label={`Compare ${s}`}
                            checked={compare.includes(s)}
                            disabled={!compare.includes(s) && compare.length >= COMPARE_MAX}
                            onChange={() => toggleCompare(s)}
                          />
                        </td>
                        <td className="sticky left-0 z-10 bg-white px-3 py-1.5 font-semibold">
                          <Link href={`/stock/${s}`} className="text-slate-900 hover:text-accent hover:underline">
                            {s}
                          </Link>
                        </td>
                        <td className="max-w-[220px] truncate px-3 py-1.5 text-slate-600">
                          {r ? (
                            <>
                              {r.sector && sectorHref(r.sector) ? (
                                <Link href={sectorHref(r.sector)!} className="hover:text-accent hover:underline">
                                  {r.sector}
                                </Link>
                              ) : (
                                r.sector ?? '—'
                              )}
                              {r.industry ? <span className="text-slate-400"> · {r.industry}</span> : null}
                            </>
                          ) : (
                            <span className="text-slate-400">{note ?? '…'}</span>
                          )}
                        </td>
                        {WATCH_COLUMNS.map((c) => {
                          const { text, tone } = cell(r, c);
                          return (
                            <td key={c} className={clsx('whitespace-nowrap px-3 py-1.5 text-right tabular-nums', tone)} title={c === 'dollar_vol_20' && r && r.dollar_vol_20 == null ? `${r.dv_bars} of 20 sessions with bars` : undefined}>
                              {text}
                            </td>
                          );
                        })}
                        <td className="px-3 py-1.5 text-right">
                          <button
                            type="button"
                            aria-label={`Remove ${s} from watchlist`}
                            onClick={() => watchlist.remove(s)}
                            className="press inline-flex h-7 w-7 items-center justify-center rounded-full text-slate-400 hover:bg-black/[0.06] hover:text-slate-700"
                          >
                            <XMarkIcon className="h-4 w-4" />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {compare.length > 0 && (
          <section className="rounded-2xl border border-black/[0.05] bg-white shadow-sm" aria-labelledby="compare-title" data-testid="compare">
            <div className="flex items-center justify-between px-4 py-3">
              <h2 id="compare-title" className="text-[15px] font-semibold text-slate-900">
                Side by side{result ? ` · session ${result.session}` : ''}
              </h2>
              <button type="button" onClick={() => setCompare([])} className="press rounded-lg px-2 py-1 text-[13px] text-slate-500 hover:bg-black/[0.04]">
                Clear
              </button>
            </div>
            <div className="relative overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead className="bg-slate-50/70 text-[11px] text-slate-500">
                  <tr>
                    <th className="sticky left-0 z-10 bg-slate-50 px-3 py-2 text-left font-medium">Metric</th>
                    {compare.map((s) => (
                      <th key={s} className="min-w-[76px] px-3 py-2 text-right font-semibold text-slate-800">
                        <Link href={`/stock/${s}`} className="hover:text-accent hover:underline">
                          {s}
                        </Link>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {COMPARE_ROWS.map((g) => (
                    <React.Fragment key={g.group}>
                      <tr className="border-t border-black/[0.05]">
                        <td colSpan={compare.length + 1} className="sticky left-0 bg-white px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                          {g.group}
                        </td>
                      </tr>
                      {g.keys.map((k) => (
                        <tr key={k} className="border-t border-black/[0.03]">
                          <td className="sticky left-0 z-10 max-w-[150px] bg-white px-3 py-1.5 text-slate-600" title={metric(k as string)?.definition}>
                            {metric(k as string)?.label ?? String(k)}
                          </td>
                          {compare.map((s) => {
                            const { text, tone } = cell(rows[s], k);
                            return (
                              <td key={s} className={clsx('whitespace-nowrap px-3 py-1.5 text-right tabular-nums', tone)}>
                                {text}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="px-4 pb-3 pt-2 text-[11px] text-slate-400">
              Values are the Screener&apos;s, unchanged; — means unavailable for this session. The 200-day average is on each stock page.
            </p>
          </section>
        )}
      </div>
    </div>
  );
}
