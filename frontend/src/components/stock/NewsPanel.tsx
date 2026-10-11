'use client';

import React, { useEffect, useState } from 'react';
import { ArrowTopRightOnSquareIcon } from '@heroicons/react/20/solid';
import { newsApi, StockNews } from '@/services/newsApi';
import LoadingSpinner from '@/components/common/LoadingSpinner';

/** "3h ago" / "2d ago" / a date for anything older than a week. */
function ago(iso: string): string {
  const then = new Date(iso);
  const mins = Math.max(0, Math.round((Date.now() - then.getTime()) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return then.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/** The story's thumbnail. Hidden if the image fails to load, so a broken image never shows. Plain <img>: the hosts are
 * the publishers' CDNs, unknown in advance, so next/image's allow-list doesn't fit. No referrer is sent. */
function Thumb({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="h-16 w-24 shrink-0 rounded-lg bg-slate-100 object-cover ring-1 ring-black/[0.05]"
    />
  );
}

/**
 * Latest headlines for the stock (headline, source, time, link — no article text). Real states only: no headlines says
 * so, and when the provider is down the page says that, showing older stored headlines flagged as such if there are any.
 */
export default function NewsPanel({ symbol }: { symbol: string }) {
  const [news, setNews] = useState<StockNews | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setNews(null);
    setFailed(false);
    newsApi
      .get(symbol, 8)
      .then((r) => !cancelled && setNews(r))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-slate-900">News</h3>
        {news?.status === 'ok' && <p className="text-[11px] text-slate-400">Headlines via Alpaca · opens the source</p>}
      </div>

      {failed ? (
        <p className="py-6 text-center text-sm text-slate-400">Couldn&apos;t load news.</p>
      ) : news == null ? (
        <LoadingSpinner className="py-8" />
      ) : (
        <>
          {news.status === 'unavailable' && (
            <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
              News is unavailable right now{news.stale ? ' — showing the last headlines we have.' : '.'}
            </p>
          )}
          {news.items.length === 0 ? (
            news.status !== 'unavailable' && <p className="py-6 text-center text-sm text-slate-400">No recent news for {symbol}.</p>
          ) : (
            <ul className="divide-y divide-black/[0.06]">
              {news.items.map((item) => (
                <li key={item.url}>
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-start gap-3 rounded-lg py-2.5 hover:bg-black/[0.02]"
                  >
                    {item.image_url && <Thumb src={item.image_url} />}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium leading-snug text-slate-800 group-hover:text-accent">{item.headline}</p>
                      <p className="mt-0.5 text-[11px] text-slate-400">
                        {item.source ? <span className="capitalize">{item.source}</span> : 'Unknown source'} · {ago(item.published_at)}
                      </p>
                    </div>
                    <ArrowTopRightOnSquareIcon className="mt-0.5 h-4 w-4 shrink-0 text-slate-300 group-hover:text-accent" />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
