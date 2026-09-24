// File: frontend/src/components/layout/SearchBox.tsx
'use client';

import React from 'react';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { useOutsideClick, useSymbolSearch } from '@/hooks/useSymbolSearch';

/**
 * The symbol search input + results dropdown, used inside both Sidebar (desktop) and MobileNav (the expanded search
 * row). `onNavigate` fires after a result is chosen or a navigation-triggering key is pressed, so a container (e.g.
 * a mobile drawer) can close itself.
 */
export default function SearchBox({ autoFocus, onNavigate }: { autoFocus?: boolean; onNavigate?: () => void }) {
  const { query, setQuery, results, open, setOpen, activeIndex, setActiveIndex, goToSymbol, handleKeyDown } = useSymbolSearch();
  const containerRef = useOutsideClick(() => setOpen(false));

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <MagnifyingGlassIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          type="text"
          value={query}
          autoFocus={autoFocus}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (handleKeyDown(e)) onNavigate?.();
          }}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="Search symbol (e.g. AAPL)"
          className="w-full rounded-md border border-slate-200 bg-slate-50 py-1.5 pl-9 pr-3 text-sm focus:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300"
        />
      </div>
      {open && results.length > 0 && (
        <div className="absolute left-0 right-0 z-10 mt-1 overflow-hidden rounded-md border border-slate-200 bg-white shadow-lg">
          {results.map((r, i) => (
            <button
              key={r.symbol}
              onClick={() => {
                goToSymbol(r.symbol);
                onNavigate?.();
              }}
              onMouseEnter={() => setActiveIndex(i)}
              className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm ${i === activeIndex ? 'bg-slate-100' : 'bg-white'}`}
            >
              <span>
                <span className="font-semibold text-slate-800">{r.symbol}</span>
                {r.sector && <span className="ml-2 text-xs text-slate-500">{r.sector}</span>}
              </span>
              {r.current_price != null && <span className="text-slate-600">${r.current_price.toFixed(2)}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
