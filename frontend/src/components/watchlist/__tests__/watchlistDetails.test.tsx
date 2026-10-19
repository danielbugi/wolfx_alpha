// Watchlist Details tab (the Screener metrics table): one Screener request for the active list, values identical to the
// Screener's, missing data and missing rows unavailable, the backend being down never touches the list, sorting, the
// side-by-side comparison, malformed storage, and stale sessions.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { QueryPairs, ScreenerDefinitions, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';

const api = vi.hoisted(() => ({ search: vi.fn(), definitions: vi.fn(), exportCsv: vi.fn() }));
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(''),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/watchlist',
}));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: api }));

import WatchlistDetails from '@/components/watchlist/WatchlistDetails';
import { formatCell } from '@/lib/screenerV2';

const KEY = 'watchlist_symbols';
const save = (s: unknown) => window.localStorage.setItem(KEY, JSON.stringify(s));
const stored = () => JSON.parse(window.localStorage.getItem(KEY) ?? 'null');

const defs: ScreenerDefinitions = {
  metrics: [
    { key: 'rs_20', label: 'RS percentile, 20 sessions', unit: 'pctl', family: 'observed', definition: 'Percentile of the 20-session return.', numeric: true },
    { key: 'ret_20', label: '20-session return', unit: '%', family: 'observed', definition: 'Close vs 20 sessions earlier.', numeric: true },
    { key: 'dist_sma50', label: 'vs 50-day average', unit: '%', family: 'derived', definition: 'Close vs 50-session average.', numeric: true },
    { key: 'atr_pct', label: 'ATR %', unit: '%', family: 'derived', definition: 'ATR as % of close.', numeric: true },
    { key: 'dollar_vol_20', label: 'Dollar volume (20)', unit: '$', family: 'derived', definition: 'Mean close x volume.', numeric: true },
    { key: 'close', label: 'Close', unit: '$', family: 'observed', definition: 'Close.', numeric: true },
  ],
  families: { observed: 'Captured for the session.', derived: 'From stored daily bars.', reference: 'Ingestion-time.' },
  presets: [],
  sort_keys: ['symbol', 'rs_20'],
  limits: { max_page_size: 200, max_export_rows: 5000 },
};

const row = (symbol: string, over: Partial<ScreenerRow> = {}): ScreenerRow => ({
  symbol, sector: 'Technology', sector_source: 'observed', industry: 'Semiconductors', close: 214.13, change_1d: -1.43, ret_5: -6.61, ret_20: -3.66,
  ret_60: 2.69, vs_spx_20: -3.8, vs_spx_60: -0.7, vs_sector_20: -0.6, rs_20: 45, rs_60: 68, breakout_20: false, new_high_20: false,
  dist_sma20: -6.16, dist_sma50: -3.96, channel_pos: 4.2, channel_width: 12, channel_width_pctile: 47, atr_pct: 2.8, atr_pct_pctile: 34,
  volume_ratio: 1.18, dollar_vol_20: 2.354e10, market_cap: 5.17e12, dv_bars: 20, fundamentals_date: '2026-10-16',
  fundamentals_ingested_at: '2026-10-16T22:32Z', fundamentals_after_close: true, ...over,
});

function result(rows: ScreenerRow[], over: Partial<ScreenerResult> = {}): ScreenerResult {
  return {
    session: '2026-10-16', previous_session: '2026-10-15', dollar_volume_window: { from: '2026-09-21', to: '2026-10-16', sessions: 20 },
    fundamentals_cutoff_utc: '2026-10-17T16:00',
    fundamentals_basis: { type: 'ingestion_time_reference', publication_time: 'unknown', point_in_time_verified: false, cutoff_mode: 'post_session', cutoff_utc: '2026-10-17T16:00', session_close_utc: '2026-10-16T20:00', note: 'Post-session reporting convention.' },
    recent_sessions: ['2026-10-16', '2026-10-15', '2026-10-14'], universe: 3000, sectors: ['Technology'],
    coverage: { rs: 2990, rs_20: 2990, rs_60: 2980, technical: 3000, dollar_vol_20: 2990, dollar_vol_insufficient: 10, fundamentals: 2900, fundamentals_after_close: 100 } as ScreenerResult['coverage'],
    total: rows.length, page: 1, page_size: Math.max(1, rows.length), sort: 'symbol', order: 'asc', preset: null, rows, truncated: false, ...over,
  } as ScreenerResult;
}

const renderDetails = () => render(<WatchlistDetails />);
const symbolsOf = (pairs: QueryPairs) => pairs.filter(([k]) => k === 'symbol').map(([, v]) => v);
const rowsOnPage = () => screen.queryAllByTestId('watchlist-row').map((tr) => within(tr).getAllByRole('link')[0].textContent);

beforeEach(() => {
  window.localStorage.clear();
  api.search.mockReset();
  api.definitions.mockReset();
  api.definitions.mockResolvedValue(defs);
  vi.setSystemTime(new Date('2026-10-18T12:00:00Z'));
});

describe('Watchlist Details tab', () => {
  it('loads every saved symbol in ONE Screener request and shows the Screener values unchanged', async () => {
    save(['NVDA', 'AAPL', 'ZZZZ']);
    const nvda = row('NVDA');
    api.search.mockResolvedValue(result([row('AAPL', { rs_20: 80 }), nvda]));
    renderDetails();
    await screen.findAllByText('Semiconductors', { exact: false });
    expect(api.search).toHaveBeenCalledTimes(1);
    expect(symbolsOf(api.search.mock.calls[0][0])).toEqual(['NVDA', 'AAPL', 'ZZZZ']);
    expect(rowsOnPage()).toEqual(['NVDA', 'AAPL', 'ZZZZ']); // saved order
    const tr = screen.getAllByTestId('watchlist-row')[0];
    // cross-page consistency: the same formatter and the same row the Screener renders
    for (const [key, unit] of [['ret_20', '%'], ['dist_sma50', '%'], ['dollar_vol_20', '$'], ['rs_20', 'pctl']] as const) {
      expect(tr.textContent).toContain(formatCell(nvda, key, unit));
    }
    expect(tr.querySelector('a[href="/stock/NVDA"]')).toBeTruthy();
    expect(tr.querySelector('a[href="/sectors/technology"]')).toBeTruthy();
    expect(screen.getAllByTestId('watchlist-row')[2].textContent).toContain('no bar on the 2026-10-16 session');
  });

  it('shows missing values as unavailable, never as zero', async () => {
    save(['NVDA']);
    api.search.mockResolvedValue(result([row('NVDA', { rs_20: null, ret_20: null, dist_sma50: null, dollar_vol_20: null, dv_bars: 12, sector: null, industry: null })]));
    renderDetails();
    await waitFor(() => expect(screen.getByTestId('watchlist-row').textContent).toContain('—'));
    const tr = screen.getByTestId('watchlist-row');
    expect(tr.querySelector('[title="12 of 20 sessions with bars"]')?.textContent).toBe('—');
    expect(tr.textContent).not.toMatch(/NaN|undefined|null/);
  });

  it('keeps saved symbols visible and removable when the analytics backend fails, and never rewrites the list', async () => {
    save(['NVDA', 'AAPL']);
    api.search.mockRejectedValue(new Error('network'));
    renderDetails();
    expect((await screen.findByTestId('analytics-error')).textContent).toContain('Your saved symbols are kept');
    expect(rowsOnPage()).toEqual(['NVDA', 'AAPL']);
    expect(screen.getAllByTestId('watchlist-row')[0].textContent).toContain('analytics unavailable');
    expect(stored()).toEqual(['NVDA', 'AAPL']);
    fireEvent.click(screen.getByRole('button', { name: 'Remove NVDA from watchlist' }));
    await waitFor(() => expect(rowsOnPage()).toEqual(['AAPL']));
    expect(stored()).toEqual(['AAPL']);
  });

  it('says an older server does not have watchlist analytics yet (404 or refused symbol filter)', async () => {
    save(['NVDA']);
    api.search.mockRejectedValue({ response: { status: 422 } });
    renderDetails();
    expect((await screen.findByTestId('analytics-error')).textContent).toContain('not available on this server yet');
    expect(rowsOnPage()).toEqual(['NVDA']);
  });

  it('sorts by a metric with missing values last, and returns to the saved order', async () => {
    save(['A', 'B', 'C']);
    api.search.mockResolvedValue(result([row('A', { rs_20: 50 }), row('B', { rs_20: null }), row('C', { rs_20: 90 })]));
    renderDetails();
    await waitFor(() => expect(screen.getAllByTestId('watchlist-row')).toHaveLength(3));
    fireEvent.click(screen.getByRole('button', { name: /RS 20/ }));
    expect(rowsOnPage()).toEqual(['C', 'A', 'B']);
    fireEvent.click(screen.getByRole('button', { name: /RS 20/ }));
    expect(rowsOnPage()).toEqual(['A', 'C', 'B']);
    fireEvent.click(screen.getByRole('button', { name: 'Back to saved order' }));
    expect(rowsOnPage()).toEqual(['A', 'B', 'C']);
  });

  it('compares up to four stocks side by side with the same values', async () => {
    save(['A', 'B', 'C', 'D', 'E']);
    api.search.mockResolvedValue(result(['A', 'B', 'C', 'D', 'E'].map((s, i) => row(s, { ret_20: i - 2 }))));
    renderDetails();
    await waitFor(() => expect(screen.getAllByTestId('watchlist-row')).toHaveLength(5));
    for (const s of ['A', 'B', 'C', 'D']) fireEvent.click(screen.getByRole('checkbox', { name: `Compare ${s}` }));
    expect((screen.getByRole('checkbox', { name: 'Compare E' }) as HTMLInputElement).disabled).toBe(true);
    const panel = screen.getByTestId('compare');
    expect(within(panel).getAllByRole('columnheader').map((th) => th.textContent)).toEqual(['Metric', 'A', 'B', 'C', 'D']);
    const retRow = within(panel).getByText('20-session return').closest('tr')!;
    expect(retRow.textContent).toBe(`20-session return${[-2, -1, 0, 1].map((v) => formatCell(row('X', { ret_20: v }), 'ret_20', '%')).join('')}`);
    expect(retRow.textContent).toContain('-2.00%');
    expect(panel.querySelector('.overflow-x-auto')).toBeTruthy();
  });

  it('ignores malformed and duplicate saved entries without erasing valid ones', async () => {
    window.localStorage.setItem(KEY, JSON.stringify(['nvda', 'NVDA', 7, 'bad sym', 'AAPL']));
    api.search.mockResolvedValue(result([row('NVDA'), row('AAPL')]));
    renderDetails();
    await waitFor(() => expect(rowsOnPage()).toEqual(['NVDA', 'AAPL']));
    expect(symbolsOf(api.search.mock.calls[0][0])).toEqual(['NVDA', 'AAPL']);
  });

  it('flags a viewed older session and data that has not been updated', async () => {
    save(['NVDA']);
    api.search.mockResolvedValueOnce(result([row('NVDA')], { session: '2026-10-09', recent_sessions: ['2026-10-09', '2026-10-08'] }));
    renderDetails();
    expect((await screen.findByTestId('not-updated')).textContent).toContain('2026-10-09 (9 days ago)');
    api.search.mockResolvedValueOnce(result([row('NVDA')], { session: '2026-10-08', recent_sessions: ['2026-10-09', '2026-10-08'] }));
    fireEvent.change(screen.getByLabelText('Market session'), { target: { value: '2026-10-08' } });
    expect((await screen.findByTestId('older-session')).textContent).toContain('viewing the 2026-10-08 session; the newest is 2026-10-09');
    expect(api.search.mock.calls[1][0]).toContainEqual(['session', '2026-10-08']);
  });

  it('lays out the table and comparison in horizontal scrollers so a phone never scrolls the page sideways', async () => {
    save(['NVDA']);
    api.search.mockResolvedValue(result([row('NVDA')]));
    const { container } = renderDetails();
    await screen.findAllByText('Semiconductors', { exact: false });
    const scroller = container.querySelector('table')!.parentElement!;
    expect(scroller.className).toContain('overflow-x-auto');
    expect(scroller.className).toContain('relative'); // keeps sr-only text inside the scroller
  });
});
