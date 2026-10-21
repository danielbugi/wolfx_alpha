// Overview visuals: the sector heatmap (snapshot values unchanged, three colour bases, unavailable tiles, sector links),
// the breadth gauges, and the breadth trend loaded from one snapshot per recorded session (oldest first, a session that
// did not measure the value is a gap, fewer than 2 sessions draws no line).
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { MarketHistory, MarketSnapshot } from '@/services/marketIntelligenceApi';
import { snapshot } from './fixture';

const api = vi.hoisted(() => ({
  snap: null as unknown,
  bySession: {} as Record<string, unknown>,
  history: { available: true, sessions: [] } as unknown,
  calls: [] as (string | undefined)[],
}));
// the sector rotation map opens a sector with the router; jsdom has no ResizeObserver for its chart
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }), usePathname: () => '/' }));
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;
vi.mock('@/services/marketIntelligenceApi', () => ({
  marketIntelligenceApi: {
    getSnapshot: vi.fn(async (session?: string) => {
      api.calls.push(session);
      return (session ? api.bySession[session] : api.snap) as MarketSnapshot;
    }),
    getHistory: vi.fn(async () => api.history as MarketHistory),
  },
}));

import MarketIntelligencePanel from '@/components/marketIntel/MarketIntelligencePanel';
import { SCALE, tileStyle } from '@/components/marketIntel/SectorHeatmap';

const withBreadth = (session: string, pct50: number) => {
  const s = snapshot({ session_date: session });
  const m = s.coverage.measurements!.breadth!.market!;
  m.above_sma50 = { numerator: pct50 * 20, denominator: 2000, universe: 2100, missing: 100, pct: pct50, state: 'ok' };
  return s;
};

beforeEach(() => {
  api.snap = snapshot();
  api.bySession = {};
  api.calls = [];
  api.history = { available: true, sessions: [{ session_date: '2026-03-02', provenance: 'observed', regime_state: 'NEUTRAL', regime_score: 0 }] };
});

describe('sector heatmap', () => {
  it('shows each sector across 5 / 20 / 60 sessions with the snapshot values, vs the S&P 500 by default', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const map = await screen.findByTestId('sector-heatmap');
    const rows = within(map).getAllByTestId('heatmap-row');
    expect(rows.map((r) => r.textContent?.match(/[A-Z][a-z]+/)?.[0])).toEqual(['Gamma', 'Alpha', 'Delta', 'Beta']);
    expect(within(rows[1]).getByTestId('tile-20').textContent).toBe('+2.8 pp'); // Alpha vs S&P 500, 20 sessions = 2.75
    expect(within(rows[3]).getByTestId('tile-60').textContent).toBe('-5.0 pp');
    expect(within(rows[2]).getByTestId('tile-20').textContent).toBe('—'); // Delta: unavailable, never 0
    expect(within(rows[1]).getAllByRole('link')[0].getAttribute('href')).toBe('/sectors/alpha');
    expect(within(rows[1]).getByTestId('tile-20').getAttribute('title')).toContain('return +4.0%, vs S&P 500 +2.8 pp, vs median stock +5.0 pp · 120 stocks');
  });

  it('switches the colour basis to plain return and to the median stock', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const map = await screen.findByTestId('sector-heatmap');
    fireEvent.click(within(map).getByRole('radio', { name: 'Return' }));
    expect(within(within(map).getAllByTestId('heatmap-row')[1]).getByTestId('tile-20').textContent).toBe('+4.0%');
    fireEvent.click(within(map).getByRole('radio', { name: 'vs median stock' }));
    expect(within(within(map).getAllByTestId('heatmap-row')[1]).getByTestId('tile-20').textContent).toBe('+5.0 pp');
  });

  it('colours by sign and distance, saturating at the stated scale, grey when unavailable', () => {
    expect(tileStyle(null, 8).background).toBe('rgba(148,163,184,0.15)');
    expect(tileStyle(8, SCALE['20']).background).toBe('rgba(16,185,129,0.8)');
    expect(tileStyle(-80, SCALE['20']).background).toBe('rgba(239,68,68,0.8)');
    expect(tileStyle(0, 8).background).toBe('rgba(16,185,129,0.12)');
  });
});

describe('breadth gauges and trend', () => {
  it('draws a gauge per measure and keeps the exact counts', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = (await screen.findByText('Market breadth')).closest('section')!;
    expect(within(card).getAllByTestId('breadth-gauge')).toHaveLength(3);
    expect(within(card).getAllByTestId('breadth-row')[0].textContent).toMatch(/830 of 2,000\s*41\.5%/);
    expect(within(card).queryByTestId('breadth-trend')).toBeNull(); // one recorded session: no line
  });

  it('loads one snapshot per recorded session (oldest first) and draws the line; a missing value is a gap', async () => {
    api.history = {
      available: true,
      sessions: ['2026-03-02', '2026-02-27', '2026-02-26'].map((d) => ({ session_date: d, provenance: 'observed', regime_state: 'NEUTRAL', regime_score: 0 })),
    };
    api.bySession = { '2026-02-26': withBreadth('2026-02-26', 35), '2026-02-27': withBreadth('2026-02-27', 38), '2026-03-02': withBreadth('2026-03-02', 41.5) };
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = (await screen.findByText('Market breadth')).closest('section')!;
    await waitFor(() => expect(within(card).getAllByTestId('breadth-trend').length).toBeGreaterThan(0));
    expect(api.calls.filter(Boolean)).toEqual(['2026-02-26', '2026-02-27', '2026-03-02']);
    expect(within(card).getAllByTestId('breadth-trend-range')[0].textContent).toBe('35.0% → 41.5% over the last 3 recorded sessions');
    expect(card.textContent).toContain('2026-02-26 to 2026-03-02');
  });
});

describe('sector rotation', () => {
  it('names a corner from the 20- and 60-session differences vs the median stock (0 = in line, unavailable never guessed)', async () => {
    const { sectorQuadrant } = await import('@/components/marketIntel/SectorRotation');
    expect(sectorQuadrant(1, 2)).toBe('leading');
    expect(sectorQuadrant(0, 0)).toBe('leading');
    expect(sectorQuadrant(1, -2)).toBe('improving');
    expect(sectorQuadrant(-1, 2)).toBe('fading');
    expect(sectorQuadrant(-1, -2)).toBe('lagging');
    expect(sectorQuadrant(null, 2)).toBeNull();
  });

  it('builds each sector trail oldest first, ending at the current snapshot, skipping sessions without values', async () => {
    const { rotationTrails } = await import('@/components/marketIntel/SectorRotation');
    const cur = snapshot();
    const older = snapshot({ session_date: '2026-02-27' });
    older.sectors = older.sectors.map((s) => (s.sector === 'Alpha' ? { ...s, horizons: { ...s.horizons, '20': { ...s.horizons['20'], vs_univ: -1 } } } : s));
    const trails = rotationTrails(cur, [{ session: '2026-02-27', sectors: older.sectors }, { session: '2026-03-02', sectors: cur.sectors }]);
    const alpha = trails.get('Alpha')!;
    expect(alpha.map((p) => [p.session, p.y, p.head])).toEqual([['2026-02-27', -1, false], ['2026-03-02', 5, true]]);
    expect(alpha[1].label).toBe('Alpha');
    expect(trails.get('Delta')!.map((p) => p.session)).toEqual([]); // Delta has no 20-session value
  });

  it('shows the rotation card with corner counts for the current session', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = await screen.findByTestId('sector-rotation');
    // fixture vs median stock (20, 60): Gamma (3, 2) Alpha (5, 4) Beta (-2, -1); Delta unavailable
    expect(within(card).getByTestId('rot-leading').textContent).toBe('Leading 2');
    expect(within(card).getByTestId('rot-lagging').textContent).toBe('Lagging 1');
    expect(within(card).getByTestId('rotation-note').textContent).toContain('not point-in-time');
  });
});

describe('regime gauge', () => {
  it('draws the dial, a pull bar per input, and the score over the recorded sessions with the thresholds', async () => {
    api.history = {
      available: true,
      sessions: [
        { session_date: '2026-03-02', provenance: 'observed', regime_state: 'NEUTRAL', regime_score: 0.04 },
        { session_date: '2026-02-27', provenance: 'observed', regime_state: 'NEUTRAL', regime_score: -0.12 },
      ],
    };
    api.bySession = { '2026-02-27': snapshot({ session_date: '2026-02-27' }), '2026-03-02': snapshot() };
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = (await screen.findByText('Market regime')).closest('section')!;
    expect(within(card).getByTestId('regime-dial')).toBeTruthy();
    expect(within(card).getAllByTestId('regime-pull').length).toBe(snapshot().regime.components.length);
    await waitFor(() => expect(within(card).getByTestId('regime-trend-range').textContent).toContain('score -0.12 → 0.04 over the last 2 recorded sessions'));
  });
});
