// File: frontend/src/lib/priceMath.ts
// Small price/date math shared by the stock detail page, the dashboard hover
// card, and the candlestick chart -- pulled out so it isn't copy-pasted a
// third time.

export function positionPct(
  price: number | undefined,
  low: number | undefined,
  high: number | undefined
): number {
  if (price == null || low == null || high == null || high === low) return 50;
  return Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100));
}

export function pctChange(
  current: number | null | undefined,
  previous: number | null | undefined
): number | null {
  if (current == null || previous == null || previous === 0) return null;
  return ((current - previous) / previous) * 100;
}

/**
 * Snap a calendar date (e.g. a yfinance earnings date) to the closest bar's
 * date within `toleranceDays`, so it can be placed as a chart marker --
 * lightweight-charts markers must land on an existing series time point, and
 * an earnings date rarely falls exactly on a trading day. Returns null if
 * nothing is within tolerance (e.g. a future earnings date past the last bar).
 */
export function nearestBarDate(
  bars: { date: string }[],
  targetDateIso: string,
  toleranceDays = 3
): string | null {
  const target = new Date(targetDateIso).getTime();
  if (Number.isNaN(target)) return null;

  let best: string | null = null;
  let bestDiffDays = Infinity;

  for (const bar of bars) {
    const barTime = new Date(bar.date).getTime();
    if (Number.isNaN(barTime)) continue;
    const diffDays = Math.abs(barTime - target) / 86_400_000;
    if (diffDays < bestDiffDays) {
      bestDiffDays = diffDays;
      best = bar.date;
    }
  }

  return best !== null && bestDiffDays <= toleranceDays ? best : null;
}
