import { describe, expect, it } from 'vitest';
import {
  REGIME_LABEL, capturedLabel, componentValue, describeCount, fmtSigned, isOlderThan, scorePosition, sectorLeaders, strengthWord,
} from '@/lib/marketIntel';
import { snapshot } from '@/components/marketIntel/__tests__/fixture';

describe('market intelligence helpers', () => {
  it('words the regime neutrally', () => {
    expect(REGIME_LABEL).toEqual({ RISK_ON: 'Broadly positive', NEUTRAL: 'Mixed', RISK_OFF: 'Broadly negative', UNAVAILABLE: 'Unavailable' });
  });

  it('describes a count with numerator and denominator, never a bare percentage', () => {
    expect(describeCount({ numerator: 861, denominator: 3053, universe: 3063, missing: 10, pct: 28.2018, state: 'ok' })).toEqual({
      ratio: '861 of 3,053', pct: '28.2%', note: '10 not evaluable',
    });
    const thin = describeCount({ numerator: 400, denominator: 900, universe: 2100, missing: 1200, pct: null, state: 'insufficient_eligible' });
    expect(thin.pct).toBeNull();
    expect(thin.note).toMatch(/too few evaluable stocks \(900\)/);
    expect(describeCount(undefined)).toEqual({ ratio: '—', pct: null, note: 'not measured' });
    expect(describeCount({ numerator: null, denominator: null, universe: null, missing: null, pct: null, state: 'no_session' }).note).toBe('no market session');
  });

  it('compares snapshot and price dates as dates only, and cannot tell when either is missing', () => {
    expect(isOlderThan('2026-10-15', '2026-10-16')).toBe(true);
    expect(isOlderThan('2026-10-16', '2026-10-16')).toBe(false);
    expect(isOlderThan('2026-10-16', null)).toBeNull();
    expect(isOlderThan(null, '2026-10-16')).toBeNull();
    expect(isOlderThan('16/10/2026', '2026-10-16')).toBeNull();
  });

  it('places the score on a -1..+1 bar and keeps a missing score missing', () => {
    expect(scorePosition(-1)).toBe(0);
    expect(scorePosition(0)).toBe(50);
    expect(scorePosition(1)).toBe(100);
    expect(scorePosition(2)).toBe(100);
    expect(scorePosition(null)).toBeNull();
  });

  it('formats component values in their own units and missing as a dash', () => {
    expect(componentValue('c1_index_trend', 2)).toBe('2 of 3 conditions');
    expect(componentValue('c3_breadth_sma50', 28.2)).toBe('28.2% of stocks');
    expect(componentValue('c5_net_highs_lows', -0.6)).toBe('-0.6 pp of stocks');
    expect(componentValue('c6_volatility', 19.99)).toBe('20.0');
    expect(componentValue('c7_risk_appetite', -4.98)).toBe('-5.0 pp');
    expect(componentValue('c2_index_momentum', null)).toBe('—');
    expect(fmtSigned(Number.NaN)).toBe('—');
  });

  it('orders sectors by stored rank with unranked last, joining strength and breadth without inventing either', () => {
    const rows = sectorLeaders(snapshot());
    expect(rows.map((r) => r.sector)).toEqual(['Alpha', 'Gamma', 'Beta', 'Delta']);
    expect(rows[0]).toMatchObject({ rank: 1, members: 120, ret20: 4, vsUniverse20: 5, vsSpx20: 2.75, strengthState: 'leading' });
    expect(rows[1].aboveSma50).toBeNull(); // Gamma has no breadth measurement
    expect(rows[3]).toMatchObject({ rank: null, ret20: null, vsUniverse20: null });
    expect(strengthWord('not_available')).toBe('—');
    expect(strengthWord('in_line')).toBe('In line');
    expect(strengthWord(null)).toBe('—');
  });

  it('labels the capture time with its own offset', () => {
    expect(capturedLabel('2026-10-17 06:46:04.789987+03')).toBe('captured 2026-10-17 06:46 (UTC+03)');
    expect(capturedLabel('2026-10-17T03:46:04Z')).toBe('captured 2026-10-17 03:46 (UTC)');
    expect(capturedLabel('yesterday')).toBeNull();
    expect(capturedLabel(null)).toBeNull();
  });
});
