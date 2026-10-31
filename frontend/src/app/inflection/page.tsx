'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { inflectionApi, InflectionEvent, InflectionQuery, InflectionSummary } from '@/services/inflectionApi';
import { DASH, fmtMoney } from '@/lib/inflectionFormat';
import EventCard from '@/components/inflection/EventCard';
import { usePagePerf } from '@/hooks/usePagePerf';

const TYPES: { value: string; label: string }[] = [
  { value: 'profitability_turnaround', label: 'Profitability turnaround' },
  { value: 'revenue_acceleration', label: 'Revenue acceleration' },
  { value: 'margin_expansion', label: 'Margin expansion' },
  { value: 'operating_leverage_inflection', label: 'Operating leverage' },
  { value: 'fcf_turnaround', label: 'FCF turnaround' },
  { value: 'improvement_with_limited_repricing', label: 'Limited repricing' },
];
const SECTORS = ['Technology', 'Healthcare', 'Financial Services', 'Consumer Cyclical', 'Industrials', 'Communication Services', 'Consumer Defensive', 'Energy', 'Basic Materials', 'Real Estate', 'Utilities'];
const STRENGTHS = [{ v: 0, l: 'Any strength' }, { v: 55, l: '55+ (medium)' }, { v: 75, l: '75+ (high)' }];
const AGES = [{ v: 7, l: 'Last 7 days' }, { v: 30, l: 'Last 30 days' }, { v: 90, l: 'Last 90 days' }, { v: 365, l: 'Last year' }];
const CAPS = [{ v: 0, l: 'Any size' }, { v: 3e8, l: '> $300M' }, { v: 2e9, l: '> $2B' }, { v: 1e10, l: '> $10B' }];
const SORTS: { v: NonNullable<InflectionQuery['sort']>; l: string }[] = [{ v: 'strength', l: 'Strongest' }, { v: 'available_at', l: 'Newest' }, { v: 'score', l: 'Highest score' }];
const PAGE = 30;

const sel = 'h-11 rounded-lg border border-slate-200 bg-white px-2 text-[15px] text-slate-800';
const dayStr = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 10);

export default function InflectionPage() {
  const { markLoaded } = usePagePerf('/inflection');
  const [types, setTypes] = useState<string[]>([]);
  const [sector, setSector] = useState('');
  const [minStrength, setMinStrength] = useState(0);
  const [days, setDays] = useState(90);
  const [cap, setCap] = useState(0);
  const [sort, setSort] = useState<NonNullable<InflectionQuery['sort']>>('strength');
  const [symbol, setSymbol] = useState('');
  const [events, setEvents] = useState<InflectionEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [summary, setSummary] = useState<InflectionSummary | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [more, setMore] = useState(false);
  const seq = useRef(0);

  const query = useMemo<InflectionQuery>(() => {
    const q: InflectionQuery = { sort, since: dayStr(days), limit: PAGE };
    if (types.length) q.event_type = types;
    if (sector) q.sector = sector;
    if (minStrength) q.min_strength = minStrength;
    if (cap) q.min_market_cap = cap;
    if (symbol.trim()) q.q = symbol.trim().toUpperCase();
    return q;
  }, [types, sector, minStrength, days, cap, sort, symbol]);

  const load = useCallback(
    async (offset: number) => {
      const my = ++seq.current;
      try {
        offset === 0 ? setState('loading') : setMore(true);
        const r = await inflectionApi.events({ ...query, offset });
        if (my !== seq.current) return;
        setEvents((cur) => (offset === 0 ? r.events : [...cur, ...r.events]));
        setTotal(r.total);
        setAvailable(r.available !== false);
        setReason(r.reason ?? null);
        setState('ready');
        if (offset === 0) markLoaded();
      } catch {
        if (my === seq.current) setState('error');
      } finally {
        if (my === seq.current) setMore(false);
      }
    },
    [query, markLoaded],
  );

  useEffect(() => {
    const t = setTimeout(() => load(0), symbol ? 250 : 0);
    return () => clearTimeout(t);
  }, [load, symbol]);

  useEffect(() => {
    inflectionApi.summary(90).then(setSummary).catch(() => setSummary(null));
  }, []);

  const toggle = (v: string) => setTypes((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]));

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <header>
        <h1 className="text-2xl font-bold text-slate-800">Fundamental inflection</h1>
        <p className="mt-1 text-sm text-slate-600">
          Companies whose fundamentals just turned: revenue accelerating, margins widening, profits or free cash flow turning positive. <strong>Research signals</strong>, not recommendations:
          rule-based and explainable, and not shown to predict returns.
        </p>
      </header>

      {summary?.available && (
        <div className="rounded-xl border border-slate-200 bg-white p-3" data-testid="inflection-summary">
          <p className="text-xs text-slate-500">
            {summary.events.toLocaleString('en-US')} events from {summary.features.toLocaleString('en-US')} analysed company-quarters · newest {summary.newest_event ?? DASH}
            {summary.last_run ? ` · last run ${summary.last_run.status} (as of ${summary.last_run.asof}, ${summary.last_run.errors} errors)` : ''}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {summary.by_type.map((t) => (
              <span key={t.event_type} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-700">
                {t.label}: {t.total} ({t.high} high)
              </span>
            ))}
          </div>
        </div>
      )}

      <section aria-label="Filters" className="space-y-2 rounded-xl border border-slate-200 bg-white p-3">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Event type">
          {TYPES.map((t) => (
            <button
              key={t.value}
              type="button"
              aria-pressed={types.includes(t.value)}
              onClick={() => toggle(t.value)}
              className={clsx('min-h-[36px] rounded-full border px-3 text-xs font-medium', types.includes(t.value) ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-700')}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <select aria-label="Sector" className={sel} value={sector} onChange={(e) => setSector(e.target.value)}>
            <option value="">All sectors</option>
            {SECTORS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select aria-label="Minimum strength" className={sel} value={minStrength} onChange={(e) => setMinStrength(Number(e.target.value))}>
            {STRENGTHS.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
          </select>
          <select aria-label="Made public" className={sel} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {AGES.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
          </select>
          <select aria-label="Market cap" className={sel} value={cap} onChange={(e) => setCap(Number(e.target.value))}>
            {CAPS.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
          </select>
          <select aria-label="Sort" className={sel} value={sort} onChange={(e) => setSort(e.target.value as NonNullable<InflectionQuery['sort']>)}>
            {SORTS.map((s) => <option key={s.v} value={s.v}>{s.l}</option>)}
          </select>
          <input aria-label="Symbol" placeholder="Symbol" inputMode="text" autoCapitalize="characters" value={symbol} onChange={(e) => setSymbol(e.target.value.replace(/[^A-Za-z.-]/g, ''))} className={sel} />
        </div>
      </section>

      {state === 'loading' && <p className="py-10 text-center text-sm text-slate-400">Loading…</p>}
      {state === 'error' && <p className="py-10 text-center text-sm text-red-500">The inflection events could not be loaded. Try again in a moment.</p>}
      {state === 'ready' && available === false && (
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          {reason ?? 'The inflection engine has not produced data yet.'} It starts filling once its database tables are installed and the nightly step has run.
        </p>
      )}
      {state === 'ready' && available !== false && events.length === 0 && (
        <p className="py-10 text-center text-sm text-slate-500">No inflection events match these filters. Widen the date range or lower the strength.</p>
      )}

      {state === 'ready' && events.length > 0 && (
        <>
          <p className="text-xs text-slate-500" aria-live="polite">
            Showing {events.length} of {total.toLocaleString('en-US')} events{cap ? ` · market cap above ${fmtMoney(cap)}` : ''}
          </p>
          <div className="space-y-3">
            {events.map((e) => (
              <EventCard key={e.id} e={e} />
            ))}
          </div>
          {events.length < total && (
            <div className="flex justify-center">
              <button type="button" onClick={() => load(events.length)} disabled={more} className="min-h-[44px] rounded-xl bg-slate-900 px-5 text-sm font-semibold text-white disabled:opacity-50">
                {more ? 'Loading…' : 'Show more'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
