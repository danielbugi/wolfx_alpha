// Overview market chart: tabs for the main indices and Bitcoin, one request per instrument (cached when switching back),
// the stock-page chart with no plan, last close / day change / close date, Bitcoin's every-day note, unavailable states,
// and the remembered tab.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const get = vi.hoisted(() => vi.fn());
const chart = vi.hoisted(() => ({ props: null as null | Record<string, unknown> }));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, apiClient: { ...actual.apiClient, get } };
});
vi.mock('@/components/charts/StockChart', () => ({
  default: (props: Record<string, unknown>) => {
    chart.props = props;
    return <div data-testid="stock-chart">{(props.bars as unknown[]).length} bars</div>;
  },
}));

import MarketChartCard, { _clearMarketChartCache, HISTORY_DAYS } from '@/components/dashboard/MarketChartCard';

const hist = (symbol: string, name: string, group: string, closes: number[], lastDate = '2026-10-16') => ({
  symbol, name, group, unit: '',
  bars: closes.map((c, i) => ({ date: i === closes.length - 1 ? lastDate : `2026-10-${String(10 + i).padStart(2, '0')}`, open: c, high: c + 1, low: c - 1, close: c, volume: null, donchian_high_20: null, donchian_low_20: null })),
});
const DATA: Record<string, ReturnType<typeof hist>> = {
  '^GSPC': hist('^GSPC', 'S&P 500', 'indices', [7700, 7659.44]),
  '^IXIC': hist('^IXIC', 'Nasdaq Composite', 'indices', [26000, 26140.03]),
  'BTC-USD': hist('BTC-USD', 'Bitcoin', 'crypto', [81000, 81356.02], '2026-10-18'),
};

beforeEach(() => {
  window.localStorage.clear();
  _clearMarketChartCache();
  get.mockReset();
  get.mockImplementation(async (url: string) => {
    const sym = decodeURIComponent(url.split('/')[4]);
    if (!DATA[sym]) throw { response: { status: 404 } };
    return { data: DATA[sym] };
  });
});

describe('MarketChartCard', () => {
  it('opens on the S&P 500 with its last close, day change and close date, on the stock-page chart', async () => {
    render(<MarketChartCard />);
    expect((await screen.findByTestId('stock-chart')).textContent).toBe('2 bars');
    expect(get).toHaveBeenCalledWith('/api/market/instruments/%5EGSPC/history', { params: { days: HISTORY_DAYS } });
    expect(chart.props?.plan).toBeNull();
    expect(screen.getByRole('heading', { name: 'S&P 500' })).toBeTruthy();
    expect(screen.getByText('7,659.44')).toBeTruthy();
    expect(screen.getByTestId('market-chart-change').textContent).toBe(`${((7659.44 / 7700 - 1) * 100).toFixed(2)}%`);
    expect(screen.getByTestId('market-chart-asof').textContent).toBe('close 2026-10-16');
    expect(screen.getByRole('link', { name: 'Full S&P 500 page →' }).getAttribute('href')).toBe('/markets/%5EGSPC');
  });

  it('switches tabs with one request per instrument and none when returning to a loaded tab', async () => {
    render(<MarketChartCard />);
    await screen.findByTestId('stock-chart');
    fireEvent.click(screen.getByRole('radio', { name: 'Nasdaq' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Nasdaq Composite' })).toBeTruthy());
    fireEvent.click(screen.getByRole('radio', { name: 'Bitcoin' }));
    await waitFor(() => expect(screen.getByTestId('market-chart-asof').textContent).toBe('close 2026-10-18 · trades every day'));
    expect(screen.getByText('$81,356.02')).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'S&P 500' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'S&P 500' })).toBeTruthy());
    expect(get.mock.calls.map((c) => c[0])).toEqual([
      '/api/market/instruments/%5EGSPC/history',
      '/api/market/instruments/%5EIXIC/history',
      '/api/market/instruments/BTC-USD/history',
    ]);
  });

  it('remembers the last tab in this browser', async () => {
    const { unmount } = render(<MarketChartCard />);
    await screen.findByTestId('stock-chart');
    fireEvent.click(screen.getByRole('radio', { name: 'Bitcoin' }));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Bitcoin' })).toBeTruthy());
    unmount();
    render(<MarketChartCard />);
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Bitcoin' })).toBeTruthy());
    expect(screen.getByRole('radio', { name: 'Bitcoin' }).getAttribute('aria-checked')).toBe('true');
  });

  it('says when a market is unavailable or not on this server, without breaking the page', async () => {
    render(<MarketChartCard />);
    await screen.findByTestId('stock-chart');
    fireEvent.click(screen.getByRole('radio', { name: 'Dow' })); // not in DATA -> 404
    expect((await screen.findByTestId('market-chart-error')).textContent).toBe('This market is not available on this server yet.');
    get.mockRejectedValueOnce(new Error('down'));
    fireEvent.click(screen.getByRole('radio', { name: 'Russell 2000' }));
    expect((await screen.findByTestId('market-chart-error')).textContent).toBe('This chart is unavailable right now.');
  });
});
