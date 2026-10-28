import { describe, expect, it } from 'vitest';
import { orderTicketText, positionSize } from '../positionSize';

describe('positionSize', () => {
  it('sizes by the risk budget', () => {
    // 50k account, 1% = $500 risk, $2.50 per share -> 200 shares ($10k = 20% of account -> capped at 10% = $5k -> 100)
    const r = positionSize(50_000, 1, 100, 97.5)!;
    expect(r.shares).toBe(50);
    expect(r.capped).toBe(true);
  });
  it('risk budget binds when the cap does not', () => {
    const r = positionSize(100_000, 0.5, 50, 45)!; // $500 / $5 = 100 shares = $5k = 5%
    expect(r).toMatchObject({ shares: 100, positionValue: 5000, maxLoss: 500, capped: false });
  });
  it('works for shorts (stop above entry)', () => {
    expect(positionSize(100_000, 0.5, 50, 55)!.shares).toBe(100);
  });
  it('returns null instead of inventing a size', () => {
    expect(positionSize(null, 1, 100, 95)).toBeNull();
    expect(positionSize(0, 1, 100, 95)).toBeNull();
    expect(positionSize(10_000, 1, 100, 100)).toBeNull();
    expect(positionSize(500, 1, 100, 95)).toBeNull(); // not even one share fits
  });
});

describe('orderTicketText', () => {
  it('formats a long and a short', () => {
    const base = { symbol: 'HRI', shares: 40, entry: 100, stop: 95, tp1: 105, tp2: 110, tp3: 115 };
    expect(orderTicketText({ ...base, direction: 'long' })).toBe('BUY 40 HRI LIMIT 100.00 | STOP 95.00 | TP1 105.00 / TP2 110.00 / TP3 115.00');
    expect(orderTicketText({ ...base, direction: 'short', shares: null })).toMatch(/^SELL SHORT HRI LIMIT/);
  });
});
