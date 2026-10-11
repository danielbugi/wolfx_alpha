import { describe, expect, it } from 'vitest';
import { formatInstrumentPrice, pricePrecision, unitSuffix } from '@/lib/instruments';
import { isActiveRoute } from '@/lib/navigation';

const gold = { symbol: 'GC=F', group: 'metals', unit: '/oz' };
const corn = { symbol: 'ZC=F', group: 'agriculture', unit: '¢/bu' };
const eur = { symbol: 'EURUSD=X', group: 'currencies', unit: '' };
const jpy = { symbol: 'JPY=X', group: 'currencies', unit: '' };
const tnx = { symbol: '^TNX', group: 'rates_vol', unit: '%' };
const spx = { symbol: '^GSPC', group: 'indices', unit: '' };
const btc = { symbol: 'BTC-USD', group: 'crypto', unit: '' };

describe('instrument prices', () => {
  it('dollars only where the quote is in dollars', () => {
    expect(formatInstrumentPrice(gold, 4380.8)).toBe('$4,380.80');
    expect(formatInstrumentPrice(btc, 87170.39)).toBe('$87,170.39');
    expect(formatInstrumentPrice(spx, 7706.03)).toBe('7,706.03');
    expect(formatInstrumentPrice(corn, 452.25)).toBe('452.25¢');
    expect(formatInstrumentPrice(tnx, 4.512)).toBe('4.512%');
  });
  it('enough decimals for currencies to show a move', () => {
    expect(pricePrecision(eur, 1.0843)).toBe(4);
    expect(formatInstrumentPrice(eur, 1.0843)).toBe('1.0843');
    expect(pricePrecision(jpy, 151.234)).toBe(3);
  });
  it('unknown stays unknown', () => {
    expect(formatInstrumentPrice(gold, null)).toBe('—');
  });
  it('unit suffix drops what the price already says', () => {
    expect(unitSuffix(corn)).toBe('/bu');
    expect(unitSuffix(tnx)).toBe('');
    expect(unitSuffix(gold)).toBe('/oz');
  });
});

describe('isActiveRoute', () => {
  it('keeps a section highlighted on its inner pages', () => {
    expect(isActiveRoute('/markets/GC%3DF', '/markets')).toBe(true);
    expect(isActiveRoute('/markets', '/markets')).toBe(true);
    expect(isActiveRoute('/marketsx', '/markets')).toBe(false);
    expect(isActiveRoute('/stock/AAPL', '/')).toBe(false);
  });
});
