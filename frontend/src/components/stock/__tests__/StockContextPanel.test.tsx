// Stock Context panel: the Screener's own row for this stock, the discovery session shown apart from the chart's newest
// bar, missing values unavailable (never substituted), the stock-only 200-day average with its reason, not-in-universe
// and undeployed-route states, and the fundamentals basis that changes with the cutoff.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ScreenerDefinitions, ScreenerRow } from '@/services/screenerV2Api';
import type { StockContext } from '@/services/stockContextApi';

const api = vi.hoisted(() => ({ get: vi.fn() }));
const scr = vi.hoisted(() => ({ definitions: vi.fn() }));
vi.mock('@/services/stockContextApi', () => ({ stockContextApi: api }));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: scr }));

import StockContextPanel from '@/components/stock/StockContextPanel';

const defs: ScreenerDefinitions = {
  metrics: [
    { key: 'rs_20', label: 'RS percentile, 20 sessions', unit: 'pctl', family: 'observed', definition: 'Percentile of the 20-session return.', numeric: true },
    { key: 'rs_60', label: 'RS percentile, 60 sessions', unit: 'pctl', family: 'observed', definition: 'Percentile of the 60-session return.', numeric: true },
    { key: 'ret_20', label: '20-session return', unit: '%', family: 'observed', definition: 'Close vs 20 sessions earlier.', numeric: true },
    { key: 'vs_spx_20', label: 'vs S&P 500, 20 sessions', unit: 'pp', family: 'observed', definition: 'Return minus the S&P 500 return.', numeric: true },
    { key: 'dist_sma20', label: 'vs 20-day average', unit: '%', family: 'derived', definition: 'Close vs 20-session average.', numeric: true },
    { key: 'dist_sma50', label: 'vs 50-day average', unit: '%', family: 'derived', definition: 'Close vs 50-session average.', numeric: true },
    { key: 'channel_pos', label: 'Channel position', unit: '%', family: 'derived', definition: 'Where the close sits in the channel.', numeric: true },
    { key: 'atr_pct', label: 'ATR %', unit: '%', family: 'derived', definition: 'ATR as % of close.', numeric: true },
    { key: 'dollar_vol_20', label: 'Dollar volume (20)', unit: '$', family: 'derived', definition: 'Mean close x volume.', numeric: true },
    { key: 'market_cap', label: 'Market cap', unit: '$', family: 'reference', definition: 'Fundamentals ingested by the cutoff.', numeric: true },
  ],
  families: { observed: 'Captured for the session.', derived: 'From stored daily bars.', reference: 'Ingested by the cutoff; publication time unknown.' },
  presets: [],
  sort_keys: [],
  limits: { max_page_size: 200, max_export_rows: 5000 },
};

const row = (over: Partial<ScreenerRow> = {}): ScreenerRow => ({
  symbol: 'ACME', sector: 'Industrials', sector_source: 'observed', industry: 'Machinery', close: 50, change_1d: 1.2, ret_5: 2, ret_20: 5.5,
  ret_60: 9, vs_spx_20: 3.1, vs_spx_60: 4, vs_sector_20: 1.4, rs_20: 91, rs_60: 88, breakout_20: true, new_high_20: true,
  dist_sma20: 2.4, dist_sma50: -1.25, channel_pos: 96, channel_width: 12, channel_width_pctile: 40, atr_pct: 2.8, atr_pct_pctile: 55,
  volume_ratio: 1.6, dollar_vol_20: 2.5e8, market_cap: 3e9, dv_bars: 20, fundamentals_date: '2026-10-16',
  fundamentals_ingested_at: '2026-10-16T22:50Z', fundamentals_after_close: true, ...over,
});

function ctx(over: Partial<StockContext> = {}): StockContext {
  return {
    symbol: 'ACME', in_universe: true, unavailable_reason: null, session: '2026-10-16', previous_session: '2026-10-15',
    newest_price_date: '2026-10-16', dollar_volume_window: { from: '2026-09-21', to: '2026-10-16', sessions: 20 },
    fundamentals_basis: { cutoff_mode: 'post_session', note: 'Fundamentals ingested by noon New York on the next day.' } as StockContext['fundamentals_basis'],
    fundamentals_cutoff_utc: '2026-10-17T16:00', universe: 3000,
    coverage: { rs: 2990, rs_20: 2990, rs_60: 2980 } as StockContext['coverage'],
    recent_sessions: ['2026-10-16'], row: row(),
    stock_only: {
      dist_sma200: {
        key: 'dist_sma200', label: 'vs 200-day average', unit: '%', family: 'derived', definition: 'Close vs its 200-session average.',
        sessions_required: 200, value: 12.5, sma200: 44.4, sessions_with_close: 200, window_from: '2025-12-31', window_to: '2026-10-16',
        state: 'ok', reason: null,
      },
    },
    ...over,
  };
}

const text = (id: string) => screen.getByTestId(id).textContent ?? '';

beforeEach(() => {
  api.get.mockReset();
  scr.definitions.mockReset();
  scr.definitions.mockResolvedValue(defs);
});

describe('StockContextPanel', () => {
  it('shows the Screener row grouped with plain labels, the session date and the 200-day average with its window', async () => {
    api.get.mockResolvedValue(ctx());
    render(<StockContextPanel symbol="ACME" chartDate="2026-10-16" />);
    expect(await screen.findByText('Stock context')).toBeTruthy();
    expect(api.get).toHaveBeenCalledWith('ACME');
    expect(screen.getByTestId('data-stamp').textContent).toContain('2026-10-16');
    expect(text('ctx-rs_20')).toContain('higher than 91% of stocks');
    expect(text('ctx-dist_sma20')).toContain('+2.4');
    expect(text('ctx-dist_sma50')).toContain('-1.25%');
    expect(text('ctx-dist_sma200')).toMatch(/\+12\.5.*%/);
    expect(text('ctx-dist_sma200')).toContain('2025-12-31 to 2026-10-16, 200 of 200 sessions');
    expect(text('ctx-dist_sma200')).toContain('this page only');
    expect(screen.queryByTestId('ctx-chart-ahead')).toBeNull();
  });

  it('states when the chart is newer than the metrics session instead of mixing the two dates', async () => {
    api.get.mockResolvedValue(ctx());
    render(<StockContextPanel symbol="ACME" chartDate="2026-10-19" />);
    expect((await screen.findByTestId('ctx-chart-ahead')).textContent).toContain('The chart shows prices to 2026-10-19; these metrics describe the 2026-10-16 session');
  });

  it('shows missing indicators as unavailable, never as zero, and explains a missing relative-strength snapshot', async () => {
    api.get.mockResolvedValue(ctx({
      coverage: { rs: 0, rs_20: 0, rs_60: 0 } as StockContext['coverage'],
      row: row({ rs_20: null, rs_60: null, ret_20: null, vs_spx_20: null, dist_sma20: null, dist_sma50: null, atr_pct: null, dollar_vol_20: null, dv_bars: 14 }),
    }));
    const { container } = render(<StockContextPanel symbol="ACME" chartDate={null} />);
    await screen.findByText('Stock context');
    for (const id of ['ctx-rs_20', 'ctx-ret_20', 'ctx-vs_spx_20', 'ctx-dist_sma20', 'ctx-dist_sma50', 'ctx-atr_pct', 'ctx-dollar_vol_20']) {
      expect(text(id)).toContain('—');
      expect(text(id)).not.toMatch(/\b0(\.0)?%/);
    }
    expect(text('ctx-rs_20')).not.toContain('higher than');
    expect(text('ctx-dollar_vol_20')).toContain('14 of 20 sessions with bars');
    expect(screen.getByTestId('ctx-no-rs')).toBeTruthy();
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
  });

  it('shows the 200-day average as unavailable with the server reason when coverage is incomplete', async () => {
    const c = ctx();
    c.stock_only.dist_sma200 = { ...c.stock_only.dist_sma200!, state: 'unavailable', value: null, sma200: null, sessions_with_close: 142, reason: '142 of 200 sessions with a valid close' };
    api.get.mockResolvedValue(c);
    render(<StockContextPanel symbol="ACME" chartDate={null} />);
    await screen.findByText('Stock context');
    expect(text('ctx-dist_sma200')).toContain('—');
    expect(text('ctx-dist_sma200')).toContain('142 of 200 sessions with a valid close');
  });

  it('says a stock with no bar on the session is unavailable and shows no metrics from another session', async () => {
    api.get.mockResolvedValue(ctx({ symbol: 'ZZZZ', in_universe: false, row: null, unavailable_reason: 'no bar for ZZZZ on the 2026-10-16 session', stock_only: { dist_sma200: null } }));
    render(<StockContextPanel symbol="ZZZZ" chartDate={null} />);
    expect((await screen.findByTestId('ctx-not-in-universe')).textContent).toContain('no bar for ZZZZ on the 2026-10-16 session');
    expect(screen.queryByTestId('ctx-rs_20')).toBeNull();
    expect(screen.queryByTestId('ctx-dist_sma200')).toBeNull();
  });

  it('shows the fundamentals ingestion time and the cutoff basis the server applied', async () => {
    api.get.mockResolvedValue(ctx({
      fundamentals_basis: { cutoff_mode: 'session_close', note: 'Fundamentals ingested by the 16:00 New York close.' } as StockContext['fundamentals_basis'],
      row: row({ market_cap: null, fundamentals_date: null, fundamentals_ingested_at: null, fundamentals_after_close: null }),
    }));
    render(<StockContextPanel symbol="ACME" chartDate={null} />);
    await screen.findByText('Stock context');
    expect(text('ctx-fundamentals')).toContain('Market cap —');
    expect(text('ctx-fundamentals')).toContain('16:00 New York close');
    expect(text('ctx-fundamentals')).not.toContain('ingested 2026');
  });

  it('labels a post-session fundamentals row with its ingestion time', async () => {
    api.get.mockResolvedValue(ctx());
    render(<StockContextPanel symbol="ACME" chartDate={null} />);
    await screen.findByText('Stock context');
    expect(text('ctx-fundamentals')).toContain('ingested 2026-10-16T22:50Z, after the close');
    expect(screen.getByText('Reference (ingestion-time)')).toBeTruthy();
  });

  it('explains an older server that does not have the endpoint yet, and other failures', async () => {
    api.get.mockRejectedValue({ response: { status: 404 } });
    const { unmount } = render(<StockContextPanel symbol="ACME" chartDate={null} />);
    expect((await screen.findByTestId('ctx-error')).textContent).toContain('not available on this server yet');
    unmount();
    api.get.mockRejectedValue(new Error('network'));
    render(<StockContextPanel symbol="ACME" chartDate={null} />);
    expect((await screen.findByTestId('ctx-error')).textContent).toContain('unavailable right now');
  });

  it('uses a single column on small screens and three on wider ones', async () => {
    api.get.mockResolvedValue(ctx());
    const { container } = render(<StockContextPanel symbol="ACME" chartDate={null} />);
    await screen.findByText('Stock context');
    const grid = container.querySelector('[class*="md:grid-cols-3"]')!;
    expect(grid.className).toContain('grid-cols-1');
    expect(grid.className).toContain('md:grid-cols-3');
  });
});
