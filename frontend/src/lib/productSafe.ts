// File: frontend/src/lib/productSafe.ts
// Small transforms that keep internal research output out of product-facing pages.
import type { BreakoutHistoryEntry } from '@/services/api';

/**
 * Past 20-day channel breakouts reduced to what is a plain technical fact (the date and the breakout type), for the
 * chart's neutral markers. The outcome fields (success, the 10-day maximum gain/loss, days to peak) and the entry price
 * are signal-performance data and are dropped. With `success` null the chart draws every marker in the neutral colour.
 */
export function neutralBreakoutMarkers(entries: readonly BreakoutHistoryEntry[]): BreakoutHistoryEntry[] {
  return entries.map((e) => ({
    date: e.date,
    breakout_type: e.breakout_type,
    entry_price: null,
    success: null,
    max_gain_10d: null,
    max_loss_10d: null,
    days_to_peak: null,
  }));
}
