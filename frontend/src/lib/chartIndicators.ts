'use client';

// File: frontend/src/lib/chartIndicators.ts
// The chart's indicator choice, saved in this browser (a per-viewer convenience) and shared by every chart that uses
// StockChart (watchlist, stock pages, markets). Reading is tolerant: unknown keys are ignored and unreadable storage falls
// back to the defaults; nothing is written until the user changes something. Context-specific layers (past breakout
// markers, a trade plan) are never saved.
import { useCallback, useEffect, useState } from 'react';

export type SavedOverlay = 'sma20' | 'sma50' | 'sma200' | 'bollinger' | 'donchian' | 'volume';
export type SavedPanel = 'rsi' | 'macd';

export interface IndicatorPrefs {
  overlays: SavedOverlay[];
  panels: SavedPanel[];
}

export const INDICATOR_CATALOG: {
  overlays: { key: SavedOverlay; label: string; description: string }[];
  panels: { key: SavedPanel; label: string; description: string }[];
} = {
  overlays: [
    { key: 'sma20', label: 'SMA 20', description: 'Simple moving average of the last 20 closes.' },
    { key: 'sma50', label: 'SMA 50', description: 'Simple moving average of the last 50 closes.' },
    { key: 'sma200', label: 'SMA 200', description: 'Simple moving average of the last 200 closes (needs about 10 months of history).' },
    { key: 'bollinger', label: 'Bollinger Bands (20, 2)', description: 'The 20-period average with bands two standard deviations above and below.' },
    { key: 'donchian', label: 'Donchian Channel (20)', description: 'The highest high and lowest low of the last 20 periods.' },
    { key: 'volume', label: 'Volume', description: 'Shares traded per candle, at the bottom of the price chart.' },
  ],
  panels: [
    { key: 'rsi', label: 'RSI (14)', description: 'Relative Strength Index in its own panel under the chart (0-100).' },
    { key: 'macd', label: 'MACD (12, 26, 9)', description: 'Moving average convergence/divergence with its signal line and histogram.' },
  ],
};

export const DEFAULT_INDICATORS: IndicatorPrefs = { overlays: ['sma20', 'sma50', 'donchian', 'volume'], panels: ['rsi'] };

const KEY = 'chart_indicators_v1';
const EVENT = 'chart-indicators-change';
const OVERLAY_KEYS = new Set<string>(INDICATOR_CATALOG.overlays.map((o) => o.key));
const PANEL_KEYS = new Set<string>(INDICATOR_CATALOG.panels.map((p) => p.key));

/** Saved preferences, or the defaults when nothing valid is stored. Unknown keys are dropped. */
export function parseIndicatorPrefs(raw: string | null): IndicatorPrefs {
  if (!raw) return DEFAULT_INDICATORS;
  try {
    const v = JSON.parse(raw) as Record<string, unknown>;
    if (!v || typeof v !== 'object' || !Array.isArray(v.overlays) || !Array.isArray(v.panels)) return DEFAULT_INDICATORS;
    return {
      overlays: [...new Set(v.overlays.filter((k): k is SavedOverlay => typeof k === 'string' && OVERLAY_KEYS.has(k)))],
      panels: [...new Set(v.panels.filter((k): k is SavedPanel => typeof k === 'string' && PANEL_KEYS.has(k)))],
    };
  } catch {
    return DEFAULT_INDICATORS;
  }
}

function read(): IndicatorPrefs {
  try {
    return parseIndicatorPrefs(window.localStorage.getItem(KEY));
  } catch {
    return DEFAULT_INDICATORS;
  }
}

/** The saved choice (synced across charts and tabs) and a setter that saves it. `saved` is false if the browser refused. */
export function useIndicatorPrefs(): { prefs: IndicatorPrefs; save: (p: IndicatorPrefs) => boolean } {
  const [prefs, setPrefs] = useState<IndicatorPrefs>(DEFAULT_INDICATORS);

  useEffect(() => {
    const sync = () => setPrefs(read());
    sync();
    const onStorage = (e: StorageEvent) => (e.key === null || e.key === KEY) && sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const save = useCallback((p: IndicatorPrefs) => {
    const clean = parseIndicatorPrefs(JSON.stringify(p));
    let ok = true;
    try {
      window.localStorage.setItem(KEY, JSON.stringify(clean));
    } catch {
      ok = false;
    }
    setPrefs(clean); // applies to this chart even when it cannot be saved
    if (ok) window.dispatchEvent(new Event(EVENT)); // other charts re-read storage, so only after a real save
    return ok;
  }, []);

  return { prefs, save };
}
