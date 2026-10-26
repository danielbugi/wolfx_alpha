// Earnings date confidence: one wording on every surface; an unconfirmed date is never an ordinary countdown.
import { describe, expect, it } from 'vitest';
import { isUnverified, previouslyReported, shortDay } from '@/lib/earningsConfidence';
import { catalyst } from '@/lib/momentumToday';
import type { ScreenerRow } from '@/services/screenerV2Api';

const row = (over: Partial<ScreenerRow>) =>
  ({ days_since_earnings: null, days_to_earnings: 2, earnings_surprise_pct: null, last_earnings_date: null, next_earnings_date: '2026-10-28', ...over }) as ScreenerRow;

describe('earnings confidence', () => {
  it('words a previously reported date the same way everywhere', () => {
    expect(shortDay('2026-10-28')).toBe('Oct 28');
    expect(previouslyReported('2026-10-28')).toBe('Previously reported: Oct 28 — not confirmed by latest check');
    expect(isUnverified('unverified')).toBe(true);
    expect(isUnverified('confirmed')).toBe(false);
    expect(isUnverified(undefined)).toBe(false);          // an older backend: unchanged behaviour
  });

  it('Screener rows: a confirmed date counts down; an unverified one never does', () => {
    expect(catalyst(row({ next_earnings_confidence: 'confirmed' })).label).toBe('Earnings in 2d');
    const c = catalyst(row({ next_earnings_confidence: 'unverified' }));
    expect(c.label).toBe('Previously reported: Oct 28 — not confirmed by latest check');
    expect(c.label).not.toMatch(/in \d+d/);
    expect(c.tone).toBe('upcoming');                       // not styled as "reports soon"
    expect(catalyst(row({ days_to_earnings: null, next_earnings_date: null })).tone).toBe('none');   // unknown stays unknown
  });
});
