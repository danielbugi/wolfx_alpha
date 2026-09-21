// File: frontend/src/components/layout/TopNav.tsx
'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { ArrowTrendingUpIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { stockApi, SymbolSearchResult } from '@/services/api';

const navigation = [
  { name: 'Dashboard', href: '/' },
  { name: 'Screener', href: '/screener' },
  { name: 'Strategy', href: '/strategy' },
  { name: 'Alerts', href: '/alerts' },
  { name: 'System Health', href: '/system-health' },
  { name: 'ML Stats', href: '/ml-stats' },
  { name: 'Performance', href: '/performance' },
];

export default function TopNav() {
  const router = useRouter();
  const pathname = usePathname();

  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SymbolSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setOpen(false);
      return;
    }

    const timeout = setTimeout(async () => {
      try {
        const matches = await stockApi.search(trimmed, 8);
        setResults(matches);
        setOpen(matches.length > 0);
        setActiveIndex(0);
      } catch {
        setResults([]);
        setOpen(false);
      }
    }, 250);

    return () => clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const goToSymbol = (symbol: string) => {
    setOpen(false);
    setQuery('');
    router.push(`/stock/${symbol}`);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!open || results.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % results.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      goToSymbol(results[activeIndex].symbol);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="sticky top-0 z-40 bg-white border-b border-slate-200 shadow-sm">
      <div className="container mx-auto max-w-7xl px-4 flex items-center gap-6 h-14">
        <Link href="/" className="flex items-center gap-2 shrink-0">
          <ArrowTrendingUpIcon className="h-6 w-6 text-slate-700" />
          <span className="font-bold text-slate-800 text-sm tracking-wide">Trading System</span>
        </Link>

        <nav className="flex items-center gap-4 shrink-0">
          {navigation.map((item) => (
            <Link
              key={item.name}
              href={item.href}
              className={clsx(
                'text-sm font-medium transition-colors',
                pathname === item.href
                  ? 'text-slate-900 border-b-2 border-slate-700'
                  : 'text-slate-500 hover:text-slate-800'
              )}
            >
              {item.name}
            </Link>
          ))}
        </nav>

        <div ref={containerRef} className="relative flex-1 max-w-sm ml-auto">
          <div className="relative">
            <MagnifyingGlassIcon className="h-4 w-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => results.length > 0 && setOpen(true)}
              placeholder="Search symbol (e.g. AAPL)"
              className="w-full pl-9 pr-3 py-1.5 text-sm rounded-md border border-slate-200 bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300"
            />
          </div>

          {open && results.length > 0 && (
            <div className="absolute right-0 mt-1 w-72 bg-white rounded-md border border-slate-200 shadow-lg overflow-hidden">
              {results.map((r, i) => (
                <button
                  key={r.symbol}
                  onClick={() => goToSymbol(r.symbol)}
                  onMouseEnter={() => setActiveIndex(i)}
                  className={clsx(
                    'w-full flex items-center justify-between px-3 py-2 text-left text-sm',
                    i === activeIndex ? 'bg-slate-100' : 'bg-white'
                  )}
                >
                  <span>
                    <span className="font-semibold text-slate-800">{r.symbol}</span>
                    {r.sector && <span className="ml-2 text-xs text-slate-500">{r.sector}</span>}
                  </span>
                  {r.current_price != null && (
                    <span className="text-slate-600">${r.current_price.toFixed(2)}</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
