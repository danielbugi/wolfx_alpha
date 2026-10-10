// File: frontend/src/components/layout/SearchBox.tsx
'use client';

import React, { useEffect, useState } from 'react';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { useOutsideClick, useSymbolSearch } from '@/hooks/useSymbolSearch';
import { shortcutLabel } from '@/hooks/useSearchShortcut';

/**
 * The symbol search input + results dropdown, used inside both Sidebar (desktop) and MobileNav (the expanded search
 * row). `onNavigate` fires after a result is chosen or a navigation-triggering key is pressed, so a container (e.g.
 * a mobile drawer) can close itself.
 */
export default function SearchBox({
  autoFocus,
  onNavigate,
  inputRef,
  showShortcut = false,
}: {
  autoFocus?: boolean;
  onNavigate?: () => void;
  inputRef?: React.Ref<HTMLInputElement>;
  /** Show the ⌘K hint inside the field (the sidebar's search, which the shortcut focuses). */
  showShortcut?: boolean;
}) {
  const { query, setQuery, results, open, setOpen, activeIndex, setActiveIndex, goToSymbol, handleKeyDown } = useSymbolSearch();
  const containerRef = useOutsideClick(() => setOpen(false));
  // Resolved after mount: the platform isn't known during the server render.
  const [hint, setHint] = useState<string | null>(null);
  useEffect(() => {
    if (showShortcut) setHint(shortcutLabel());
  }, [showShortcut]);

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <MagnifyingGlassIcon className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          autoFocus={autoFocus}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (handleKeyDown(e)) onNavigate?.();
          }}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="Search symbol"
          aria-label="Search symbol (e.g. AAPL)"
          className="peer w-full rounded-lg border border-transparent bg-black/[0.05] py-[7px] pl-8 pr-12 text-[13px] text-slate-800 transition-[background-color,box-shadow] duration-150 placeholder:text-slate-400 focus:bg-white focus:outline-none focus:ring-[3px] focus:ring-accent/25 focus:border-accent/50"
        />
        {hint && !query && (
          <kbd className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded-md border border-black/[0.08] bg-white/70 px-1.5 py-px font-sans text-[10px] font-medium text-slate-400 peer-focus:hidden">
            {hint}
          </kbd>
        )}
      </div>
      {open && results.length > 0 && (
        <div className="material-thick absolute left-0 right-0 z-10 mt-1.5 overflow-hidden rounded-xl border border-black/[0.06] p-1 shadow-lg">
          {results.map((r, i) => (
            <button
              key={r.symbol}
              onClick={() => {
                goToSymbol(r.symbol);
                onNavigate?.();
              }}
              onMouseEnter={() => setActiveIndex(i)}
              className={`flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[13px] ${i === activeIndex ? 'bg-accent text-white' : ''}`}
            >
              <span>
                <span className={`font-semibold ${i === activeIndex ? 'text-white' : 'text-slate-800'}`}>{r.symbol}</span>
                {r.sector && <span className={`ml-2 text-xs ${i === activeIndex ? 'text-white/80' : 'text-slate-500'}`}>{r.sector}</span>}
              </span>
              {r.current_price != null && <span className={`tabular-nums ${i === activeIndex ? 'text-white/90' : 'text-slate-600'}`}>${r.current_price.toFixed(2)}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
