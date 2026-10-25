// Overview movers card: "View all" opens the Screener ranked the same way as the visible list (a real Screener query,
// not the old `?filter=` the Screener never read).
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));

import MarketMovers from '@/components/dashboard/MarketMovers';

describe('MarketMovers', () => {
  it('links each list to the matching ranked Screener query', () => {
    render(<MarketMovers gainers={[]} losers={[]} unusualVolume={[]} />);
    const href = () => screen.getByTestId('movers-view-all').getAttribute('href');
    expect(href()).toBe('/screener?min_close=1&min_dollar_vol_20=1000000&sort=change_1d&order=desc');
    fireEvent.click(screen.getByRole('radio', { name: 'Top Losers' }));
    expect(href()).toBe('/screener?min_close=1&min_dollar_vol_20=1000000&sort=change_1d&order=asc');
    fireEvent.click(screen.getByRole('radio', { name: 'Unusual Volume' }));
    expect(href()).toBe('/screener?min_close=1&min_dollar_vol_20=1000000&min_volume_ratio=2&sort=volume_ratio&order=desc');
    expect(href()).not.toContain('filter=');
  });
});
