// Overview movers card: "View all" opens the Screener ranked the same way as the visible list (a real Screener query,
// not the old `?filter=` the Screener never read), pinned to the session the lists describe; the session is stated;
// missing values stay dashes; an empty list says why, from the session's data.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { MoversMeta, StockItem } from '@/services/api';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));

import MarketMovers from '@/components/dashboard/MarketMovers';

const META: MoversMeta = {
  session: '2026-10-23',
  previous_session: '2026-10-22',
  universe: 3060,
  coverage: { change_1d: 3055, volume_ratio: 3054, dollar_vol_20: 3060 },
  totals: { top_gainers: 1200, top_losers: 1700, unusual_volume: 71 },
  floor: { min_close: 1, min_dollar_vol_20: 1_000_000 },
  min_volume_ratio: 2,
};
const item = (symbol: string, over: Partial<StockItem> = {}): StockItem => ({
  symbol, current_price: 10, price_change_pct: 5, volume: 2_000_000, volume_ratio: 3, sector: 'Technology', market_cap: null, ...over,
});

describe('MarketMovers', () => {
  it('links each list to the matching ranked Screener query for the same session', () => {
    render(<MarketMovers gainers={[]} losers={[]} unusualVolume={[]} meta={META} />);
    const href = () => screen.getByTestId('movers-view-all').getAttribute('href');
    expect(href()).toBe('/screener?min_close=1&min_dollar_vol_20=1000000&sort=change_1d&order=desc&session=2026-10-23');
    fireEvent.click(screen.getByRole('radio', { name: 'Top Losers' }));
    expect(href()).toBe('/screener?min_close=1&min_dollar_vol_20=1000000&sort=change_1d&order=asc&session=2026-10-23');
    fireEvent.click(screen.getByRole('radio', { name: 'Unusual Volume' }));
    expect(href()).toBe('/screener?min_close=1&min_dollar_vol_20=1000000&min_volume_ratio=2&sort=volume_ratio&order=desc&session=2026-10-23');
    expect(href()).not.toContain('filter=');
  });

  it('states the session, and keeps working without one from an older backend', () => {
    const { unmount } = render(<MarketMovers gainers={[item('UP')]} losers={[]} unusualVolume={[]} meta={META} />);
    expect(screen.getByTestId('movers-session').textContent).toContain('session 2026-10-23');
    unmount();
    render(<MarketMovers gainers={[item('UP')]} losers={[]} unusualVolume={[]} />);
    expect(screen.getByTestId('movers-session').textContent).not.toContain('session');
    expect(screen.getByTestId('movers-view-all').getAttribute('href')).toBe('/screener?min_close=1&min_dollar_vol_20=1000000&sort=change_1d&order=desc');
  });

  it('shows missing change and volume as dashes, never 0', () => {
    render(<MarketMovers gainers={[]} losers={[]} unusualVolume={[item('VOL', { price_change_pct: null, volume: null })]} meta={META} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Unusual Volume' }));
    const row = screen.getByRole('row', { name: /VOL/ });
    expect(row.textContent).toContain('—');
    expect(row.textContent).not.toMatch(/0\.0M|\+0\.00%/);
  });

  it('says why a list is empty, from the session data', () => {
    const noRatios = { ...META, coverage: { ...META.coverage, volume_ratio: 0 } };
    const { unmount } = render(<MarketMovers gainers={[]} losers={[]} unusualVolume={[]} meta={noRatios} />);
    expect(screen.getByTestId('movers-empty').textContent).toBe('No stock rose on 2026-10-23.');
    fireEvent.click(screen.getByRole('radio', { name: 'Unusual Volume' }));
    expect(screen.getByTestId('movers-empty').textContent).toBe('Volume ratios for the 2026-10-23 session are not available yet.');
    unmount();
    render(<MarketMovers gainers={[]} losers={[]} unusualVolume={[]} meta={META} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Unusual Volume' }));
    expect(screen.getByTestId('movers-empty').textContent).toBe('No stock traded at 2× its usual volume on 2026-10-23.');
  });
});
