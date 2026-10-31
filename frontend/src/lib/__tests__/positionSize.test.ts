import { describe, expect, it } from 'vitest';
import { defaultLevels, orderTicketText, positionSize, rTargets } from '../positionSize';

describe('positionSize', () => {
  it('is held to 10% of the account when the risk budget alone would allow more', () => {
    // $500 risk / $2.50 per share = 200 shares, but 10% of a 50k account is $5k = 50 shares
    const r = positionSize(50_000, 1, 100, 97.5)!;
    expect(r.shares).toBe(50);
    expect(r.capped).toBe(true);
  });
  it('the risk budget binds when the cap does not', () => {
    expect(positionSize(100_000, 0.5, 50, 45)).toMatchObject({ shares: 100, positionValue: 5000, maxLoss: 500, capped: false });
  });
  it('works for shorts (stop above entry)', () => {
    expect(positionSize(100_000, 0.5, 50, 55)!.shares).toBe(100);
  });
  it('returns null instead of inventing a size', () => {
    expect(positionSize(null, 1, 100, 95)).toBeNull();
    expect(positionSize(0, 1, 100, 95)).toBeNull();
    expect(positionSize(10_000, 1, 100, 100)).toBeNull();
    expect(positionSize(500, 1, 100, 95)).toBeNull(); // not even one share fits
    expect(positionSize(10_000, 1, 100, 0)).toBeNull();
  });
});

describe('defaultLevels', () => {
  it('puts the stop 2 ATR below for a buy and above for a short', () => {
    expect(defaultLevels('long', 100, 3)).toEqual({ entry: 100, stop: 94 });
    expect(defaultLevels('short', 100, 3)).toEqual({ entry: 100, stop: 106 });
  });
  it('is null without a price or ATR, or when the stop would not be positive', () => {
    expect(defaultLevels('long', null, 3)).toBeNull();
    expect(defaultLevels('long', 100, null)).toBeNull();
    expect(defaultLevels('long', 5, 3)).toBeNull();
  });
});

describe('rTargets', () => {
  it('lays 1R/2R/3R on the side the stop implies', () => {
    expect(rTargets(100, 95)).toEqual([105, 110, 115]);
    expect(rTargets(100, 105)).toEqual([95, 90, 85]);
  });
  it('is null when entry equals stop or a level is missing', () => {
    expect(rTargets(100, 100)).toBeNull();
    expect(rTargets(0, 5)).toBeNull();
  });
});

describe('orderTicketText', () => {
  it('formats a long and a short, with or without a share count and targets', () => {
    const base = { symbol: 'HRI', entry: 100, stop: 95, targets: [105, 110, 115] as [number, number, number] };
    expect(orderTicketText({ ...base, direction: 'long', shares: 40 })).toBe('BUY 40 HRI LIMIT 100.00 | STOP 95.00 | TP1 105.00 / TP2 110.00 / TP3 115.00');
    expect(orderTicketText({ ...base, direction: 'short', shares: null, targets: null })).toBe('SELL SHORT HRI LIMIT 100.00 | STOP 95.00');
  });
});
