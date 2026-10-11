import { describe, expect, it } from 'vitest';
import { marketStatus, MarketCalendar, zoneParts } from '@/lib/marketSession';

// Thanksgiving week 2026: Thu 11-26 is a holiday (absent), Fri 11-27 closes early at 13:00.
const cal: MarketCalendar = {
  timezone: 'America/New_York',
  regular_open: '09:30',
  source: 'alpaca',
  holidays_known: true,
  sessions: [
    { date: '2026-11-24', close: '16:00' },
    { date: '2026-11-25', close: '16:00' },
    { date: '2026-11-27', close: '13:00' },
    { date: '2026-11-30', close: '16:00' },
  ],
};
// EST (UTC-5) in late November
const at = (isoNy: string) => new Date(`${isoNy}-05:00`);

describe('marketStatus', () => {
  it('open during regular hours, with time to the close', () => {
    const s = marketStatus(at('2026-11-25T10:00:00'), cal);
    expect(s.state).toBe('open');
    expect(s.detail).toBe('closes in 6h 0m');
  });
  it('pre-market before 09:30 counts down to the open', () => {
    const s = marketStatus(at('2026-11-25T08:45:00'), cal);
    expect(s).toMatchObject({ state: 'pre', label: 'Pre-market' });
    expect(s.detail).toBe('opens today 09:30 · in 45m');
  });
  it('after hours until 4h past the close, then closed', () => {
    expect(marketStatus(at('2026-11-25T17:00:00'), cal).state).toBe('after');
    expect(marketStatus(at('2026-11-25T21:00:00'), cal).state).toBe('closed');
  });
  it('a holiday is named and skipped to the next session', () => {
    const s = marketStatus(at('2026-11-26T11:00:00'), cal);
    expect(s.label).toBe('Closed · market holiday');
    expect(s.detail).toMatch(/^opens Fri 09:30 ET · in 22h 30m$/);
  });
  it('an early close is shown', () => {
    const s = marketStatus(at('2026-11-27T12:00:00'), cal);
    expect(s).toMatchObject({ state: 'open', earlyClose: '13:00' });
    expect(s.detail).toBe('closes early at 13:00 · in 1h 0m');
  });
  it('weekend says weekend and points to Monday', () => {
    const s = marketStatus(at('2026-11-28T12:00:00'), cal);
    expect(s.label).toBe('Closed · weekend');
    expect(s.detail.startsWith('opens Mon 09:30 ET')).toBe(true);
  });
  it('without a verified calendar a weekday is not called a holiday', () => {
    const s = marketStatus(at('2026-11-26T11:00:00'), { ...cal, holidays_known: false });
    expect(s.label).toBe('Market closed');
  });
});

describe('zoneParts', () => {
  it('reads wall-clock time in a time zone', () => {
    const p = zoneParts(new Date('2026-10-11T12:00:00Z'), 'Asia/Jerusalem');
    expect(p.isoDate).toBe('2026-10-11');
    expect(p.minutes).toBe(15 * 60); // IDT = UTC+3
  });
});
