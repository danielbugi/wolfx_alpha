// The product Overview: neutral wording, no momentum board / confirmed-signal content, a data stamp that says
// "unavailable" rather than inventing a date, and no call to the internal momentum APIs.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// recharts' ResponsiveContainer needs ResizeObserver, which jsdom lacks.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
}

const calls = vi.hoisted(() => ({ board: 0, leaders: 0 }));

vi.mock('@/hooks/usePagePerf', () => ({ usePagePerf: () => ({ markLoaded: () => {} }) }));
vi.mock('@/components/earnings/EarningsCard', () => ({ default: () => null }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }), usePathname: () => '/' }));
vi.mock('@/components/marketIntel/MarketIntelligencePanel', () => ({ default: () => <div data-testid="mi-panel" /> }));
vi.mock('@/lib/topProgress', () => ({ useTopProgress: () => ({ start: () => {}, done: () => {} }) }));

const mainPage = (lastUpdated: string | null) => ({
  timestamp: '2026-10-16T21:00:00Z',
  market_summary: { last_updated: lastUpdated },
  top_gainers: [],
  top_losers: [],
  unusual_volume: [],
});
const state = vi.hoisted(() => ({ lastUpdated: '2026-10-16' as string | null }));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    apiClient: { get: vi.fn(async () => ({ data: mainPage(state.lastUpdated) })) },
    screenerApi: {
      ...actual.screenerApi,
      getMarketOverview: vi.fn(async () => ({
        market_breadth: { gainers: 1800, losers: 1100, advance_decline_ratio: 1.64, total_stocks: 2950, unusual_volume_count: 40, overbought_count: 90, oversold_count: 30 },
        market_indicators: { avg_rsi: 55, avg_volume_ratio: 1.1 },
        sector_performance: [{ sector: 'Technology', stock_count: 400, avg_performance: 0.8 }],
      })),
    },
    marketApi: {
      ...actual.marketApi,
      getIndices: vi.fn(async () => ({ indices: [] })),
      getSectorHistory: vi.fn(async () => ({ sectors: [] })),
    },
    momentumBoardApi: { getBoard: vi.fn(async () => { calls.board += 1; return { results: [], counts: {}, starred_total: 3, universe_n: 0, session_date: null }; }) },
    momentumLeadersApi: { getLeaders: vi.fn(async () => { calls.leaders += 1; return { board: null, session_date: null }; }) },
  };
});

import DashboardPage from '@/app/page';

describe('Overview (product)', () => {
  it('uses neutral wording and shows no momentum board, confirmed names or signal language', async () => {
    const { container } = render(<DashboardPage />);
    expect(await screen.findByText('Market overview')).toBeTruthy();
    const text = container.textContent ?? '';
    for (const re of [/momentum board/i, /momentum leaders/i, /confirmed/i, /signals?\b/i, /professional market analysis/i, /donchian breakout screening platform/i]) {
      expect(text).not.toMatch(re);
    }
    expect(calls.board).toBe(0);
    expect(calls.leaders).toBe(0);
    expect(screen.getByTestId('data-stamp').textContent).toMatch(/Data as of\s*2026-10-16/);
    expect(screen.getByTestId('mi-panel')).toBeTruthy();
  });

  it('says the data date is unavailable instead of inventing one', async () => {
    state.lastUpdated = null;
    render(<DashboardPage />);
    expect(await screen.findByText('Market overview')).toBeTruthy();
    expect(screen.getByTestId('data-stamp').textContent).toMatch(/Data as of\s*unavailable/);
    state.lastUpdated = '2026-10-16';
  });
});
