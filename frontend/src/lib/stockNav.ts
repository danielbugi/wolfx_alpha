'use client';

import { useEffect } from 'react';

/**
 * "Next / previous symbol" on the stock page: remembers the list a stock was opened from.
 *
 * One document-level click listener (installed once by the app shell) notices any click inside a stock list — a table
 * of stock links, or any element marked `data-stock-list="Name"` — and stores that list's symbols in page order plus
 * its name. No table has to opt in: links to `/stock/<SYMBOL>` inside the clicked container ARE the list. Stored per
 * browser tab (sessionStorage); if storage is unavailable the arrows simply don't appear.
 */

const KEY = 'stock_nav_list';
const SYMBOL_HREF = /^\/stock\/([^/?#]+)/;

export interface StockNavList {
  label: string;
  symbols: string[];
}

function symbolsIn(container: Element): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  container.querySelectorAll('a[href^="/stock/"]').forEach((a) => {
    const m = (a.getAttribute('href') || '').match(SYMBOL_HREF);
    if (!m) return;
    const sym = decodeURIComponent(m[1]).toUpperCase();
    if (!seen.has(sym)) {
      seen.add(sym);
      out.push(sym);
    }
  });
  return out;
}

function remember(list: StockNavList) {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* no storage: no arrows */
  }
}

export function readStockNavList(): StockNavList | null {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StockNavList;
    return Array.isArray(parsed.symbols) && typeof parsed.label === 'string' ? parsed : null;
  } catch {
    return null;
  }
}

/** Install once (app shell). Captures the list on pointer-down/click inside any stock table or marked container. */
export function useStockNavCapture() {
  useEffect(() => {
    // pointerdown as well as click: table rows navigate on press release, which can come before the click event,
    // so the list must already be stored by then. click still covers keyboard activation (Enter on a link).
    function onClick(e: Event) {
      const target = e.target as Element | null;
      if (!target?.closest) return;
      // Only clicks that lead to a stock: a stock link, or a row that contains one (row click opens the stock).
      const row = target.closest('tr');
      const link = target.closest('a[href^="/stock/"]') ?? row?.querySelector('a[href^="/stock/"]');
      if (!link) return;
      const container = target.closest('[data-stock-list]') ?? target.closest('table');
      if (!container) return;
      const symbols = symbolsIn(container);
      if (symbols.length < 2) return;
      const label =
        container.getAttribute('data-stock-list') || container.getAttribute('aria-label') || 'List';
      remember({ label, symbols });
    }
    document.addEventListener('pointerdown', onClick, true);
    document.addEventListener('click', onClick, true);
    return () => {
      document.removeEventListener('pointerdown', onClick, true);
      document.removeEventListener('click', onClick, true);
    };
  }, []);
}
