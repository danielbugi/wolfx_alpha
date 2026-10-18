// Sector pages: observed (point-in-time) leadership kept apart from current-membership performance, provenance and
// freshness stated, unavailable / stale / incomplete data said out loud, and navigation to sector and stock pages.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { snapshot } from '@/components/marketIntel/__tests__/fixture';
import type { SectorDetail, SectorsResponse } from '@/lib/sectors';

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
}

const state = vi.hoisted(() => ({ sectors: null as unknown, sectorsStatus: 200, detail: null as unknown, detailStatus: 200, detailBody: { detail: "Unknown sector 'zz'" } as unknown, snap: null as unknown, snapFail: false, slug: 'alpha' }));

vi.mock('next/navigation', () => ({
  useParams: () => ({ slug: state.slug }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/sectors',
}));
vi.mock('@/components/dashboard/SymbolHoverLink', () => ({ default: ({ symbol }: { symbol: string }) => <a href={`/stock/${symbol}`}>{symbol}</a> }));
vi.mock('@/services/marketIntelligenceApi', () => ({
  marketIntelligenceApi: {
    getSnapshot: vi.fn(async () => {
      if (state.snapFail) throw new Error('down');
      return state.snap;
    }),
    getHistory: vi.fn(async () => ({ available: true, sessions: [] })),
  },
}));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    apiClient: {
      get: vi.fn(async (url: string) => {
        if (url === '/api/market/sectors') {
          if (state.sectorsStatus !== 200) throw Object.assign(new Error('x'), { response: { status: state.sectorsStatus, data: { detail: 'Not Found' } } });
          return { data: state.sectors };
        }
        if (url.startsWith('/api/market/sectors/')) {
          if (state.detailStatus !== 200) throw Object.assign(new Error('x'), { response: { status: state.detailStatus, data: state.detailBody } });
          return { data: state.detail };
        }
        throw new Error(`unexpected ${url}`);
      }),
    },
  };
});

import SectorsPage from '@/app/sectors/page';
import SectorPage from '@/app/sectors/[slug]/page';

const change = (m: number | null, n = 10) => ({ median: m, up_share: m == null ? null : 60, n: m == null ? 0 : n });
const changes = (m: number | null) => ({ '1d': change(m), '1w': change(m), '1m': change(m), '3m': change(m), ytd: change(m), '1y': change(m) });
const membership = { basis: 'current', point_in_time: false, source: 'daily_fundamentals.sector (latest row)', note: 'Membership is today’s sector assignment; not a record of the sector as it was.' };

function sectorsResponse(n = 11): SectorsResponse {
  const names = ['Alpha', 'Beta', 'Gamma', 'Delta', 'E5', 'E6', 'E7', 'E8', 'E9', 'E10', 'E11'].slice(0, n);
  return {
    as_of: '2026-03-02',
    membership,
    sectors: names.map((s, i) => ({
      sector: s, slug: s.toLowerCase(), stock_count: 50, priced_count: i === 0 ? 48 : 50, industry_count: 3, market_cap: 1e11,
      change: changes(i === 1 ? null : 1 - i * 0.2), above_sma50_pct: 40, sparkline: [100, 101, 102],
    })),
  };
}

function detail(): SectorDetail {
  return {
    as_of: '2026-03-02', sector: 'Alpha', slug: 'alpha', stock_count: 2, priced_count: 1, membership, market_cap: 2e10,
    change: changes(1.5), above_sma50_pct: 50,
    industries: [{ industry: 'Software', stock_count: 2, market_cap: 2e10, change: changes(1.5), above_sma50_pct: 50 }],
    stocks: [
      { symbol: 'AAA', industry: 'Software', close: 10, change_pct: { '1d': 1, '1w': 2, '1m': 3, '3m': 4, ytd: 5, '1y': 6 }, market_cap: 1e10, pe_ratio: 20, quality_grade: 'B', rsi_14: 55, volume_ratio: 1, above_sma50: true },
      { symbol: 'BBB', industry: 'Software', close: null, change_pct: { '1d': null, '1w': null, '1m': null, '3m': null, ytd: null, '1y': null }, market_cap: 1e10, pe_ratio: null, quality_grade: null, rsi_14: null, volume_ratio: null, above_sma50: null },
    ],
    index: [{ date: '2025-09-01', level: 100 }, { date: '2026-03-02', level: 110 }],
    spx: [{ date: '2025-09-01', close: 5000 }, { date: '2026-03-02', close: 5250 }],
  };
}

beforeEach(() => {
  state.sectors = sectorsResponse();
  state.detail = detail();
  state.detailStatus = 200;
  state.sectorsStatus = 200;
  state.detailBody = { detail: "Unknown sector 'zz'" };
  state.snap = snapshot();
  state.snapFail = false;
  state.slug = 'alpha';
});

describe('Sectors comparison page', () => {
  it('shows observed leadership (session, provenance) separately from current-membership performance with its caveat', async () => {
    render(<SectorsPage />);
    const observed = await screen.findByRole('region', { name: 'Observed sector leadership' });
    expect(within(observed).getByTestId('data-stamp').textContent).toMatch(/Session\s*2026-03-02.*observed/);
    expect(within(observed).getByText('Sector leadership')).toBeTruthy();
    expect(await screen.findByText('Performance of current members')).toBeTruthy();
    expect(screen.getByTestId('membership-note').textContent).toMatch(/Current membership, not point-in-time/);
    expect(screen.queryByTestId('sector-count-note')).toBeNull();
  });

  it('links observed leadership rows and cards to sector pages', async () => {
    render(<SectorsPage />);
    const observed = await screen.findByRole('region', { name: 'Observed sector leadership' });
    expect(within(observed).getByRole('link', { name: 'Alpha' }).getAttribute('href')).toBe('/sectors/alpha');
    const cardLinks = (await screen.findAllByRole('link')).map((a) => a.getAttribute('href'));
    expect(cardLinks).toContain('/sectors/e11');
  });

  it('states sample sizes, unpriced members and a missing median instead of a zero', async () => {
    const { container } = render(<SectorsPage />);
    await screen.findByText('Performance of current members');
    expect(container.textContent).toMatch(/50 stocks \(48 priced on 2026-03-02\)/);
    expect(container.textContent).toMatch(/median stock · 1D · n=10/);
    expect(container.textContent).toMatch(/median stock · 1D · n=0/); // Beta: no values -> n=0 and a dash, not 0%
  });

  it('flags fewer than 11 sectors', async () => {
    state.sectors = sectorsResponse(9);
    render(<SectorsPage />);
    expect((await screen.findByTestId('sector-count-note')).textContent).toMatch(/9 sectors in the data \(11 expected\)/);
  });

  it('flags an observed snapshot older than the prices, and says when it is unavailable or failed', async () => {
    state.sectors = { ...sectorsResponse(), as_of: '2026-03-03' };
    const { unmount } = render(<SectorsPage />);
    expect((await screen.findByTestId('leadership-stale')).textContent).toMatch(/snapshot is for 2026-03-02; the current-membership figures below go to 2026-03-03/);
    unmount();
    state.snap = { schema: 'mi_payload_v1', available: false, reason: 'not_provisioned', message: 'Storage not provisioned.' };
    const r2 = render(<SectorsPage />);
    expect((await screen.findByTestId('leadership-unavailable')).textContent).toMatch(/not available: Storage not provisioned\.$/);
    r2.unmount();
    state.snapFail = true;
    render(<SectorsPage />);
    expect(await screen.findByText(/Couldn.t load the observed snapshot/)).toBeTruthy();
  });
});

describe('Sector detail page', () => {
  it('shows current-membership figures with their caveat and the observed context separately', async () => {
    const { container } = render(<SectorPage />);
    expect(await screen.findByText('Alpha')).toBeTruthy();
    expect(container.textContent).toMatch(/2 current stocks \(1 priced\)/);
    expect(screen.getByTestId('membership-note')).toBeTruthy();
    const observed = screen.getByText('Observed sector context').closest('section')!;
    expect(observed.textContent).toMatch(/#1 of 3 by 20-session return vs the median stock/);
    expect(observed.textContent).toMatch(/Leading/);
    expect(observed.textContent).toMatch(/70 of 118/);
    expect(observed.textContent).toMatch(/Point-in-time/);
    expect(container.textContent).toMatch(/Alpha \(current members\) vs the S&P 500/);
    expect(container.textContent).toMatch(/not point-in-time/);
  });

  it('keeps stock links and shows missing member values as dashes', async () => {
    render(<SectorPage />);
    expect((await screen.findByRole('link', { name: 'AAA' })).getAttribute('href')).toBe('/stock/AAA');
    const bbb = screen.getByRole('link', { name: 'BBB' }).closest('tr')!;
    expect(bbb.textContent).toMatch(/—/);
    expect(bbb.textContent).not.toMatch(/0\.00%/);
  });

  it('says when the sector has no observed entry, and when the snapshot is stale', async () => {
    state.slug = 'zeta';
    state.detail = { ...detail(), sector: 'Zeta', slug: 'zeta', as_of: '2026-03-04' };
    render(<SectorPage />);
    expect((await screen.findByTestId('observed-missing')).textContent).toMatch(/No observed entry for Zeta/);
    expect(screen.getByTestId('observed-stale').textContent).toMatch(/snapshot is for 2026-03-02; prices on this page go to 2026-03-04/);
  });

  it('shows "No such sector." for an unknown slug', async () => {
    state.detailStatus = 404;
    render(<SectorPage />);
    expect(await screen.findByText('No such sector.')).toBeTruthy();
  });

  it('says the sector pages are not on this server yet when the route itself is missing (older backend)', async () => {
    state.detailStatus = 404;
    state.detailBody = { detail: 'Not Found' };
    render(<SectorPage />);
    expect(await screen.findByText('Sector pages are not available on this server yet.')).toBeTruthy();
    expect(screen.queryByText('No such sector.')).toBeNull();
  });
});

describe('Sectors page against an older backend', () => {
  it('explains that the sector routes are not deployed, while observed leadership still renders', async () => {
    state.sectorsStatus = 404;
    render(<SectorsPage />);
    expect(await screen.findByText('Sector pages are not available on this server yet.')).toBeTruthy();
    expect(await screen.findByText('Sector leadership')).toBeTruthy();
  });
});
