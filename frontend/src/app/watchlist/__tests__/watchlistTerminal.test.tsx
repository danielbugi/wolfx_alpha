// Watchlist page, terminal layout: chart (left) for the selected symbol on the stock page's TradingView chart, the list
// panel (right) with last close / day change from ONE Screener request, row and arrow-key selection, Performance (default) /
// Chart / Details tabs with the last view remembered, several lists (create, switch, rename, delete, main list protected), the Screener star adding to the
// ACTIVE list, the 30-symbol refusal, and the backend being down never touching any list.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { QueryPairs, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';

const scr = vi.hoisted(() => ({ search: vi.fn(), definitions: vi.fn(), exportCsv: vi.fn() }));
const stock = vi.hoisted(() => ({ getPriceHistory: vi.fn(), getEarnings: vi.fn() }));
const chart = vi.hoisted(() => ({ props: null as null | Record<string, unknown>, renders: 0 }));
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(''),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/watchlist',
}));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: scr }));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, stockApi: { ...actual.stockApi, ...stock } };
});
vi.mock('@/components/charts/StockChart', () => ({
  default: (props: Record<string, unknown>) => {
    chart.props = props;
    chart.renders++;
    return <div data-testid="stock-chart">{(props.bars as unknown[]).length} bars</div>;
  },
}));
vi.mock('@/components/watchlist/WatchlistPerformance', () => ({
  default: ({ saved, rows, session }: { saved: string[]; rows: Record<string, unknown>; session: string | null }) => (
    <div data-testid="perf-stub" data-rows={Object.keys(rows ?? {}).join(',')} data-session={session ?? ''}>{saved.join(',')}</div>
  ),
}));
vi.mock('@/components/watchlist/WatchlistDetails', () => ({ default: () => <div data-testid="details-stub" /> }));

import WatchlistPage from '@/app/watchlist/page';
import ScreenerPage from '@/app/screener/page';
import { _clearChartCache } from '@/components/watchlist/SymbolChart';

const row = (symbol: string, over: Partial<ScreenerRow> = {}) =>
  ({ symbol, sector: 'Technology', industry: 'Semiconductors', close: 100, change_1d: 1.5, dv_bars: 20, ...over }) as ScreenerRow;
const result = (rows: ScreenerRow[]) =>
  ({ session: '2026-10-16', previous_session: '2026-10-15', universe: 3000, sectors: [], page: 1, page_size: 50, rows, total: rows.length, recent_sessions: ['2026-10-16'], coverage: { rs: 3000, technical: 3000, dollar_vol_20: 3000, fundamentals: 3000 }, dollar_volume_window: { from: null, to: '2026-10-16', sessions: 20 } }) as unknown as ScreenerResult;
const symbolsOf = (pairs: QueryPairs) => pairs.filter(([k]) => k === 'symbol').map(([, v]) => v);
const panelRows = () => screen.queryAllByTestId('panel-row').map((r) => within(r).getAllByRole('button')[0].getAttribute('data-symbol'));
const save = (s: string[]) => window.localStorage.setItem('watchlist_symbols', JSON.stringify(s));

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem('watchlist_view', 'chart'); // the terminal tests start on the Chart view; the default is tested below
  _clearChartCache();
  chart.props = null;
  Object.values(scr).forEach((f) => f.mockReset());
  Object.values(stock).forEach((f) => f.mockReset());
  scr.definitions.mockResolvedValue({ metrics: [], families: {}, presets: [], sort_keys: [], limits: { max_page_size: 200, max_export_rows: 5000 } });
  scr.search.mockImplementation(async (pairs: QueryPairs) => result(symbolsOf(pairs).filter((s) => s !== 'ZZZZ').map((s, i) => row(s, { close: 100 + i, change_1d: i % 2 ? -1.2 : 2.4 }))));
  stock.getPriceHistory.mockImplementation(async (s: string) => ({ bars: [{ date: '2026-10-15', close: 1 }, { date: '2026-10-16', close: 2 }], symbol: s }));
  stock.getEarnings.mockResolvedValue({ earnings_dates: [{ date: '2026-11-17' }] });
});

describe('Watchlist terminal layout', () => {
  it('charts the first symbol on the TradingView chart and lists the active list with ONE Screener request', async () => {
    save(['NVDA', 'AAPL', 'ZZZZ']);
    render(<WatchlistPage />);
    expect((await screen.findByTestId('stock-chart')).textContent).toBe('2 bars');
    expect(stock.getPriceHistory).toHaveBeenCalledWith('NVDA', 730);
    expect(chart.props?.plan).toBeNull();
    expect(chart.props?.earnings).toEqual([{ date: '2026-11-17' }]);
    expect(screen.getByTestId('symbol-chart').textContent).toContain('NVDA');
    expect(panelRows()).toEqual(['NVDA', 'AAPL', 'ZZZZ']);
    await waitFor(() => expect(screen.getAllByTestId('panel-row')[0].textContent).toContain('$100.00'));
    expect(scr.search).toHaveBeenCalledTimes(1);
    expect(symbolsOf(scr.search.mock.calls[0][0])).toEqual(['NVDA', 'AAPL', 'ZZZZ']);
    expect(screen.getAllByTestId('panel-row')[2].textContent).toContain('no bar on 2026-10-16');
    expect(screen.getByTestId('panel-footer').textContent).toContain('Closes for the 2026-10-16 session');
    expect(screen.getByTestId('data-stamp').textContent).toBe('Data as of 2026-10-16·end-of-day prices · same data as the Screener');
  });

  it('a click or the arrow keys switch the charted symbol; re-selecting a symbol refetches nothing', async () => {
    save(['NVDA', 'AAPL', 'MSFT']);
    render(<WatchlistPage />);
    await screen.findByTestId('stock-chart');
    fireEvent.click(screen.getByRole('button', { name: /^AAPL/ }));
    await waitFor(() => expect(screen.getByTestId('symbol-chart').textContent).toContain('AAPL'));
    fireEvent.keyDown(screen.getByRole('list', { name: /Symbols in/ }), { key: 'ArrowDown' });
    await waitFor(() => expect(screen.getByTestId('symbol-chart').textContent).toContain('MSFT'));
    fireEvent.keyDown(screen.getByRole('list', { name: /Symbols in/ }), { key: 'ArrowUp' });
    fireEvent.keyDown(screen.getByRole('list', { name: /Symbols in/ }), { key: 'ArrowUp' });
    await waitFor(() => expect(screen.getByTestId('symbol-chart').textContent).toContain('NVDA'));
    expect(stock.getPriceHistory.mock.calls.map((c) => c[0])).toEqual(['NVDA', 'AAPL', 'MSFT']);
    expect(screen.getByRole('button', { name: /^NVDA/ }).getAttribute('aria-current')).toBe('true');
  });

  it('opens on Performance by default with the ONE Screener request passed in, and remembers the last view', async () => {
    window.localStorage.removeItem('watchlist_view');
    save(['NVDA', 'AAPL', 'ZZZZ']);
    const { unmount } = render(<WatchlistPage />);
    const stub = await screen.findByTestId('perf-stub');
    expect(stub.textContent).toBe('NVDA,AAPL,ZZZZ');
    await waitFor(() => expect(screen.getByTestId('perf-stub').getAttribute('data-rows')).toBe('NVDA,AAPL'));
    expect(screen.getByTestId('perf-stub').getAttribute('data-session')).toBe('2026-10-16');
    expect(scr.search).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('stock-chart')).toBeNull(); // no TradingView history fetched for the default view
    fireEvent.click(screen.getByRole('radio', { name: 'Details' }));
    expect(window.localStorage.getItem('watchlist_view')).toBe('details');
    unmount();
    render(<WatchlistPage />);
    expect(await screen.findByTestId('details-stub')).toBeTruthy();
  });

  it('falls back to Performance when the stored view is unknown', async () => {
    window.localStorage.setItem('watchlist_view', 'bogus');
    save(['NVDA']);
    render(<WatchlistPage />);
    expect(await screen.findByTestId('perf-stub')).toBeTruthy();
  });

  it('switches between Performance, Chart and Details; picking a stock in the list charts it', async () => {
    save(['NVDA', 'AAPL']);
    render(<WatchlistPage />);
    await screen.findByTestId('stock-chart');
    fireEvent.click(screen.getByRole('radio', { name: 'Performance' }));
    expect(screen.getByTestId('perf-stub').textContent).toBe('NVDA,AAPL');
    fireEvent.click(screen.getByRole('button', { name: /^NVDA/ })); // from Performance, too
    await waitFor(() => expect(screen.getByTestId('symbol-chart').textContent).toContain('NVDA'));
    fireEvent.click(screen.getByRole('radio', { name: 'Details' }));
    expect(screen.getByTestId('details-stub')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^AAPL/ })); // picking a stock from Details returns to its chart
    await waitFor(() => expect(screen.getByTestId('symbol-chart').textContent).toContain('AAPL'));
  });

  it('creates, switches, renames and deletes lists; the first list cannot be deleted', async () => {
    save(['NVDA']);
    render(<WatchlistPage />);
    await screen.findByTestId('stock-chart');
    expect(screen.getByRole('button', { name: 'Delete list' })).toHaveProperty('disabled', true);
    fireEvent.click(screen.getByRole('button', { name: 'New list' }));
    fireEvent.change(screen.getByLabelText('List name'), { target: { value: '  Semis  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect((screen.getByRole('combobox', { name: 'Watchlist' }) as HTMLSelectElement).selectedOptions[0].textContent).toBe('Semis (0)'));
    expect(screen.getByTestId('watchlist-empty').textContent).toContain('"Semis" is empty');
    fireEvent.change(screen.getByLabelText('Symbol to add'), { target: { value: 'amd' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(panelRows()).toEqual(['AMD']));
    expect(JSON.parse(window.localStorage.getItem('watchlist_symbols')!)).toEqual(['NVDA']); // the main list is untouched
    fireEvent.click(screen.getByRole('button', { name: 'Rename list' }));
    fireEvent.change(screen.getByLabelText('List name'), { target: { value: 'Chips' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByRole('option', { name: 'Chips (1)' })).toBeTruthy());
    fireEvent.change(screen.getByRole('combobox', { name: 'Watchlist' }), { target: { value: 'main' } });
    await waitFor(() => expect(panelRows()).toEqual(['NVDA']));
    fireEvent.change(screen.getByRole('combobox', { name: 'Watchlist' }), { target: { value: screen.getByRole('option', { name: 'Chips (1)' }).getAttribute('value') } });
    await waitFor(() => expect(panelRows()).toEqual(['AMD']));
    fireEvent.click(screen.getByRole('button', { name: 'Delete list' }));
    expect(screen.getByRole('alertdialog').textContent).toContain('Delete "Chips" and its 1 symbols?');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(panelRows()).toEqual(['NVDA']));
    expect(screen.queryByRole('option', { name: /Chips/ })).toBeNull();
  });

  it('refuses an empty list name and a 31st symbol, keeping everything', async () => {
    const thirty = Array.from({ length: 30 }, (_, i) => `S${i}`);
    save(thirty);
    render(<WatchlistPage />);
    await screen.findByTestId('stock-chart');
    fireEvent.change(screen.getByLabelText('Symbol to add'), { target: { value: 'msft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(screen.getByTestId('watchlist-notice').textContent).toBe('Your watchlist is full (30 stocks). Remove one to add MSFT.');
    expect(JSON.parse(window.localStorage.getItem('watchlist_symbols')!)).toEqual(thirty);
    fireEvent.click(screen.getByRole('button', { name: 'New list' }));
    fireEvent.change(screen.getByLabelText('List name'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(screen.getByText('A list name needs 1 to 30 characters.')).toBeTruthy();
  });

  it('keeps every list visible and usable when prices are unavailable', async () => {
    save(['NVDA', 'AAPL']);
    scr.search.mockRejectedValue(new Error('down'));
    render(<WatchlistPage />);
    await waitFor(() => expect(screen.getByTestId('panel-footer').textContent).toBe('Prices are unavailable right now; your lists are kept.'));
    expect(panelRows()).toEqual(['NVDA', 'AAPL']);
    expect(await screen.findByTestId('stock-chart')).toBeTruthy(); // the chart has its own source
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove AAPL from watchlist' })[0]);
    await waitFor(() => expect(panelRows()).toEqual(['NVDA']));
    expect(JSON.parse(window.localStorage.getItem('watchlist_symbols')!)).toEqual(['NVDA']);
  });

  it('says when the chart history is unavailable without breaking the page', async () => {
    save(['NVDA']);
    stock.getPriceHistory.mockRejectedValue({ response: { status: 404 } });
    render(<WatchlistPage />);
    expect((await screen.findByTestId('chart-error')).textContent).toContain('The price history for NVDA is unavailable right now.');
    expect(panelRows()).toEqual(['NVDA']);
  });

  it('the Screener star adds to the ACTIVE list and the page follows it', async () => {
    window.localStorage.setItem('watchlist_lists', JSON.stringify([{ id: 'l-semis', name: 'Semis', symbols: [] }]));
    window.localStorage.setItem('watchlist_active', 'l-semis');
    scr.search.mockImplementation(async (pairs: QueryPairs) =>
      symbolsOf(pairs).length ? result(symbolsOf(pairs).map((s) => row(s))) : result([row('NVDA'), row('AAPL')]));
    render(
      <>
        <ScreenerPage />
        <WatchlistPage />
      </>,
    );
    expect(await screen.findByTestId('watchlist-empty')).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'Add NVDA to watchlist' }));
    await waitFor(() => expect(panelRows()).toEqual(['NVDA']));
    expect(JSON.parse(window.localStorage.getItem('watchlist_lists')!)[0].symbols).toEqual(['NVDA']);
    expect(window.localStorage.getItem('watchlist_symbols')).toBeNull(); // the main list did not change
    await act(async () => {
      fireEvent.change(screen.getByRole('combobox', { name: 'Watchlist' }), { target: { value: 'main' } });
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add NVDA to watchlist' })).toBeTruthy()); // star follows the active list
  });

  it('remembers lists across a reload and ignores malformed list storage without losing the main list', async () => {
    save(['NVDA']);
    window.localStorage.setItem('watchlist_lists', '{not json');
    const { unmount } = render(<WatchlistPage />);
    await screen.findByTestId('stock-chart');
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['My watchlist (1)']);
    unmount();
    window.localStorage.setItem('watchlist_lists', JSON.stringify([{ id: 'l-a', name: 'A', symbols: ['msft', 'MSFT', 'bad sym'] }, { id: 'main', name: 'dup', symbols: [] }, { name: 'no id' }]));
    render(<WatchlistPage />);
    await waitFor(() => expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['My watchlist (1)', 'A (1)']));
  });
});
