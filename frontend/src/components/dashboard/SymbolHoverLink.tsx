'use client';

import React, { useRef, useState, useCallback, useEffect } from 'react';
import Link from 'next/link';
import { Popover, PopoverTrigger, PopoverContent } from '@nextui-org/react';
import StockHoverCard from '@/components/dashboard/StockHoverCard';
import { useHoverStockSummary } from '@/hooks/useHoverStockSummary';

const HOVER_OPEN_DELAY_MS = 320;
// Start fetching bars + signal well before the popover opens, so the data is
// usually already there when it does. Short enough to hide most of the 320ms
// open delay, long enough that a quick mouse sweep across rows never fires it.
const HOVER_PREFETCH_DELAY_MS = 120;
const HOVER_CLOSE_DELAY_MS = 150;

export interface SymbolHoverLinkProps {
  symbol: string;
  className?: string;
}

/**
 * Wraps a symbol link in a hover-triggered popover showing a mini chart +
 * key stats. Debounces the open on mouseenter so a quick mouse pass over
 * several table rows doesn't fire a burst of API calls or mount a chart for
 * every row -- only a row the user actually pauses on gets one. Closing is
 * also debounced (shorter delay) so moving the cursor from the link into the
 * popover content itself doesn't immediately dismiss it. On touch devices
 * onMouseEnter simply never fires, so this degrades to a plain link.
 */
export default function SymbolHoverLink({ symbol, className }: SymbolHoverLinkProps) {
  const [open, setOpen] = useState(false);
  const [prefetch, setPrefetch] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prefetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const data = useHoverStockSummary(symbol, open ? 'open' : prefetch ? 'prefetch' : 'off');

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (prefetchTimerRef.current) {
      clearTimeout(prefetchTimerRef.current);
      prefetchTimerRef.current = null;
    }
  }, []);

  const handleEnter = useCallback(() => {
    clearTimer();
    prefetchTimerRef.current = setTimeout(() => setPrefetch(true), HOVER_PREFETCH_DELAY_MS);
    timerRef.current = setTimeout(() => setOpen(true), HOVER_OPEN_DELAY_MS);
  }, [clearTimer]);

  const handleLeave = useCallback(() => {
    clearTimer();
    timerRef.current = setTimeout(() => setOpen(false), HOVER_CLOSE_DELAY_MS);
  }, [clearTimer]);

  useEffect(() => clearTimer, [clearTimer]);

  return (
    // NextUI's default popover background is `bg-content1`, a theme token that
    // only exists when the NextUI Tailwind plugin is configured -- this app
    // doesn't load it (Tailwind v4, no `@plugin`), so the token generates no
    // CSS and the popover renders transparent. Set explicit colors on both the
    // card (`content`) and its arrow (`base`'s ::before) instead of relying on it.
    <Popover
      isOpen={open}
      onOpenChange={setOpen}
      placement="right-start"
      showArrow
      classNames={{
        base: 'before:bg-white before:border before:border-slate-200',
        content: 'bg-white border border-slate-200 rounded-lg shadow-xl',
      }}
    >
      <PopoverTrigger>
        <Link
          href={`/stock/${symbol}`}
          className={className ?? 'font-semibold text-slate-800 hover:text-cyan-700'}
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
        >
          {symbol}
        </Link>
      </PopoverTrigger>
      <PopoverContent onMouseEnter={handleEnter} onMouseLeave={handleLeave}>
        <StockHoverCard symbol={symbol} data={data} />
      </PopoverContent>
    </Popover>
  );
}
