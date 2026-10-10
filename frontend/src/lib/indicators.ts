// Technical indicators computed in the browser from the daily bars the API already returns (no backend change).
// Every function returns one value per input bar, aligned by index, with `null` until there is enough history —
// a value is never invented for the warm-up period.
import type { PriceBar } from '@/services/api';

export type Interval = 'daily' | 'weekly' | 'monthly';

/** A bar after aggregation. Same shape as PriceBar so charts can treat all intervals alike. */
export type Candle = PriceBar;

/** ISO date of the Monday starting the week that contains `date` (UTC, bars are plain trading dates). */
function weekKey(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  const day = (d.getUTCDay() + 6) % 7; // Monday = 0
  d.setUTCDate(d.getUTCDate() - day);
  return d.toISOString().slice(0, 10);
}

/**
 * Daily bars -> weekly or monthly candles: first open, highest high, lowest low, last close, summed volume. The candle
 * is dated by its LAST trading day (so the latest, still-forming week/month sits at today's date). A period whose
 * bars lack open/high/low keeps those as null rather than guessing. The Donchian columns are recomputed for the new
 * interval by `withDonchian`, not carried over from the daily rows.
 */
export function aggregateBars(bars: PriceBar[], interval: Interval): Candle[] {
  if (interval === 'daily') return bars;
  const groups = new Map<string, PriceBar[]>();
  for (const b of bars) {
    const key = interval === 'weekly' ? weekKey(b.date) : b.date.slice(0, 7);
    const g = groups.get(key);
    if (g) g.push(b);
    else groups.set(key, [b]);
  }
  const out: Candle[] = [];
  for (const g of groups.values()) {
    const highs = g.map((b) => b.high).filter((v): v is number => v != null);
    const lows = g.map((b) => b.low).filter((v): v is number => v != null);
    const vols = g.map((b) => b.volume).filter((v): v is number => v != null);
    out.push({
      date: g[g.length - 1].date,
      open: g[0].open,
      high: highs.length === g.length ? Math.max(...highs) : null,
      low: lows.length === g.length ? Math.min(...lows) : null,
      close: g[g.length - 1].close,
      volume: vols.length ? vols.reduce((a, v) => a + v, 0) : null,
      donchian_high_20: null,
      donchian_low_20: null,
    });
  }
  return out;
}

/**
 * Donchian channel over `period` candles, INCLUDING the current one — the same definition the pipeline stores for
 * daily bars (mechanism/data_updaters/daily_data_updater.py: high.rolling(20, min_periods=20).max()).
 */
export function withDonchian(candles: Candle[], period = 20): Candle[] {
  return candles.map((c, i) => {
    if (i < period - 1) return { ...c, donchian_high_20: null, donchian_low_20: null };
    const win = candles.slice(i - period + 1, i + 1);
    if (win.some((b) => b.high == null || b.low == null)) return { ...c, donchian_high_20: null, donchian_low_20: null };
    return {
      ...c,
      donchian_high_20: Math.max(...win.map((b) => b.high as number)),
      donchian_low_20: Math.min(...win.map((b) => b.low as number)),
    };
  });
}

export function sma(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  let count = 0; // consecutive non-null values in the window
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null) {
      sum = 0;
      count = 0;
      continue;
    }
    sum += v;
    count++;
    if (count > period) {
      sum -= values[i - period] as number;
      count = period;
    }
    if (count === period) out[i] = sum / period;
  }
  return out;
}

/** Exponential moving average seeded with the SMA of the first `period` values (the standard convention). */
export function ema(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  const k = 2 / (period + 1);
  let prev: number | null = null;
  let seed: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null) {
      prev = null;
      seed = [];
      continue;
    }
    if (prev == null) {
      seed.push(v);
      if (seed.length === period) {
        prev = seed.reduce((a, x) => a + x, 0) / period;
        out[i] = prev;
      }
      continue;
    }
    prev = v * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** Bollinger bands: SMA(period) ± `mult` population standard deviations. */
export function bollinger(values: (number | null)[], period = 20, mult = 2) {
  const mid = sma(values, period);
  const upper: (number | null)[] = [];
  const lower: (number | null)[] = [];
  for (let i = 0; i < values.length; i++) {
    const m = mid[i];
    if (m == null) {
      upper.push(null);
      lower.push(null);
      continue;
    }
    const win = values.slice(i - period + 1, i + 1) as number[];
    const sd = Math.sqrt(win.reduce((a, v) => a + (v - m) ** 2, 0) / period);
    upper.push(m + mult * sd);
    lower.push(m - mult * sd);
  }
  return { mid, upper, lower };
}

/** RSI with Wilder's smoothing (the standard RSI definition; matches typical charting platforms). */
export function rsi(values: (number | null)[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let avgGain = 0;
  let avgLoss = 0;
  let seen = 0;
  for (let i = 1; i < values.length; i++) {
    const cur = values[i];
    const prev = values[i - 1];
    if (cur == null || prev == null) {
      seen = 0;
      avgGain = 0;
      avgLoss = 0;
      continue;
    }
    const change = cur - prev;
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);
    seen++;
    if (seen <= period) {
      avgGain += gain / period;
      avgLoss += loss / period;
      if (seen < period) continue;
    } else {
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
    }
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

/** MACD(fast, slow, signal): line = EMA(fast) − EMA(slow); signal = EMA(signal) of the line; histogram = line − signal. */
export function macd(values: (number | null)[], fast = 12, slow = 26, signalPeriod = 9) {
  const ef = ema(values, fast);
  const es = ema(values, slow);
  const line = values.map((_, i) => (ef[i] != null && es[i] != null ? (ef[i] as number) - (es[i] as number) : null));
  const signal = ema(line, signalPeriod);
  const hist = line.map((v, i) => (v != null && signal[i] != null ? v - (signal[i] as number) : null));
  return { line, signal, hist };
}
