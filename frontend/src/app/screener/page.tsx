// File: frontend/src/app/screener/page.tsx
'use client';

import React, { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { clsx } from 'clsx';
import { ArrowDownTrayIcon, ChevronLeftIcon, ChevronRightIcon, StarIcon as StarOutline } from '@heroicons/react/24/outline';
import { StarIcon as StarSolid } from '@heroicons/react/20/solid';
import { screenerV2Api, ScreenerDefinitions, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';
import DataStamp from '@/components/common/DataStamp';
import ErrorAlert from '@/components/common/ErrorAlert';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import SortHeader from '@/components/ui/SortHeader';
import { MultiSelect, TextField } from '@/components/ui/Field';
import { useWatchlist } from '@/lib/watchlist';
import { sectorHref } from '@/lib/sectors';
import {
  COLUMNS, DEFAULT_STATE, FAMILY_LABEL, PAGE_SIZES, RANGE_FILTERS, ScreenState, formatCell, fromSearchParams, ofTotal, toQueryPairs,
} from '@/lib/screenerV2';

const FAMILY_TONE = { observed: 'bg-sky-50 text-sky-700', derived: 'bg-slate-100 text-slate-600', reference: 'bg-amber-50 text-amber-700' } as const;
const SHORT: Partial<Record<keyof ScreenerRow, string>> = {
  close: 'Close', change_1d: '1D', ret_5: '5S', ret_20: '20S', ret_60: '60S', vs_spx_20: 'vs SPX 20', vs_sector_20: 'vs sector 20',
  rs_20: 'RS 20', rs_60: 'RS 60', breakout_20: 'Breakout', channel_pos: 'Chan pos', channel_width_pctile: 'Width pctl',
  atr_pct: 'ATR %', volume_ratio: 'Vol ratio', dollar_vol_20: '$ vol 20', market_cap: 'Mkt cap',
};
const SIGNED = new Set(['change_1d', 'ret_5', 'ret_20', 'ret_60', 'vs_spx_20', 'vs_sector_20']);

/** `useSearchParams()` opts the route out of static prerendering, so Next requires it under a Suspense boundary. */
export default function ScreenerPage() {
  return (
    <Suspense fallback={<div className="flex min-h-screen items-center justify-center bg-slate-50"><LoadingSpinner size="large" /></div>}>
      <Screener />
    </Suspense>
  );
}

/**
 * Smart Screener v2: the whole universe for ONE explicit market session, filtered, sorted and paged on the server.
 * Every column says whether it is observed (captured for the session), derived (from stored daily bars) or reference
 * (fundamentals known by the session), and the header reports coverage, so a gap is visible rather than filled in.
 * The screen's state lives in the URL, so a screen can be shared or bookmarked.
 */
function Screener() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const query = params.toString();
  const state = useMemo(() => fromSearchParams(new URLSearchParams(query)), [query]);
  const [draft, setDraft] = useState<ScreenState>(state);
  const [defs, setDefs] = useState<ScreenerDefinitions | null>(null);
  const [result, setResult] = useState<ScreenerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const watchlist = useWatchlist();

  useEffect(() => setDraft(state), [state]);

  useEffect(() => {
    screenerV2Api.definitions().then(setDefs).catch(() => setDefs(null));
  }, []);

  const pairs = useMemo(() => toQueryPairs(state), [state]);
  const pairsKey = JSON.stringify(pairs);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    screenerV2Api
      .search(JSON.parse(pairsKey))
      .then((r) => {
        if (cancelled) return;
        setResult(r);
        setError(null);
      })
      .catch((e) => {
        if (cancelled) return;
        const status = e?.response?.status;
        setError(
          status === 422
            ? `The screen was refused: ${e?.response?.data?.detail ?? 'invalid parameters'}.`
            : status === 404
              ? 'The screener v2 service is not available on this server yet.'
              : 'Screener data is unavailable right now.',
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pairsKey]);

  const apply = useCallback(
    (next: ScreenState) => {
      const p = new URLSearchParams();
      for (const [k, v] of toQueryPairs(next)) p.append(k, v);
      router.replace(`${pathname}?${p.toString()}`, { scroll: false });
    },
    [router, pathname],
  );

  const metric = useCallback((key: string) => defs?.metrics.find((m) => m.key === key), [defs]);
  const sortBy = (key: string) => apply({ ...state, sort: key, order: state.sort === key && state.order === 'desc' ? 'asc' : 'desc', page: 1 });
  const totalPages = result ? Math.max(1, Math.ceil(result.total / result.page_size)) : 1;

  const exportCsv = async () => {
    setExporting(true);
    try {
      const blob = await screenerV2Api.exportCsv(pairs);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `first-light-screener-${result?.session ?? 'export'}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('The export failed. Please try again.');
    } finally {
      setExporting(false);
    }
  };

  const cov = result?.coverage;
  const maxExport = defs?.limits.max_export_rows ?? 5000;
  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-[1500px] space-y-4 px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-end justify-between gap-3 px-1 pt-2">
          <div>
            <h1 className="large-title">Screener</h1>
            <p className="mt-1.5 text-[15px] text-slate-500">
              The whole universe for one market session: relative strength, returns, channels, volatility and liquidity.
            </p>
            {result && (
              <DataStamp
                className="mt-1"
                label="Session"
                asOf={result.session}
                source={`previous ${result.previous_session ?? '—'} · dollar volume ${result.dollar_volume_window.from ?? '—'} to ${result.dollar_volume_window.to}`}
              />
            )}
          </div>
          {result && result.recent_sessions.length > 0 && (
            <label className="text-xs text-slate-500">
              Session{' '}
              <select
                className="ml-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-[13px] text-slate-800"
                value={state.session ?? ''}
                onChange={(e) => apply({ ...state, session: e.target.value || null, page: 1 })}
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

        {/* Presets */}
        <div className="flex flex-wrap gap-2 px-1" role="group" aria-label="Presets">
          <button
            type="button"
            onClick={() => apply({ ...DEFAULT_STATE, session: state.session })}
            className={clsx('press rounded-full px-3 py-1.5 text-[13px] font-medium', !state.preset ? 'bg-slate-900 text-white' : 'bg-white text-slate-700 shadow-sm hover:bg-slate-100')}
          >
            All stocks
          </button>
          {(defs?.presets ?? []).map((p) => (
            <button
              key={p.key}
              type="button"
              title={p.description}
              onClick={() => apply({ ...DEFAULT_STATE, session: state.session, preset: p.key, sort: p.sort[0], order: p.sort[1] })}
              className={clsx('press rounded-full px-3 py-1.5 text-[13px] font-medium', state.preset === p.key ? 'bg-slate-900 text-white' : 'bg-white text-slate-700 shadow-sm hover:bg-slate-100')}
            >
              {p.label}
            </button>
          ))}
        </div>
        {state.preset && defs && <p className="px-1 text-xs text-slate-500">{defs.presets.find((p) => p.key === state.preset)?.description}</p>}

        {/* Filters */}
        <form
          className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            apply({ ...draft, page: 1 });
          }}
          aria-label="Filters"
        >
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <TextField label="Symbol starts with" value={draft.q} maxLength={10} onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
            <MultiSelect
              className="col-span-2 sm:col-span-1"
              label="Sector"
              options={(result?.sectors ?? []).map((s) => ({ value: s, label: s }))}
              selected={draft.sectors}
              onChange={(sectors) => setDraft({ ...draft, sectors })}
              placeholder="All sectors"
            />
            {RANGE_FILTERS.map((f) => (
              <fieldset key={f.key} className="min-w-0">
                <legend className="mb-1 block text-xs font-medium text-slate-600">
                  {f.label}
                  {f.suffix ? <span className="font-normal text-slate-400"> ({f.suffix})</span> : null}
                </legend>
                <div className="grid grid-cols-2 gap-1">
                  <input
                    aria-label={`${f.label} minimum`}
                    inputMode="decimal"
                    placeholder="min"
                    value={draft.ranges[f.key]?.min ?? ''}
                    onChange={(e) => setDraft({ ...draft, ranges: { ...draft.ranges, [f.key]: { min: e.target.value, max: draft.ranges[f.key]?.max ?? '' } } })}
                    className="w-full min-w-0 rounded-lg border border-slate-300 bg-white px-2 py-[7px] text-[13px] tabular-nums placeholder:text-slate-400 focus:border-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                  />
                  <input
                    aria-label={`${f.label} maximum`}
                    inputMode="decimal"
                    placeholder="max"
                    value={draft.ranges[f.key]?.max ?? ''}
                    onChange={(e) => setDraft({ ...draft, ranges: { ...draft.ranges, [f.key]: { min: draft.ranges[f.key]?.min ?? '', max: e.target.value } } })}
                    className="w-full min-w-0 rounded-lg border border-slate-300 bg-white px-2 py-[7px] text-[13px] tabular-nums placeholder:text-slate-400 focus:border-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                  />
                </div>
              </fieldset>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-[13px] text-slate-700">
              <input type="checkbox" checked={draft.breakout20} onChange={(e) => setDraft({ ...draft, breakout20: e.target.checked })} />
              Closed above the previous 20-day high
            </label>
            <label className="flex items-center gap-2 text-[13px] text-slate-700">
              <input type="checkbox" checked={draft.newHigh20} onChange={(e) => setDraft({ ...draft, newHigh20: e.target.checked })} />
              New 20-day high on the session
            </label>
            <label className="flex min-w-0 max-w-full flex-wrap items-center gap-2 text-[13px] text-slate-700">
              Fundamentals cutoff
              <select
                aria-label="Fundamentals cutoff"
                className="min-w-0 max-w-full rounded-lg border border-slate-300 bg-white px-2 py-1 text-[13px]"
                value={draft.fundamentalsCutoff}
                onChange={(e) => setDraft({ ...draft, fundamentalsCutoff: e.target.value === 'session_close' ? 'session_close' : 'post_session' })}
              >
                <option value="post_session">Post-session reporting (ingested by noon New York next day)</option>
                <option value="session_close">Ingested before the session close</option>
              </select>
            </label>
            <div className="ml-auto flex gap-2">
              <button
                type="button"
                onClick={() => apply({ ...DEFAULT_STATE, session: state.session })}
                className="press rounded-lg px-3 py-1.5 text-[13px] text-slate-600 hover:bg-black/[0.04]"
              >
                Clear
              </button>
              <button type="submit" className="press rounded-lg bg-slate-900 px-4 py-1.5 text-[13px] font-medium text-white">
                Apply filters
              </button>
            </div>
          </div>
        </form>

        {/* Coverage */}
        {result && cov && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 px-1 text-xs text-slate-500" data-testid="coverage">
            <span>
              Universe {result.universe.toLocaleString('en-US')} stocks with a bar on {result.session}
            </span>
            <span>Relative strength (observed): {ofTotal(cov.rs, result.universe)}</span>
            <span>Technicals: {ofTotal(cov.technical, result.universe)}</span>
            <span>
              Dollar volume: {ofTotal(cov.dollar_vol_20, result.universe)}
              {cov.dollar_vol_insufficient ? ` (${cov.dollar_vol_insufficient} with fewer than 20 sessions)` : ''}
            </span>
            <span title={result.fundamentals_basis?.note}>
              Fundamentals (ingestion-time reference, publication time unknown): {ofTotal(cov.fundamentals, result.universe)}
              {cov.fundamentals_after_close ? ` · ${cov.fundamentals_after_close.toLocaleString('en-US')} ingested after the close` : ''}
            </span>
            {cov.rs === 0 && (
              <span className="font-medium text-amber-700" data-testid="no-rs">
                No observed relative-strength snapshot for this session: RS and return columns are unavailable.
              </span>
            )}
          </div>
        )}

        {error && <ErrorAlert title="Screener" message={error} />}

        {/* Results */}
        <div className="rounded-2xl border border-black/[0.05] bg-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
            <p className="text-sm text-slate-600" aria-live="polite">
              {loading ? 'Loading…' : result ? `${result.total.toLocaleString('en-US')} matching stocks` : ''}
            </p>
            {watchlist.notice && (
              <p role="status" className="text-[13px] text-amber-700" data-testid="watchlist-notice">
                {watchlist.notice}{' '}
                <Link href="/watchlist" className="font-medium text-accent hover:underline">
                  Open watchlist
                </Link>
              </p>
            )}
            <button
              type="button"
              onClick={exportCsv}
              disabled={!result || exporting || result.total === 0}
              className="press inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium text-accent hover:bg-black/[0.04] disabled:opacity-40"
            >
              <ArrowDownTrayIcon className="h-4 w-4" /> {exporting ? 'Exporting…' : `CSV${result && result.total > maxExport ? ` (first ${maxExport.toLocaleString('en-US')})` : ''}`}
            </button>
          </div>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[1280px] text-[13px]">
              <thead className="bg-slate-50/70 text-[11px] text-slate-500">
                <tr>
                  <th className="w-8 px-2 py-2">
                    <span className="sr-only">Watchlist</span>
                  </th>
                  <th className="sticky left-0 z-10 bg-slate-50 px-3 py-2 text-left">
                    <SortHeader label="Symbol" active={state.sort === 'symbol'} dir={state.sort === 'symbol' ? state.order : null} onClick={() => sortBy('symbol')} />
                  </th>
                  <th className="px-3 py-2 text-left">
                    <SortHeader label="Sector" active={state.sort === 'sector'} dir={state.sort === 'sector' ? state.order : null} onClick={() => sortBy('sector')} />
                  </th>
                  {COLUMNS.map((c) => {
                    const m = metric(c as string);
                    return (
                      <th key={c} className="whitespace-nowrap px-3 py-2 text-right font-medium" title={m ? `${m.label} (${FAMILY_LABEL[m.family].toLowerCase()}): ${m.definition}` : undefined}>
                        <SortHeader label={SHORT[c] ?? String(c)} active={state.sort === c} dir={state.sort === c ? state.order : null} onClick={() => sortBy(c as string)} align="end" />
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {(result?.rows ?? []).map((r) => (
                  <tr key={r.symbol} className="border-t border-black/[0.05] hover:bg-black/[0.015]" data-testid="screener-row">
                    <td className="px-2 py-1.5 text-center">
                      <button
                        type="button"
                        onClick={() => watchlist.toggle(r.symbol)}
                        aria-pressed={watchlist.has(r.symbol)}
                        aria-label={watchlist.has(r.symbol) ? `Remove ${r.symbol} from watchlist` : `Add ${r.symbol} to watchlist`}
                        className="press inline-flex h-7 w-7 items-center justify-center rounded-full hover:bg-black/[0.05]"
                      >
                        {watchlist.has(r.symbol) ? <StarSolid className="h-4 w-4 text-amber-400" /> : <StarOutline className="h-4 w-4 text-slate-400" />}
                      </button>
                    </td>
                    <td className="sticky left-0 z-10 bg-white px-3 py-1.5 font-semibold">
                      <Link href={`/stock/${r.symbol}`} className="text-slate-900 hover:text-accent hover:underline">
                        {r.symbol}
                      </Link>
                    </td>
                    <td
                      className="max-w-[160px] truncate px-3 py-1.5 text-slate-600"
                      title={r.sector_source === 'reference' ? 'Sector from the fundamentals snapshot (no observed sector for this session)' : undefined}
                    >
                      {r.sector && sectorHref(r.sector) ? (
                        <Link href={sectorHref(r.sector)!} className="hover:text-accent hover:underline">
                          {r.sector}
                        </Link>
                      ) : (
                        r.sector ?? '—'
                      )}
                    </td>
                    {COLUMNS.map((c) => {
                      const m = metric(c as string);
                      const text = formatCell(r, c, m?.unit ?? '');
                      const v = r[c];
                      return (
                        <td
                          key={c}
                          className={clsx(
                            'whitespace-nowrap px-3 py-1.5 text-right tabular-nums',
                            text === '—' ? 'text-slate-400' : SIGNED.has(c as string) && typeof v === 'number' ? (v >= 0 ? 'text-emerald-600' : 'text-red-600') : 'text-slate-700',
                          )}
                          title={
                            c === 'dollar_vol_20' && v == null
                              ? `${r.dv_bars} of 20 sessions with bars`
                              : c === 'market_cap' && r.fundamentals_ingested_at
                                ? `Fundamentals row ${r.fundamentals_date} ingested ${r.fundamentals_ingested_at}${r.fundamentals_after_close ? ' (after the close: post-session convention)' : ''}; publication time unknown`
                                : undefined
                          }
                        >
                          {text}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {result && result.rows.length === 0 && (
                  <tr>
                    <td colSpan={COLUMNS.length + 3} className="py-10 text-center text-sm text-slate-400">
                      No stocks match these filters for {result.session}.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {result && result.total > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-black/[0.06] px-4 py-2.5 text-[13px] text-slate-600">
              <label className="flex items-center gap-2">
                Rows per page
                <select
                  className="rounded-lg border border-slate-300 bg-white px-2 py-1"
                  value={state.pageSize}
                  onChange={(e) => apply({ ...state, pageSize: Number(e.target.value), page: 1 })}
                  aria-label="Rows per page"
                >
                  {PAGE_SIZES.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  aria-label="Previous page"
                  disabled={state.page <= 1}
                  onClick={() => apply({ ...state, page: state.page - 1 })}
                  className="press rounded-lg p-1.5 hover:bg-black/[0.04] disabled:opacity-30"
                >
                  <ChevronLeftIcon className="h-4 w-4" />
                </button>
                <span>
                  Page {state.page} of {totalPages}
                </span>
                <button
                  type="button"
                  aria-label="Next page"
                  disabled={state.page >= totalPages}
                  onClick={() => apply({ ...state, page: state.page + 1 })}
                  className="press rounded-lg p-1.5 hover:bg-black/[0.04] disabled:opacity-30"
                >
                  <ChevronRightIcon className="h-4 w-4" />
                </button>
              </div>
            </div>
          )}
        </div>

        {defs && (
          <details className="rounded-2xl border border-black/[0.05] bg-white p-4 shadow-sm" data-testid="definitions">
            <summary className="cursor-pointer text-[13px] font-medium text-slate-700">Metric definitions and data families</summary>
            <ul className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 text-xs text-slate-600 md:grid-cols-2">
              {defs.metrics.map((m) => (
                <li key={m.key}>
                  <span className={clsx('mr-1.5 rounded-full px-1.5 py-0.5 text-[10px] font-semibold', FAMILY_TONE[m.family])}>{FAMILY_LABEL[m.family]}</span>
                  <span className="font-medium text-slate-800">{m.label}</span>: {m.definition}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs leading-relaxed text-slate-500">
              <span className="font-medium">Observed</span>: {defs.families.observed} <span className="font-medium">Derived</span>: {defs.families.derived}{' '}
              <span className="font-medium">Reference</span>: {defs.families.reference} A dash means the value is unavailable for this session.
            </p>
          </details>
        )}
      </div>
    </div>
  );
}
