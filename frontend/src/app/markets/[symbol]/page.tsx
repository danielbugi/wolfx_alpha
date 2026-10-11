'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Card, CardBody } from '@nextui-org/react';
import { ChevronLeftIcon } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { apiClient } from '@/services/api';
import StockChart from '@/components/charts/StockChart';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  fmtChange,
  formatInstrumentPrice,
  Instrument,
  InstrumentHistory,
  InstrumentsResponse,
  pricePrecision,
  unitSuffix,
  WINDOWS,
} from '@/lib/instruments';

function decodeSymbol(raw: string | string[] | undefined): string {
  const s = Array.isArray(raw) ? raw[0] : raw ?? '';
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="min-w-0 px-4 py-3">
      <p className="truncate text-[11px] font-medium text-slate-500">{label}</p>
      <p className="mt-0.5 truncate text-[15px] font-semibold tabular-nums text-slate-900">{value}</p>
      {hint && <p className="truncate text-[11px] text-slate-400">{hint}</p>}
    </div>
  );
}

/** High/low over the last 365 calendar days -- only when the history actually covers the year. */
function yearRange(bars: InstrumentHistory['bars']) {
  const last = bars[bars.length - 1];
  if (!last) return null;
  const from = new Date(`${last.date}T00:00:00Z`);
  from.setUTCFullYear(from.getUTCFullYear() - 1);
  const start = from.toISOString().slice(0, 10);
  if (!bars[0] || bars[0].date > start) return null;
  const year = bars.filter((b) => b.date > start);
  const highs = year.map((b) => b.high ?? b.close);
  const lows = year.map((b) => b.low ?? b.close);
  return { high: Math.max(...highs), low: Math.min(...lows) };
}

/** One market (future, commodity, currency, crypto, index): price, changes, 52-week range and the full chart. */
export default function InstrumentPage() {
  const params = useParams();
  const symbol = decodeSymbol(params?.symbol as string | string[] | undefined);

  const [history, setHistory] = useState<InstrumentHistory | null>(null);
  const [list, setList] = useState<InstrumentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    setError(null);
    apiClient
      .get<InstrumentHistory>(`/api/market/instruments/${encodeURIComponent(symbol)}/history`, { params: { days: 1825 } })
      .then((r) => !cancelled && setHistory(r.data))
      .catch((e) => !cancelled && setError(e?.response?.status === 404 ? `${symbol} isn't one of the tracked markets.` : 'This market is unavailable right now.'));
    apiClient
      .get<InstrumentsResponse>('/api/market/instruments')
      .then((r) => !cancelled && setList(r.data))
      .catch(() => {
        /* the changes strip and sibling links just don't show */
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const inst: Instrument | undefined = list?.instruments.find((i) => i.symbol === symbol);
  const kind = history ?? inst;
  const bars = history?.bars ?? [];
  const last = bars[bars.length - 1];
  const close = last?.close ?? inst?.close ?? null;
  const dayChange = inst?.change_pct['1d'] ?? (bars.length > 1 ? (last.close / bars[bars.length - 2].close - 1) * 100 : null);
  const range = yearRange(bars);
  const groupLabel = list?.groups.find((g) => g.key === kind?.group)?.label;
  const siblings = list?.instruments.filter((i) => i.group === kind?.group && i.symbol !== symbol && i.has_data) ?? [];
  const fmtPrice = (v: number | null) => (kind ? formatInstrumentPrice(kind, v) : '—');

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-7xl space-y-4 px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <Link href="/markets" className="inline-flex items-center gap-1 px-1 text-sm font-medium text-accent hover:underline">
          <ChevronLeftIcon className="h-4 w-4" /> Markets
        </Link>

        {error ? (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="py-10 text-center text-sm text-slate-500">{error}</CardBody>
          </Card>
        ) : !history ? (
          <div className="space-y-4">
            <Skeleton className="h-24 rounded-2xl" />
            <Skeleton className="h-[480px] rounded-2xl" />
          </div>
        ) : (
          <>
            <Card className="border border-black/[0.05] shadow-sm">
              <CardBody className="flex-row flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 py-4">
                <div className="min-w-0">
                  <h1 className="large-title">{history.name}</h1>
                  <p className="mt-0.5 text-sm text-slate-500">
                    {history.symbol}
                    {groupLabel && ` · ${groupLabel}`}
                    {last && ` · close of ${last.date}`}
                  </p>
                </div>
                <div className="sm:text-right">
                  <p className="large-title tabular-nums">
                    {fmtPrice(close)}
                    <span className="text-base font-normal text-slate-400">{unitSuffix(history)}</span>
                  </p>
                  <p className={clsx('text-sm font-semibold tabular-nums', dayChange == null ? 'text-slate-400' : dayChange >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                    {fmtChange(dayChange)}
                  </p>
                </div>
              </CardBody>
            </Card>

            {bars.length === 0 ? (
              <Card className="border border-black/[0.05] shadow-sm">
                <CardBody className="py-10 text-center text-sm text-slate-500">No price history stored for this market.</CardBody>
              </Card>
            ) : (
              <>
                <Card className="border border-black/[0.05] shadow-sm">
                  <CardBody className="grid grid-cols-2 p-0 sm:grid-cols-4 lg:grid-cols-[repeat(4,minmax(0,1fr))_2fr]">
                    <Stat label="Open" value={fmtPrice(last.open)} />
                    <Stat label="Day high" value={fmtPrice(last.high)} />
                    <Stat label="Day low" value={fmtPrice(last.low)} />
                    <Stat label="Volume" value={last.volume ? last.volume.toLocaleString('en-US') : '—'} />
                    <Stat
                      label="52-week range"
                      value={range ? `${fmtPrice(range.low)} – ${fmtPrice(range.high)}` : '—'}
                      hint={range && close != null && range.high > range.low ? `${(((close - range.low) / (range.high - range.low)) * 100).toFixed(0)}% of the range` : undefined}
                    />
                  </CardBody>
                  {inst && (
                    <div className="grid grid-cols-3 gap-y-2 border-t border-black/[0.06] px-4 py-3 sm:grid-cols-6" aria-label="Performance">
                      {WINDOWS.map((w) => {
                        const v = inst.change_pct[w.key];
                        return (
                          <div key={w.key} className="text-center">
                            <p className="text-[11px] font-medium text-slate-500">{w.label}</p>
                            <p className={clsx('text-[13px] font-semibold tabular-nums', v == null ? 'text-slate-400' : v >= 0 ? 'text-emerald-600' : 'text-red-600')}>
                              {fmtChange(v)}
                            </p>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Card>

                <Card className="border border-black/[0.05] shadow-sm">
                  <div className="px-5 pb-3 pt-4">
                    <h3 className="text-[15px] font-semibold text-slate-900">Chart</h3>
                  </div>
                  <StockChart key={history.symbol} bars={bars} height={440} precision={pricePrecision(history, close)} />
                </Card>
              </>
            )}

            {siblings.length > 0 && (
              <div className="px-1">
                <p className="mb-2 text-[13px] font-medium text-slate-500">More in {groupLabel ?? 'this group'}</p>
                <div className="flex flex-wrap gap-2">
                  {siblings.map((s) => (
                    <Link
                      key={s.symbol}
                      href={`/markets/${encodeURIComponent(s.symbol)}`}
                      className="press rounded-full border border-black/[0.06] bg-white px-3 py-1.5 text-[13px] font-medium text-slate-700 hover:bg-black/[0.03]"
                    >
                      {s.name}{' '}
                      <span className={clsx('tabular-nums', s.change_pct['1d'] == null ? 'text-slate-400' : s.change_pct['1d'] >= 0 ? 'text-emerald-600' : 'text-red-600')}>{fmtChange(s.change_pct['1d'])}</span>
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
