// S7 critical fixes: Deep Value pages its results, Earnings shows no screen/board signals and fits a phone, the dashboard
// Watchlist card makes ONE batched request, and Markets dates exchanges and crypto separately and names uncovered
// instruments honestly.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

const api = vi.hoisted(() => ({ scan: vi.fn(), get: vi.fn(), search: vi.fn(), getPriceHistory: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    deepValueApi: { scan: api.scan },
    apiClient: { ...actual.apiClient, get: api.get },
    stockApi: { ...actual.stockApi, getPriceHistory: api.getPriceHistory },
  };
});
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: { search: api.search, definitions: vi.fn() } }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, email: 'o@x.test', role: 'owner' } }) }));
vi.mock('@/components/dashboard/SymbolHoverLink', () => ({ default: ({ symbol }: { symbol: string }) => <span>{symbol}</span> }));

import AlertsPage from '@/app/alerts/page';
import { TURNAROUND_PAGE, WATCH_PAGE } from '@/lib/deepValueView';
import EarningsTable from '@/components/earnings/EarningsTable';
import WatchlistCard from '@/components/dashboard/WatchlistCard';
import MarketsPage from '@/app/markets/page';
import type { DeepValueEntry } from '@/services/api';
import type { EarningsRow } from '@/lib/earningsCalendar';

const entry = (i: number, over: Partial<DeepValueEntry> = {}): DeepValueEntry => ({
  symbol: `DV${i}`, sector: 'Energy', current_price: 10 + i, price_date: '2026-10-16', distance_from_low_pct: 5, donchian_low_12m: 9,
  valuation_score: 15, overall_quality_score: null, quality_grade: null, pe_ratio: 8, market_cap: 1e9, is_turnaround: false, ...over,
});

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  window.localStorage.clear();
});

describe('Deep Value', () => {
  it('renders the first page of each section with Show more, and a freshness stamp', async () => {
    api.scan.mockResolvedValue({
      turnaround_alerts: Array.from({ length: 30 }, (_, i) => entry(i, { symbol: `TA${i}`, is_turnaround: true, latest_net_income: 1, previous_net_income: -1, latest_quarter: 'Q2', previous_quarter: 'Q1' })),
      deep_value_watch: Array.from({ length: 120 }, (_, i) => entry(i)),
      excluded_balance_sheet: 3,
    });
    render(<AlertsPage />);
    await screen.findByText('Showing 50 of 120 stocks');
    expect(screen.getByText(`Showing ${TURNAROUND_PAGE} of 30 turnaround flips`)).toBeTruthy();
    expect(screen.getByText('DV49')).toBeTruthy();
    expect(screen.queryByText('DV50')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: `Show ${WATCH_PAGE} more` }));
    expect(screen.getByText('DV99')).toBeTruthy();
    expect(screen.getByText('Showing 100 of 120 stocks')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Show 20 more' }));
    expect(screen.getByText('DV119')).toBeTruthy();
    expect(screen.queryByText(/Showing \d+ of 120/)).toBeNull();
    expect(screen.getByTestId('data-stamp').textContent).toContain('2026-10-16');
  });
});

describe('Earnings table (product view)', () => {
  const row = (symbol: string, over: Partial<EarningsRow> = {}): EarningsRow => ({
    symbol, date: '2026-10-20', sector: 'Industrials', close: 100, marketCap: null, signal: 'breakout', starred: true, onWatchlist: false,
    epsEstimate: 1.2, epsActual: null, surprisePct: null, beats: 3, judged: 4, ...over,
  });

  it('shows no board signal, star or breakout wording even when the row carries them', () => {
    const { container } = render(<EarningsTable rows={[row('LMT'), row('RTX', { signal: 'near_breakout' as EarningsRow['signal'] })]} histories={{}} ariaLabel="Earnings" />);
    expect(container.textContent).not.toMatch(/breakout|board|★/i);
    expect(screen.queryByRole('columnheader', { name: /board/i })).toBeNull();
  });

  it('moves the sector under the symbol on a phone and keeps the table in its own scroller', () => {
    const { container } = render(<EarningsTable rows={[row('LMT')]} histories={{}} ariaLabel="Earnings" />);
    const sectorHeader = screen.getByRole('columnheader', { name: /sector/i });
    expect(sectorHeader.className).toContain('hidden');
    expect(sectorHeader.className).toContain('sm:table-cell');
    const firstCell = screen.getAllByRole('rowheader').concat(screen.getAllByRole('gridcell'))[0];
    expect(within(firstCell.closest('tr')!).getAllByText('Industrials').some((el) => el.className.includes('sm:hidden'))).toBe(true);
    expect((container.firstElementChild as HTMLElement).className).toContain('overflow-x-auto');
  });
});

describe('dashboard Watchlist card', () => {
  it('loads every saved symbol in ONE Screener request (no per-symbol price requests)', async () => {
    window.localStorage.setItem('watchlist_symbols', JSON.stringify(['NVDA', 'AAPL', 'ZZZZ']));
    api.search.mockResolvedValue({ session: '2026-10-16', rows: [{ symbol: 'NVDA', close: 214.13, change_1d: -1.43 }, { symbol: 'AAPL', close: 343.46, change_1d: 0.43 }] });
    render(<WatchlistCard />);
    await screen.findByText('$214.13');
    expect(api.search).toHaveBeenCalledTimes(1);
    expect(api.search.mock.calls[0][0].filter(([k]: [string]) => k === 'symbol').map(([, v]: [string, string]) => v)).toEqual(['NVDA', 'AAPL', 'ZZZZ']);
    expect(api.getPriceHistory).not.toHaveBeenCalled();
    expect(screen.getByText('-1.43%')).toBeTruthy();
    expect(screen.getByText('no bar on 2026-10-16')).toBeTruthy();
  });

  it('keeps saved symbols listed when the request fails', async () => {
    window.localStorage.setItem('watchlist_symbols', JSON.stringify(['NVDA', 'AAPL']));
    api.search.mockRejectedValue(new Error('down'));
    render(<WatchlistCard />);
    await screen.findByText('Prices are unavailable right now; your saved stocks are kept.');
    expect(screen.getAllByTestId('watchlist-card-row')).toHaveLength(2);
    expect(JSON.parse(window.localStorage.getItem('watchlist_symbols')!)).toEqual(['NVDA', 'AAPL']);
  });
});

describe('Markets freshness and coverage wording', () => {
  it('dates exchanges and crypto separately, does not flag exchange rows stale because of crypto, and says "not covered yet"', async () => {
    const inst = (symbol: string, name: string, group: string, latest_date: string | null, has_data = true) => ({
      symbol, name, group, unit: '', has_data, latest_date, close: has_data ? 100 : null,
      change_pct: { '1d': 1, '1w': null, '1m': null, '3m': null, ytd: null, '1y': null }, sparkline: [],
    });
    api.get.mockResolvedValue({
      data: {
        groups: [{ key: 'indices', label: 'Indices' }, { key: 'metals', label: 'Metals' }, { key: 'crypto', label: 'Crypto' }],
        instruments: [inst('^GSPC', 'S&P 500', 'indices', '2026-10-16'), inst('GC=F', 'Gold', 'metals', '2026-10-16'), inst('SI=F', 'Silver', 'metals', null, false), inst('BTC-USD', 'Bitcoin', 'crypto', '2026-10-18')],
      },
    });
    const { container } = render(<MarketsPage />);
    await screen.findByText('S&P 500');
    expect(screen.getByTestId('data-stamp').textContent).toMatch(/^Data as of\s*2026-10-16·exchange closes · crypto as of 2026-10-18$/);
    expect(container.textContent).not.toContain('last close 2026-10-16');
    expect(container.textContent).toContain('Not covered yet: Silver.');
    expect(container.textContent).not.toMatch(/next data update/);
  });
});
