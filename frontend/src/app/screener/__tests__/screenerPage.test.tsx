// Smart Screener v2 page: session and coverage stated, missing values shown as unavailable, presets / sorting /
// pagination go through the URL, stock and sector navigation, watchlist and CSV export, and refused screens explained.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ScreenerDefinitions, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';

const nav = vi.hoisted(() => ({ search: '', replace: vi.fn() }));
const api = vi.hoisted(() => ({ search: vi.fn(), definitions: vi.fn(), exportCsv: vi.fn() }));

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: nav.replace, push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/screener',
}));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: api }));

import ScreenerPage from '@/app/screener/page';

// jsdom has no ResizeObserver (the Strength Map's chart container sizes itself with it)
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

const defs: ScreenerDefinitions = {
  metrics: [
    { key: 'rs_20', label: 'RS percentile, 20 sessions', unit: 'pctl', family: 'observed', definition: 'Percentile of the 20-session return.', numeric: true },
    { key: 'ret_20', label: '20-session return', unit: '%', family: 'observed', definition: 'Close vs 20 sessions earlier.', numeric: true },
    { key: 'atr_pct', label: 'ATR %', unit: '%', family: 'derived', definition: 'ATR as % of close.', numeric: true },
    { key: 'dollar_vol_20', label: 'Dollar volume (20)', unit: '$', family: 'derived', definition: 'Mean close x volume, 20 sessions.', numeric: true },
    { key: 'market_cap', label: 'Market cap', unit: '$', family: 'reference', definition: 'Fundamentals known by the session.', numeric: true },
    { key: 'breakout_20', label: '20-day breakout', unit: 'flag', family: 'derived', definition: 'Close above previous 20-day high.', numeric: false },
  ],
  families: { observed: 'Captured for the session.', derived: 'From stored daily bars.', reference: 'Fundamentals known by the session.' },
  presets: [{ key: 'leaders', label: 'Leaders', description: 'RS at least 80 over 20 and 60 sessions.', sort: ['rs_60', 'desc'] }],
  sort_keys: ['symbol', 'sector', 'rs_20', 'ret_20', 'atr_pct'],
  limits: { max_page_size: 200, max_export_rows: 5000 },
};

const row = (symbol: string, over: Partial<ScreenerRow> = {}): ScreenerRow => ({
  symbol, sector: 'Technology', sector_source: 'observed', industry: 'Software', close: 50, change_1d: 1.2, ret_5: 2, ret_20: 5.5, ret_60: 9,
  vs_spx_20: 3.1, vs_sector_20: 1.4, rs_20: 91, rs_60: 88, breakout_20: true, new_high_20: true, channel_pos: 96, channel_width: 12,
  channel_width_pctile: 40, atr_pct: 2.8, atr_pct_pctile: 55, volume_ratio: 1.6, dollar_vol_20: 2.5e8, market_cap: 3e11, dv_bars: 20,
  fundamentals_date: '2026-10-16', fundamentals_ingested_at: '2026-10-16T22:50Z', fundamentals_after_close: true, vs_spx_60: 4, dist_sma20: 1.5, dist_sma50: 4.2, ...over,
});

function result(over: Partial<ScreenerResult> = {}): ScreenerResult {
  return {
    session: '2026-10-16', previous_session: '2026-10-15', dollar_volume_window: { from: '2026-09-21', to: '2026-10-16', sessions: 20 },
    fundamentals_cutoff_utc: '2026-10-17T16:00',
    fundamentals_basis: { type: 'ingestion_time_reference', publication_time: 'unknown', point_in_time_verified: false, cutoff_mode: 'post_session',
      cutoff_utc: '2026-10-17T16:00', session_close_utc: '2026-10-16T20:00', note: 'Post-session reporting convention: rows our pipeline ingested up to noon New York on the next calendar day. Not an intraday or historical decision-time guarantee.' },
    recent_sessions: ['2026-10-16', '2026-10-15', '2026-10-14'], universe: 3063,
    sectors: ['Energy', 'Technology'],
    coverage: { rs: 3063, rs_20: 3061, rs_60: 3038, technical: 3053, breakout_20: 3053, atr_pct: 3053, dollar_vol_20: 3060, dollar_vol_insufficient: 3,
      dollar_vol_no_bars: 0, fundamentals: 3063, fundamentals_after_close: 3063, market_cap: 3042, sector: 3041, sector_observed: 3041, change_1d: 3063 },
    total: 120, page: 1, page_size: 50, sort: 'rs_20', order: 'desc', preset: null, truncated: false,
    rows: [row('AAA'), row('BBB', { rs_20: null, rs_60: null, ret_20: null, dollar_vol_20: null, dv_bars: 12, market_cap: null, sector: null, sector_source: null })],
    ...over,
  };
}

beforeEach(() => {
  nav.search = '';
  nav.replace.mockReset();
  api.search.mockReset();
  api.definitions.mockReset();
  api.exportCsv.mockReset();
  api.definitions.mockResolvedValue(defs);
  api.search.mockResolvedValue(result());
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

describe('Screener v2 page', () => {
  it('states the session, previous session, dollar-volume window and coverage', async () => {
    render(<ScreenerPage />);
    expect((await screen.findByTestId('data-stamp')).textContent).toMatch(/Data as of\s*2026-10-16.*end-of-day prices · previous 2026-10-15.*2026-09-21 to 2026-10-16/);
    const cov = screen.getByTestId('coverage').textContent!;
    expect(cov).toMatch(/Universe 3,063 stocks with a bar on 2026-10-16/);
    expect(cov).toMatch(/Relative strength \(observed\): 3,063 of 3,063/);
    expect(cov).toMatch(/Technicals: 3,053 of 3,063/);
    expect(cov).toMatch(/Dollar volume: 3,060 of 3,063 \(3 with fewer than 20 sessions\)/);
    expect(screen.getByText('120 matching stocks')).toBeTruthy();
    expect(api.search).toHaveBeenCalledWith([['sort', 'rs_20'], ['order', 'desc'], ['page', '1'], ['page_size', '50']]);
  });

  it('shows missing values as dashes and keeps stock and sector navigation and the watchlist', async () => {
    render(<ScreenerPage />);
    const rows = await screen.findAllByTestId('screener-row');
    const b = rows[1];
    expect(within(b).getByRole('link', { name: 'BBB' }).getAttribute('href')).toBe('/stock/BBB');
    expect(b.textContent).toMatch(/—/);
    expect(b.textContent).not.toMatch(/\$0|\b0\.00%/);
    expect(within(rows[0]).getByRole('link', { name: 'Technology' }).getAttribute('href')).toBe('/sectors/technology');
    const star = within(rows[0]).getByRole('button', { name: 'Add AAA to watchlist' });
    fireEvent.click(star);
    expect(await within(rows[0]).findByRole('button', { name: 'Remove AAA from watchlist' })).toBeTruthy();
  });

  it('applies presets, sorting and pagination through the URL', async () => {
    render(<ScreenerPage />);
    await screen.findAllByTestId('screener-row');
    fireEvent.click(screen.getByRole('button', { name: 'Leaders' }));
    expect(nav.replace).toHaveBeenLastCalledWith(expect.stringContaining('preset=leaders&sort=rs_60&order=desc'), { scroll: false });
    fireEvent.click(screen.getByRole('button', { name: /^ATR %/ }));
    expect(nav.replace).toHaveBeenLastCalledWith(expect.stringContaining('sort=atr_pct&order=desc&page=1'), { scroll: false });
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
    expect(nav.replace).toHaveBeenLastCalledWith(expect.stringContaining('page=2'), { scroll: false });
  });

  it('reads the screen from the URL', async () => {
    nav.search = 'preset=leaders&sort=rs_60&order=desc&page=2&page_size=100&min_dollar_vol_20=5000000';
    render(<ScreenerPage />);
    await waitFor(() =>
      expect(api.search).toHaveBeenCalledWith([
        ['preset', 'leaders'], ['sort', 'rs_60'], ['order', 'desc'], ['page', '2'], ['page_size', '100'], ['min_dollar_vol_20', '5000000'],
      ]),
    );
  });

  it('warns when the session has no observed relative-strength snapshot', async () => {
    api.search.mockResolvedValue(result({ coverage: { ...result().coverage, rs: 0, rs_20: 0, rs_60: 0 } }));
    render(<ScreenerPage />);
    expect((await screen.findByTestId('no-rs')).textContent).toMatch(/RS and return columns are unavailable/);
  });

  it('explains a refused screen and an undeployed service', async () => {
    api.search.mockRejectedValue({ response: { status: 422, data: { detail: "unknown sort key 'x'" } } });
    const { unmount } = render(<ScreenerPage />);
    expect(await screen.findByText(/The screen was refused: unknown sort key 'x'\./)).toBeTruthy();
    unmount();
    api.search.mockRejectedValue({ response: { status: 404, data: { detail: 'Not Found' } } });
    render(<ScreenerPage />);
    expect(await screen.findByText('The screener v2 service is not available on this server yet.')).toBeTruthy();
  });

  it('exports the current screen as CSV without paging parameters handled client-side', async () => {
    api.exportCsv.mockResolvedValue(new Blob(['a,b\n']));
    const create = vi.fn(() => 'blob:x');
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    render(<ScreenerPage />);
    await screen.findAllByTestId('screener-row');
    fireEvent.click(screen.getByRole('button', { name: /CSV/ }));
    await waitFor(() => expect(api.exportCsv).toHaveBeenCalled());
    expect(create).toHaveBeenCalled();
  });

  it('lists every metric definition with its data family, and explains each column on tap without sorting it', async () => {
    render(<ScreenerPage />);
    const d = await screen.findByTestId('definitions');
    expect(d.textContent).toMatch(/ObservedRS percentile, 20 sessions: Percentile of the 20-session return\./);
    expect(d.textContent).toMatch(/Reference \(ingestion-time\)Market cap: Fundamentals known by the session\./);
    const th = screen.getByRole('button', { name: /^RS 20/ }).closest('th')!;
    const calls = nav.replace.mock.calls.length;
    fireEvent.click(within(th).getByRole('button', { name: 'About RS percentile, 20 sessions' }));
    expect(screen.getByRole('tooltip').textContent).toBe('RS percentile, 20 sessionsPercentile of the 20-session return. Data: observed.');
    expect(nav.replace.mock.calls.length).toBe(calls); // explaining a column never sorts it
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();
    // the same API definitions explain the filters
    const legend = screen.getByText('Market cap', { selector: 'legend' });
    fireEvent.click(within(legend).getByRole('button', { name: 'About Market cap' }));
    expect(screen.getByRole('tooltip').textContent).toContain('Fundamentals known by the session.');
  });

  it('keeps the wide table inside its own positioned scroller (no page-wide horizontal scroll on phones)', async () => {
    render(<ScreenerPage />);
    const table = (await screen.findAllByTestId('screener-row'))[0].closest('table')!;
    // screen-reader-only text in the headers is absolutely positioned: without a positioned scroller it escapes the
    // overflow container and widens the whole page (measured: 1,023 px at a 390 px viewport before the fix).
    expect(table.parentElement!.className.split(' ')).toContain('relative');
    expect(table.parentElement!.className.split(' ')).toContain('overflow-x-auto');
  });

  it('labels fundamentals as ingestion-time reference data and lets the cutoff be tightened to the close', async () => {
    render(<ScreenerPage />);
    const cov = (await screen.findByTestId('coverage')).querySelector('span[title]')!;
    expect(cov.textContent).toMatch(/Fundamentals \(ingestion-time reference, publication time unknown\): 3,063 of 3,063 · 3,063 ingested after the close/);
    expect(cov.getAttribute('title')).toMatch(/Post-session reporting convention.*Not an intraday or historical decision-time guarantee/);
    const mcap = screen.getAllByTestId('screener-row')[0].querySelectorAll('td');
    expect([...mcap].some((td) => /ingested 2026-10-16T22:50Z \(after the close: post-session convention\); publication time unknown/.test(td.getAttribute('title') ?? ''))).toBe(true);
    fireEvent.change(screen.getByLabelText('Fundamentals cutoff'), { target: { value: 'session_close' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(nav.replace).toHaveBeenLastCalledWith(expect.stringContaining('fundamentals_cutoff=session_close'), { scroll: false });
  });

  it('carries no advice or prediction wording', async () => {
    const { container } = render(<ScreenerPage />);
    await screen.findAllByTestId('screener-row');
    const t = container.textContent ?? '';
    for (const re of [/\bbuy\b/i, /\bsell\b/i, /signal/i, /predict/i, /recommend/i, /outperform/i, /opportunit/i]) expect(t).not.toMatch(re);
  });

  // ---------------------------------------------------------------------------------------------- strength views
  it('shows the Strength Map corner and strength meters in the table, unavailable stays a dash', async () => {
    render(<ScreenerPage />);
    const [a, b] = await screen.findAllByTestId('screener-row');
    expect(within(a).getByTestId('quadrant-chip').textContent).toBe('Leading'); // AAA: RS 20 = 91, RS 60 = 88
    expect(within(a).getByRole('img', { name: 'RS 20: 91 of 100 (very strong)' })).toBeTruthy();
    expect(within(b).queryByTestId('quadrant-chip')).toBeNull();
    expect(within(b).getByRole('img', { name: 'RS 20: unavailable' })).toBeTruthy();
  });

  it('switches the result view through the URL and keeps the screen', async () => {
    nav.search = 'preset=leaders&sort=rs_60&order=desc';
    render(<ScreenerPage />);
    await screen.findAllByTestId('screener-row');
    fireEvent.click(screen.getByRole('radio', { name: 'Strength Map' }));
    expect(nav.replace).toHaveBeenLastCalledWith('/screener?preset=leaders&sort=rs_60&order=desc&view=map', { scroll: false });
    fireEvent.click(screen.getByRole('radio', { name: 'Table' }));
    expect(nav.replace).toHaveBeenLastCalledWith('/screener?preset=leaders&sort=rs_60&order=desc', { scroll: false });
  });

  it('the Strength Map asks for the first 200 of the same screen in one request (never sending "view" to the API)', async () => {
    nav.search = 'sort=atr_pct&order=desc&page=3&page_size=50&view=map';
    render(<ScreenerPage />);
    await screen.findByTestId('strength-map');
    const pairs = api.search.mock.calls[api.search.mock.calls.length - 1][0] as [string, string][];
    expect(pairs).toContainEqual(['page', '1']);
    expect(pairs).toContainEqual(['page_size', '200']);
    expect(pairs).toContainEqual(['sort', 'atr_pct']);
    expect(pairs.some(([k]) => k === 'view')).toBe(false);
    expect(screen.getByTestId('corner-leading').textContent).toBe('Leading 1');
    expect(screen.getByTestId('map-note').textContent).toContain('1 without relative strength for 2026-10-16 not plotted');
    expect(screen.queryByRole('button', { name: 'Next page' })).toBeNull();
    // sorting from the map keeps the map
    fireEvent.change(screen.getByLabelText('Market session'), { target: { value: '2026-10-15' } });
    expect(nav.replace).toHaveBeenLastCalledWith(expect.stringContaining('view=map'), { scroll: false });
  });

  it('the card view shows each stock with its corner, meters, relative returns, channel and the star', async () => {
    nav.search = 'view=cards';
    render(<ScreenerPage />);
    const cards = await screen.findAllByTestId('strength-card');
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toContain('Leading');
    expect(cards[0].textContent).toContain('96% of the way up');
    expect(cards[0].textContent).toContain('Above its 20- and 50-day averages');
    expect(cards[1].textContent).toContain('—');
    fireEvent.click(within(cards[0]).getByRole('button', { name: 'Add AAA to watchlist' }));
    expect(within(cards[0]).getByRole('button', { name: 'Remove AAA from watchlist' })).toBeTruthy();
  });

});
