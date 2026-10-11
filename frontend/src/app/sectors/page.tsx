'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Card, CardBody } from '@nextui-org/react';
import { clsx } from 'clsx';
import { apiClient, formatters } from '@/services/api';
import SegmentedControl from '@/components/ui/SegmentedControl';
import { Skeleton } from '@/components/ui/Skeleton';
import { SECTOR_WINDOWS, SectorsResponse, SectorWindow } from '@/lib/sectors';

const fmt = (v: number | null | undefined) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => (v == null ? 'text-slate-400' : v > 0 ? 'text-emerald-600' : v < 0 ? 'text-red-600' : 'text-slate-600');

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return <div className="h-10 w-full" />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 200},${38 - ((v - min) / span) * 34}`).join(' ');
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox="0 0 200 40" preserveAspectRatio="none" className="h-10 w-full" aria-hidden>
      <polyline points={pts} fill="none" stroke={up ? '#10b981' : '#ef4444'} strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * The 11 sectors of the stock universe, ranked by the median move of their stocks over the chosen window, with how
 * broad the move is (share of stocks up), how many trade above their 50-day average, and a 3-month equal-weighted
 * index. Each card opens the sector's page.
 */
export default function SectorsPage() {
  const [data, setData] = useState<SectorsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [win, setWin] = useState<SectorWindow>('1d');

  useEffect(() => {
    let cancelled = false;
    apiClient
      .get<SectorsResponse>('/api/market/sectors')
      .then((r) => !cancelled && setData(r.data))
      .catch(() => !cancelled && setError('Sector data is unavailable right now.'));
    return () => {
      cancelled = true;
    };
  }, []);

  const ranked = useMemo(
    () => [...(data?.sectors ?? [])].sort((a, b) => (b.change[win].median ?? -Infinity) - (a.change[win].median ?? -Infinity)),
    [data, win],
  );
  const label = SECTOR_WINDOWS.find((w) => w.key === win)!.label;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-3 px-1 pt-2">
          <div>
            <h1 className="large-title">Sectors</h1>
            <p className="mt-1.5 text-[15px] text-slate-500">
              How each sector&apos;s stocks are moving · median stock, breadth and trend
              {data?.as_of && <span className="text-slate-400"> · as of {data.as_of}</span>}
            </p>
          </div>
          <SegmentedControl aria-label="Window" value={win} onChange={setWin} options={SECTOR_WINDOWS.map((w) => ({ value: w.key, label: w.label }))} />
        </div>

        {error ? (
          <Card className="border border-black/[0.05] shadow-sm">
            <CardBody className="py-10 text-center text-sm text-slate-500">{error}</CardBody>
          </Card>
        ) : !data ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-48 rounded-2xl" />
            ))}
          </div>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {ranked.map((s, rank) => {
                const c = s.change[win];
                return (
                  <Link key={s.slug} href={`/sectors/${s.slug}`} className="press block">
                    <Card className="h-full border border-black/[0.05] shadow-sm transition-shadow hover:shadow-md">
                      <CardBody className="gap-3 p-5">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="text-[11px] font-medium tabular-nums text-slate-400">#{rank + 1}</p>
                            <h2 className="truncate text-[17px] font-semibold text-slate-900">{s.sector}</h2>
                            <p className="text-xs text-slate-500">
                              {s.stock_count} stocks · {s.industry_count} industries
                              {s.market_cap ? ` · ${formatters.marketCap(s.market_cap)}` : ''}
                            </p>
                          </div>
                          <div className="shrink-0 text-right">
                            <p className={clsx('text-[22px] font-semibold leading-tight tabular-nums', tone(c.median))}>{fmt(c.median)}</p>
                            <p className="text-[11px] text-slate-400">median stock · {label}</p>
                          </div>
                        </div>
                        {c.up_share != null && (
                          <div>
                            <div className="flex h-1.5 overflow-hidden rounded-full bg-red-400/70" aria-hidden>
                              <div className="h-full bg-emerald-500" style={{ width: `${c.up_share}%` }} />
                            </div>
                            <p className="mt-1 flex justify-between text-[11px] tabular-nums text-slate-500">
                              <span>{c.up_share.toFixed(0)}% of stocks up</span>
                              <span>{s.above_sma50_pct != null ? `${s.above_sma50_pct.toFixed(0)}% above 50-day avg` : ''}</span>
                            </p>
                          </div>
                        )}
                        <Sparkline values={s.sparkline} />
                        <div className="grid grid-cols-3 gap-2 border-t border-black/[0.05] pt-2 text-center">
                          {(['1m', 'ytd', '1y'] as SectorWindow[]).map((w) => (
                            <div key={w}>
                              <p className="text-[10px] font-medium uppercase text-slate-400">{SECTOR_WINDOWS.find((x) => x.key === w)!.label}</p>
                              <p className={clsx('text-[13px] font-semibold tabular-nums', tone(s.change[w].median))}>{fmt(s.change[w].median)}</p>
                            </div>
                          ))}
                        </div>
                      </CardBody>
                    </Card>
                  </Link>
                );
              })}
            </div>
            <p className="mt-5 px-1 text-xs leading-relaxed text-slate-400">
              Changes are the median of the sector&apos;s stocks over calendar windows; the mini chart is an equal-weighted
              index of today&apos;s members over the last 3 months (so it carries survivorship bias). Stocks without a sector
              are left out.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
