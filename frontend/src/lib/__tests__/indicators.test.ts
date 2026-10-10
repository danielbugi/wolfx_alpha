import { describe, expect, it } from 'vitest';
import { aggregateBars, bollinger, ema, macd, rsi, sma, withDonchian } from '@/lib/indicators';
import type { PriceBar } from '@/services/api';

const bar = (date: string, o: number, h: number, l: number, c: number, v = 100): PriceBar => ({
  date, open: o, high: h, low: l, close: c, volume: v, donchian_high_20: null, donchian_low_20: null,
});

describe('sma / ema', () => {
  it('sma is null during warm-up, then the plain average', () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });
  it('sma restarts its window after a missing value instead of averaging across it', () => {
    expect(sma([1, 2, null, 4, 5, 6], 2)).toEqual([null, 1.5, null, null, 4.5, 5.5]);
  });
  it('ema is seeded with the sma of the first period values', () => {
    const out = ema([2, 4, 6, 8], 3);
    expect(out.slice(0, 2)).toEqual([null, null]);
    expect(out[2]).toBe(4);
    expect(out[3]).toBeCloseTo(8 * 0.5 + 4 * 0.5); // k = 2/(3+1) = 0.5
  });
});

describe('rsi', () => {
  it('is 100 when price only rises, and null before 14 changes exist', () => {
    const rising = Array.from({ length: 20 }, (_, i) => 10 + i);
    const out = rsi(rising, 14);
    expect(out.slice(0, 14).every((v) => v === null)).toBe(true);
    expect(out[14]).toBe(100);
  });
  // The classic StockCharts worked example (first RSI 70.53, then 66.32) rounds its averages to 2 decimals mid-way.
  // Unrounded: avg gain = 3.34/14, avg loss = 1.40/14 -> RSI 70.46; next bar (-0.28) -> 66.25.
  it('matches the Wilder RSI worked example computed without intermediate rounding', () => {
    const closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.1, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.0, 46.03, 46.41, 46.22, 45.64];
    const out = rsi(closes, 14);
    expect(out[14]).toBeCloseTo(70.46, 2);
    expect(out[15]).toBeCloseTo(66.25, 2);
  });
});

describe('bollinger / macd', () => {
  it('bands collapse onto the average when prices are flat', () => {
    const { mid, upper, lower } = bollinger(Array(25).fill(10), 20, 2);
    expect(mid[19]).toBe(10);
    expect(upper[19]).toBe(10);
    expect(lower[19]).toBe(10);
    expect(mid[18]).toBeNull();
  });
  it('macd line is fast ema minus slow ema, histogram is line minus signal', () => {
    const values = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 4) * 5 + i * 0.2);
    const { line, signal, hist } = macd(values);
    const i = 59;
    expect(hist[i]).toBeCloseTo((line[i] as number) - (signal[i] as number));
    expect(line[24]).toBeNull(); // slow ema (26) not warm yet
    expect(line[25]).not.toBeNull();
  });
});

describe('aggregateBars / withDonchian', () => {
  const daily = [
    bar('2026-09-28', 10, 12, 9, 11), // Mon
    bar('2026-09-29', 11, 13, 10, 12),
    bar('2026-10-02', 12, 14, 8, 13), // Fri, still week 1
    bar('2026-10-05', 13, 15, 12, 14), // Mon, week 2
  ];
  it('weekly candles: first open, max high, min low, last close, summed volume, dated by last day', () => {
    const w = aggregateBars(daily, 'weekly');
    expect(w).toHaveLength(2);
    expect(w[0]).toMatchObject({ date: '2026-10-02', open: 10, high: 14, low: 8, close: 13, volume: 300 });
    expect(w[1]).toMatchObject({ date: '2026-10-05', open: 13, high: 15, low: 12, close: 14 });
  });
  it('monthly candles group by calendar month', () => {
    const m = aggregateBars(daily, 'monthly');
    expect(m.map((c) => c.date)).toEqual(['2026-09-29', '2026-10-05']);
  });
  it('donchian includes the current candle and needs a full window', () => {
    const c = withDonchian(
      [bar('a', 1, 5, 1, 2), bar('b', 1, 7, 2, 3), bar('c', 1, 6, 0.5, 4)],
      2,
    );
    expect(c[0].donchian_high_20).toBeNull();
    expect(c[1]).toMatchObject({ donchian_high_20: 7, donchian_low_20: 1 });
    expect(c[2]).toMatchObject({ donchian_high_20: 7, donchian_low_20: 0.5 });
  });
});
