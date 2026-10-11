'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ChevronLeftIcon, ChevronRightIcon } from '@heroicons/react/20/solid';
import { readStockNavList, StockNavList } from '@/lib/stockNav';

const BUTTON =
  'press flex h-8 w-8 items-center justify-center rounded-full bg-black/[0.05] text-slate-700 hover:bg-black/[0.09] disabled:pointer-events-none disabled:opacity-30';

/**
 * Previous / next stock in the list this one was opened from (see lib/stockNav.ts), with ← / → as shortcuts. Renders
 * nothing when the stock wasn't opened from a list, or isn't in the remembered one.
 */
export function useStockNav(symbol: string) {
  const router = useRouter();
  const [list, setList] = useState<StockNavList | null>(null);

  useEffect(() => {
    setList(readStockNavList());
  }, [symbol]);

  const index = list ? list.symbols.indexOf(symbol) : -1;
  const prev = index > 0 ? list!.symbols[index - 1] : null;
  const next = index >= 0 && index < list!.symbols.length - 1 ? list!.symbols[index + 1] : null;

  // Warm the neighbours so stepping through a list feels instant.
  useEffect(() => {
    if (prev) router.prefetch(`/stock/${prev}`);
    if (next) router.prefetch(`/stock/${next}`);
  }, [prev, next, router]);

  useEffect(() => {
    if (index < 0) return;
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName))) return;
      if (document.querySelector('dialog[open]')) return;
      if (e.key === 'ArrowLeft' && prev) {
        e.preventDefault();
        router.push(`/stock/${prev}`);
      } else if (e.key === 'ArrowRight' && next) {
        e.preventDefault();
        router.push(`/stock/${next}`);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [index, prev, next, router]);

  return { list, index, prev, next, go: (s: string) => router.push(`/stock/${s}`) };
}

export function StockNavButtons({ nav }: { nav: ReturnType<typeof useStockNav> }) {
  if (!nav.list || nav.index < 0) return null;
  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        className={BUTTON}
        disabled={!nav.prev}
        onClick={() => nav.prev && nav.go(nav.prev)}
        aria-label={nav.prev ? `Previous: ${nav.prev}` : 'No previous stock'}
        title={nav.prev ? `${nav.prev} (←)` : undefined}
      >
        <ChevronLeftIcon className="h-5 w-5" />
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={!nav.next}
        onClick={() => nav.next && nav.go(nav.next)}
        aria-label={nav.next ? `Next: ${nav.next}` : 'No next stock'}
        title={nav.next ? `${nav.next} (→)` : undefined}
      >
        <ChevronRightIcon className="h-5 w-5" />
      </button>
    </div>
  );
}

/** "3 of 12 · Screener results" — where this stock sits in the list being stepped through. */
export function StockNavCaption({ nav }: { nav: ReturnType<typeof useStockNav> }) {
  if (!nav.list || nav.index < 0) return null;
  return (
    <span className="tabular-nums">
      {nav.index + 1} of {nav.list.symbols.length} · {nav.list.label}
    </span>
  );
}
