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
// The Momentum Today card reads the session's list ranks (through services/dayListsApi) to rank Today's Movers, by owner
// decision 2026-10-21; it has its own test (components/dashboard/__tests__/MomentumTopCard.test.tsx). The page itself
// must still make no direct call to the internal board or leaders APIs.
vi.mock('@/components/dashboard/MomentumTopCard', () => ({ default: () => <div data-testid="momentum-top" /> }));

const mainPage = (lastUpdated: string | null) => ({
  timestamp: '2026-10-16T21:00:00Z',
  market_summary: { last_updated: lastUpdated },
  top_gainers: [],
  top_losers: [],
  unusual_volume: [],
});
const state = vi.hoisted(() => ({ lastUpdated: '2026-10-16' as string | null, overview: null as null | Record<string, unknown> }));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    apiClient: { get: vi.fn(async () => ({ data: mainPage(state.lastUpdated) })) },
    screenerApi: {
      ...actual.screenerApi,
      getMarketOverview: vi.fn(async () => state.overview ?? ({
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
  it('renders a partially loaded session (no indicators yet) without crashing: every unknown figure is a dash', async () => {
    state.overview = {
      market_breadth: { total_stocks: 3043, gainers: 990, losers: 0, advance_decline_ratio: null, unusual_volume_count: 0, overbought_count: 0, oversold_count: 0 },
      market_indicators: { avg_rsi: null, avg_volume_ratio: null, total_market_cap: null },
      sector_performance: [{ sector: 'Energy', stock_count: 126, avg_performance: null }, { sector: 'Technology', stock_count: 425, avg_performance: 0.45 }],
      session: '2026-10-27',
      coverage: { change_known: 3043, volume_ratio_known: 0, rsi_known: 0, market_cap_known: 0 },
    };
    const { container } = render(<DashboardPage />);
    expect((await screen.findByTestId('activity-title')).textContent).toContain('session 2026-10-27');
    expect(container.textContent).toMatch(/— \/ —/);
    expect(container.textContent).toContain('known for 0');
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
    state.overview = null;
  });

  it('renders a payload with whole blocks missing (older or partial response) without crashing', async () => {
    state.overview = { sector_performance: null, session: null } as unknown as Record<string, unknown>;
    const { container } = render(<DashboardPage />);
    expect((await screen.findByTestId('activity-title')).textContent).toBe("Today's activity latest session");
    expect(container.textContent).toMatch(/— of —/);
    expect(container.textContent).not.toMatch(/NaN|undefined/);
    state.overview = null;
  });

  it('states the market-activity session, its population, links the unusual-volume tile to the same Screener view, and shows missing averages as dashes', async () => {
    state.overview = {
      market_breadth: { gainers: 990, losers: 1850, advance_decline_ratio: 0.54, total_stocks: 3043, unusual_volume_count: 80, overbought_count: 202, oversold_count: 254 },
      market_indicators: { avg_rsi: null, avg_volume_ratio: null, total_market_cap: null },
      sector_performance: [{ sector: 'Technology', stock_count: 425, avg_performance: 0.45 }],
      session: '2026-10-23',
      population: { min_close: 1, liquidity_floor: null, universe: 3060, description: '' },
      coverage: { change_known: 3043, volume_ratio_known: 3040, rsi_known: 3001, market_cap_known: 3000 },
    };
    const { container } = render(<DashboardPage />);
    expect((await screen.findByTestId('activity-title')).textContent).toBe("Today's activity session 2026-10-23");
    expect(screen.getByTestId('activity-unusual-link').getAttribute('href')).toBe('/screener?min_close=1&min_volume_ratio=2&sort=volume_ratio&order=desc&session=2026-10-23');
    expect(container.textContent).toContain('all stocks priced $1+, no liquidity floor');
    expect(container.textContent).toContain('known for 3001');
    expect(container.textContent).toMatch(/— \/ —/);
    expect(container.textContent).not.toMatch(/\b50 \/|1\.00x/);
    state.overview = null;
  });

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
    expect(screen.getByTestId('momentum-top')).toBeTruthy();
    // breadth appears once (in the market-intelligence panel); the legacy "N% up" tile and A/D box are gone
    expect(text).not.toMatch(/% up/);
    expect(text).not.toMatch(/Advance\/Decline/);
    expect(text).toMatch(/volume at least 2× its 10-day average/);
    expect(text).toMatch(/14-session RSI/);
  });

  it('says the data date is unavailable instead of inventing one', async () => {
    state.lastUpdated = null;
    render(<DashboardPage />);
    expect(await screen.findByText('Market overview')).toBeTruthy();
    expect(screen.getByTestId('data-stamp').textContent).toMatch(/Data as of\s*unavailable/);
    state.lastUpdated = '2026-10-16';
  });
});
