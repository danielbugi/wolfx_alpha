'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Card, CardBody } from '@nextui-org/react';
import { clsx } from 'clsx';
import { apiClient } from '@/services/api';
import SegmentedControl from '@/components/ui/SegmentedControl';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  fmtChange,
  formatInstrumentPrice,
  Instrument,
  InstrumentsResponse,
  unitSuffix,
  WINDOWS,
} from '@/lib/instruments';

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="h-7 w-24" />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 96},${26 - ((v - min) / span) * 24}`).join(' ');
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox="0 0 96 28" className="h-7 w-24" aria-hidden>
      <polyline points={pts} fill="none" stroke={up ? '#10b981' : '#ef4444'} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function Change({ value, className }: { value: number | null; className?: string }) {
  return (
    <span className={clsx('tabular-nums', value == null ? 'text-slate-400' : value >= 0 ? 'text-emerald-600' : 'text-red-600', className)}>
      {fmtChange(value)}
    </span>
  );
}

function Row({ inst, asOf }: { inst: Instrument; asOf: string | null }) {
  const stale = inst.latest_date && asOf && inst.latest_date < asOf;
  return (
    <Link
      href={`/markets/${encodeURIComponent(inst.symbol)}`}
      className="grid grid-cols-[minmax(0,1fr)_auto_4.5rem] items-center gap-x-4 px-5 py-2.5 transition-colors hover:bg-black/[0.025] md:grid-cols-[minmax(0,1fr)_8.5rem_repeat(6,4.5rem)_6rem]"
    >
      <div className="min-w-0">
        <p className="truncate text-[14px] font-semibold text-slate-900">{inst.name}</p>
        <p className="truncate text-[11px] text-slate-400">
          {inst.symbol}
          {stale && ` · last close ${inst.latest_date}`}
        </p>
      </div>
      <p className="text-right text-[14px] font-semibold tabular-nums text-slate-900">
        {formatInstrumentPrice(inst, inst.close)}
        <span className="text-[11px] font-normal text-slate-400">{unitSuffix(inst)}</span>
      </p>
      {WINDOWS.map((w, i) => (
        <Change key={w.key} value={inst.change_pct[w.key]} className={clsx('text-right text-[13px] font-semibold', i > 0 && 'hidden md:block')} />
      ))}
      <div className="hidden justify-end md:flex">
        <Sparkline values={inst.sparkline} />
      </div>
    </Link>
  );
}

/**
 * Every macro market the pipeline tracks, by group: indices, index futures, metals, energy, agriculture, currencies,
 * crypto, rates & volatility. Daily closes; changes are over calendar windows ("—" where history doesn't reach).
 * Instruments the pipeline hasn't fetched yet are named as pending, never shown with numbers.
 */
export default function MarketsPage() {
  const [data, setData] = useState<InstrumentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [group, setGroup] = useState<string>('all');

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get<InstrumentsResponse>('/api/market/instruments')
      .then((r) => !cancelled && setData(r.data))
      .catch(() => !cancelled && setError('Markets data is unavailable right now.'));
    return () => {
      cancelled = true;
    };
  }, []);

  const asOf = useMemo(
    () => data?.instruments.reduce<string | null>((m, i) => (i.latest_date && (!m || i.latest_date > m) ? i.latest_date : m), null) ?? null,
    [data],
  );
  const groups = (data?.groups ?? []).filter((g) => group === 'all' || g.key === group);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="mb-5 px-1 pt-2">
          <h1 className="large-title">Markets</h1>
          <p className="mt-1.5 text-[15px] text-slate-500">
            Indices, futures, commodities, currencies and crypto · daily closes
            {asOf && <span className="text-slate-400"> · as of {asOf}</span>}
          </p>
        </div>

        {data && (
          <div className="-mx-4 mb-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
            <SegmentedControl
              aria-label="Market group"
              value={group}
              onChange={setGroup}
              options={[{ value: 'all', label: 'All' }, ...data.groups.map((g) => ({ value: g.key, label: g.label }))]}
            />
          </div>
        )}

        {error ? (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="py-10 text-center text-sm text-slate-500">{error}</CardBody>
          </Card>
        ) : !data ? (
          <div className="space-y-4">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-56 rounded-2xl" />
            ))}
          </div>
        ) : (
          <div className="space-y-4">
            {groups.map((g) => {
              const items = data.instruments.filter((i) => i.group === g.key);
              const live = items.filter((i) => i.has_data);
              const pending = items.filter((i) => !i.has_data);
              if (items.length === 0) return null;
              return (
                <Card key={g.key} className="overflow-hidden border border-black/[0.05] shadow-sm">
                  <div className="grid grid-cols-[minmax(0,1fr)_auto_4.5rem] items-end gap-x-4 border-b border-black/[0.06] px-5 pb-2 pt-4 md:grid-cols-[minmax(0,1fr)_8.5rem_repeat(6,4.5rem)_6rem]">
                    <h2 className="text-[17px] font-semibold text-slate-900">{g.label}</h2>
                    <span className="text-right text-[11px] font-medium text-slate-400">Last</span>
                    {WINDOWS.map((w, i) => (
                      <span key={w.key} className={clsx('text-right text-[11px] font-medium text-slate-400', i > 0 && 'hidden md:block')}>
                        {w.label}
                      </span>
                    ))}
                    <span className="hidden text-right text-[11px] font-medium text-slate-400 md:block">30 sessions</span>
                  </div>
                  <div className="divide-y divide-black/[0.05]">
                    {live.map((inst) => (
                      <Row key={inst.symbol} inst={inst} asOf={asOf} />
                    ))}
                  </div>
                  {pending.length > 0 && (
                    <p className={clsx('px-5 py-3 text-[12px] text-slate-400', live.length > 0 && 'border-t border-black/[0.05]')}>
                      {pending.map((i) => i.name).join(', ')} {pending.length === 1 ? 'appears' : 'appear'} after the next data update.
                    </p>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
