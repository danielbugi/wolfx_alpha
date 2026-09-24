// File: frontend/src/hooks/useSymbolSearch.ts
'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { stockApi, SymbolSearchResult } from '@/services/api';

const DEBOUNCE_MS = 250;

/**
 * The top-nav symbol search: debounced lookup, arrow-key navigation of the results, Enter/click to jump to the
 * stock page. Shared by Sidebar (desktop) and MobileNav (phone/tablet) so the two never implement it differently.
 */
export function useSymbolSearch() {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const requestId = useRef(0);
  const [results, setResults] = useState<SymbolSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setOpen(false);
      return;
    }
    const mine = ++requestId.current;   // a slower earlier request must not overwrite a faster later one's results
    const timeout = setTimeout(async () => {
      try {
        const matches = await stockApi.search(trimmed, 8);
        if (mine !== requestId.current) return;
        setResults(matches);
        setOpen(matches.length > 0);
        setActiveIndex(0);
      } catch {
        if (mine !== requestId.current) return;
        setResults([]);
        setOpen(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(timeout);
  }, [query]);

  const goToSymbol = (symbol: string) => {
    setOpen(false);
    setQuery('');
    router.push(`/stock/${symbol}`);
  };

  /** Returns true only when the key press actually navigated somewhere (a caller can use this to know whether to, say, close itself). */
  const handleKeyDown = (e: React.KeyboardEvent): boolean => {
    if (!open || results.length === 0) return false;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % results.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      goToSymbol(results[activeIndex].symbol);
      return true;
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
    return false;
  };

  const reset = () => {
    setQuery('');
    setResults([]);
    setOpen(false);
  };

  return { query, setQuery, results, open, setOpen, activeIndex, setActiveIndex, goToSymbol, handleKeyDown, reset };
}

/** True (used with a ref) when a mousedown landed outside the given element — for closing the results dropdown on an outside click. */
export function useOutsideClick(onOutside: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function handle(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) onOutside();
    }
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [onOutside]);
  return ref;
}
